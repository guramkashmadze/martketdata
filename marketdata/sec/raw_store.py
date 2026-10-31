"""Raw store: every file from the SEC, kept unchanged and write-once (D-003).

Parsing fixes re-read stored files instead of downloading them again. A key is a relative
path such as `raw/sec/320193/0000320193-24-000123/index.json`. Putting a key again with
identical bytes does nothing; different bytes raise `RawObjectConflict`, because a stored
original must never change.

`LocalRawStore` keeps files under RAW_STORE_PATH (dev); `S3RawStore` keeps them in an
S3-compatible bucket (R2 or B2 in production). RAW_STORE_KIND chooses one.

Key layout (E1-S3, D-047):
- filing documents: `raw/sec/{cik}/{accession_no}/{file}`; they never change once filed;
- API responses: `raw/sec/api/{endpoint}/{cik}/{fetched_at}.json`. They change over time, so
  each fetch is a new object. Endpoints without a CIK leave it out; endpoints with more path
  parameters (companyconcept, frames, older submissions pages) add them before the time.
"""

import asyncio
import hashlib
import os
import re
import tempfile
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from marketdata.config import Settings, SettingsError
from marketdata.sec import urls

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RawStoreError(Exception):
    """The raw store refused or failed an operation."""


class RawObjectNotFound(RawStoreError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return f"no raw object at {self.key}"


class RawObjectConflict(RawStoreError):
    """The key already holds different bytes. Stored originals are never overwritten."""

    def __init__(self, key: str, stored_sha256: str, new_sha256: str) -> None:
        super().__init__(key, stored_sha256, new_sha256)
        self.key = key
        self.stored_sha256 = stored_sha256
        self.new_sha256 = new_sha256

    def __str__(self) -> str:
        return (
            f"{self.key} already holds different bytes (stored sha256 {self.stored_sha256}, "
            f"new {self.new_sha256}); raw objects are write-once"
        )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_key(key: str) -> str:
    """A key is a relative path of plain segments: letters, digits, '.', '_' and '-'."""
    segments = key.split("/")
    if any(segment in (".", "..") or not _SEGMENT.match(segment) for segment in segments):
        raise ValueError(
            f"a raw key is a relative path of letters, digits, '.', '_' and '-', got {key!r}"
        )
    return key


class RawStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str | None) -> bool:
        """Store `data` at `key`. True if written, False if the identical bytes were there."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """The stored bytes; raises RawObjectNotFound."""

    @abstractmethod
    async def exists(self, key: str) -> bool: ...


class LocalRawStore(RawStore):
    """Files under a folder. The content type is not kept here; `raw_objects` records it."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    async def put(self, key: str, data: bytes, content_type: str | None) -> bool:
        return await asyncio.to_thread(self._put, self._path(key), key, data)

    def _put(self, path: Path, key: str, data: bytes) -> bool:
        if path.exists():
            return _same_or_conflict(key, path.read_bytes(), data)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a temporary file, then link it into place: the link fails if the key appeared
        # meanwhile, and nobody ever sees a half-written file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return _same_or_conflict(key, path.read_bytes(), data)
            return True
        finally:
            os.unlink(tmp)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise RawObjectNotFound(key) from None

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)


class S3RawStore(RawStore):
    """Objects in an S3-compatible bucket, each with its sha256 in the object metadata.

    Write-once: an existing key is compared by that sha256; a new key is written with
    `If-None-Match: *`, so a concurrent writer can't replace it. boto3 is synchronous, so its
    calls run in a thread.
    """

    def __init__(self, client: "S3Client", bucket: str) -> None:
        self._s3 = client
        self.bucket = bucket

    async def put(self, key: str, data: bytes, content_type: str | None) -> bool:
        return await asyncio.to_thread(self._put, check_key(key), data, content_type)

    def _put(self, key: str, data: bytes, content_type: str | None) -> bool:
        digest = sha256(data)
        stored = self._stored_sha256(key)
        if stored is not None:
            return _same_digest_or_conflict(key, stored, digest)
        try:
            self._s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _error_code(exc) not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
            stored = self._stored_sha256(key)
            if stored is None:
                raise
            return _same_digest_or_conflict(key, stored, digest)
        return True

    def _stored_sha256(self, key: str) -> str | None:
        """The sha256 of the object at `key`, or None if there is none."""
        try:
            head = self._s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        recorded = head.get("Metadata", {}).get("sha256")
        if recorded is not None:
            return recorded
        # Not written by us: hash the bytes themselves.
        return sha256(self._read(key))

    def _read(self, key: str) -> bytes:
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                raise RawObjectNotFound(key) from None
            raise
        data = response["Body"].read()
        recorded = response.get("Metadata", {}).get("sha256")
        if recorded is not None and recorded != sha256(data):
            raise RawStoreError(f"{key}: stored bytes don't match their recorded sha256")
        return data

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read, check_key(key))

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._exists, check_key(key))

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def create_raw_store(settings: Settings) -> RawStore:
    """The store RAW_STORE_KIND selects. Fails with SettingsError when S3 settings are missing."""
    if settings.raw_store_kind == "local":
        return LocalRawStore(settings.raw_store_path)
    if settings.s3_bucket is None:
        raise SettingsError(
            "Invalid configuration:\n  - S3_BUCKET is missing; RAW_STORE_KIND=s3 needs it.\n"
            "Set it in the environment or in .env (see .env.example and docs/PLAN.md §11)."
        )
    secret = settings.s3_secret_access_key
    client = boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
        # Send checksums only where the API requires them: since boto3 1.36 the default adds
        # CRC32 headers that some S3-compatible services rejected. Integrity is checked with
        # the sha256 we store with each object.
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"mode": "standard", "max_attempts": 5},
        ),
    )
    return S3RawStore(client, settings.s3_bucket)


def fetched_at_name(fetched_at: datetime) -> str:
    """A fetch time as a key segment: 20261024T205501.123456Z (UTC, no colons)."""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    return fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def filing_key(cik: int, accession: str, file: str) -> str:
    """Where a filing document is kept, e.g. raw/sec/320193/0000320193-24-000123/index.json."""
    urls.cik10(cik)
    return check_key(f"raw/sec/{cik}/{urls.accession_no(accession)}/{file}")


def data_set_key(data_set: str, file: str) -> str:
    """Where a file of an SEC data set is kept (E8), e.g.
    raw/sec/datasets/form345/2026q3_form345.zip; a landing page under its fetch time."""
    return check_key(f"raw/sec/datasets/{data_set}/{file}")


def api_response_key(
    endpoint: str, fetched_at: datetime, *, cik: int | None = None, parts: tuple[str, ...] = ()
) -> str:
    """Where one fetch of an API response is kept."""
    segments = ["raw", "sec", "api", endpoint]
    if cik is not None:
        urls.cik10(cik)
        segments.append(str(cik))
    segments.extend(parts)
    return check_key("/".join([*segments, f"{fetched_at_name(fetched_at)}.json"]))


def key_for_url(url: str, fetched_at: datetime) -> str:
    """The key for a URL built by sec/urls.py. Raises ValueError for anything else."""
    parsed = urls.parse(url)
    if parsed is None:
        raise ValueError(f"no raw key layout for {url}; pass the key explicitly")
    if parsed.endpoint == "filing":
        if parsed.file is None or parsed.cik is None or parsed.accession_no is None:
            raise ValueError(f"{url} is a folder listing; fetch its index.json instead")
        return filing_key(parsed.cik, parsed.accession_no, parsed.file)
    if parsed.endpoint.startswith("bulk_"):
        raise ValueError(
            f"{url} is a bulk file of a gigabyte or more; fetch_and_store holds files in "
            "memory, so bulk files need a streaming fetch (PLAN.md §16)"
        )
    return api_response_key(parsed.endpoint, fetched_at, cik=parsed.cik, parts=parsed.parts)


def _same_or_conflict(key: str, stored: bytes, new: bytes) -> bool:
    if stored == new:
        return False
    raise RawObjectConflict(key, sha256(stored), sha256(new))


def _same_digest_or_conflict(key: str, stored: str, new: str) -> bool:
    if stored == new:
        return False
    raise RawObjectConflict(key, stored, new)


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))

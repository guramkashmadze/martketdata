"""E1-S2: the raw store. The S3 store runs against moto's in-process S3: no network, no Docker."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

from marketdata.config import Settings, SettingsError
from marketdata.sec.raw_store import (
    LocalRawStore,
    RawObjectConflict,
    RawObjectNotFound,
    RawStore,
    RawStoreError,
    S3RawStore,
    create_raw_store,
    sha256,
)

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

KEY = "raw/sec/320193/0000320193-24-000123/index.json"
BUCKET = "marketdata-raw-test"
DB = "postgresql://marketdata:marketdata@localhost:5432/marketdata"


@pytest.fixture
def s3() -> Iterator["S3Client"]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture(params=["local", "s3"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[RawStore]:
    if request.param == "local":
        yield LocalRawStore(tmp_path / "raw")
    else:
        yield S3RawStore(request.getfixturevalue("s3"), BUCKET)


# --- The interface, for both stores ---


async def test_put_get_exists(store: RawStore) -> None:
    assert not await store.exists(KEY)

    assert await store.put(KEY, b'{"a": 1}', "application/json") is True

    assert await store.exists(KEY)
    assert await store.get(KEY) == b'{"a": 1}'


async def test_putting_identical_bytes_again_is_a_no_op(store: RawStore) -> None:
    await store.put(KEY, b"original", "text/plain")

    assert await store.put(KEY, b"original", "text/plain") is False
    assert await store.get(KEY) == b"original"


async def test_putting_different_bytes_raises_and_keeps_the_original(store: RawStore) -> None:
    await store.put(KEY, b"original", "text/plain")

    with pytest.raises(RawObjectConflict) as excinfo:
        await store.put(KEY, b"changed", "text/plain")

    assert excinfo.value.stored_sha256 == sha256(b"original")
    assert excinfo.value.new_sha256 == sha256(b"changed")
    assert await store.get(KEY) == b"original"


async def test_empty_files_are_stored(store: RawStore) -> None:
    assert await store.put(KEY, b"", None) is True
    assert await store.get(KEY) == b""


async def test_get_missing_key(store: RawStore) -> None:
    with pytest.raises(RawObjectNotFound, match="no raw object at"):
        await store.get(KEY)


@pytest.mark.parametrize(
    "key", ["/etc/passwd", "raw/../secret", "raw//x", "raw/./x", "", "raw/a b", "raw/x/"]
)
async def test_keys_are_plain_relative_paths(store: RawStore, key: str) -> None:
    with pytest.raises(ValueError, match="raw key"):
        await store.put(key, b"x", None)


async def test_concurrent_puts_of_different_bytes_keep_exactly_one(store: RawStore) -> None:
    results = await asyncio.gather(
        *(store.put(KEY, f"version {i}".encode(), None) for i in range(8)),
        return_exceptions=True,
    )

    written = [r for r in results if r is True]
    assert len(written) == 1
    assert all(isinstance(r, RawObjectConflict) for r in results if r is not True)
    assert (await store.get(KEY)).startswith(b"version ")


# --- Local specifics ---


async def test_local_store_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    await store.put(KEY, b"data", None)
    await store.put(KEY, b"data", None)

    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == [KEY]


# --- S3 specifics ---


async def test_s3_object_has_content_type_and_sha256(s3: "S3Client") -> None:
    await S3RawStore(s3, BUCKET).put(KEY, b"{}", "application/json")

    head = s3.head_object(Bucket=BUCKET, Key=KEY)
    assert head["ContentType"] == "application/json"
    assert head["Metadata"] == {"sha256": sha256(b"{}")}


async def test_s3_compares_objects_written_by_others_by_their_bytes(s3: "S3Client") -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"from elsewhere")  # no sha256 metadata
    store = S3RawStore(s3, BUCKET)

    assert await store.put(KEY, b"from elsewhere", None) is False
    with pytest.raises(RawObjectConflict):
        await store.put(KEY, b"different", None)


async def test_s3_new_keys_are_written_only_if_absent(
    s3: "S3Client", monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that saw no object still can't replace one that appeared meanwhile."""
    store = S3RawStore(s3, BUCKET)
    await store.put(KEY, b"first writer", None)
    real_head = s3.head_object
    calls = {"n": 0}

    def head_misses_once(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            s3.head_object(Bucket=BUCKET, Key="raw/no-such-key")  # raises the real 404
        return real_head(**kwargs)

    monkeypatch.setattr(s3, "head_object", head_misses_once)

    with pytest.raises(RawObjectConflict):
        await store.put(KEY, b"second writer", None)
    assert await store.get(KEY) == b"first writer"


async def test_s3_get_checks_the_recorded_sha256(s3: "S3Client") -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"tampered", Metadata={"sha256": sha256(b"x")})

    with pytest.raises(RawStoreError, match="don't match"):
        await S3RawStore(s3, BUCKET).get(KEY)


# --- Selection by RAW_STORE_KIND ---


def test_local_store_is_the_default(tmp_path: Path) -> None:
    settings = Settings.model_validate({"database_url": DB, "raw_store_path": tmp_path})

    store = create_raw_store(settings)

    assert isinstance(store, LocalRawStore)
    assert store.root == tmp_path


async def test_s3_store_from_settings(s3: "S3Client") -> None:
    settings = Settings.model_validate(
        {
            "database_url": DB,
            "raw_store_kind": "s3",
            "s3_bucket": BUCKET,
            "s3_region": "us-east-1",
            "s3_access_key_id": "testing",
            "s3_secret_access_key": "testing",
        }
    )

    store = create_raw_store(settings)
    await store.put(KEY, b"via settings", None)

    assert isinstance(store, S3RawStore)
    assert s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read() == b"via settings"


def test_s3_store_needs_a_bucket() -> None:
    settings = Settings.model_validate({"database_url": DB, "raw_store_kind": "s3"})

    with pytest.raises(SettingsError, match="S3_BUCKET is missing"):
        create_raw_store(settings)


def test_secret_key_is_not_shown() -> None:
    settings = Settings.model_validate({"database_url": DB, "s3_secret_access_key": "hunter2"})

    assert "hunter2" not in repr(settings)

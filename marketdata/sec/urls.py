"""SEC URL builders for every endpoint in PLAN.md §8, and the parser that reverses them.

CIKs are integers everywhere in the code; they are zero-padded to 10 digits only here, where
the SEC's APIs need it. Archive paths use the unpadded CIK. Accession numbers are stored with
dashes (`0000320193-24-000123`) and lose them only in archive folder names.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

WWW = "https://www.sec.gov"
DATA = "https://data.sec.gov"

COMPANY_TICKERS_EXCHANGE = f"{WWW}/files/company_tickers_exchange.json"
COMPANY_TICKERS_MF = f"{WWW}/files/company_tickers_mf.json"
BULK_COMPANYFACTS = f"{WWW}/Archives/edgar/daily-index/xbrl/companyfacts.zip"
BULK_SUBMISSIONS = f"{WWW}/Archives/edgar/daily-index/bulkdata/submissions.zip"

# Landing pages that link to the quarterly data-set ZIPs. The ZIP names are read from these
# pages (or verified) in the epic that loads them: E8, E9 and E14. Use `www_path` for the links.
INSIDER_DATA_SETS = f"{WWW}/dera/data/form-345"
F13_DATA_SETS = f"{WWW}/dera/data/form-13f"
NPORT_DATA_SETS = f"{WWW}/dera/data/form-n-port-data-sets"

# Not an SEC URL, so never fetched through SecClient (it refuses other hosts). Used from E9.
OPENFIGI_MAPPING = "https://api.openfigi.com/v3/mapping"

MAX_CIK = 9_999_999_999
_ACCESSION = re.compile(r"^(\d{10})-?(\d{2})-?(\d{6})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SUBMISSIONS_PAGE = re.compile(r"^CIK(\d{10})-submissions-(\d{3})\.json$")


def cik10(cik: int) -> str:
    """The CIK as the SEC's APIs want it: 10 digits, zero-padded."""
    if isinstance(cik, bool) or not isinstance(cik, int) or not 1 <= cik <= MAX_CIK:
        raise ValueError(f"a CIK is an integer from 1 to {MAX_CIK}, got {cik!r}")
    return f"{cik:010d}"


def accession_no(value: str) -> str:
    """Normalize an accession number to the stored form with dashes: 0000320193-24-000123."""
    match = _ACCESSION.match(value.strip())
    if match is None:
        raise ValueError(
            f"an accession number is 18 digits, as 0000320193-24-000123 or "
            f"000032019324000123, got {value!r}"
        )
    return "-".join(match.groups())


def accession_nodash(value: str) -> str:
    """The accession number as archive folder names use it: 000032019324000123."""
    return accession_no(value).replace("-", "")


def submissions(cik: int) -> str:
    """Filing history and company details; older filings are on the pages in `filings.files`."""
    return f"{DATA}/submissions/CIK{cik10(cik)}.json"


def submissions_page(name: str) -> str:
    """An older submissions page, by the name listed in `filings.files`."""
    if not _SUBMISSIONS_PAGE.match(name):
        raise ValueError(f"expected a name like CIK0000320193-submissions-001.json, got {name!r}")
    return f"{DATA}/submissions/{name}"


def companyfacts(cik: int) -> str:
    return f"{DATA}/api/xbrl/companyfacts/CIK{cik10(cik)}.json"


def companyconcept(cik: int, concept: str, taxonomy: str = "us-gaap") -> str:
    return f"{DATA}/api/xbrl/companyconcept/CIK{cik10(cik)}/{_name(taxonomy)}/{_name(concept)}.json"


def frame_period(year: int, quarter: int | None = None, *, instant: bool = False) -> str:
    """CY2024 (a year), CY2024Q1 (a quarter) or CY2024Q1I (an instant, always quarter-dated)."""
    if not 1993 <= year <= 9999:
        raise ValueError(f"expected a four-digit year, got {year!r}")
    if quarter is None:
        if instant:
            raise ValueError("instant frames are dated by quarter, e.g. CY2024Q4I")
        return f"CY{year}"
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1 to 4, got {quarter!r}")
    return f"CY{year}Q{quarter}{'I' if instant else ''}"


def frame_unit(unit: str) -> str:
    """Units as frames URLs spell them: USD/shares becomes USD-per-shares."""
    return _name(unit.replace("/", "-per-"))


def frames(
    concept: str,
    unit: str,
    year: int,
    quarter: int | None = None,
    *,
    instant: bool = False,
    taxonomy: str = "us-gaap",
) -> str:
    """One concept for every company in one period (for sanity checks)."""
    period = frame_period(year, quarter, instant=instant)
    return (
        f"{DATA}/api/xbrl/frames/{_name(taxonomy)}/{_name(concept)}/{frame_unit(unit)}/"
        f"{period}.json"
    )


def filing_folder(cik: int, accession: str) -> str:
    cik10(cik)
    return f"{WWW}/Archives/edgar/data/{cik}/{accession_nodash(accession)}/"


def filing_index_json(cik: int, accession: str) -> str:
    """The filing's file list as JSON."""
    return filing_folder(cik, accession) + "index.json"


def filing_index_page(cik: int, accession: str) -> str:
    """The filing's index page, whose document table gives each document's type (EX-99.1)."""
    return filing_folder(cik, accession) + f"{accession_no(accession)}-index.htm"


def filing_document(cik: int, accession: str, file: str) -> str:
    """A document in the filing folder. `file` may have a folder, e.g. xslF345X05/form4.xml."""
    return filing_folder(cik, accession) + _relative_path(file)


def www_path(path: str) -> str:
    """A file on www.sec.gov by its path, e.g. a data-set ZIP linked from a landing page."""
    return f"{WWW}/{_relative_path(path.removeprefix('/'))}"


@dataclass(frozen=True)
class SecUrl:
    """What an SEC URL points at. `parts` are the endpoint's other path parameters."""

    endpoint: str
    cik: int | None = None
    accession_no: str | None = None
    file: str | None = None
    parts: tuple[str, ...] = ()


_FIXED = {
    COMPANY_TICKERS_EXCHANGE: "company_tickers_exchange",
    COMPANY_TICKERS_MF: "company_tickers_mf",
    BULK_COMPANYFACTS: "bulk_companyfacts",
    BULK_SUBMISSIONS: "bulk_submissions",
}
_PATTERNS = [
    ("submissions", re.compile(r"^/submissions/CIK(?P<cik>\d{10})\.json$")),
    (
        "submissions",
        re.compile(r"^/submissions/CIK(?P<cik>\d{10})-submissions-(?P<page>\d{3})\.json$"),
    ),
    ("companyfacts", re.compile(r"^/api/xbrl/companyfacts/CIK(?P<cik>\d{10})\.json$")),
    (
        "companyconcept",
        re.compile(
            r"^/api/xbrl/companyconcept/CIK(?P<cik>\d{10})/(?P<taxonomy>[^/]+)/(?P<concept>[^/]+)"
            r"\.json$"
        ),
    ),
    (
        "frames",
        re.compile(
            r"^/api/xbrl/frames/(?P<taxonomy>[^/]+)/(?P<concept>[^/]+)/(?P<unit>[^/]+)"
            r"/(?P<period>CY\d{4}(?:Q[1-4]I?)?)\.json$"
        ),
    ),
]
_ARCHIVE = re.compile(
    r"^/Archives/edgar/data/(?P<cik>\d{1,10})/(?P<accession>\d{18})/(?P<file>.*)$"
)


def parse(url: str) -> SecUrl | None:
    """The endpoint behind an SEC URL built by this module, or None for any other URL."""
    split = urlsplit(url)
    if split.query or split.fragment:
        return None
    clean = f"{split.scheme}://{split.netloc}{split.path}"
    if clean in _FIXED:
        return SecUrl(_FIXED[clean])
    if clean.startswith(DATA + "/"):
        for endpoint, pattern in _PATTERNS:
            match = pattern.match(split.path)
            if match is None:
                continue
            groups = match.groupdict()
            parts = tuple(
                groups[name]
                for name in ("page", "taxonomy", "concept", "unit", "period")
                if groups.get(name)
            )
            cik = int(groups["cik"]) if "cik" in groups else None
            return SecUrl(endpoint, cik=cik, parts=parts)
    if clean.startswith(WWW + "/"):
        match = _ARCHIVE.match(split.path)
        if match is not None:
            file = match["file"] or None
            if file is not None:
                file = _relative_path(file)
            return SecUrl(
                "filing",
                cik=int(match["cik"]),
                accession_no=accession_no(match["accession"]),
                file=file,
            )
    return None


def _name(value: str) -> str:
    if not _NAME.match(value):
        raise ValueError(f"expected a name of letters, digits, '.', '_' or '-', got {value!r}")
    return value


def _relative_path(path: str) -> str:
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments) or "\\" in path:
        raise ValueError(f"expected a relative path inside the folder, got {path!r}")
    return "/".join(_name(segment) for segment in segments)

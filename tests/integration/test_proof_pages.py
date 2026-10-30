"""E5b-S3: proof pages (PLAN.md §9.10). A release or a metric value with its proof, the SEC
links, the checks, the cross-check and the change history, behind HTTP Basic auth with an
`api`-scope key; errors are pages too, and each request is logged like a /v1 request."""

import base64
from typing import Any

import httpx2
import psycopg
import pytest
from api_seed import OLD, OLD_Q1_10Q, Q2_10Q, Q2_RELEASE, Q3_RELEASE, seed

from marketdata.service.clients import create_client, create_key

Conn = psycopg.AsyncConnection[Any]

SEC = "https://www.sec.gov/Archives/edgar/data/"
DISCLAIMER = "Information from public filings. Not investment advice."


def _basic(key: str, user: str = "anyone") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def auth(conn: Conn, api_key: str) -> dict[str, str]:
    await seed(conn)
    return _basic(api_key)


async def test_a_page_needs_a_key(api: httpx2.AsyncClient, conn: Conn) -> None:
    await create_client(conn, "reviewer")
    review = (await create_key(conn, "reviewer", scope="review")).key
    path = f"/proof/earnings/{Q2_RELEASE}"

    missing = await api.get(path)
    wrong = await api.get(path, headers=_basic("mdk_not-a-key"))
    garbled = await api.get(path, headers={"Authorization": "Basic %%%"})
    scope = await api.get(path, headers=_basic(review))

    for response in (missing, wrong, garbled):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == 'Basic realm="marketdata proof pages"'
        assert response.headers["content-type"].startswith("text/html")
    assert "sign in with any user name and the key as the password" in missing.text
    assert scope.status_code == 403 and "text/html" in scope.headers["content-type"]


async def test_a_release_page_shows_each_value_with_its_proof(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    response = await api.get(f"/proof/earnings/{Q2_RELEASE}", headers=auth)

    assert response.status_code == 200, response.text
    page = response.text
    assert "Acme Corp (ACME): earnings release" in page
    assert "1,000 USD" in page and "950 USD" in page and "5.3%" in page
    assert "1.25 USD per share" in page
    assert "Total revenues | 950 | 1000" in page  # the row as printed
    assert "in millions, except per share amounts" in page
    assert f'href="{SEC}1001/000100100026000020/{Q2_RELEASE}-index.htm"' in page
    assert f'href="{SEC}1001/000100100026000020/ex99.htm"' in page  # the exhibit
    assert "matched" in page and "mismatch" in page and Q2_10Q in page
    assert "release_vs_filing_mismatch" in page  # the flag, with what it means
    # The release states no period end: the quarter of the 10-Q it was checked against.
    assert "the release states no end" in page and "2026-06-30" in page
    # The history: the correction of its revenue, and the 8-K/A that amended it.
    assert "rules version 2 to 3" in page and '<td class="num">990</td>' in page
    assert f"8-K/A {Q3_RELEASE}" in page
    assert DISCLAIMER in page
    assert "<script" not in page
    assert response.headers["cache-control"] == "private, no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    logged = await (
        await conn.execute("select path, status from api_requests order by id desc limit 1")
    ).fetchone()
    assert logged == (f"/proof/earnings/{Q2_RELEASE}", 200)


async def test_a_failed_release_says_why_its_values_are_missing(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    page = (await api.get(f"/proof/earnings/{Q3_RELEASE}", headers=auth)).text

    assert 'status <span class="failed">failed</span>' in page
    assert "not in the source" in page and "the exhibit is printed as pictures" in page


async def test_text_from_a_filing_is_escaped(
    api: httpx2.AsyncClient, auth: dict[str, str], conn: Conn
) -> None:
    await conn.execute(
        "update earnings_facts set source_quote = '<script>alert(1)</script> | 1,000' "
        "where accession_no = %s and slot = 'revenue'",
        [Q2_RELEASE],
    )

    page = (await api.get(f"/proof/earnings/{Q2_RELEASE}", headers=auth)).text

    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


async def test_a_metric_page_shows_the_value_its_filings_and_history(
    api: httpx2.AsyncClient, auth: dict[str, str]
) -> None:
    own = await api.get("/proof/metrics/acme/revenue/Q/2026-06-30", headers=auth)
    inherited = await api.get("/proof/metrics/ACME/revenue/Q/2026-03-31", headers=auth)

    assert own.status_code == 200, own.text
    assert "Acme Corp (ACME): revenue" in own.text and "1,000 USD" in own.text
    assert "<code>Revenues</code>" in own.text and "reported for this period" in own.text
    assert f'href="{SEC}1001/000100100026000025/{Q2_10Q}-index.htm"' in own.text
    assert "No value on this page has changed" in own.text
    # The first quarter is in the predecessor's filings (D-068), restated once.
    assert inherited.status_code == 200, inherited.text
    assert f"CIK {OLD}, a predecessor of Acme Corp" in inherited.text
    assert OLD_Q1_10Q in inherited.text and "restatement" in inherited.text
    assert "880 USD" in inherited.text and "900 USD" in inherited.text


@pytest.mark.parametrize(
    ("path", "status", "text"),
    [
        ("/proof/earnings/0001001000-26-000099", 404, "No earnings release has the accession"),
        ("/proof/earnings/not-an-accession", 400, "accession"),
        ("/proof/metrics/ACME/revenue/Q/2020-03-31", 404, "No revenue value of ACME"),
        ("/proof/metrics/ACME/profit/Q/2026-06-30", 400, "profit"),
        ("/proof/metrics/ACME/revenue/H1/2026-06-30", 400, "H1"),
        ("/proof/metrics/ACME/revenue/Q/June", 400, "period_end must be a day"),
        ("/proof/metrics/FNDX/revenue/Q/2026-06-30", 422, "fund"),
        ("/proof/nothing/here", 404, "There is no endpoint /proof/nothing/here"),
    ],
)
async def test_errors_are_pages(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str, status: int, text: str
) -> None:
    response = await api.get(path, headers=auth)

    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert text in response.text and DISCLAIMER in response.text


async def test_pages_are_left_out_of_the_api_reference(api: httpx2.AsyncClient) -> None:
    paths = (await api.get("/openapi.json")).json()["paths"]

    assert not [path for path in paths if path.startswith("/proof")]

"""E6-S1: the MCP server's parts that need no database: the Host and Origin headers it accepts,
its error text, and how lookup_company compares names (D-109)."""

from marketdata.config import Settings
from marketdata.mcp_server.server import error_text, transport_security
from marketdata.service.companies import name_words

DATABASE_URL = "postgresql://marketdata@localhost/marketdata_test"


def test_the_server_accepts_this_machine_and_the_public_host() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=DATABASE_URL,
        public_base_url="https://md.example.com",
    )

    security = transport_security(settings)

    assert security.enable_dns_rebinding_protection is True
    assert set(security.allowed_hosts) == {
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "md.example.com",
        "md.example.com:*",
    }
    assert "https://md.example.com" in security.allowed_origins
    assert "http://localhost:*" in security.allowed_origins


def test_without_a_public_url_only_this_machine() -> None:
    settings = Settings(_env_file=None, database_url=DATABASE_URL)  # type: ignore[call-arg]

    security = transport_security(settings)

    assert set(security.allowed_hosts) == {
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
    }


def test_a_failed_call_answers_the_apis_error_envelope() -> None:
    assert error_text("not_found", "No security has the ticker ZZZZ.") == (
        '{"error":{"code":"not_found","message":"No security has the ticker ZZZZ."}}'
    )


def test_names_are_compared_as_lowercase_letters_and_digits() -> None:
    assert name_words("Coca-Cola Co.") == "coca cola co"
    assert name_words("  BRK.B ") == "brk b"
    assert name_words("JPMORGAN CHASE & CO") == "jpmorgan chase co"
    assert name_words("ეპლი") == ""  # Georgian letters: names are matched in the Latin alphabet

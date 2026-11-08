"""D-139: a requested form matches its family. Schedules 13D and 13G under both their old and
their structured codes (D-061), the 10-K and 10-Q with their transition reports, the 8-K with a
successor issuer's 8-Ks, every form with its amendment; an amendment asked for matches the
family's amendments only. Text as clients type it."""

import pytest

from marketdata.normalize.form_families import STAKE_CODES, form_family, normalized

THIRTEEN_G = ("SCHEDULE 13G", "SCHEDULE 13G/A", "SC 13G", "SC 13G/A")
THIRTEEN_D = ("SCHEDULE 13D", "SCHEDULE 13D/A", "SC 13D", "SC 13D/A")


@pytest.mark.parametrize(
    "asked", ["13G", "13g", "SCHEDULE 13G", "Schedule 13G", "schedule13g", " schedule   13G "]
)
def test_any_spelling_of_13g_matches_both_codes_and_their_amendments(asked: str) -> None:
    assert form_family(asked) == THIRTEEN_G


@pytest.mark.parametrize("asked", ["SC 13G", "sc 13g", "SC13G", "sc  13G"])
def test_the_old_code_comes_first_when_asked_for(asked: str) -> None:
    assert form_family(asked) == ("SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A")


@pytest.mark.parametrize("asked", ["13D", "SCHEDULE 13D", "schedule 13d"])
def test_13d_matches_both_codes_and_their_amendments(asked: str) -> None:
    assert form_family(asked) == THIRTEEN_D
    assert form_family("SC 13D") == ("SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A")


@pytest.mark.parametrize(
    ("asked", "matched"),
    [
        ("13G/A", ("SCHEDULE 13G/A", "SC 13G/A")),
        ("Schedule 13G/A", ("SCHEDULE 13G/A", "SC 13G/A")),
        ("sc 13g / a", ("SC 13G/A", "SCHEDULE 13G/A")),
        ("SCHEDULE 13D/A", ("SCHEDULE 13D/A", "SC 13D/A")),
        ("8-K/A", ("8-K/A", "8-K12B/A", "8-K12G3/A", "8-K15D5/A")),
        ("10-K/A", ("10-K/A", "10-KT/A")),
        ("10-q/a", ("10-Q/A", "10-QT/A")),
        ("S-1/A", ("S-1/A",)),
        ("4/A", ("4/A",)),
    ],
)
def test_an_amendment_matches_the_familys_amendments_only(
    asked: str, matched: tuple[str, ...]
) -> None:
    assert form_family(asked) == matched


@pytest.mark.parametrize(
    ("asked", "matched"),
    [
        ("10-K", ("10-K", "10-K/A", "10-KT", "10-KT/A")),
        ("10-Q", ("10-Q", "10-Q/A", "10-QT", "10-QT/A")),
        (
            "8-K",
            ("8-K", "8-K/A", "8-K12B", "8-K12B/A", "8-K12G3", "8-K12G3/A", "8-K15D5", "8-K15D5/A"),
        ),
    ],
)
def test_reports_take_in_the_forms_filed_in_their_place(
    asked: str, matched: tuple[str, ...]
) -> None:
    assert form_family(asked) == matched


@pytest.mark.parametrize(
    ("asked", "matched"),
    [
        ("10-KT", ("10-KT", "10-KT/A")),
        ("8-K12B", ("8-K12B", "8-K12B/A")),
        ("S-1", ("S-1", "S-1/A")),
        ("424B2", ("424B2", "424B2/A")),
        ("4", ("4", "4/A")),
        ("20-F", ("20-F", "20-F/A")),
        ("6-k", ("6-K", "6-K/A")),
        ("S-8 POS", ("S-8 POS", "S-8 POS/A")),  # the SEC's own amendment code: a form of its own
        ("SC 13E3", ("SC 13E3", "SC 13E3/A")),  # not a 5% stake
        ("SC TO-T", ("SC TO-T", "SC TO-T/A")),
    ],
)
def test_any_other_form_matches_itself_and_its_amendment(
    asked: str, matched: tuple[str, ...]
) -> None:
    assert form_family(asked) == matched


@pytest.mark.parametrize(
    ("typed", "code"),
    [
        ("Form 4", "4"),
        ("form  10-K", "10-K"),
        ("8‑K", "8-K"),  # a non-breaking hyphen
        ("10–Q", "10-Q"),  # an en dash
        ("sc 13g", "SC 13G"),  # a no-break space
        ("8-K /A", "8-K/A"),
        ("def 14a", "DEF 14A"),
        ("FORM", "FORM"),
    ],
)
def test_text_is_read_as_clients_type_it(typed: str, code: str) -> None:
    assert normalized(typed) == code


def test_form_4_is_form_4() -> None:
    assert form_family("Form 4") == ("4", "4/A")


@pytest.mark.parametrize("asked", ["8-K, 10-Q", "13D;13G", "/A", "", "  ", "10-K*", "(4)"])
def test_text_that_isnt_one_form_is_refused(asked: str) -> None:
    with pytest.raises(ValueError, match="one form at a time"):
        form_family(asked)


def test_the_stake_codes_are_every_13d_and_13g_code() -> None:
    assert STAKE_CODES == {*THIRTEEN_G, *THIRTEEN_D}
    assert all(set(form_family(code)) <= STAKE_CODES for code in STAKE_CODES)

"""Domain exceptions raised by service functions (PLAN.md §6). E5a's api/errors.py maps them to
the error envelope: InvalidParameter to 400, NotFound to 404. Messages say how to fix the
request where possible."""


class DomainError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class InvalidParameter(DomainError):
    """The request can't be right as written, e.g. a ticker with characters no ticker has."""


class NotFound(DomainError):
    """Nothing matches, e.g. a ticker no SEC list has."""

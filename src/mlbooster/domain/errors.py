"""Domain errors. Their messages may be shown to users; they contain no secrets or file paths."""


class DomainError(Exception):
    """Base class of all expected (user-displayable) errors."""


class InvalidInput(DomainError, ValueError):
    pass


class NotFound(DomainError, LookupError):
    def __str__(self) -> str:  # suppress LookupError's repr-style quoting
        return str(self.args[0]) if self.args else ""


class InconsistentCatalog(DomainError):
    pass

"""Errors that are part of the port contract. Infrastructure translates its own errors into these."""

from mlbooster.domain.errors import DomainError


class ProviderError(DomainError):
    """Data could not be obtained from a data provider in the expected form."""


class CredentialsMissing(ProviderError):
    pass


class AuthenticationFailed(ProviderError):
    """Credentials were rejected; the request is not retried."""


class UnexpectedResponse(ProviderError):
    """The response does not match the expected schema (HTML, empty body, redirect, missing field, too large)."""


class ProviderUnavailable(ProviderError):
    """Network error, or still 5xx/429 after retries."""


class ProviderNotImplemented(ProviderError):
    pass


class ResearchUnavailable(DomainError):
    """The web search cannot start here; the message names the next step. The rest of the report still works."""

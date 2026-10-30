"""Port sözleşmesinin parçası olan hatalar. Altyapı kendi hatalarını bunlara çevirir."""

from mlbooster.domain.errors import DomainError


class ProviderError(DomainError):
    """Bir veri sağlayıcısından beklenen biçimde veri alınamadı."""


class CredentialsMissing(ProviderError):
    pass


class AuthenticationFailed(ProviderError):
    """Kimlik bilgisi reddedildi; tekrar denenmez."""


class UnexpectedResponse(ProviderError):
    """Yanıt beklenen şemada değil (HTML, boş gövde, yönlendirme, eksik alan, aşırı büyük)."""


class ProviderUnavailable(ProviderError):
    """Ağ hatası veya tekrar denemelerden sonra hâlâ 5xx/429."""


class ProviderNotImplemented(ProviderError):
    pass

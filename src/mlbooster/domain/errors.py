"""Domain hataları. Mesajları kullanıcıya gösterilebilir; sır veya dosya yolu içermez."""


class DomainError(Exception):
    """Tüm beklenen (kullanıcıya gösterilebilir) hataların tabanı."""


class InvalidInput(DomainError, ValueError):
    pass


class NotFound(DomainError, LookupError):
    def __str__(self) -> str:  # LookupError'ın repr tarzı tırnaklamasını engelle
        return str(self.args[0]) if self.args else ""


class InconsistentCatalog(DomainError):
    pass

"""Sır değer nesnesi: repr/str/log çıktısında asla açık değer göstermez."""

from __future__ import annotations

import os
from collections.abc import Iterable


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not value:
            raise ValueError("Boş sır")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('********')"

    __str__ = __repr__

    def __reduce__(self):  # pickle ile sızmayı engelle
        raise TypeError("Secret serileştirilemez")


class EnvSecretProvider:
    def get(self, env_var: str) -> Secret | None:
        value = os.environ.get(env_var, "").strip()
        return Secret(value) if value else None


def redact(text: str, secrets: Iterable[Secret | None]) -> str:
    """Hata mesajlarına sızabilecek sır değerlerini maskeler (ör. sunucunun yankıladığı header)."""
    for secret in secrets:
        if secret is not None:
            text = text.replace(secret.reveal(), "********")
    return text

"""Yerel web paneli: `mlbooster web` ile yalnızca bu bilgisayarda (127.0.0.1) açılır."""

from mlbooster.interfaces.web.app import create_app

__all__ = ["create_app"]

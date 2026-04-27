"""SAIS client istisna hiyerarşisi."""
from __future__ import annotations

from typing import Optional


class SaisClientError(Exception):
    """Tüm ``sais_domain.clients`` hatalarının base'i."""


class SaisAuthError(SaisClientError):
    """Login başarısız veya ticket yenilenemedi."""


class SaisResponseError(SaisClientError):
    """Beklenmedik HTTP statüsü, parse edilemeyen yanıt veya eksik field."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_text: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

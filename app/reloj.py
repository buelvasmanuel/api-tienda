"""Hora actual de la aplicación, en un solo sitio.

Siempre en UTC y sin zona (naive), que es como MySQL/MariaDB guardan un
DATETIME: así lo que se escribe y lo que se lee se comparan sin conversiones.

Todo el código pide la hora con `reloj.ahora()` (no `from app.reloj import
ahora`) para que los tests puedan adelantar el reloj con monkeypatch y probar
caducidades sin esperar.
"""

from datetime import UTC, datetime


def ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

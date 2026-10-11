"""Límite de peticiones por IP para /auth (con la librería `limits`).

Es la segunda línea de defensa: la primera es el bloqueo por cuenta, que vive
en la base de datos (app/routers/auth.py). Por eso los límites por IP son
altos: en un aula, toda la clase sale a internet por la misma IP pública
(docs/CONTRATO_AUTH.md §7). En el login solo cuentan los intentos fallidos.

Los contadores viven en memoria: se reinician si Render duerme el servicio, y
solo hay un proceso, así que no hace falta compartirlos (Redis).

Antes se usaba slowapi; se sustituyó por esta dependencia de unas líneas sobre
`limits` (la misma librería que slowapi usa por dentro) porque slowapi 0.1.10
usa una API de asyncio que desaparece en Python 3.16 y obligaba a leer sus
atributos internos para calcular el Retry-After.
"""

import logging
import math
import time

from fastapi import Request
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from app.config import settings
from app.errores import demasiados_intentos

log = logging.getLogger("app.auth")

LOGIN = parse("120/minute")  # todas las peticiones de login (techo de carga)
REGISTRO = parse("60/hour")
REFRESH = parse("300/minute")  # un refresh no se adivina: solo frena abusos de carga
# Logins fallidos (401) por IP. Se comprueba antes de calcular ningún hash.
FALLOS_LOGIN = parse("30/minute")


class Limitador:
    """Contadores en memoria con ventana fija. `activo` permite apagarlo en los tests."""

    def __init__(self) -> None:
        self.activo = True
        self._almacen = MemoryStorage()
        self._estrategia = FixedWindowRateLimiter(self._almacen)

    def reiniciar(self) -> None:
        self._almacen.reset()

    def superado(self, limite: RateLimitItem, espacio: str, clave: str, *, contar: bool = True) -> int | None:
        """Cuenta una petición (o solo mira, si contar=False). Devuelve los
        segundos que faltan para poder seguir si el límite está superado, o None."""
        if not self.activo:
            return None
        permitido = (self._estrategia.hit if contar else self._estrategia.test)(limite, espacio, clave)
        if permitido:
            return None
        reinicio, _ = self._estrategia.get_window_stats(limite, espacio, clave)
        return max(1, math.ceil(reinicio - time.time()))


limitador = Limitador()


def ip_cliente(request: Request) -> str:
    """IP del cliente según IP_CLIENTE (ver app/config.py)."""
    conexion = request.client.host if request.client else "desconocida"
    modo = settings.IP_CLIENTE
    if modo == "conexion":
        return conexion
    if modo.startswith("x-forwarded-for:"):
        posicion = int(modo.split(":")[1])
        ips = [ip.strip() for ip in request.headers.get("x-forwarded-for", "").split(",") if ip.strip()]
        return ips[-posicion] if len(ips) >= posicion else conexion
    return request.headers.get(modo, "").strip() or conexion


def _rechazar(request: Request, motivo: str, segundos: int):
    # La cabecera cruda permite comprobar en los logs de Render que IP_CLIENTE
    # elige bien la IP (ver docs/ARQUITECTURA.md §9).
    log.warning("%s ip=%s ruta=%s x-forwarded-for=%r", motivo, ip_cliente(request), request.url.path,
                request.headers.get("x-forwarded-for"))
    return demasiados_intentos(segundos)


def por_ip(limite: RateLimitItem, espacio: str):
    """Dependencia: 429 si esta IP supera `limite` en ese endpoint.
    Uso: @router.post(..., dependencies=[Depends(por_ip(LOGIN, "login"))])."""

    def comprobar(request: Request) -> None:
        segundos = limitador.superado(limite, espacio, ip_cliente(request))
        if segundos is not None:
            raise _rechazar(request, "límite por IP superado", segundos)

    return comprobar


def comprobar_fallos_login(request: Request) -> None:
    """429 si esta IP ya acumuló demasiados logins fallidos en la ventana."""
    segundos = limitador.superado(FALLOS_LOGIN, "login-fallidos", ip_cliente(request), contar=False)
    if segundos is not None:
        raise _rechazar(request, "demasiados logins fallidos", segundos)


def registrar_fallo_login(request: Request) -> None:
    limitador.superado(FALLOS_LOGIN, "login-fallidos", ip_cliente(request))

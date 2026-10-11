"""Límite de peticiones por IP (slowapi) para /auth.

Es la segunda línea de defensa: la primera es el bloqueo por cuenta, que vive
en la base de datos (app/routers/auth.py). Por eso los límites por IP son
altos: en un aula, toda la clase sale a internet por la misma IP pública
(docs/CONTRATO_AUTH.md §7). En el login solo cuentan los intentos fallidos.

Estos contadores viven en memoria: se reinician si Render duerme el servicio,
y solo hay un proceso, así que no hace falta compartirlos (Redis).
"""

import logging
import math
import time

from fastapi import Request
from limits import parse
from fastapi.exception_handlers import http_exception_handler
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.errores import demasiados_intentos

log = logging.getLogger("app.auth")


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


limiter = Limiter(key_func=ip_cliente)

LOGIN = "120/minute"  # todas las peticiones de login (techo de carga)
REGISTRO = "60/hour"
REFRESH = "300/minute"  # un refresh no se adivina: solo frena abusos de carga
# Logins fallidos (401) por IP. Se comprueba antes de calcular ningún hash.
FALLOS_LOGIN = parse("30/minute")
_ESPACIO_FALLOS = "login-fallidos"


def _segundos_hasta(reinicio: float) -> int:
    return max(1, math.ceil(reinicio - time.time()))


def comprobar_fallos_login(request: Request) -> None:
    """429 si esta IP ya acumuló demasiados logins fallidos en la ventana."""
    if not limiter.enabled:
        return
    ip = ip_cliente(request)
    if not limiter.limiter.test(FALLOS_LOGIN, _ESPACIO_FALLOS, ip):
        reinicio, _ = limiter.limiter.get_window_stats(FALLOS_LOGIN, _ESPACIO_FALLOS, ip)
        log.warning("demasiados logins fallidos ip=%s x-forwarded-for=%r",
                    ip, request.headers.get("x-forwarded-for"))
        raise demasiados_intentos(_segundos_hasta(reinicio))


def registrar_fallo_login(request: Request) -> None:
    if limiter.enabled:
        limiter.limiter.hit(FALLOS_LOGIN, _ESPACIO_FALLOS, ip_cliente(request))


async def limite_superado(request: Request, exc: RateLimitExceeded):
    """429 con el formato del contrato y Retry-After en segundos."""
    limite, claves = request.state.view_rate_limit
    reinicio, _ = limiter.limiter.get_window_stats(limite, *claves)
    segundos = _segundos_hasta(reinicio)
    # La cabecera cruda ayuda a comprobar en los logs de Render que IP_CLIENTE
    # elige bien la IP (ver docs/ARQUITECTURA.md, despliegue).
    log.warning("límite por IP superado ip=%s ruta=%s x-forwarded-for=%r",
                ip_cliente(request), request.url.path, request.headers.get("x-forwarded-for"))
    return await http_exception_handler(request, demasiados_intentos(segundos))

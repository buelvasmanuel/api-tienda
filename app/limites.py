"""Límite de peticiones por IP (slowapi) para /auth.

Es la segunda línea de defensa: la primera es el bloqueo por cuenta, que vive
en la base de datos (app/routers/auth.py). Estos contadores viven en memoria:
se reinician si Render duerme el servicio, y solo hay un proceso, así que no
hace falta compartirlos (Redis).
"""

import logging
import math
import time

from fastapi import Request
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

LOGIN = "10/minute"
REGISTRO = "5/hour"
REFRESH = "30/minute"


async def limite_superado(request: Request, exc: RateLimitExceeded):
    """429 con el formato del contrato y Retry-After en segundos."""
    limite, claves = request.state.view_rate_limit
    reinicio, _ = limiter.limiter.get_window_stats(limite, *claves)
    segundos = max(1, math.ceil(reinicio - time.time()))
    # La cabecera cruda ayuda a comprobar en los logs de Render que IP_CLIENTE
    # elige bien la IP (ver docs/ARQUITECTURA.md, despliegue).
    log.warning("límite por IP superado ip=%s ruta=%s x-forwarded-for=%r",
                ip_cliente(request), request.url.path, request.headers.get("x-forwarded-for"))
    return await http_exception_handler(request, demasiados_intentos(segundos))

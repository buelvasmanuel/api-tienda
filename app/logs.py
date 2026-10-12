"""Logs estructurados (structlog) con un id por petición.

- Cada línea es un JSON con `event`, `level`, `logger`, `momento` (UTC) y,
  dentro de una petición o de un WebSocket, `request_id`.
- El id viaja en la cabecera `X-Request-ID` de cada respuesta. Si el cliente
  envía uno con formato válido, se reutiliza (para correlacionar con sus logs).
- Sin datos personales ni secretos:
  - las IPs se guardan como un hash con clave (`ip_hash`): sirve para ver que
    varias peticiones vienen de la misma IP, pero no permite saber cuál;
  - se registra el id del usuario, nunca su alias ni su contraseña;
  - un último procesador borra cualquier token o IP que se cuele en un texto
    (por ejemplo, en los mensajes de uvicorn), como red de seguridad.
- El log de acceso de uvicorn (que lleva la IP) se sustituye por uno propio:
  `peticion` con método, ruta, estado y milisegundos.
"""

import hashlib
import hmac
import logging
import re
import sys
import time
import uuid

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

# ---------- Datos personales y secretos ----------

_CLAVE_IP = hmac.new(settings.clave_tokens, b"api-tienda/hash-de-ip", hashlib.sha256).digest()


def hash_ip(ip: str | None) -> str | None:
    """Hash con clave (no reversible sin la clave) de una IP, 12 caracteres."""
    if not ip:
        return None
    return hmac.new(_CLAVE_IP, ip.encode(), hashlib.sha256).hexdigest()[:12]


_TOKEN_PASETO = re.compile(r"v4\.(local|public)\.[A-Za-z0-9_\-.]+")
_BEARER = re.compile(r"(?i)bearer\s+\S+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")


def limpiar(texto: str) -> str:
    texto = _TOKEN_PASETO.sub("[token]", texto)
    texto = _BEARER.sub("Bearer [token]", texto)
    texto = _IPV4.sub(lambda m: f"ip:{hash_ip(m.group())}", texto)
    return _IPV6.sub(lambda m: f"ip:{hash_ip(m.group())}", texto)


_CLAVES_PROHIBIDAS = {"password", "password_actual", "password_nueva", "token", "access_token", "refresh_token",
                      "authorization", "alias", "ip"}


def redactar(_logger, _metodo, evento: dict) -> dict:
    """Último filtro de cada línea: quita campos sensibles y limpia los textos."""
    evento.pop("color_message", None)  # copia con códigos de color de terminal que añade uvicorn
    for clave in list(evento):
        if clave.lower() in _CLAVES_PROHIBIDAS:
            evento[clave] = "[oculto]"
        elif isinstance(evento[clave], str):
            evento[clave] = limpiar(evento[clave])
    return evento


# ---------- Configuración ----------

def _procesadores_comunes():
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="momento"),
        structlog.stdlib.ExtraAdder(),
    ]


def formateador() -> logging.Formatter:
    """Formatter de logging que pasa por structlog (también para los logs de
    otras librerías, como uvicorn) y escribe JSON."""
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_procesadores_comunes(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redactar,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )


def configurar() -> None:
    structlog.configure(
        processors=[*_procesadores_comunes(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    manejador = logging.StreamHandler(sys.stderr)
    manejador.setFormatter(formateador())
    raiz = logging.getLogger()
    raiz.handlers = [manejador]
    raiz.setLevel(logging.INFO)
    # uvicorn configura sus propios loggers al arrancar (antes de importar la
    # app): se les quitan los manejadores para que pasen por el de arriba.
    for nombre in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(nombre)
        logger.handlers = []
        logger.propagate = True
    # Su log de acceso lleva la IP del cliente: se sustituye por "peticion".
    logging.getLogger("uvicorn.access").disabled = True


def obtener(nombre: str):
    return structlog.get_logger(nombre)


# ---------- Id por petición ----------

CABECERA = "x-request-id"
_ID_VALIDO = re.compile(r"[A-Za-z0-9\-]{8,64}")
log_acceso = obtener("app.acceso")


class IdDePeticion:
    """Middleware ASGI (peticiones y WebSockets): pone `request_id` en todos los
    logs de esa petición, lo devuelve en X-Request-ID y escribe una línea
    `peticion` al terminar."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        recibido = dict(scope["headers"]).get(CABECERA.encode(), b"").decode("latin-1")
        id_peticion = recibido if _ID_VALIDO.fullmatch(recibido) else uuid.uuid4().hex
        clear_contextvars()
        bind_contextvars(request_id=id_peticion)
        inicio = time.perf_counter()
        estado = None

        async def enviar(mensaje: Message) -> None:
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
                mensaje.setdefault("headers", [])
                mensaje["headers"] = [*mensaje["headers"], (b"x-request-id", id_peticion.encode())]
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            if scope["type"] == "http":
                log_acceso.info("peticion", metodo=scope["method"], ruta=scope["path"], estado=estado,
                                ms=round(1000 * (time.perf_counter() - inicio), 1))
            clear_contextvars()

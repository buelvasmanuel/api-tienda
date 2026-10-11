"""Middlewares propios: tamaño máximo del cuerpo y errores inesperados.

Van en app/main.py *dentro* de CORS (se añaden antes que él), para que sus
respuestas (413, 500) lleven las cabeceras CORS: si no, Flutter Web vería un
error de red/CORS en lugar del error real.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("app.errores")

TAMANO_MAXIMO = 64 * 1024  # bytes; el cuerpo legítimo más grande (un lote de 50) ronda 5 KB


def _error(estado: int, codigo: str, mensaje: str) -> JSONResponse:
    return JSONResponse(status_code=estado, content={"detail": {"codigo": codigo, "mensaje": mensaje}})


class LimiteDeCuerpo:
    """413 si el cuerpo pasa de TAMANO_MAXIMO, sin leerlo entero en memoria
    (Render free tiene 512 MB).

    - Con Content-Length (lo normal), se responde 413 sin leer nada.
    - Sin él (envío por trozos), se cuenta lo que llega; al pasarse se corta el
      cuerpo, se descarta lo que responda la app y se envía el 413. Sin lanzar
      excepciones: FastAPI convierte en 400 cualquier error al leer el cuerpo.
    """

    def __init__(self, app: ASGIApp, maximo: int = TAMANO_MAXIMO):
        self.app, self.maximo = app, maximo

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        demasiado = _error(413, "cuerpo_demasiado_grande", f"El cuerpo de la petición supera {self.maximo} bytes")

        longitud = dict(scope["headers"]).get(b"content-length")
        if longitud is not None and longitud.isdigit() and int(longitud) > self.maximo:
            return await demasiado(scope, receive, send)

        recibido = 0
        excedido = False

        async def recibir() -> Message:
            nonlocal recibido, excedido
            if excedido:
                return {"type": "http.request", "body": b"", "more_body": False}
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibido += len(mensaje.get("body", b""))
                if recibido > self.maximo:
                    excedido = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return mensaje

        async def enviar(mensaje: Message) -> None:
            if not excedido:  # si se pasó, la respuesta de la app se descarta
                await send(mensaje)

        await self.app(scope, recibir, enviar)
        if excedido:
            await demasiado(scope, receive, send)


async def errores_inesperados(request: Request, call_next):
    """Cualquier excepción no controlada → 500 en JSON con código (y con CORS).
    El detalle va solo al log, nunca al cliente."""
    try:
        return await call_next(request)
    except Exception:
        log.exception("error no controlado en %s %s", request.method, request.url.path)
        return _error(500, "error_interno", "Error interno del servidor")

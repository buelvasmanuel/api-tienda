"""Refresh token en una cookie Partitioned (CHIPS) para la web.

Contrato: docs/CONTRATO_AUTH.md §6.3. Es opcional y lo pide el cliente con la
cabecera `X-Refresh-En-Cookie: 1`; sin ella todo sigue como antes (el APK y
las apps de escritorio mandan y reciben el refresh en el cuerpo).

Con la cabecera:
- Las respuestas que abren o renuevan una sesión (registro, login, refresh,
  cambio de contraseña, login con passkey) guardan el refresh en la cookie
  `__Host-refresh` (HttpOnly, Secure, SameSite=None, Partitioned) y devuelven
  `refresh_token: null` en el cuerpo: JavaScript nunca lo ve.
- /auth/refresh y /auth/logout lo leen de la cookie si no viene en el cuerpo.
- /auth/logout y /auth/logout-todo borran la cookie.

Defensas contra CSRF (la cookie viaja sola en cada petición del navegador):
1. Partitioned: el navegador solo la envía cuando la página de arriba es la
   web que la recibió (buelvasmanuel.github.io), no desde otra web.
2. La cabecera propia obliga al navegador a pedir permiso (preflight de CORS)
   antes de enviarla desde otro origen, y CORS solo lo da a CORS_ORIGINS.
3. Se comprueba `Origin` contra CORS_ORIGINS (que debe ser explícito: con "*"
   el modo cookie se rechaza).
4. Si la petición lleva cuerpo, debe ser JSON (un formulario HTML no puede).
"""

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError

from app import schemas
from app.config import settings
from app.errores import error, no_autorizado

CABECERA = "x-refresh-en-cookie"
# __Host-: el navegador la rechaza si no es Secure, con Path=/ y sin Domain
# (nadie puede fijarla desde otro subdominio).
NOMBRE = "__Host-refresh"


def _es_json(request: Request) -> bool:
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return tipo == "application/json"


def _tiene_cuerpo(request: Request) -> bool:
    return request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers


def modo_cookie(request: Request) -> bool:
    """Dependencia: True si el cliente pide el refresh en cookie. 403 si lo
    pide desde un origen no permitido; 415 si manda un cuerpo que no es JSON."""
    if request.headers.get(CABECERA) != "1":
        return False
    origen = (request.headers.get("origin") or "").rstrip("/")
    if not origen or origen not in settings.origenes_con_credenciales:
        raise error(status.HTTP_403_FORBIDDEN, "origen_no_permitido",
                    "El refresh en cookie solo se acepta desde la web de la app")
    if _tiene_cuerpo(request) and not _es_json(request):
        raise error(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "json_obligatorio",
                    "El cuerpo debe ser JSON (Content-Type: application/json)")
    return True


def _poner(respuesta: Response, valor: str, segundos: int) -> None:
    respuesta.set_cookie(NOMBRE, valor, max_age=segundos, path="/", secure=True, httponly=True,
                         samesite="none", partitioned=True)


def entregar(respuesta: Response, en_cookie: bool, salida: schemas.TokensOut) -> schemas.TokensOut:
    """Devuelve los tokens; en modo cookie, el refresh va en la cookie y no en el cuerpo."""
    if en_cookie:
        _poner(respuesta, salida.refresh_token, salida.refresh_expires_in)
        salida.refresh_token = None
    return salida


def borrar(respuesta: Response) -> None:
    # Para borrar una cookie Partitioned hay que repetir sus atributos.
    _poner(respuesta, "", 0)


def cabeceras_para_borrar() -> dict[str, str]:
    """Set-Cookie que borra la cookie, para añadirlo a una respuesta de error."""
    respuesta = Response()
    borrar(respuesta)
    return {"Set-Cookie": respuesta.headers["set-cookie"]}


def refresh_pedido(request: Request, datos: schemas.RefreshEntrada, en_cookie: bool) -> str:
    """El refresh de la petición: el del cuerpo o, en modo cookie, el de la cookie."""
    if datos.refresh_token is not None:
        return datos.refresh_token
    if not en_cookie:
        # Igual que antes de existir el modo cookie: el campo es obligatorio.
        raise RequestValidationError([{"type": "missing", "loc": ("body", "refresh_token"),
                                       "msg": "Field required"}])
    valor = request.cookies.get(NOMBRE)
    if not valor or len(valor) > 512:
        raise no_autorizado("refresh_invalido", "No hay sesión guardada en este navegador; inicia sesión")
    return valor

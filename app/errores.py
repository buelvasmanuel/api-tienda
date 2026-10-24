"""Errores con código para el cliente: {"detail": {"codigo": ..., "mensaje": ...}}.

Los códigos y qué debe hacer el cliente con cada uno están en
docs/CONTRATO_AUTH.md (§2). El cliente decide por `codigo`, nunca por `mensaje`.
"""

from fastapi import HTTPException, status
from fastapi.exceptions import RequestValidationError


def error(estado: int, codigo: str, mensaje: str, cabeceras: dict[str, str] | None = None,
          **extra) -> HTTPException:
    """`extra` añade campos al detalle, p. ej. aliases=[...] en un 409 del lote."""
    return HTTPException(estado, detail={"codigo": codigo, "mensaje": mensaje} | extra, headers=cabeceras)


def no_autorizado(codigo: str, mensaje: str) -> HTTPException:
    """401 con WWW-Authenticate, como pide el estándar para Bearer."""
    return error(status.HTTP_401_UNAUTHORIZED, codigo, mensaje, {"WWW-Authenticate": "Bearer"})


def demasiados_intentos(segundos: int) -> HTTPException:
    return error(
        status.HTTP_429_TOO_MANY_REQUESTS, "demasiados_intentos",
        f"Demasiados intentos. Vuelve a intentarlo en {segundos} s",
        {"Retry-After": str(segundos)},
    )


def password_comun(campo: str) -> HTTPException:
    """422 con código (CONTRATO_AUTH §2): la contraseña está en la lista de
    contraseñas comunes (app/contrasenas.py). `campo` dice cuál del cuerpo."""
    return error(422, "password_comun",
                 "Esa contraseña es demasiado común y se adivina enseguida: elige otra", campo=campo)


def validacion_password_igual_al_alias(campo: str) -> RequestValidationError:
    """422 con el mismo formato que cualquier error de validación de FastAPI,
    sin repetir la contraseña en la respuesta."""
    return RequestValidationError([{
        "type": "value_error", "loc": ("body", campo),
        "msg": "La contraseña no puede ser igual al alias",
    }])

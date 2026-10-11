"""Funciones y constantes comunes a varios archivos de tests."""

from app import seguridad
from app.models import Usuario

PASSWORD = "clave-de-pruebas-123"


def cabeceras(usuario: Usuario, familia_id: str = "f" * 32) -> dict[str, str]:
    """Authorization con un access token válido para ese usuario."""
    return {"Authorization": f"Bearer {seguridad.crear_access(usuario.id, familia_id)}"}


def codigo(respuesta) -> str:
    """El `codigo` de un error del contrato: {"detail": {"codigo": ...}}."""
    return respuesta.json()["detail"]["codigo"]

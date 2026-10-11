"""Genera una clave nueva para CLAVE_TOKENS (32 bytes aleatorios en base64url).

Uso (desde la raíz del proyecto):
    python -m scripts.generar_clave

Cópiala en el .env local o en las variables de Render. Cambiarla invalida todos
los access tokens (los clientes refrescan solos) pero no los refresh.
"""

import base64
import secrets


def generar() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


if __name__ == "__main__":
    print(generar())

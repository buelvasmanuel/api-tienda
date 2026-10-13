"""Arranque del contenedor de la API (docker compose, solo para desarrollo local).

Deja todo listo sin pasos a mano y después arranca uvicorn:
1. CLAVE_TOKENS: si no viene en el entorno, la genera la primera vez y la
   guarda en /datos/.env (en el volumen; /app/.env es un enlace a él). Así las
   sesiones sobreviven a `docker compose down` / `up`, y los comandos con
   `docker compose exec api …` (p. ej. scripts.crear_usuario) también la ven.
2. `alembic upgrade head`.
3. Si no hay estudiantes, inserta los 300 de prueba.
4. Si no está el modelo de riesgo, lo entrena (en el volumen /app/modelos).

Va en Python y no en un .sh para que no le afecten los finales de línea CRLF
que Git pone en Windows.
"""

import os
import subprocess
import sys
from pathlib import Path

from scripts.generar_clave import generar

RUTA_ENV = Path("/datos/.env")


def asegurar_clave_tokens() -> None:
    if os.environ.get("CLAVE_TOKENS") or RUTA_ENV.exists():
        return
    RUTA_ENV.touch(mode=0o600)
    RUTA_ENV.write_text(f"CLAVE_TOKENS={generar()}\n")
    print("Generada una CLAVE_TOKENS nueva (se guarda en el volumen).", flush=True)


def ejecutar(*orden: str) -> None:
    print("→", " ".join(orden), flush=True)
    subprocess.run([sys.executable, "-m", *orden], check=True)


def main() -> None:
    asegurar_clave_tokens()
    ejecutar("alembic", "upgrade", "head")

    # Se importa después de crear la clave: app.config la exige.
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.ml import RUTA_MODELO
    from app.models import Estudiante

    with SessionLocal() as db:
        hay_estudiantes = db.scalar(select(func.count()).select_from(Estudiante))
    if not hay_estudiantes:
        ejecutar("scripts.seed_estudiantes")
    if not RUTA_MODELO.exists():
        ejecutar("scripts.entrenar_modelo", "--sin-graficas")

    # Mismos límites del WebSocket que en producción (render.yaml).
    os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
                          "--ws-max-size", "65536", "--ws-max-queue", "4"])


if __name__ == "__main__":
    main()

"""Crea un usuario en la base del .env. Es la única forma de crear el primer
admin: el registro público (/auth/registro) siempre crea jugadores.

La contraseña se pide por teclado (no se escribe en la línea de comandos, que
queda en el historial de la terminal).

Uso (desde la raíz del proyecto):
    python -m scripts.crear_usuario Profe_Admin --rol admin
    python -m scripts.crear_usuario Profe_Lopez --rol docente

Contra Aiven, cargando antes sus variables:
    set -a; source .env.aiven; set +a
    python -m scripts.crear_usuario Profe_Admin --rol admin
"""

import argparse
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy import select

from app import seguridad
from app.database import SessionLocal
from app.models import ROLES, Usuario
from app.schemas import RegistroEntrada


def pedir_password() -> str:
    password = getpass.getpass("Contraseña (10-128 caracteres): ")
    if password != getpass.getpass("Repítela: "):
        sys.exit("Las contraseñas no coinciden.")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alias")
    parser.add_argument("--rol", choices=ROLES, default="jugador")
    args = parser.parse_args()

    try:
        # Mismas reglas que el registro público (alias y contraseña).
        datos = RegistroEntrada(alias=args.alias, password=pedir_password())
    except ValidationError as e:
        sys.exit("Datos no válidos:\n" + "\n".join(f"- {err['msg']}" for err in e.errors()))

    with SessionLocal() as db:
        if db.scalar(select(Usuario.id).where(Usuario.alias == datos.alias)) is not None:
            sys.exit(f"Ya existe un usuario con el alias {datos.alias!r}.")
        usuario = Usuario(alias=datos.alias, rol=args.rol,
                          hash_password=seguridad.hashear_password(datos.password))
        db.add(usuario)
        db.commit()
        print(f"Usuario creado: id={usuario.id} alias={usuario.alias} rol={usuario.rol}")


if __name__ == "__main__":
    main()

"""Desactiva una cuenta a través de la API, entrando como admin.

Antes de tocar nada comprueba que el id corresponde al alias esperado (así un
id mal escrito no desactiva la cuenta de otra persona). Desactivar cierra
también todas las sesiones de esa cuenta. Al terminar cierra la sesión del
admin y muestra las cuentas que quedan activas.

El alias del admin y la contraseña se piden por teclado (la contraseña sin
eco); nada de eso ni los tokens se imprime ni se guarda.

Uso (desde la raíz del proyecto; por defecto, contra producción):
    python -m scripts.desactivar_cuenta 20 Prueba_V1101_20261024
    python -m scripts.desactivar_cuenta 20 Prueba_V1101_20261024 --url http://127.0.0.1:8000
"""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

URL_PRODUCCION = "https://api-tienda-wjac.onrender.com"
ADMIN_POR_DEFECTO = "Detective_Manu"

# (método, ruta, cuerpo, token) -> (estado HTTP, cuerpo JSON o texto)
Pedir = Callable[[str, str, dict | None, str | None], tuple[int, Any]]


def cliente_http(url: str) -> Pedir:
    base = url.rstrip("/")

    def pedir(metodo: str, ruta: str, cuerpo: dict | None = None, token: str | None = None) -> tuple[int, Any]:
        cabeceras = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            cabeceras["Authorization"] = f"Bearer {token}"
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        peticion = urllib.request.Request(base + ruta, data=datos, method=metodo, headers=cabeceras)
        try:
            # Margen para el arranque en frío de Render (más de 1 min tras días sin uso).
            with urllib.request.urlopen(peticion, timeout=120) as r:
                contenido = r.read()
                return r.status, json.loads(contenido) if contenido else None
        except urllib.error.HTTPError as e:
            contenido = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(contenido)
            except ValueError:
                return e.code, contenido[:300]

    return pedir


def _error(estado: int, cuerpo: Any) -> str:
    """Estado y código de error del contrato, sin volcar la respuesta entera."""
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("detail"), dict):
        detalle = cuerpo["detail"]
        return f"{estado} {detalle.get('codigo')}: {detalle.get('mensaje')}"
    return f"{estado} {str(cuerpo)[:200]}"


def _resumen(usuario: dict) -> str:
    estado = "activa" if usuario["activo"] else "desactivada"
    return f"id={usuario['id']} alias={usuario['alias']} rol={usuario['rol']} ({estado})"


def desactivar(pedir: Pedir, usuario_id: int, alias: str, alias_admin: str, password: str) -> int:
    """Hace todo el proceso y devuelve el código de salida (0 si fue bien)."""
    estado, tokens = pedir("POST", "/auth/login", {"alias": alias_admin, "password": password}, None)
    if estado != 200:
        print(f"No se pudo iniciar sesión como {alias_admin}: {_error(estado, tokens)}")
        return 1
    token, refresh = tokens["access_token"], tokens["refresh_token"]
    try:
        estado, usuarios = pedir("GET", "/usuarios", None, token)
        if estado != 200:
            print(f"No se pudo leer la lista de cuentas: {_error(estado, usuarios)}")
            return 1
        usuario = next((u for u in usuarios if u["id"] == usuario_id), None)
        if usuario is None:
            print(f"No existe ninguna cuenta con id {usuario_id}: no se toca nada.")
            return 1
        print("Antes:  ", _resumen(usuario))
        if usuario["alias"] != alias:
            print(f"El id {usuario_id} es de {usuario['alias']!r}, no de {alias!r}: no se toca nada.")
            return 1
        if not usuario["activo"]:
            print("Ya estaba desactivada: no hace falta cambiar nada.")
        else:
            estado, usuario = pedir("PATCH", f"/usuarios/{usuario_id}", {"activo": False}, token)
            if estado != 200:
                print(f"No se pudo desactivar: {_error(estado, usuario)}")
                return 1
            print("Después:", _resumen(usuario))

        estado, usuarios = pedir("GET", "/usuarios", None, token)
        if estado == 200:
            activas = [u for u in usuarios if u["activo"]]
            print(f"Cuentas activas ({len(activas)} de {len(usuarios)}):")
            for u in activas:
                print("  ", _resumen(u))
        return 0
    finally:
        estado, cuerpo = pedir("POST", "/auth/logout", {"refresh_token": refresh}, token)
        print("Sesión del admin cerrada." if estado == 204
              else f"Aviso: no se pudo cerrar la sesión del admin: {_error(estado, cuerpo)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", type=int, help="id de la cuenta que se desactiva")
    parser.add_argument("alias", help="alias que debe tener esa cuenta")
    parser.add_argument("--url", default=URL_PRODUCCION, help=f"URL de la API (por defecto {URL_PRODUCCION})")
    args = parser.parse_args()

    print(f"API: {args.url}")
    alias_admin = input(f"Alias del admin [{ADMIN_POR_DEFECTO}]: ").strip() or ADMIN_POR_DEFECTO
    password = getpass.getpass("Contraseña: ")
    print("Conectando (si el servidor estaba dormido, puede tardar más de un minuto)…", flush=True)
    sys.exit(desactivar(cliente_http(args.url), args.id, args.alias, alias_admin, password))


if __name__ == "__main__":
    main()

"""Contraseñas demasiado comunes (auditoría 2026-10-13, C1).

app/contrasenas_comunes.txt: unas 10 000 contraseñas reales de 10 caracteres
o más (el mínimo de la API), en minúsculas, de las listas de SecLists
(licencia MIT, commit a61e8f5):
  Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt
  Passwords/Common-Credentials/xato-net-10-million-passwords-100000.txt
más unas pocas en español añadidas a mano ("contraseña123", "universidad"…).
Son las primeras que prueba cualquier ataque de diccionario.

Se comprueba al fijar una contraseña (registro, cambio, restablecimiento del
admin, scripts.crear_usuario), no en el login: no deja fuera a nadie que ya
la tenga. Sin mayúsculas: "Universidad1" cuenta como "universidad1".
"""

from pathlib import Path

_COMUNES = frozenset((Path(__file__).with_name("contrasenas_comunes.txt")
                      .read_text(encoding="utf-8").splitlines()))


def es_comun(password: str) -> bool:
    return password.lower() in _COMUNES

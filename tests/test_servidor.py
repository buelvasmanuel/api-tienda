"""Límites del servidor (uvicorn) que no se ven desde el TestClient.

uvicorn acepta por defecto mensajes de WebSocket de 16 MiB y guarda 32 por
conexión, también antes de autenticarse (auditoría 2026-10-13). Se limitan
en la orden de arranque: aquí se comprueba que siguen en render.yaml y en el
arranque de Docker, y que uvicorn de verdad corta un mensaje grande.
"""

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

RAIZ = Path(__file__).resolve().parent.parent
MAXIMO = 65536


def orden_de_render() -> list[str]:
    servicio = yaml.safe_load((RAIZ / "render.yaml").read_text())["services"][0]
    return shlex.split(servicio["startCommand"])


def valor(orden: list[str], opcion: str) -> int:
    return int(orden[orden.index(opcion) + 1])


def test_render_limita_los_mensajes_del_websocket():
    orden = orden_de_render()
    assert valor(orden, "--ws-max-size") == MAXIMO
    assert valor(orden, "--ws-max-queue") <= 8


def test_docker_usa_los_mismos_limites():
    codigo = (RAIZ / "scripts" / "arranque_docker.py").read_text()
    assert f'"--ws-max-size", "{MAXIMO}"' in codigo
    assert '"--ws-max-queue", "4"' in codigo


@pytest.fixture(scope="module")
def servidor():
    """uvicorn de verdad, arrancado con la orden de render.yaml."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    orden = [a.replace("$PORT", str(puerto)) for a in orden_de_render()]
    orden[orden.index("--host") + 1] = "127.0.0.1"
    proceso = subprocess.Popen([sys.executable, "-m", *orden], cwd=RAIZ, env=os.environ.copy(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", puerto), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn no arrancó")
        yield f"ws://127.0.0.1:{puerto}/duelos/{'0' * 32}/ws"
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)


def test_un_mensaje_grande_corta_la_conexion_antes_de_autenticarse(servidor):
    with connect(servidor) as ws:
        ws.send(json.dumps({"tipo": "auth", "token": "x" * (MAXIMO + 1)}))
        with pytest.raises(ConnectionClosed) as e:
            ws.recv(timeout=5)
    assert e.value.rcvd.code == 1009  # "message too big"


def test_un_mensaje_normal_sigue_funcionando(servidor):
    with connect(servidor) as ws:
        ws.send(json.dumps({"tipo": "ping"}))  # no es auth: la API avisa y cierra con 4401
        assert json.loads(ws.recv(timeout=5)) == {"tipo": "error", "codigo": 4401, "motivo": "no_autenticado"}

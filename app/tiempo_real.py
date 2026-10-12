"""Central de avisos del WebSocket del duelo (CONTRATO_DUELO §8).

Guarda en memoria qué WebSockets tiene abiertos cada jugador en cada duelo y
les reenvía los avisos. Solo avisa: el estado vive en la base, así que si el
proceso se reinicia (o Render lo duerme) se pierden las conexiones, no el
duelo; los clientes reconectan y se resincronizan con GET /duelos/{id}.

Los endpoints REST son síncronos (corren en hilos) y los WebSockets viven en
el bucle de asyncio: `avisar` sirve desde cualquier hilo.
"""

import asyncio
import logging
import threading
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("app.duelos")


class Central:
    def __init__(self) -> None:
        self._conexiones: dict[str, dict[int, set[WebSocket]]] = defaultdict(lambda: defaultdict(set))
        self._bucles: dict[WebSocket, asyncio.AbstractEventLoop] = {}
        self._candado = threading.Lock()

    def conectar(self, duelo_id: str, usuario_id: int, ws: WebSocket) -> None:
        with self._candado:
            self._conexiones[duelo_id][usuario_id].add(ws)
            self._bucles[ws] = asyncio.get_running_loop()

    def desconectar(self, duelo_id: str, usuario_id: int, ws: WebSocket) -> None:
        with self._candado:
            self._conexiones[duelo_id][usuario_id].discard(ws)
            self._bucles.pop(ws, None)
            if not self._conexiones[duelo_id][usuario_id]:
                del self._conexiones[duelo_id][usuario_id]
            if not self._conexiones[duelo_id]:
                del self._conexiones[duelo_id]

    def en_linea(self, duelo_id: str, usuario_id: int) -> bool:
        with self._candado:
            return bool(self._conexiones.get(duelo_id, {}).get(usuario_id))

    def avisar(self, duelo_id: str, usuario_id: int, mensaje: dict) -> None:
        """Envía `mensaje` a todos los WebSockets de ese jugador en ese duelo.
        Se puede llamar desde un hilo (endpoints REST) o desde el bucle."""
        with self._candado:
            destinos = [(ws, self._bucles[ws]) for ws in self._conexiones.get(duelo_id, {}).get(usuario_id, ())]
        try:
            actual = asyncio.get_running_loop()
        except RuntimeError:  # llamada desde un hilo (endpoint REST síncrono)
            actual = None
        for ws, bucle in destinos:
            if bucle is actual:
                actual.create_task(self._enviar(ws, mensaje))
            else:
                asyncio.run_coroutine_threadsafe(self._enviar(ws, mensaje), bucle)

    @staticmethod
    async def _enviar(ws: WebSocket, mensaje: dict) -> None:
        try:
            await ws.send_json(mensaje)
        except Exception:  # noqa: BLE001  (conexión ya cerrada: el cliente se resincroniza al volver)
            log.info("no se pudo enviar un aviso: el WebSocket ya estaba cerrado")

    def vaciar(self) -> None:
        """Solo para los tests."""
        with self._candado:
            self._conexiones.clear()
            self._bucles.clear()


central = Central()

"""Límite por IP con los contadores en Redis (LIMITES_URI=redis://…).

Producción usa memory:// (una sola instancia). Redis se usa en Docker local
para aprender cómo se compartirían los contadores entre varias instancias de
la API. Si no hay un Redis en REDIS_URL_TESTS (por defecto, el de localhost),
estos tests se saltan. En CI hay uno (.github/workflows/tests.yml). En local:
    docker run --rm -p 127.0.0.1:6379:6379 redis:8.2-alpine
"""

import os
import uuid

import pytest
from limits import parse

from app.config import settings
from app.limites import Limitador

URI = os.environ.get("REDIS_URL_TESTS", "redis://localhost:6379/15")


def _hay_redis() -> bool:
    import redis

    try:
        return redis.Redis.from_url(URI, socket_connect_timeout=0.5).ping()
    except redis.RedisError:
        return False


requiere_redis = pytest.mark.skipif(not _hay_redis(), reason=f"no hay Redis en {URI}")


@pytest.fixture
def espacio():
    """Un espacio de claves propio por test: no choca con otros ni con restos."""
    return f"test-{uuid.uuid4().hex}"


def test_produccion_usa_memoria():
    assert settings.LIMITES_URI == "memory://"


@requiere_redis
def test_cuenta_y_corta_como_en_memoria(espacio):
    limitador = Limitador(URI)
    limite = parse("3/minute")

    resultados = [limitador.superado(limite, espacio, "1.2.3.4") for _ in range(4)]

    assert resultados[:3] == [None, None, None]
    assert 1 <= resultados[3] <= 60  # segundos hasta poder seguir (Retry-After)
    assert limitador.superado(limite, espacio, "5.6.7.8") is None  # otra IP, otro contador


@requiere_redis
def test_dos_instancias_comparten_los_contadores(espacio):
    """Lo que Redis aporta: dos procesos de la API ven los mismos contadores.
    Con memory:// cada uno tendría los suyos y el límite real sería el doble."""
    a, b = Limitador(URI), Limitador(URI)
    limite = parse("4/minute")

    for limitador in (a, b, a, b):
        assert limitador.superado(limite, espacio, "1.2.3.4") is None

    assert a.superado(limite, espacio, "1.2.3.4") is not None
    assert b.superado(limite, espacio, "1.2.3.4") is not None


@requiere_redis
def test_mirar_sin_contar(espacio):
    limitador = Limitador(URI)
    limite = parse("1/minute")

    assert limitador.superado(limite, espacio, "ip", contar=False) is None
    assert limitador.superado(limite, espacio, "ip") is None  # el primero cuenta
    assert limitador.superado(limite, espacio, "ip", contar=False) is not None


def test_en_memoria_no_se_comparten(espacio):
    a, b = Limitador("memory://"), Limitador("memory://")
    limite = parse("1/minute")

    assert a.superado(limite, espacio, "ip") is None
    assert b.superado(limite, espacio, "ip") is None

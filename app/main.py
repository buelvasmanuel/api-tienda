import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth, estadisticas, estudiantes, prediccion

# Logs de la app (eventos de seguridad: bloqueos, reuso de tokens...). uvicorn
# solo configura sus propios loggers; sin esto los INFO de "app.*" no se ven.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
logging.getLogger("app").addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")

# Orígenes de CORS_ORIGINS (ver app/config.py). En local vale "*" (cualquier
# origen, p. ej. Flutter Web en localhost:XXXX); en producción, solo el dominio
# de la app web. Las apps nativas (APK) no usan CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(estudiantes.router)
app.include_router(estadisticas.router)
app.include_router(prediccion.router)


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import middleware
from app.config import settings
from app.database import get_db
from app.errores import error
from app.routers import auth, duelos, estadisticas, estudiantes, juego, laboratorio, prediccion, usuarios

# Logs de la app (eventos de seguridad: bloqueos, reuso de tokens...). uvicorn
# solo configura sus propios loggers; sin esto los INFO de "app.*" no se ven.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
logging.getLogger("app").addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")


@app.exception_handler(RequestValidationError)
async def errores_de_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Los 422 de FastAPI, pero sin el campo "input": repetiría en la respuesta
    lo que se envió, contraseñas incluidas (y las respuestas pueden acabar en
    logs o cachés)."""
    errores = [{clave: valor for clave, valor in e.items() if clave != "input"} for e in exc.errors()]
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errores}))


# Orden de los middlewares: el último que se añade es el más externo. Quedan,
# de fuera a dentro: cabeceras de seguridad → CORS → errores inesperados →
# límite del cuerpo → app. Los dos propios van dentro de CORS para que sus
# respuestas (500, 413) lleven sus cabeceras.
app.add_middleware(middleware.LimiteDeCuerpo)
app.middleware("http")(middleware.errores_inesperados)

# Orígenes de CORS_ORIGINS (ver app/config.py). En local vale "*" (cualquier
# origen, p. ej. Flutter Web en localhost:XXXX); en producción, solo el dominio
# de la app web. Las apps nativas (APK) no usan CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    # Sin esto, Flutter Web no puede leer Retry-After en un 429.
    expose_headers=["Retry-After"],
    max_age=600,  # el navegador reutiliza el preflight 10 min
)

# Cabeceras de seguridad (OWASP) en todas las respuestas. No hay CSP: esta API
# devuelve JSON, y la única página HTML (/docs) carga Swagger desde un CDN.
CABECERAS_SEGURIDAD = {
    "Strict-Transport-Security": "max-age=31536000",  # solo HTTPS (los navegadores la ignoran en http)
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next):
    respuesta = await call_next(request)
    for nombre, valor in CABECERAS_SEGURIDAD.items():
        respuesta.headers.setdefault(nombre, valor)
    return respuesta


app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(estudiantes.router)
app.include_router(estadisticas.router)
app.include_router(prediccion.router)
app.include_router(laboratorio.router)
app.include_router(juego.router)
app.include_router(duelos.router)


@app.get("/")
def raiz():
    """Healthcheck de Render: no toca la base a propósito, para que Render no
    reinicie la API si Aiven tarda en responder (p. ej. al despertar)."""
    return {"mensaje": "API funcionando"}


@app.get("/salud")
def salud(db: Session = Depends(get_db)):
    """Comprueba también la conexión con la base (SELECT 1): para diagnosticar,
    no como healthcheck de Render."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logging.getLogger("app.errores").exception("/salud: la base de datos no responde")
        raise error(503, "base_de_datos_no_disponible", "No hay conexión con la base de datos")
    return {"estado": "ok", "base_de_datos": "ok"}

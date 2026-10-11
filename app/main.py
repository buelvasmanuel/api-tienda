import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app import limites
from app.config import settings
from app.routers import auth, estadisticas, estudiantes, prediccion, usuarios

# Logs de la app (eventos de seguridad: bloqueos, reuso de tokens...). uvicorn
# solo configura sus propios loggers; sin esto los INFO de "app.*" no se ven.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
logging.getLogger("app").addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")
app.state.limiter = limites.limiter
app.add_exception_handler(RateLimitExceeded, limites.limite_superado)


@app.exception_handler(RequestValidationError)
async def errores_de_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Los 422 de FastAPI, pero sin el campo "input": repetiría en la respuesta
    lo que se envió, contraseñas incluidas (y las respuestas pueden acabar en
    logs o cachés)."""
    errores = [{clave: valor for clave, valor in e.items() if clave != "input"} for e in exc.errors()]
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errores}))


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


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

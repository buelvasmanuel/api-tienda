from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import categorias, estadisticas, estudiantes, productos

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")

# SOLO DESARROLLO: "*" permite peticiones desde cualquier origen (p. ej. Flutter
# Web en localhost:XXXX). En producción, lista aquí los orígenes permitidos.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(productos.router)
app.include_router(categorias.router)
app.include_router(estudiantes.router)
app.include_router(estadisticas.router)


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

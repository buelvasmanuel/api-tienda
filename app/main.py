from fastapi import FastAPI

from app.routers import categorias, estadisticas, estudiantes, productos

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")

app.include_router(productos.router)
app.include_router(categorias.router)
app.include_router(estudiantes.router)
app.include_router(estadisticas.router)


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

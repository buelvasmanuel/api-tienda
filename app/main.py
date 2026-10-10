from fastapi import FastAPI

from app.routers import productos

# Las tablas se crean y modifican con migraciones de Alembic (alembic upgrade head).
app = FastAPI(title="API Tienda")
app.include_router(productos.router)


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

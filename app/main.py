from fastapi import FastAPI

from app.database import Base, engine
from app.routers import productos

Base.metadata.create_all(bind=engine)  # crea las tablas si no existen

app = FastAPI(title="API Tienda")
app.include_router(productos.router)


@app.get("/")
def raiz():
    return {"mensaje": "API funcionando"}

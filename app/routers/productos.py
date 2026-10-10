from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/productos", tags=["Productos"])


def _buscar(db: Session, producto_id: int) -> models.Producto:
    producto = db.get(models.Producto, producto_id)
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return producto


@router.get("/", response_model=list[schemas.ProductoOut])
def listar(db: Session = Depends(get_db)):
    return db.scalars(select(models.Producto)).all()


@router.get("/{producto_id}", response_model=schemas.ProductoOut)
def obtener(producto_id: int, db: Session = Depends(get_db)):
    return _buscar(db, producto_id)


@router.post("/", response_model=schemas.ProductoOut, status_code=status.HTTP_201_CREATED)
def crear(datos: schemas.ProductoCreate, db: Session = Depends(get_db)):
    producto = models.Producto(**datos.model_dump())
    db.add(producto)
    db.commit()
    db.refresh(producto)
    return producto


@router.put("/{producto_id}", response_model=schemas.ProductoOut)
def actualizar(producto_id: int, datos: schemas.ProductoCreate, db: Session = Depends(get_db)):
    producto = _buscar(db, producto_id)
    for campo, valor in datos.model_dump().items():
        setattr(producto, campo, valor)
    db.commit()
    db.refresh(producto)
    return producto


@router.delete("/{producto_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar(producto_id: int, db: Session = Depends(get_db)):
    producto = _buscar(db, producto_id)
    db.delete(producto)
    db.commit()

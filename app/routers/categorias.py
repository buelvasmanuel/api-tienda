from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/categorias", tags=["Categorías"])


def _buscar(db: Session, categoria_id: int) -> models.Categoria:
    categoria = db.get(models.Categoria, categoria_id)
    if not categoria:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    return categoria


def _comprobar_nombre_libre(db: Session, nombre: str, excluir_id: int | None = None) -> None:
    """Lanza 409 si otra categoría ya usa ese nombre.

    La comparación la hace MariaDB con la collation de la columna
    (utf8mb4_unicode_ci), así que "ropa" y "Ropa" cuentan como el mismo nombre.
    """
    consulta = select(models.Categoria.id).where(models.Categoria.nombre == nombre)
    if excluir_id is not None:
        consulta = consulta.where(models.Categoria.id != excluir_id)
    if db.scalar(consulta) is not None:
        raise HTTPException(status_code=409, detail="Ya existe una categoría con ese nombre")


def _guardar(db: Session, categoria: models.Categoria) -> models.Categoria:
    """Hace commit; si dos peticiones chocan por el UNIQUE, responde 409."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una categoría con ese nombre")
    db.refresh(categoria)
    return categoria


@router.get("/", response_model=list[schemas.CategoriaOut])
def listar(db: Session = Depends(get_db)):
    return db.scalars(select(models.Categoria)).all()


@router.get("/{categoria_id}", response_model=schemas.CategoriaOut)
def obtener(categoria_id: int, db: Session = Depends(get_db)):
    return _buscar(db, categoria_id)


@router.get("/{categoria_id}/productos", response_model=list[schemas.ProductoOut])
def listar_productos(categoria_id: int, db: Session = Depends(get_db)):
    return _buscar(db, categoria_id).productos


@router.post("/", response_model=schemas.CategoriaOut, status_code=status.HTTP_201_CREATED)
def crear(datos: schemas.CategoriaCreate, db: Session = Depends(get_db)):
    _comprobar_nombre_libre(db, datos.nombre)
    categoria = models.Categoria(**datos.model_dump())
    db.add(categoria)
    return _guardar(db, categoria)


@router.put("/{categoria_id}", response_model=schemas.CategoriaOut)
def actualizar(categoria_id: int, datos: schemas.CategoriaCreate, db: Session = Depends(get_db)):
    categoria = _buscar(db, categoria_id)
    _comprobar_nombre_libre(db, datos.nombre, excluir_id=categoria_id)
    for campo, valor in datos.model_dump().items():
        setattr(categoria, campo, valor)
    return _guardar(db, categoria)


@router.delete("/{categoria_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar(categoria_id: int, db: Session = Depends(get_db)):
    categoria = _buscar(db, categoria_id)
    db.delete(categoria)  # sus productos quedan con categoria_id = NULL (ON DELETE SET NULL)
    db.commit()

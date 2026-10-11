from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencias import requiere_rol

# Leer es público; crear, modificar y borrar, solo un admin.
solo_admin = [Depends(requiere_rol("admin"))]

router = APIRouter(prefix="/estudiantes", tags=["Estudiantes"])


def _buscar(db: Session, estudiante_id: int) -> models.Estudiante:
    estudiante = db.get(models.Estudiante, estudiante_id)
    if not estudiante:
        raise HTTPException(status_code=404, detail="Estudiante no encontrado")
    return estudiante


@router.get("/", response_model=list[schemas.EstudianteOut])
def listar(db: Session = Depends(get_db)):
    return db.scalars(select(models.Estudiante).order_by(models.Estudiante.id)).all()


@router.get("/{estudiante_id}", response_model=schemas.EstudianteOut)
def obtener(estudiante_id: int, db: Session = Depends(get_db)):
    return _buscar(db, estudiante_id)


@router.post("/", response_model=schemas.EstudianteOut, status_code=status.HTTP_201_CREATED,
             dependencies=solo_admin)
def crear(datos: schemas.EstudianteCreate, db: Session = Depends(get_db)):
    estudiante = models.Estudiante(**datos.model_dump())
    db.add(estudiante)
    db.commit()
    db.refresh(estudiante)
    return estudiante


@router.put("/{estudiante_id}", response_model=schemas.EstudianteOut, dependencies=solo_admin)
def actualizar(estudiante_id: int, datos: schemas.EstudianteCreate, db: Session = Depends(get_db)):
    estudiante = _buscar(db, estudiante_id)
    for campo, valor in datos.model_dump().items():
        setattr(estudiante, campo, valor)
    db.commit()
    db.refresh(estudiante)
    return estudiante


@router.delete("/{estudiante_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=solo_admin)
def eliminar(estudiante_id: int, db: Session = Depends(get_db)):
    estudiante = _buscar(db, estudiante_id)
    db.delete(estudiante)
    db.commit()

"""Genera estudiantes de prueba realistas en la base del .env.

Los datos NO son independientes entre sí: el promedio se calcula a partir de
las horas de estudio, la asistencia y la carrera (más ruido), para que sirvan
después en un modelo predictivo de riesgo académico (promedio < 3.0).

Uso (desde la raíz del proyecto):
    python -m scripts.seed_estudiantes                 # 300 estudiantes
    python -m scripts.seed_estudiantes --cantidad 500
    python -m scripts.seed_estudiantes --reset         # borra los existentes antes
"""

import argparse
import random
import sys
from decimal import Decimal

from faker import Faker
from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app.models import Estudiante

SEMILLA = 42  # misma semilla + mismas versiones de Faker = mismos datos

# carrera: (peso para elegirla, efecto sobre el promedio)
CARRERAS = {
    "Ingeniería de Sistemas": (18, -0.10),
    "Administración": (16, +0.05),
    "Medicina": (12, -0.15),
    "Derecho": (14, 0.00),
    "Psicología": (12, +0.10),
    "Contaduría": (11, 0.00),
    "Arquitectura": (8, -0.05),
    "Diseño Gráfico": (9, +0.12),
}

# ciudad: peso (Antioquia pesa más; Medellín, sobre todo)
CIUDADES = {
    "Medellín": 30, "Envigado": 7, "Itagüí": 6, "Bello": 6, "Sabaneta": 4,
    "Rionegro": 4, "La Estrella": 3, "Caldas": 2, "Copacabana": 2, "Apartadó": 2,
    "Bogotá": 8, "Cali": 5, "Barranquilla": 3, "Cartagena": 3,
    "Bucaramanga": 3, "Pereira": 3, "Manizales": 3, "Montería": 2,
}

# Más estudiantes en los primeros semestres (hay deserción con los años).
PESOS_SEMESTRE = [14, 13, 12, 11, 10, 10, 9, 8, 7, 6]

# Coeficientes de la fórmula del promedio (ver calcular_promedio).
BASE = 3.40
POR_HORA = 0.035  # cada hora semanal por encima de la media suma 0.035
POR_PUNTO_ASISTENCIA = 0.02  # cada punto de asistencia por encima de la media suma 0.02
MEDIA_HORAS = 14
MEDIA_ASISTENCIA = 82
RUIDO = 0.30  # desviación del ruido: lo que el modelo NO podrá explicar


def _recortar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def calcular_promedio(horas: int, asistencia: int, carrera: str, rng: random.Random) -> Decimal:
    """promedio = BASE + efecto_horas + efecto_asistencia + efecto_carrera + ruido.

    Los efectos se miden respecto a la media (14 h, 82 %): un estudiante
    "típico" de una carrera neutra queda en BASE antes del ruido.
    """
    promedio = (
        BASE
        + POR_HORA * (horas - MEDIA_HORAS)
        + POR_PUNTO_ASISTENCIA * (asistencia - MEDIA_ASISTENCIA)
        + CARRERAS[carrera][1]
        + rng.gauss(0, RUIDO)
    )
    return Decimal(f"{_recortar(promedio, 0, 5):.2f}")


def generar_estudiante(fake: Faker, rng: random.Random) -> Estudiante:
    carrera = rng.choices(list(CARRERAS), weights=[p for p, _ in CARRERAS.values()])[0]
    ciudad = rng.choices(list(CIUDADES), weights=list(CIUDADES.values()))[0]
    semestre = rng.choices(range(1, 11), weights=PESOS_SEMESTRE)[0]

    # Edad ligada al semestre (~17 años en 1.º, ~23 en 10.º); un 5 % empezó tarde.
    edad = 16.5 + 0.6 * semestre + rng.gauss(0, 1.2)
    if rng.random() < 0.05:
        edad += rng.uniform(4, 12)
    edad = round(_recortar(edad, 16, 35))

    horas = round(_recortar(rng.gauss(MEDIA_HORAS, 6), 0, 40))
    # Quien estudia más suele faltar menos: algo de relación entre ambas.
    asistencia = round(_recortar(
        MEDIA_ASISTENCIA + 0.5 * (horas - MEDIA_HORAS) + rng.gauss(0, 11), 0, 100
    ))

    return Estudiante(
        nombre=fake.first_name(),
        apellido=fake.last_name(),
        edad=edad,
        carrera=carrera,
        semestre=semestre,
        promedio=calcular_promedio(horas, asistencia, carrera, rng),
        ciudad=ciudad,
        horas_estudio=horas,
        asistencia=asistencia,
    )


def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor < 1:
        raise argparse.ArgumentTypeError("debe ser un entero mayor que 0")
    return valor


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera estudiantes de prueba.")
    parser.add_argument("--cantidad", type=_entero_positivo, default=300,
                        help="cuántos estudiantes crear (por defecto: 300)")
    parser.add_argument("--reset", action="store_true",
                        help="borra los estudiantes existentes antes de insertar")
    args = parser.parse_args()

    fake = Faker("es_CO")
    fake.seed_instance(SEMILLA)
    rng = random.Random(SEMILLA)

    with SessionLocal() as db:
        base = db.get_bind().url.database
        existentes = db.scalar(select(func.count()).select_from(Estudiante))

        if existentes and not args.reset:
            print(f"La base '{base}' ya tiene {existentes} estudiantes. "
                  "Usa --reset para borrarlos y generar de nuevo.", file=sys.stderr)
            return 1

        if existentes:
            db.execute(delete(Estudiante))
            print(f"Borrados {existentes} estudiantes de '{base}'.")

        estudiantes = [generar_estudiante(fake, rng) for _ in range(args.cantidad)]
        # Se cuenta antes del commit: después, los atributos quedan "expirados"
        # y leerlos con la sesión cerrada lanzaría DetachedInstanceError.
        en_riesgo = sum(e.promedio < 3 for e in estudiantes)
        db.add_all(estudiantes)
        db.commit()  # el borrado y la inserción van en la misma transacción

    print(f"Insertados {len(estudiantes)} estudiantes en '{base}' "
          f"({en_riesgo} en riesgo, {100 * en_riesgo / len(estudiantes):.1f} %).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

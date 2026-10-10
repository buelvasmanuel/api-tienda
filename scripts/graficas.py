"""Genera una gráfica PNG por cada endpoint de estadísticas de la API.

La API tiene que estar en marcha. Uso (desde la raíz del proyecto):
    python -m scripts.graficas
    python -m scripts.graficas --url http://127.0.0.1:8001 --salida graficas
"""

import argparse
import sys
from pathlib import Path

import httpx2
import matplotlib

matplotlib.use("Agg")  # sin ventanas: solo genera archivos
import matplotlib.pyplot as plt  # noqa: E402

# endpoint: (título, etiqueta del eje de valores, horizontal, límite del eje, sufijo del valor)
GRAFICAS = {
    "por-carrera": ("Estudiantes por carrera", "Estudiantes", True, None, ""),
    "por-edad": ("Estudiantes por edad", "Estudiantes", False, None, ""),
    "por-semestre": ("Estudiantes por semestre", "Estudiantes", False, None, ""),
    "por-ciudad": ("Top 10 ciudades", "Estudiantes", True, None, ""),
    "promedio-por-carrera": ("Promedio por carrera", "Promedio (0 - 5)", True, (0, 5), ""),
    "riesgo-por-carrera": ("Estudiantes en riesgo (promedio < 3.0)", "% en riesgo", True, (0, 100), " %"),
}


def obtener(cliente: httpx2.Client, endpoint: str) -> list[dict]:
    respuesta = cliente.get(f"/estudiantes/estadisticas/{endpoint}")
    respuesta.raise_for_status()
    return respuesta.json()


def dibujar(datos: list[dict], titulo: str, eje: str, horizontal: bool,
            limite: tuple[int, int] | None, sufijo: str, archivo: Path) -> None:
    etiquetas = [d["etiqueta"] for d in datos]
    valores = [d["valor"] for d in datos]

    fig, ax = plt.subplots(figsize=(9, 5.5), layout="constrained")
    if horizontal:
        # Invertidas para que el primero (el mayor) quede arriba.
        barras = ax.barh(etiquetas[::-1], valores[::-1], color="#3b6ea5")
        ax.set_xlabel(eje)
        if limite:
            ax.set_xlim(*limite)
    else:
        barras = ax.bar(etiquetas, valores, color="#3b6ea5")
        ax.set_ylabel(eje)
        if limite:
            ax.set_ylim(*limite)

    ax.bar_label(barras, labels=[f"{v:g}{sufijo}" for v in (valores[::-1] if horizontal else valores)],
                 padding=3, fontsize=9)
    ax.set_title(titulo, fontsize=14, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(x=0.08) if horizontal else ax.margins(y=0.08)

    fig.savefig(archivo, dpi=120)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera gráficas PNG desde la API.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="URL base de la API")
    parser.add_argument("--salida", type=Path, default=Path("graficas"), help="carpeta de salida")
    args = parser.parse_args()

    args.salida.mkdir(exist_ok=True)
    try:
        with httpx2.Client(base_url=args.url, timeout=10) as cliente:
            for endpoint, (titulo, eje, horizontal, limite, sufijo) in GRAFICAS.items():
                datos = obtener(cliente, endpoint)
                if not datos:
                    print(f"  {endpoint}: sin datos (¿ejecutaste el seed?), se omite")
                    continue
                archivo = args.salida / f"{endpoint}.png"
                dibujar(datos, titulo, eje, horizontal, limite, sufijo, archivo)
                print(f"  {archivo}  ({len(datos)} barras)")
    except httpx2.ConnectError:
        print(f"No se pudo conectar a {args.url}. ¿Está la API en marcha?\n"
              "  uvicorn app.main:app --reload", file=sys.stderr)
        return 1
    except httpx2.HTTPStatusError as error:
        print(f"La API respondió {error.response.status_code} en {error.request.url}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

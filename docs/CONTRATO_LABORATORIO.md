# Contrato del Laboratorio de Rescate (Fase 4)

> Contrato entre `api-tienda` y `app_estudiantes` para el Modo 3 del juego
> («El Laboratorio de Rescate», PROPUESTA §2.2). Versión **1.1**, 2026-10-11.
>
> **v1.1 (Fase 5):** `resolver` guarda cada intento y añade a su respuesta
> `xp_ganada`, `record_estrellas`, `insignias_nuevas` y `progreso`
> (CONTRATO_JUEGO.md §6). Solo añade campos: lo demás no cambia.
> Autenticación, formato de errores con `codigo` y comportamiento del
> interceptor: los de [CONTRATO_AUTH.md](CONTRATO_AUTH.md).

---

## 1. Resumen

| Pieza | Valor |
|---|---|
| Modelo del juego | Regresión logística **congelada** en `app/modelo_juego.json` (versión `2026-10-11`). No cambia con los deploys ni con los datos: solo cambia si alguien la vuelve a congelar a propósito (`scripts/congelar_modelo_juego.py`) y hace commit |
| Explicación | Aporte **exacto** de cada variable: `coef × (x − media)` en el espacio escalado del modelo. Coincide con SHAP (`LinearExplainer`) |
| Caso de rescate | Un estudiante real con riesgo ≥ 60 % que se puede bajar de 50 % con 5 fichas |
| Intervenciones | `estudio`: +2 h de estudio/semana, 1 ficha, tope 40 h. `tutoria`: +10 puntos de asistencia, 2 fichas, tope 100 % |
| Estrellas | Las calcula **siempre el servidor** a partir de las intervenciones. El cliente nunca envía estrellas ni probabilidades |
| Aviso | Todas las respuestas llevan `aviso`: son estimaciones del modelo (correlación, no causalidad). El cliente lo muestra en la pantalla de explicación |

### Qué modelo usa cada endpoint

| Endpoint | Modelo |
|---|---|
| `POST /prediccion/riesgo`, `GET /prediccion/metricas` (pestaña Predicción) | El del build (se reentrena en cada deploy y gana el de mejor F2) — **sin cambios** |
| `POST /prediccion/explicacion`, `POST /prediccion/riesgo/lote`, `/laboratorio/*` | El **modelo del juego**, congelado |

Hoy los dos dan la misma probabilidad: el del juego se congeló con los mismos
datos, el mismo reparto entrenamiento/prueba (semilla 42) y el mismo pipeline
que ganó en el build. Si en el futuro cambian los datos o gana el árbol, solo
la pestaña Predicción cambiaría; el juego no. Cada respuesta del juego dice
qué modelo usó (`modelo`).

---

## 2. Tipos comunes

```text
Entrada      = {"horas_estudio": 0–40, "asistencia": 0–100, "semestre": 1–10,
                "edad": 16–35, "carrera": texto}           ← igual que /prediccion/riesgo
Probabilidad = número 0–1 con 4 decimales (riesgo de que el promedio sea < 3.0)
Variable     = "horas_estudio" | "asistencia" | "semestre" | "edad" | "carrera"
Intervencion = "estudio" | "tutoria"
```

**`ModeloOut`**
```json
{"nombre": "Regresión Logística (juego)", "version": "2026-10-11"}
```

**`ExplicacionOut`**
```json
{
  "probabilidad": 0.8123,
  "en_riesgo": true,
  "base": {"probabilidad": 0.1557, "logit": -1.6912},
  "logit": 1.4652,
  "aportes": [
    {"variable": "asistencia",    "valor": 58,         "aporte": 1.9071,  "peso": 0.5634, "efecto": "sube"},
    {"variable": "horas_estudio", "valor": 6,          "aporte": 0.8102,  "peso": 0.2393, "efecto": "sube"},
    {"variable": "carrera",       "valor": "Medicina", "aporte": 0.3820,  "peso": 0.1128, "efecto": "sube"},
    {"variable": "edad",          "valor": 19,         "aporte": 0.1974,  "peso": 0.0583, "efecto": "sube"},
    {"variable": "semestre",      "valor": 2,          "aporte": -0.0886, "peso": 0.0262, "efecto": "baja"}
  ],
  "modelo": {"nombre": "Regresión Logística (juego)", "version": "2026-10-11"},
  "aviso": "Estimación de un modelo estadístico entrenado con datos simulados. Muestra asociaciones (correlación), no causas: cambiar una variable en el juego no garantiza el mismo efecto en un estudiante real."
}
```
(Valores ilustrativos.)

Cómo se lee:
- `base`: el riesgo del **estudiante medio** de los datos de entrenamiento.
- `aporte`: cuánto empuja cada variable el riesgo respecto a ese estudiante
  medio, en **log-odds** (la escala interna del modelo). Positivo = sube el
  riesgo; negativo = lo baja.
- Se cumple **exactamente**: `base.logit + Σ aporte = logit` y
  `probabilidad = 1 / (1 + e^(−logit))`.
- `peso`: `|aporte| / Σ|aporte|` (entre 0 y 1; suman 1 salvo redondeo). Es lo
  que permite decir «la asistencia pesó el doble que las horas de estudio».
- `efecto`: `"sube"` (aporte > 0), `"baja"` (< 0) o `"neutro"` (= 0).
- `aportes` va ordenado de mayor a menor `|aporte|`. `carrera` es una sola
  entrada (suma la parte de cada categoría del one-hot).
- `en_riesgo`: `probabilidad ≥ 0.5`, decidido con el valor sin redondear.

---

## 3. Endpoints de predicción (públicos)

Públicos como `/prediccion/riesgo`: ignoran `Authorization` (CONTRATO_AUTH §4.10).

### 3.1 `POST /prediccion/explicacion`

Cuerpo: `Entrada` → `200 ExplicacionOut`.

| Error | Cuándo |
|---|---|
| `422` estándar | Validación (rangos, campos que faltan) |
| `422 {"detail": {"codigo": "carrera_desconocida", "mensaje", "carreras_validas"}}` | Carrera desconocida (mismo formato que `/prediccion/riesgo`; `codigo` desde la Fase 6) |

### 3.2 `POST /prediccion/riesgo/lote`

Cuerpo: `{"entradas": [Entrada, …]}` (1–50) →

```json
{
  "resultados": [{"probabilidad": 0.8123, "en_riesgo": true}, {"probabilidad": 0.4410, "en_riesgo": false}],
  "modelo": {"nombre": "Regresión Logística (juego)", "version": "2026-10-11"},
  "aviso": "…"
}
```
`resultados[i]` corresponde a `entradas[i]`. Una entrada inválida invalida el
lote entero (`422`; en `loc` va su índice). Carrera desconocida:
`422 {"detail": {"codigo": "carrera_desconocida", "mensaje", "carreras_validas", "indices": [i, …]}}`.

Pensado para el medidor del Laboratorio: con un caso de 5 fichas hay como
mucho 12 combinaciones de intervenciones, así que el cliente puede pedirlas
todas de una vez y mover la aguja sin esperar a la red en cada toque.

---

## 4. Laboratorio (`/laboratorio`, requiere sesión)

Cualquier rol (`jugador`, `docente`, `admin`). Sin token → `401` y con token
caducado → `401`, como en CONTRATO_AUTH (el interceptor refresca). Desde la
v1.1 cada `resolver` se guarda y da experiencia (CONTRATO_JUEGO.md §6).

### 4.1 Reglas del rescate

| Regla | Valor |
|---|---|
| Presupuesto | **5 fichas** |
| Objetivo | Probabilidad **< 0.5** (estrictamente menor) |
| `estudio` | 1 ficha → `horas_estudio + 2`, sin pasar de 40 |
| `tutoria` | 2 fichas → `asistencia + 10`, sin pasar de 100 |
| Topes | Una intervención que ya no cabe (p. ej. asistencia 95 → 100) aplica lo que queda; si ya está en el tope **no tiene efecto pero cuesta igual**. El cliente debería deshabilitar el botón |
| Orden | No importa: las dos intervenciones tocan variables distintas |
| Caso válido | Estudiante con probabilidad inicial **≥ 0.6** y **resoluble**: alguna combinación de ≤ 5 fichas lo deja < 0.5 |
| Solución óptima | La combinación que lo logra con **menos fichas**; si empatan, la que deja menor probabilidad |
| Estrellas | Logrado con las fichas óptimas → **3**; con una ficha más → **2**; logrado con más → **1**; no logrado → **0** |

### 4.2 `GET /laboratorio/casos`

```json
{
  "casos": [
    {"estudiante_id": 17, "nombre": "Ana", "apellido": "Gómez", "carrera": "Medicina", "semestre": 2, "probabilidad": 0.8123}
  ],
  "presupuesto": 5,
  "objetivo": 0.5
}
```
Todos los casos válidos, del más grave al más leve. Se calculan con los
estudiantes actuales de la base (si un admin edita uno, puede entrar o salir
de la lista). No incluye la solución.

### 4.3 `GET /laboratorio/casos/{estudiante_id}`

```json
{
  "estudiante": {"id": 17, "nombre": "Ana", "apellido": "Gómez", "edad": 19, "carrera": "Medicina",
                 "semestre": 2, "horas_estudio": 6, "asistencia": 58},
  "probabilidad": 0.8123,
  "presupuesto": 5,
  "objetivo": 0.5,
  "intervenciones": [
    {"codigo": "estudio", "nombre": "Plan de estudio", "fichas": 1, "variable": "horas_estudio",
     "incremento": 2, "tope": 40, "efecto": "+2 h de estudio por semana"},
    {"codigo": "tutoria", "nombre": "Tutoría", "fichas": 2, "variable": "asistencia",
     "incremento": 10, "tope": 100, "efecto": "+10 puntos de asistencia"}
  ],
  "explicacion": ExplicacionOut,
  "aviso": "…"
}
```
- El `promedio` **no** se envía: el modelo no lo usa (es lo que predice) y no
  cambia con las intervenciones.
- `404 {"detail": {"codigo": "caso_no_disponible", …}}`: el estudiante no
  existe, su riesgo es < 0.6 o no se puede rescatar con 5 fichas.

### 4.4 `POST /laboratorio/casos/{estudiante_id}/resolver`

Cuerpo: `{"intervenciones": [Intervencion, …]}` (0–5 elementos; p. ej.
`["tutoria", "estudio"]`).

```json
{
  "logrado": true,
  "estrellas": 2,
  "fichas_usadas": 4,
  "fichas_optimas": 3,
  "probabilidad_inicial": 0.8123,
  "probabilidad_final": 0.4720,
  "estado_final": {"horas_estudio": 8, "asistencia": 68},
  "solucion_optima": {"estudio": 1, "tutoria": 1, "fichas": 3, "probabilidad": 0.4890},
  "explicacion_final": ExplicacionOut,
  "aviso": "…"
}
```

| Error | Cuándo |
|---|---|
| `401` | Sin sesión (ver CONTRATO_AUTH) |
| `404 caso_no_disponible` | Como en §4.3 |
| `422 {"detail": {"codigo": "presupuesto_superado", "mensaje", "fichas": n, "presupuesto": 5}}` | Las intervenciones cuestan más de 5 fichas |
| `422` estándar | Intervención desconocida, más de 5 elementos, campos extra |

- La `solucion_optima` se revela solo aquí, después del intento.
- v1.1: también devuelve `"xp_ganada"`, `"record_estrellas"`,
  `"insignias_nuevas"` y `"progreso"` (ver CONTRATO_JUEGO.md §6). Si el
  intento supera el presupuesto (422), no se guarda nada.
- Se puede resolver el mismo caso tantas veces como se quiera; cada intento
  se evalúa por separado.

---

## 5. Qué debe hacer el cliente

1. Al abrir el Laboratorio: `GET /laboratorio/casos` y elegir un caso (al azar
   o el que toque el jugador).
2. `GET /laboratorio/casos/{id}`: datos, explicación inicial y catálogo de
   intervenciones (los costes y efectos salen de aquí, no del código del
   cliente).
3. Para el medidor en vivo: aplicar las intervenciones en local (con los
   `incremento` y `tope` del catálogo) y pedir las probabilidades con
   `POST /prediccion/riesgo/lote`, idealmente de todas las combinaciones de
   una vez. La aguja es solo visual.
4. Al confirmar: `POST …/resolver` con la lista de intervenciones y mostrar
   **lo que devuelve el servidor** (estrellas, probabilidad final, solución
   óptima). Nunca calcular las estrellas en el cliente.
5. Pantalla de explicación: `explicacion_final.aportes` (barras con `peso` y
   color por `efecto`) y el texto de `aviso`, siempre visible.

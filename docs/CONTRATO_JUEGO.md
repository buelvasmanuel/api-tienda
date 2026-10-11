# Contrato del juego: casos, progreso, insignias y ranking (Fase 5)

> Contrato entre `api-tienda` y `app_estudiantes` para el Modo 2 («Los Casos»)
> y la progresión (PROPUESTA §2.2 y §2.3). Versión **1**, 2026-10-11.
> Autenticación, formato de errores (`{"detail": {"codigo", "mensaje"}}`) e
> interceptor: [CONTRATO_AUTH.md](CONTRATO_AUTH.md). Laboratorio:
> [CONTRATO_LABORATORIO.md](CONTRATO_LABORATORIO.md) (v1.1 añade aquí la XP de
> los rescates).

---

## 1. Resumen

| Pieza | Regla |
|---|---|
| Acceso | Todo `/juego` requiere sesión (cualquier rol). `GET /juego/jugadores` solo docente o admin |
| Casos | 9 preguntas en 3 niveles (Novato, Investigador, Experto). Las respuestas correctas se **calculan en el servidor con los datos reales en cada petición**: si cambian los estudiantes, cambian las respuestas |
| Empates | Si dos opciones empatan en el dato que se pregunta, **todas las empatadas son correctas** |
| XP | La calcula **solo el servidor**. Casos: 20 / 40 / 60 según el nivel, multiplicado por 1 (primer intento), 0,5 (segundo) o 0,25 (tercero o más). Solo se gana la primera vez que se resuelve. Rescates: 10 XP por cada estrella que mejore el récord del jugador en ese estudiante (máx. 30 por estudiante) |
| Niveles | Novato (0 XP) → Investigador (150) → Inspector (400) → Jefe de datos (800) |
| Insignias | 7, otorgadas por el servidor (§5). Nunca se pierden |
| Racha | Días seguidos con actividad (responder un caso o resolver un rescate), en hora de Colombia (UTC−5) |
| Ranking | Por XP, solo jugadores, por alias; semanal (desde el lunes 00:00 de Colombia) o histórico. Se calcula con SQL (sin Redis) |
| Fuente de verdad | El progreso **no se guarda como un total**: XP, nivel, racha e insignias salen de los intentos y rescates guardados, así no pueden desincronizarse |

---

## 2. Tipos comunes

```text
Nivel        = "novato" | "investigador" | "experto"
CodigoCaso   = texto, p. ej. "carrera_mas_estudiantes" (lista en §3.1)
Fecha        = ISO 8601 UTC con "Z", como en CONTRATO_AUTH
```

**`InsigniaOut`**
```json
{"codigo": "primer_rescate", "nombre": "Primer rescate", "descripcion": "Rescata a tu primer estudiante.",
 "obtenida_en": "2026-10-11T18:04:05.123456Z"}
```
`obtenida_en` es `null` si aún no la tiene (en `GET /juego/progreso`).

**`ProgresoResumenOut`** (va dentro de las respuestas que dan XP)
```json
{"xp": 230, "nivel": "Investigador", "subio_de_nivel": false}
```

---

## 3. Casos (Modo 2)

### 3.1 Catálogo

| Código | Nivel (XP) | Pregunta | Opciones | Respuesta correcta (con los datos actuales) | Gráfica sugerida |
|---|---|---|---|---|---|
| `carrera_mas_estudiantes` | novato (20) | ¿Qué carrera tiene más estudiantes? | Todas las carreras | La de más estudiantes | `por-carrera` |
| `ciudad_mas_estudiantes` | novato (20) | ¿De qué ciudad vienen más estudiantes? | Las 6 ciudades con más estudiantes | La de más estudiantes | `por-ciudad` |
| `semestre_mas_estudiantes` | novato (20) | ¿En qué semestre hay más estudiantes? | Los semestres con estudiantes | El de más estudiantes (hoy empatan 3 y 5: las dos valen) | `por-semestre` |
| `carrera_mas_riesgo` | investigador (40) | ¿Qué carrera tiene el mayor **porcentaje** de estudiantes en riesgo? | Todas las carreras | Mayor % con promedio < 3.0 | `riesgo-por-carrera` |
| `carrera_mejor_promedio` | investigador (40) | ¿Qué carrera tiene el promedio más alto? | Todas las carreras | Mayor promedio medio | `promedio-por-carrera` |
| `asistencia_en_riesgo` | investigador (40) | Los estudiantes en riesgo, ¿van más o menos a clase que los demás? | `mas`, `menos`, `igual` | Compara la asistencia media; diferencia < 2 puntos = `igual` | — |
| `horas_y_promedio` | experto (60) | ¿Cómo se relacionan las horas de estudio con el promedio? | `positiva`, `negativa`, `ninguna` | Correlación de Pearson r: > 0,1 positiva; < −0,1 negativa; si no, ninguna | — |
| `variable_mas_influyente` | experto (60) | ¿Qué variable pesa más en el modelo del juego? | `horas_estudio`, `asistencia`, `semestre`, `edad` | Mayor \|coeficiente\| del modelo congelado (variables escaladas, comparables) | — (ver la explicación del Laboratorio) |
| `edad_y_riesgo` | experto (60) | El modelo da **menos** (o **más**, según el signo de su coeficiente) riesgo a los estudiantes mayores. ¿La edad influye en el promedio? | `causa`, `asociacion`, `espurio` | Según la correlación real edad–promedio: \|r\| < 0,1 → `espurio` (el modelo aprendió un patrón casual de la muestra); si no, `asociacion` (hay relación en los datos, sin probar causa). `causa` nunca es correcta: con datos observacionales no se demuestra causalidad | — |

Las rutas de «gráfica sugerida» son de `/estudiantes/estadisticas/…`: el
cliente puede mostrar esa gráfica como «ficha del caso».

### 3.2 `GET /juego/casos`

```json
{
  "casos": [
    {"codigo": "carrera_mas_estudiantes", "nivel": "novato", "titulo": "El edificio más lleno",
     "xp": 20, "resuelto": true, "intentos": 1, "xp_ganada": 20}
  ]
}
```
Orden: por nivel (novato → experto) y dentro de cada nivel como en §3.1.
Todos los casos están disponibles desde el principio (el nivel indica la
dificultad, no bloquea).

### 3.3 `GET /juego/casos/{codigo}`

```json
{
  "codigo": "carrera_mas_riesgo",
  "nivel": "investigador",
  "titulo": "La facultad en apuros",
  "enunciado": "¿Qué carrera tiene el mayor porcentaje de estudiantes en riesgo (promedio menor que 3.0)?",
  "opciones": [{"valor": "Administración", "texto": "Administración"}, …],
  "grafica": "/estudiantes/estadisticas/riesgo-por-carrera",
  "xp": 40,
  "resuelto": false,
  "intentos": 0
}
```
- `opciones[].valor` es lo que se envía al responder; `texto` es lo que se
  muestra. Vienen en orden alfabético por `texto`: el cliente puede barajarlas.
- `grafica`: ruta de la estadística que ayuda, o `null`.
- No incluye la respuesta ni la pista.
- `404 {"codigo": "caso_no_encontrado"}` si el código no existe.

### 3.4 `POST /juego/casos/{codigo}/responder`

Cuerpo: `{"respuesta": "Medicina"}` (un `valor` de las opciones).

Correcta:
```json
{
  "correcto": true,
  "xp_ganada": 40,
  "dato": "Medicina: 33,33 % de sus estudiantes está en riesgo.",
  "explicacion": "Medicina tiene el porcentaje más alto (33,33 %). Ojo: es un porcentaje; en número de estudiantes en riesgo podría ganar otra carrera más grande.",
  "pista": null,
  "intento": 2,
  "insignias_nuevas": [InsigniaOut],
  "progreso": ProgresoResumenOut
}
```
Incorrecta:
```json
{
  "correcto": false,
  "xp_ganada": 0,
  "dato": "Derecho: 26,53 % de sus estudiantes está en riesgo.",
  "explicacion": null,
  "pista": "Mira la gráfica de riesgo por carrera: cuenta el porcentaje, no el número de estudiantes.",
  "intento": 1,
  "insignias_nuevas": [],
  "progreso": ProgresoResumenOut
}
```
- `dato`: el valor real de **la opción elegida** (enseña sin revelar la
  respuesta). En los casos cuyas opciones no son categorías de los datos
  (`asistencia_en_riesgo`, `horas_y_promedio`, `edad_y_riesgo`) cualquier dato
  revelaría la respuesta, así que al fallar es `null`; al acertar trae las cifras.
- `explicacion` solo si acierta; `pista` solo si falla.
- `intento`: número de este intento en ese caso (cuenta también los de después de resolverlo).
- `xp_ganada > 0` solo la primera vez que se resuelve (ver §1).
- Un caso ya resuelto se puede volver a responder: dice si es correcto, con `xp_ganada = 0`.

| Error | Cuándo |
|---|---|
| `404 caso_no_encontrado` | Código desconocido |
| `422 {"codigo": "respuesta_invalida", "mensaje", "opciones": [valor, …]}` | La respuesta no es una de las opciones actuales |
| `422` estándar | Cuerpo inválido o campos extra |

---

## 4. Progreso

### 4.1 `GET /juego/progreso`

```json
{
  "alias": "Detective_Manu",
  "xp": 230,
  "nivel": {"numero": 2, "nombre": "Investigador", "xp_desde": 150},
  "siguiente_nivel": {"numero": 3, "nombre": "Inspector", "xp_desde": 400},
  "racha_dias": 2,
  "casos_resueltos": 4,
  "casos_total": 9,
  "rescates_logrados": 3,
  "estrellas_totales": 7,
  "insignias": [InsigniaOut, …]
}
```
- `siguiente_nivel` es `null` en el último nivel.
- `insignias`: el catálogo entero (§5), con `obtenida_en` o `null`.
- `estrellas_totales`: suma del récord de estrellas de cada estudiante rescatado.
- `racha_dias`: días seguidos con actividad terminando hoy o ayer (si el
  último día con actividad es anterior a ayer, vale 0).

### 4.2 `GET /juego/ranking?periodo=semana|siempre`

```json
{
  "periodo": "semana",
  "desde": "2026-10-06T05:00:00.000000Z",
  "jugadores": [{"posicion": 1, "alias": "Detective_Ana", "xp": 310, "nivel": "Investigador", "soy_yo": false}],
  "yo": {"posicion": 4, "xp": 120}
}
```
- `periodo` por defecto `semana` (desde el lunes 00:00 de Colombia; `desde`
  en UTC). `siempre`: toda la XP; `desde` es `null`.
- Top 20, solo usuarios con rol `jugador` y activos, con XP > 0 en el periodo.
  Empate: mismo `posicion` (1, 2, 2, 4) y orden por alias.
- `yo`: la posición del que pregunta (o `null` si no es jugador o no tiene XP
  en el periodo), aunque no esté en el top 20.
- Solo se muestra el alias.

### 4.3 `GET /juego/jugadores` — docente, admin

```json
[{"id": 7, "alias": "Detective_Ana", "activo": true, "xp": 310, "nivel": "Investigador",
  "casos_resueltos": 5, "rescates_logrados": 4, "insignias": 3, "ultima_actividad": "…Z"}]
```
Todos los jugadores (decisión: sin grupos), ordenados por alias.
`ultima_actividad` es `null` si no ha jugado. Jugador → `403 permiso_denegado`.

---

## 5. Insignias

| Código | Nombre | Se obtiene al… |
|---|---|---|
| `primera_pista` | Primera pista | Resolver el primer caso |
| `ojo_de_halcon` | Ojo de halcón | Resolver 5 casos distintos al primer intento |
| `mente_experta` | Mente experta | Resolver los 3 casos de nivel experto |
| `primer_rescate` | Primer rescate | Lograr el primer rescate |
| `rescate_perfecto` | Rescate perfecto | Lograr un rescate con 3 estrellas |
| `rescatista` | Rescatista | Rescatar a 10 estudiantes distintos |
| `constancia` | Constancia | Llegar a una racha de 3 días |

Se comprueban después de cada respuesta a un caso y de cada rescate; las que
se ganan en esa acción vuelven en `insignias_nuevas`.

---

## 6. Cambio en el Laboratorio (CONTRATO_LABORATORIO v1.1, solo añade campos)

`POST /laboratorio/casos/{id}/resolver` guarda cada intento y añade a su respuesta:

```json
{"xp_ganada": 20, "record_estrellas": 3, "insignias_nuevas": [InsigniaOut], "progreso": ProgresoResumenOut}
```
- `record_estrellas`: el mejor resultado del jugador con ese estudiante, contando este intento.
- `xp_ganada = 10 × (estrellas − récord anterior)` si mejora el récord; si no, 0.

---

## 7. Qué debe hacer el cliente

1. Pantalla de casos: `GET /juego/casos`; al abrir uno, `GET /juego/casos/{codigo}`
   y mostrar la gráfica de `grafica` si la hay.
2. Al responder, mostrar **lo que devuelve el servidor**: `dato` siempre, y
   `explicacion` (acierto) o `pista` (fallo) con el búho. Nunca decidir en el
   cliente si es correcto ni cuánta XP da.
3. Si `insignias_nuevas` no está vacío o `progreso.subio_de_nivel` es `true`,
   mostrar la celebración (≤ 1,5 s y saltable, PROPUESTA §2.4).
4. Perfil: `GET /juego/progreso`. Ranking: `GET /juego/ranking`.
5. Vista del docente (rol `docente`/`admin`): `GET /juego/jugadores`.

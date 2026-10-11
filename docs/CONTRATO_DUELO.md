# Contrato del Duelo de cartas (Modo 4, Fase 7)

> Contrato entre `api-tienda` y `app_estudiantes`. Versión **1** (duelo contra
> la máquina), 2026-10-11. Autenticación, formato de errores
> (`{"detail": {"codigo", "mensaje"}}`) e interceptor: CONTRATO_AUTH.md.
> XP, insignias y ranking: CONTRATO_JUEGO.md (este contrato añade el duelo).
>
> La v1 ya está pensada para la **v2 (jugador contra jugador, §7)**: los
> mismos recursos, el mismo estado y las mismas jugadas; la v2 solo añade el
> emparejamiento, el tiempo por turno y un canal WebSocket de avisos.

---

## 1. Resumen

| Pieza | Regla |
|---|---|
| Cartas | Cada carta es un **estudiante real** con 4 atributos: `promedio`, `asistencia`, `horas_estudio`, `semestre`. Cada valor viene con su **percentil** entre todos los estudiantes |
| Reparto | El servidor baraja y reparte **5 cartas distintas** a cada lado. Cada mazo se guarda en el servidor al empezar (una copia de los valores: si después se edita un estudiante, el duelo no cambia) |
| Rondas | 5. En cada ronda, cada lado juega la siguiente carta de su mazo. Quien tiene el turno elige un atributo **viendo solo su carta**; gana la ronda el valor **más alto**; si empatan, nadie suma |
| Turno | Ronda 1: el jugador. Después se alterna (jugador, máquina, jugador…) |
| Máquina | Elige siempre el atributo en el que **su carta tiene el percentil más alto** (si empatan, en el orden de §2). Es determinista a propósito: el búho la puede explicar |
| Resultado | Gana quien sume más rondas; con los mismos puntos, empate |
| Anti-trampa | El servidor reparte, guarda los mazos, decide el turno, valida cada jugada y calcula el resultado y la XP. El cliente **nunca ve la carta del rival antes de que se resuelva la ronda**, ni las cartas que vienen después |
| XP | Victoria 30, empate 15, derrota 5. Solo los **3 primeros duelos terminados cada día** (hora de Colombia) dan XP: se puede seguir jugando, pero sin subir en el ranking por repetición |
| Búho | Cada ronda resuelta trae una `explicacion`: qué significa ganar con ese atributo, con los percentiles y la correlación real de ese atributo con el promedio |
| Acceso | Todo `/duelos` requiere sesión (cualquier rol). Un duelo solo lo ve quien juega en él |

---

## 2. Atributos

| Código | Nombre | Unidad | Qué significa ganar con él (base del texto del búho) |
|---|---|---|---|
| `promedio` | Promedio | 0–5 | Es el **resultado** académico, lo que el modelo intenta predecir: ganar con él es ganar con el efecto, no con la causa |
| `asistencia` | Asistencia | % | Mide cuánto va a clase. En estos datos se asocia con mejor promedio (r ≈ 0,6), pero ir a clase no garantiza aprender |
| `horas_estudio` | Horas de estudio | h/semana | Mide cuánto estudia. También se asocia con mejor promedio (r ≈ 0,5) |
| `semestre` | Semestre | 1–10 | Solo dice cuánto lleva en la carrera. «Más alto» no es «mejor»: casi no se relaciona con el promedio (r ≈ 0). Ganar con él es ganar por antigüedad |

Las correlaciones se calculan con los datos actuales cada vez que se genera una
explicación (las cifras de arriba son de hoy).

**Percentil** de un valor: porcentaje de estudiantes con un valor menor, más la
mitad de los que tienen el mismo valor (0–100, entero). Permite comparar
atributos con escalas distintas: un 4,2 de promedio y un 92 % de asistencia no
se pueden comparar, pero «percentil 85» y «percentil 60» sí.

---

## 3. Tipos

**`CartaOut`**
```json
{
  "estudiante_id": 17, "nombre": "Ana", "apellido": "Gómez", "carrera": "Medicina",
  "atributos": {
    "promedio":      {"valor": 3.8, "percentil": 72},
    "asistencia":    {"valor": 92,  "percentil": 81},
    "horas_estudio": {"valor": 14,  "percentil": 49},
    "semestre":      {"valor": 3,   "percentil": 30}
  }
}
```

**`RondaOut`** (solo rondas ya resueltas: las dos cartas a la vista)
```json
{
  "numero": 1,
  "elegido_por": "yo",
  "atributo": "asistencia",
  "mi_carta": CartaOut,
  "carta_rival": CartaOut,
  "ganador": "yo",
  "explicacion": "Ganaste con la asistencia: 92 % (percentil 81) frente a 70 % (percentil 22). La asistencia mide cuánto va a clase; en estos datos se asocia con mejor promedio (r = 0,62), pero ir a clase no garantiza aprender."
}
```
`elegido_por` y `ganador`: `"yo"`, `"rival"` (y `"empate"` en `ganador`), siempre
desde el punto de vista de quien pregunta.

**`DueloOut`**
```json
{
  "id": "4f1c…(32 hex)",
  "modo": "maquina",
  "estado": "en_juego",
  "rival": {"tipo": "maquina", "alias": "Búho Máquina"},
  "rondas_total": 5,
  "ronda": 2,
  "turno": "rival",
  "marcador": {"yo": 1, "rival": 0},
  "mi_carta": CartaOut,
  "rondas": [RondaOut],
  "resultado": null,
  "xp_ganada": 0,
  "duelos_con_xp_hoy": {"jugados": 1, "maximo": 3},
  "atributos": [{"codigo": "promedio", "nombre": "Promedio", "unidad": "0–5", "descripcion": "…"}],
  "expira_turno_en": null,
  "creado_en": "…Z",
  "actualizado_en": "…Z"
}
```
- `estado`: `en_juego` | `terminado` | `abandonado` (en la v2 también `esperando`).
- `ronda`: la ronda que toca jugar (1–5); cuando el duelo termina, `null`.
- `turno`: `"yo"` | `"rival"` (`null` si terminó).
- `mi_carta`: la carta que toca jugar ahora (`null` si terminó). **Nunca** se
  envía la carta del rival de la ronda en curso ni las siguientes.
- `resultado`: `null` mientras se juega; `"victoria"`, `"derrota"` o `"empate"`.
  Abandonar cuenta como `"derrota"`.
- `xp_ganada`: la XP que dio este duelo (0 hasta que termina, o si ya se
  agotaron los 3 diarios, o si se abandonó).
- `duelos_con_xp_hoy`: cuántos duelos con XP lleva hoy quien pregunta.
- `expira_turno_en`: siempre `null` contra la máquina (en la v2, el límite del turno).

---

## 4. Endpoints

### 4.1 `POST /duelos`

Cuerpo: `{"modo": "maquina"}` → `201 DueloOut` (ronda 1, turno `"yo"`).

- Si el jugador ya tenía un duelo contra la máquina `en_juego`, ese duelo se
  da por **abandonado** (sin XP) y empieza uno nuevo: solo hay un duelo
  contra la máquina activo por jugador.
- `409 {"codigo": "sin_datos"}` si hay menos de 10 estudiantes (no hay cartas).
- `422` si `modo` no es `"maquina"` (la v2 añadirá `"jugador"`).

### 4.2 `GET /duelos/{id}`

`200 DueloOut`. `404 duelo_no_encontrado` si no existe **o no eres
participante** (no se distingue, para no revelar ids ajenos). El cliente lo
usa para recuperar el estado tras un error de red o un reinicio.

### 4.3 `GET /duelos?estado=en_juego|terminado`

`200 {"duelos": [DueloResumenOut]}`: tus duelos, los más recientes primero
(máx. 20). `DueloResumenOut` = `{id, modo, estado, rival, marcador, resultado, xp_ganada, creado_en}`.
Sirve para retomar un duelo `en_juego` al abrir la app.

### 4.4 `POST /duelos/{id}/jugadas`

Cuerpo: `{"ronda": 2, "atributo": "asistencia"}` (en tu turno) o
`{"ronda": 2, "atributo": null}` (en el turno del rival: pide que la máquina
juegue). → `200 DueloOut` con la ronda ya resuelta en `rondas` y la siguiente
carta en `mi_carta`.

| Error | Cuándo |
|---|---|
| `404 duelo_no_encontrado` | No existe o no participas |
| `409 duelo_terminado` | El duelo ya terminó o se abandonó |
| `409 ronda_incorrecta` (+ `"ronda_actual": n`) | `ronda` no es la que toca, o repites una ronda ya resuelta con otro atributo |
| `409 no_es_tu_turno` | Envías un atributo en el turno del rival |
| `422 atributo_requerido` | Es tu turno y `atributo` es `null` |
| `422` estándar | Atributo desconocido, campos extra (p. ej. intentar enviar `"ganador"`) |

**Reintentos seguros:** repetir exactamente la jugada de una ronda ya
resuelta (misma `ronda` y mismo `atributo`, o `null` si eligió el rival)
devuelve `200` con el estado actual, sin jugarla dos veces. Así, si se pierde
la respuesta, el cliente reintenta sin miedo.

### 4.5 `POST /duelos/{id}/abandonar`

`200 DueloOut` con `estado = "abandonado"` y `resultado = "derrota"`, sin XP.
Repetirlo devuelve el mismo estado. `409 duelo_terminado` si ya había terminado.

---

## 5. XP e insignia

- XP al terminar: victoria 30, empate 15, derrota 5; abandonar, 0.
- Solo los 3 primeros duelos **terminados** por día (hora de Colombia, UTC−5)
  dan XP. Los demás se juegan igual con `xp_ganada = 0`.
- La XP del duelo entra en `/juego/progreso`, el ranking y la vista del
  docente como la de casos y rescates.
- **Nueva insignia** en CONTRATO_JUEGO §5 (aditivo):
  `duelista` — «Duelista» — «Gana tu primer duelo». Llega en `insignias_nuevas`
  de la jugada que termina el duelo:
  `POST /duelos/{id}/jugadas` → `DueloOut` añade `"insignias_nuevas": [InsigniaOut]`
  y `"progreso": ProgresoResumenOut` solo en la respuesta que lo termina
  (en las demás, `[]` y `null`).

---

## 6. Qué debe hacer el cliente

1. Al abrir el duelo: `GET /duelos?estado=en_juego`; si hay uno, retomarlo con
   `GET /duelos/{id}`; si no, `POST /duelos`.
2. En tu turno: mostrar `mi_carta` (con los percentiles: ayudan a elegir) y
   enviar el atributo elegido.
3. En el turno del rival: mostrar `mi_carta` y un botón «Ver jugada del rival»
   que envía la jugada con `atributo: null`.
4. Tras cada jugada, animar la última ronda de `rondas` (las dos cartas, el
   ganador) y mostrar su `explicacion` con el búho.
5. Ante un error de red o un 5xx: reintentar **la misma** jugada; si persiste,
   `GET /duelos/{id}` y continuar desde ahí. Ante `409 ronda_incorrecta`:
   `GET /duelos/{id}`.
6. Nunca calcular en el cliente quién gana ni la XP.

---

## 7. Diseño para la v2: jugador contra jugador (no implementado aún)

Lo que la v1 ya deja preparado: `modo`, `rival.tipo`, `turno` desde el punto
de vista de quien pregunta, `expira_turno_en`, jugadas idempotentes por
`ronda`, y todo el estado en la base de datos (nada en memoria).

**Emparejamiento.** `POST /duelos {"modo": "jugador"}`: si hay alguien
esperando, se emparejan y el duelo pasa a `en_juego`; si no, queda en
`esperando` (máx. 60 s; después, `abandonado` sin XP). La cola es la tabla de
duelos en la base, no memoria: sobrevive a un reinicio de Render.

**Canal de avisos (WebSocket) — solo avisa, no decide.** `GET /duelos/{id}/ws`.
El primer mensaje del cliente es `{"tipo": "auth", "token": "<access>"}` (el
token no va en la URL, que acaba en los logs). El servidor envía
`emparejado`, `turno`, `ronda_resuelta`, `rival_desconectado`,
`rival_reconectado` y `terminado`, cada uno con el `DueloOut` completo. Las
**jugadas se siguen enviando por REST** (§4.4, validadas e idempotentes): si
el WebSocket se cae, el cliente no pierde nada y sigue con `GET /duelos/{id}`
cada pocos segundos.

**Tiempo y desconexiones.** Cada turno dura como máximo 45 s
(`expira_turno_en`); si vence, la ronda la gana el rival. Si un jugador no
juega en 2 turnos seguidos, el duelo se da por abandonado y gana el otro. Los
plazos se comprueban **al leer o escribir el duelo** (no hace falta ningún
proceso en segundo plano, que en Render free podría dormirse).

**Render dormido o reiniciado.** Se pierden los WebSockets (están en memoria
de un único proceso), no el duelo. Los clientes reconectan con espera
creciente (1, 2, 4… hasta 30 s) y se resincronizan con `GET /duelos/{id}`. Un
duelo en espera cuando Render se duerme caduca por los 60 s.

**XP y abusos.** Victoria 40, empate 20, derrota 10, con máximo diario propio
para que dos cuentas no puedan pasarse XP jugando entre ellas sin límite.

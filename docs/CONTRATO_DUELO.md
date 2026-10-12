# Contrato del Duelo de cartas (Modo 4, Fase 7)

> Contrato entre `api-tienda` y `app_estudiantes`. Versión **2.1**, 2026-10-12 (2.1: mensaje `error` antes de cerrar el WebSocket, §8):
> duelo contra la máquina (v1) y **jugador contra jugador** (v2, §7 y §8).
> Autenticación, formato de errores (`{"detail": {"codigo", "mensaje"}}`) e
> interceptor: CONTRATO_AUTH.md. XP, insignias y ranking: CONTRATO_JUEGO.md.

### Cambios de la v2 (todo lo de la v1 sigue igual)

| Cambio | Dónde |
|---|---|
| `POST /duelos {"modo": "jugador"}`: busca rival; si no hay, el duelo queda `esperando` (60 s) | §7.1 |
| Turno de 45 s (`expira_turno_en`); si vence, la ronda es para el rival; 2 turnos vencidos seguidos = abandono | §7.2 |
| `RondaOut` gana `motivo` (`"jugada"` / `"tiempo"`); `atributo` es `null` si la ronda se perdió por tiempo | §3 |
| `DueloOut` gana `rival_en_linea` (`null` contra la máquina) y `rival.alias` puede ser `null` mientras se espera | §3 |
| Nuevo error `409 tiempo_agotado` y `409 duelo_en_curso` | §4.4, §7.1 |
| XP propia del modo jugador (40/20/10, máx. 3 al día) | §7.4 |
| WebSocket `/duelos/{id}/ws` de avisos: qué viaja por él y cómo se autentica | §8 |

---

## 1. Resumen

| Pieza | Regla |
|---|---|
| Cartas | Cada carta es un **estudiante real** con 4 atributos: `promedio`, `asistencia`, `horas_estudio`, `semestre`. Cada valor viene con su **percentil** entre todos los estudiantes |
| Reparto | El servidor baraja y reparte **5 cartas distintas** a cada lado. Cada mazo se guarda en el servidor al empezar (una copia de los valores: si después se edita un estudiante, el duelo no cambia) |
| Rondas | 5. En cada ronda, cada lado juega la siguiente carta de su mazo. Quien tiene el turno elige un atributo **viendo solo su carta**; gana la ronda el valor **más alto**; si empatan, nadie suma |
| Turno | Ronda 1: quien creó el duelo (contra la máquina, el jugador). Después se alterna |
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
  "explicacion": "Ganaste con asistencia: tu carta tiene 92 % (percentil 81) y la del rival, 70 % (percentil 22). Elegiste asistencia. La asistencia mide cuánto va a clase; en estos datos se asocia con mejor promedio (r = 0,62), pero una asociación no garantiza el resultado en cada estudiante."
}
```
`elegido_por` y `ganador`: `"yo"`, `"rival"` (y `"empate"` en `ganador`), siempre
desde el punto de vista de quien pregunta. **v2:** `"motivo": "jugada"` (lo
normal) o `"tiempo"` (a quien le tocaba elegir se le acabó el turno: `atributo`
es `null`, la ronda es para el otro y la explicación lo dice).

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
  "rival_en_linea": null,
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
- `expira_turno_en`: `null` contra la máquina. En modo jugador: hasta cuándo
  puede elegir quien tiene el turno; mientras `esperando`, hasta cuándo se
  busca rival.
- `rival_en_linea` (v2): `true`/`false` según el rival tenga abierto el
  WebSocket de este duelo; `null` contra la máquina o mientras se espera.
- `rival` (v2): `{"tipo": "jugador", "alias": "Detective_Ana"}`; mientras
  `esperando`, `{"tipo": "jugador", "alias": null}`.
- `duelos_con_xp_hoy`: cuenta los duelos del **mismo modo** que este.

---

## 4. Endpoints

### 4.1 `POST /duelos`

Cuerpo: `{"modo": "maquina"}` → `201 DueloOut` (ronda 1, turno `"yo"`).
Con `{"modo": "jugador"}`, ver §7.1.

- Si el jugador ya tenía un duelo contra la máquina `en_juego`, ese duelo se
  da por **abandonado** (sin XP) y empieza uno nuevo: solo hay un duelo
  contra la máquina activo por jugador.
- `409 {"codigo": "sin_datos"}` si hay menos de 10 estudiantes (no hay cartas).
- `422` si `modo` no es `"maquina"` ni `"jugador"`.

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
`{"ronda": 2, "atributo": null}` (en el turno de la máquina: pide que juegue).
Contra otro jugador solo juega quien tiene el turno; el otro espera el aviso
del WebSocket (§8) o consulta `GET /duelos/{id}`. → `200 DueloOut` con la ronda ya resuelta en `rondas` y la siguiente
carta en `mi_carta`.

| Error | Cuándo |
|---|---|
| `404 duelo_no_encontrado` | No existe o no participas |
| `409 duelo_terminado` | El duelo ya terminó o se abandonó |
| `409 ronda_incorrecta` (+ `"ronda_actual": n`) | `ronda` no es la que toca, o repites una ronda ya resuelta con otro atributo |
| `409 no_es_tu_turno` | Envías un atributo en el turno del rival (o, contra un jugador, cualquier jugada en su turno) |
| `409 tiempo_agotado` | v2: esa ronda ya se resolvió porque se te acabó el turno |
| `422 atributo_requerido` | Es tu turno y `atributo` es `null` |
| `422` estándar | Atributo desconocido, campos extra (p. ej. intentar enviar `"ganador"`) |

**Reintentos seguros:** repetir exactamente la jugada de una ronda ya
resuelta (misma `ronda` y mismo `atributo`, o `null` si eligió el rival)
devuelve `200` con el estado actual, sin jugarla dos veces. Así, si se pierde
la respuesta, el cliente reintenta sin miedo.

### 4.5 `POST /duelos/{id}/abandonar`

`200 DueloOut` con `estado = "abandonado"` y `resultado = "derrota"`, sin XP.
Repetirlo devuelve el mismo estado. `409 duelo_terminado` si ya había terminado.
v2: abandonar un duelo `esperando` lo cancela (`abandonado`, `resultado: null`);
abandonar contra un jugador le da a él la victoria (§7.4).

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

## 7. Jugador contra jugador (v2)

Mismos recursos, estado y jugadas que contra la máquina. Cambia quién elige
en los turnos pares (el otro jugador, no la máquina) y se añaden el
emparejamiento, el tiempo por turno y el WebSocket de avisos (§8).

### 7.1 Emparejamiento: `POST /duelos {"modo": "jugador"}`

| Situación | Respuesta |
|---|---|
| Alguien lleva menos de 60 s esperando | Os empareja: `201 DueloOut` `en_juego`; **el que esperaba** es el lado que empieza (ronda 1). Al que esperaba le llega `emparejado` por el WebSocket |
| Nadie esperando | `201 DueloOut` con `estado: "esperando"`, `expira_turno_en` = ahora + 60 s |
| Ya estás esperando | `200` con ese mismo duelo (pulsar dos veces no crea dos) |
| Ya tienes un duelo contra un jugador `en_juego` | `409 duelo_en_curso` con `"duelo_id"`: termínalo o abandónalo |

- Nunca te empareja contigo mismo. La cola es la propia tabla de duelos (no
  memoria) y el emparejamiento va en fila (un candado en la base), así que dos
  jugadores que buscan a la vez se emparejan entre sí en vez de quedarse los
  dos esperando.
- Si pasan los 60 s sin rival, el duelo pasa a `abandonado` (sin resultado ni
  XP) la próxima vez que se consulte. El cliente puede ofrecer «jugar contra
  la máquina».
- Esperar en una pantalla: abrir el WebSocket (§8) y esperar `emparejado`; o,
  sin WebSocket, `GET /duelos/{id}` cada 3 s.

### 7.2 Turnos y tiempo

- Rondas impares: elige el lado que empezó; pares, el otro. Solo puede
  enviar jugada quien tiene el turno; la ronda se resuelve en ese momento.
- Cada turno dura **45 s** (`expira_turno_en`). Si vence, la ronda se
  resuelve sola: es para el rival (`motivo: "tiempo"`) y el turno pasa al
  siguiente, con otros 45 s contados desde el vencimiento.
- Si a un jugador se le acaban **2 turnos suyos seguidos**, el duelo se da
  por abandonado por él: pierde y el otro gana.
- **No hay temporizadores en el servidor**: los vencimientos se aplican
  cuando alguien lee o escribe el duelo (`GET`, jugada, abandonar). Así nada
  depende de que el servidor esté despierto. Por eso, cuando en pantalla
  llega a 0 la cuenta atrás de `expira_turno_en`, **los dos clientes hacen
  `GET /duelos/{id}`**: eso aplica el vencimiento y avisa a ambos.

### 7.3 Desconexiones y Render dormido

- Si se cae la red o el WebSocket, el duelo no se pierde: está en la base.
  Al volver: reconectar el WebSocket y `GET /duelos/{id}`.
- Si el servidor se reinicia o Render lo duerme, se cierran todos los
  WebSockets (viven en memoria del único proceso). Los clientes reconectan
  con espera creciente (1, 2, 4, 8… hasta 30 s) y, mientras tanto, usan
  `GET /duelos/{id}` cada 3 s.
- Si los dos se van, al volver cualquiera de ellos los turnos vencidos se
  resuelven de golpe (y, con 2 seguidos sin jugar, el duelo termina).
- `rival_en_linea` dice si el rival tiene el WebSocket abierto: sirve para
  mostrar «tu rival se desconectó»; no cambia las reglas (lo que cuenta es el
  tiempo del turno).

### 7.4 XP y abandonos

- Victoria 40, empate 20, derrota 10. Solo los **3 primeros duelos contra
  jugadores terminados cada día** (aparte de los 3 contra la máquina).
- Quien abandona (o pierde 2 turnos seguidos por tiempo) no gana XP.
- Quien gana por abandono del otro recibe la XP de victoria **solo si ya se
  habían resuelto al menos 3 rondas**: así no se puede regalar XP a un amigo
  abandonando nada más empezar.
- La insignia `duelista` vale igual para victorias contra jugadores.

---

## 8. El WebSocket, en simple

**Qué es.** Una conexión que se queda abierta entre la app y la API para que
la API pueda **avisar** sin que la app pregunte. Con REST, la app pregunta y
la API responde; con el WebSocket, la API puede decir «tu rival ya jugó» en
cuanto ocurre.

**Qué viaja por él: solo avisos.** Cada aviso trae el `DueloOut` completo,
desde el punto de vista de quien lo recibe:

| `tipo` | Cuándo |
|---|---|
| `listo` | Justo después de autenticarse: el estado actual |
| `error` | Justo antes de cerrar la conexión: `codigo` y `motivo` (sin `duelo`) |
| `emparejado` | Alguien se unió a tu duelo en espera |
| `ronda_resuelta` | Se resolvió una ronda (por jugada o por tiempo) |
| `terminado` | El duelo terminó o alguien abandonó |
| `rival_conectado` / `rival_desconectado` | El rival abrió o cerró su WebSocket |

**Qué sigue por REST: todo lo que cambia el duelo.** Crear y buscar rival,
jugar, abandonar y consultar van por los endpoints de §4, con sus
validaciones, sus errores y sus reintentos seguros. El WebSocket **no
decide nada**: si se cae, la app no pierde nada y puede seguir jugando solo
con REST (`GET /duelos/{id}` cada pocos segundos). Así el juego funciona
igual con mala conexión o con Render reiniciándose.

**Cómo se autentica sin poner el token en la URL.** Las URL quedan
guardadas en logs de proxies y servidores, así que un token ahí se filtraría.
Y los navegadores no dejan añadir la cabecera `Authorization` a un
WebSocket. Por eso:

1. La app abre `wss://…/duelos/{id}/ws` sin token.
2. Su **primer mensaje** es `{"tipo": "auth", "token": "<access_token>"}`.
3. La API comprueba el token igual que en REST (firma, caducidad, usuario
   activo, sesión no cerrada) y que eres participante del duelo, y responde
   `listo`. Si no llega el mensaje en 10 s, o no es válido, cierra la
   conexión con un código que dice por qué.

Una vez dentro, la conexión sigue valiendo aunque el access token caduque a
los 15 min (ya se comprobó al entrar). La API también comprueba el origen
(`Origin`) de las conexiones que vienen de un navegador: solo acepta la web
de la app.

**Mensajes de la app al servidor**: solo `auth` (el primero) y
`{"tipo": "ping"}` cada 25 s, al que la API contesta `{"tipo": "pong"}`. El
ping evita que los proxies (Cloudflare, Render) corten una conexión callada.

**Antes de cerrar, la API avisa con un mensaje** `{"tipo": "error", "codigo":
4401, "motivo": "token_invalido"}` y después cierra con ese mismo código.
Detrás del proxy de Render y Cloudflare los mensajes llegan siempre, pero el
cierre con su código no siempre llega (comprobado en producción): **el
cliente debe actuar al recibir el mensaje `error`** (y cerrar él la conexión),
no esperar al código de cierre.

**Códigos de cierre** (los mismos que el `codigo` del mensaje `error`)

| Código | Motivo | Qué hace la app |
|---|---|---|
| 4401 | Token ausente, inválido o caducado | Refrescar el token y reconectar |
| 4403 | Origen no permitido o cuenta desactivada | No reconectar |
| 4404 | El duelo no existe o no participas | No reconectar |
| 4408 | No llegó el mensaje `auth` en 10 s | Reconectar |
| 1000/1001/1006 | Cierre normal, servidor reiniciándose o red caída | Reconectar con espera creciente y `GET /duelos/{id}` |

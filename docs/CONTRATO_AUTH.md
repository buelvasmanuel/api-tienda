# Contrato de autenticación (`/auth` y `/usuarios`)

> Contrato entre `api-tienda` y `app_estudiantes`. Versión **1.3**, 2026-10-13.
> El interceptor de Flutter se implementa leyendo este archivo. Cualquier
> cambio de la API que lo afecte se hace aquí primero, en el mismo commit.

### Cambios de la v1.3 (refresh en cookie para la web)

**Compatible:** sin la cabecera nueva nada cambia (el APK y las apps de
escritorio siguen igual).

| Cambio | Qué toca en el cliente |
|---|---|
| **Refresh en cookie, opcional** (§6.3): con `X-Refresh-En-Cookie: 1`, el refresh va en una cookie `HttpOnly` + `Partitioned` en vez de en el cuerpo, y la web recuerda la sesión al recargar | Solo la web, y solo si lo adopta: cabecera, `withCredentials` y `{}` en refresh/logout |
| `TokensOut.refresh_token` puede ser `null` (solo en modo cookie) | Nada en el APK: sin la cabecera siempre viene |
| `refresh_token` del cuerpo de `/auth/refresh` y `/auth/logout`: sigue siendo obligatorio **salvo** en modo cookie | Nada |
| Errores nuevos, solo en modo cookie: `403 origen_no_permitido`, `415 json_obligatorio` | Solo la web |

### Cambios de la v1.2 (Fase 6: pulido y auditoría)

Todos compatibles con el cliente actual (que ya lee `{"codigo", "mensaje"}`).

| Cambio | Qué toca en el cliente |
|---|---|
| **Errores con `codigo` en toda la API**: `estudiante_no_encontrado` (404), `carrera_desconocida` (422, conserva `carreras_validas` e `indices`), `modelo_no_disponible` (503) | Nada obligatorio; puede decidir por `codigo` también en esos casos |
| **500** no controlado: `{"codigo": "error_interno", "mensaje"}`, ahora **con cabeceras CORS** (antes Flutter Web lo veía como error de red) | Mostrar «Error interno» en vez de «sin conexión» |
| **413** `cuerpo_demasiado_grande` si el cuerpo supera 64 KiB | Nada (ningún cuerpo legítimo se acerca) |
| **Bloqueo por cuenta de 1 min como máximo** (antes hasta 15 min) | Nada: sigue siendo `429` con `Retry-After` |
| **`403 registro_cerrado`** en `POST /auth/registro` si el servidor cierra el registro (`REGISTRO_ABIERTO=false`; hoy está abierto) | Mostrar el mensaje («pide una cuenta a tu docente») |
| **`GET /salud`**: `200 {"estado": "ok", "base_de_datos": "ok"}` o `503 base_de_datos_no_disponible` | Opcional, para diagnosticar |

### Cambios de la v1.1 (respuesta a las dudas del frontend)

| # | Cambio | Qué toca en el cliente |
|---|---|---|
| a | **Límites por IP pensados para un aula** (toda la clase sale por la misma IP): en el login por IP solo cuentan los intentos **fallidos** (30/min) con un techo de 120/min; registro 60/hora; refresh 300/min. Nuevo `POST /usuarios/lote` para que el docente cree hasta 40 cuentas de una vez (§4.9, §7) | Nada en el interceptor. Pantalla de docente opcional para el lote |
| b | **Ventana del refresh idempotente: 120 s** (antes 30 s), para cubrir el timeout de 60 s más un reintento (§5) | Regla 5 del interceptor (§6) |
| c | **Los endpoints públicos ignoran `Authorization`** (§4.10) | Puede enviar la cabecera siempre; un token caducado nunca rompe una lectura pública |

---

## 1. Resumen

| Pieza | Valor |
|---|---|
| Access token | PASETO `v4.local` (cifrado). **Opaco para el cliente**: no se decodifica. Dura **15 min** (`expires_in: 900`). |
| Refresh token | Texto aleatorio de 43 caracteres (base64url). Dura **14 días** desde que se emite, y la sesión entera (familia) como máximo **30 días** desde el login. |
| Rotación | Cada `POST /auth/refresh` invalida el refresh usado y entrega otro. |
| Reintento seguro | Repetir el **mismo** refresh dentro de **120 s** devuelve **el mismo** refresh nuevo (idempotente). Ver §5. |
| Reuso | Presentar un refresh ya rotado fuera de esa ventana = robo → se revoca toda la sesión (`401 sesion_revocada`). |
| Transporte | Access: cabecera `Authorization: Bearer <access_token>`. Refresh: en el **cuerpo JSON** (nunca en cabecera ni en cookie). |
| Usuario | `alias` + contraseña. Sin email. |
| Roles | `jugador` (todo registro público), `docente`, `admin` (se asignan por un admin). |

---

## 2. Formato de errores

Los errores de autenticación y de `/usuarios` tienen siempre esta forma:

```json
{"detail": {"codigo": "token_expirado", "mensaje": "El token ha caducado"}}
```

- El cliente decide **por `codigo`**, nunca por `mensaje` (el mensaje puede cambiar; está en español y se puede mostrar al usuario).
- Los `401` llevan la cabecera `WWW-Authenticate: Bearer`.
- Los `429` llevan la cabecera `Retry-After` (segundos, entero).
- **Toda respuesta lleva `X-Request-ID`** (Fase 7; expuesta por CORS). Es el
  id con el que esa petición aparece en los logs del servidor: si un usuario
  reporta un error, ese id permite encontrarlo. El cliente puede enviar su
  propio `X-Request-ID` (8–64 caracteres `[A-Za-z0-9-]`) y la API lo reutiliza;
  si no es válido, genera otro.
- Los `422` de validación son los estándar de FastAPI
  (`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`) **sin el campo
  `input`** en toda la API: así nunca se devuelve una contraseña enviada.
- **Desde la Fase 6 toda la API usa este formato** (antes `/estudiantes` y
  `/prediccion` devolvían `detail` como texto). Los campos que ya existían se
  conservan: el 422 de carrera desconocida sigue llevando `mensaje` y
  `carreras_validas` (y `indices` en el lote), ahora junto a `codigo`. La única
  excepción son los `422` de validación, con el formato estándar de arriba.

### Códigos

| HTTP | `codigo` | Cuándo | Qué hace el cliente |
|---|---|---|---|
| 401 | `no_autenticado` | Falta `Authorization: Bearer …` en un endpoint protegido | Refrescar (§6) y reintentar una vez |
| 401 | `token_expirado` | Access caducado | Refrescar y reintentar una vez |
| 401 | `token_invalido` | Access alterado, de otra clave, de otra versión de PASETO, `iss`/`aud` incorrectos, o un refresh enviado como access | Refrescar y reintentar una vez |
| 401 | `sesion_cerrada` | Access emitido antes de un «cerrar todas las sesiones» o un cambio de contraseña, o el usuario ya no existe | Refrescar y reintentar una vez (el refresh fallará y llevará al login) |
| 401 | `credenciales_invalidas` | Login: alias inexistente **o** contraseña incorrecta (indistinguibles a propósito) | Mostrar error en el formulario. **No** refrescar |
| 401 | `refresh_invalido` | Refresh desconocido, mal formado o caducado | Borrar tokens → pantalla de login |
| 401 | `sesion_revocada` | Refresh de una sesión cerrada (logout, reuso detectado, admin) | Borrar tokens → login con aviso «Tu sesión se cerró por seguridad» |
| 403 | `permiso_denegado` | Rol insuficiente | Mostrar «No tienes permiso». No refrescar |
| 403 | `cuenta_desactivada` | Un admin desactivó la cuenta (en login, refresh o cualquier petición) | Borrar tokens → login con ese mensaje |
| 403 | `password_actual_incorrecta` | `PUT /auth/password` con la contraseña actual mal | Error en el formulario |
| 403 | `registro_cerrado` | `POST /auth/registro` con el registro cerrado en el servidor (v1.2) | Mostrar el mensaje; no reintentar |
| 403 | `origen_no_permitido` | Modo cookie (§6.3) desde un `Origin` que no es la web de la app, o sin `Origin` (v1.3) | Error de configuración: no reintentar |
| 404 | `usuario_no_encontrado` | `/usuarios/{id}` inexistente | — |
| 404 | `estudiante_no_encontrado` | `/estudiantes/{id}` inexistente (Fase 6; antes, texto) | — |
| 409 | `alias_en_uso` | Registro con un alias que ya existe (sin distinguir mayúsculas). En `POST /usuarios/lote` el detalle añade `"aliases": [...]` con los que chocan | Error en el formulario |
| 409 | `no_puedes_modificarte` | Un admin intenta cambiar su propio rol/estado desde `/usuarios` | — |
| 413 | `cuerpo_demasiado_grande` | Cuerpo de más de 64 KiB (v1.2) | — |
| 415 | `json_obligatorio` | Modo cookie (§6.3) con un cuerpo que no es `application/json` (v1.3) | Enviar `Content-Type: application/json` |
| 422 | `carrera_desconocida` | `/prediccion/*` con una carrera que el modelo no conoce; añade `carreras_validas` (y `indices` en el lote) | Mostrar las válidas |
| 429 | `demasiados_intentos` | Límite por cuenta o por IP superado | Esperar `Retry-After` s; mostrar cuenta atrás. **No** reintentar en bucle |
| 500 | `error_interno` | Error no controlado del servidor; el detalle solo va al log (v1.2) | «Error interno», reintentar más tarde |
| 503 | `base_de_datos_no_disponible` | `GET /salud` sin conexión con la base (v1.2) | — |
| 503 | `modelo_no_disponible` | `/prediccion/riesgo` o `/metricas` sin modelo entrenado (Fase 6; antes, texto) | Reintentar más tarde |

**No existe un código de concurrencia (no hay 409 en `/auth/refresh`).**
Dos refresh simultáneos con el mismo token reciben ambos `200` con el mismo
refresh nuevo. Ver §5.

---

## 3. Esquemas

```text
Alias       = texto 3–30 caracteres, solo [A-Za-z0-9_]. Único sin distinguir mayúsculas
              ("Ana_01" y "ana_01" son el mismo). Se guarda como se escribió.
Password    = texto 10–128 caracteres, sin reglas de composición. No puede ser igual
              al alias (sin distinguir mayúsculas). No se recortan espacios.
Rol         = "jugador" | "docente" | "admin"
Fecha       = texto ISO 8601 en UTC, p. ej. "2026-10-11T18:04:05.123456Z"
```

**`UsuarioOut`**
```json
{"id": 7, "alias": "Detective_Ana", "rol": "jugador"}
```

**`UsuarioAdminOut`** (solo en `/usuarios`)
```json
{"id": 7, "alias": "Detective_Ana", "rol": "jugador", "activo": true, "creado_en": "2026-10-11T18:04:05.123456Z"}
```

**`TokensOut`** (respuesta de registro, login, refresh y cambio de contraseña)
```json
{
  "access_token": "v4.local.AL3oT_6Z…",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_token": "Qm9yZGVy…(43 caracteres)",
  "refresh_expires_in": 1209600,
  "usuario": {"id": 7, "alias": "Detective_Ana", "rol": "jugador"}
}
```
- `refresh_token`: `null` **solo** si se pidió en cookie (§6.3, v1.3); sin la
  cabecera `X-Refresh-En-Cookie: 1` siempre viene.
- `expires_in`: segundos de vida del access.
- `refresh_expires_in`: segundos hasta que caduca **este** refresh
  (mínimo entre 14 días y lo que le quede a la sesión de sus 30 días).

Todos los cuerpos de entrada rechazan campos desconocidos con `422`
(p. ej. enviar `"rol": "admin"` en el registro).

---

## 4. Endpoints

Rutas **sin** barra final. Todas las respuestas de `/auth/*` llevan
`Cache-Control: no-store`.

### 4.1 `POST /auth/registro` — público

Cuerpo: `{"alias": Alias, "password": Password}`

| Respuesta | Cuándo |
|---|---|
| `201 TokensOut` | Cuenta creada con rol `jugador` y sesión iniciada |
| `403 registro_cerrado` | El servidor tiene el registro cerrado (`REGISTRO_ABIERTO=false`) |
| `409 alias_en_uso` | |
| `422` | Alias/contraseña inválidos, campos extra |
| `429 demasiados_intentos` | Más de 60 registros por hora desde la misma IP |

### 4.2 `POST /auth/login` — público

Cuerpo: `{"alias": str, "password": str}`

| Respuesta | Cuándo |
|---|---|
| `200 TokensOut` | Crea una sesión (familia) nueva |
| `401 credenciales_invalidas` | |
| `403 cuenta_desactivada` | Solo si la contraseña era correcta |
| `429 demasiados_intentos` | Cuenta bloqueada temporalmente, más de 30 intentos **fallidos**/min desde la IP, o más de 120 intentos/min en total desde la IP |

Bloqueo por cuenta: a partir de **5 fallos seguidos**, la cuenta se bloquea
30 s, y cada fallo posterior la bloquea **1 min** (máximo; v1.2, antes llegaba
a 15 min). Mientras está bloqueada responde `429` **aunque la contraseña sea
correcta**. Un login correcto pone el contador a cero. Es corto a propósito:
un bloqueo largo permitiría a cualquiera dejar sin acceso a otro jugador.

### 4.3 `POST /auth/refresh` — público (el refresh es la credencial)

Cuerpo: `{"refresh_token": str}` (hasta 512 caracteres; cualquier texto que
no sea un refresh vigente da `401 refresh_invalido`, no `422`). En modo
cookie (§6.3) basta `{}`: el refresh sale de la cookie.

| Respuesta | Cuándo |
|---|---|
| `200 TokensOut` | Refresh válido: access nuevo + refresh nuevo (el anterior queda usado) |
| `200 TokensOut` | Mismo refresh repetido ≤ 120 s después y su sucesor aún sin usar: **el mismo** refresh nuevo que la primera vez, access nuevo |
| `401 refresh_invalido` | Desconocido o caducado |
| `401 sesion_revocada` | Sesión cerrada, o reuso detectado (en ese caso la API cierra la sesión entera) |
| `403 cuenta_desactivada` | |
| `429 demasiados_intentos` | Más de 300/min desde la IP |

### 4.4 `POST /auth/logout` — público

Cuerpo: `{"refresh_token": str}` (o `{}` en modo cookie, §6.3) → **siempre
`204`** (también si el token no existe o ya estaba revocado). Cierra la sesión a la que pertenece ese refresh.
El access de esa sesión sigue siendo válido hasta que caduque (≤ 15 min): el
cliente lo descarta.

### 4.5 `POST /auth/logout-todo` — autenticado

Sin cuerpo → `204`. Cierra **todas** las sesiones del usuario e invalida
**al instante** todos sus access (incluido el de esta petición).

### 4.6 `GET /auth/yo` — autenticado

`200 UsuarioOut`.

### 4.7 `GET /auth/sesiones` — autenticado

```json
[{"id": "9f2c…(32 hex)", "iniciada_en": "…Z", "ultimo_uso": "…Z", "dispositivo": "Dart/3.9 (dart:io)", "actual": true}]
```
Sesiones abiertas del usuario, la más reciente primero (por último uso).
`dispositivo` es la cabecera `User-Agent` del último login o refresh de esa
sesión, recortada a 200 caracteres (o `null`). `actual` marca la sesión del
access usado.

### 4.8 `PUT /auth/password` — autenticado

Cuerpo: `{"password_actual": str, "password_nueva": Password}`

| Respuesta | Cuándo |
|---|---|
| `200 TokensOut` | Contraseña cambiada; **todas** las sesiones se cierran y se devuelve una nueva para este dispositivo |
| `403 password_actual_incorrecta` | |
| `422` | Contraseña nueva inválida |

### 4.9 `/usuarios` — gestión (no la usa el juego del jugador)

| Método y ruta | Rol | Cuerpo | Respuesta |
|---|---|---|---|
| `GET /usuarios` | docente, admin | — | `200 list[UsuarioAdminOut]` ordenada por `id` |
| `PATCH /usuarios/{id}` | admin | `{"rol"?: Rol, "activo"?: bool}` | `200 UsuarioAdminOut`; `404`; `409 no_puedes_modificarte`. Desactivar cierra todas sus sesiones |
| `PUT /usuarios/{id}/password` | admin | `{"password_nueva": Password}` | `204`; cierra todas sus sesiones. Es la forma de recuperar una cuenta (no hay email) |
| `POST /usuarios/lote` | docente, admin | `{"aliases": [Alias, …]}` (1–40, sin repetir) | `201 {"cuentas": [{"id", "alias", "password"}]}`; `409 alias_en_uso` con `"aliases"`; `422` |

**Alta en lote** (`POST /usuarios/lote`): crea cuentas de **jugador** con una
contraseña generada por la API (12 caracteres en 3 grupos, p. ej.
`k7mq-x3hp-9tfa`, sin `i l o 0 1` para que se pueda dictar). **Todas o
ninguna**: si algún alias ya existe, no se crea ninguna. Las contraseñas solo
aparecen en esta respuesta (`Cache-Control: no-store`): el docente debe
guardarlas o repartirlas en ese momento; cada alumno puede cambiar la suya con
`PUT /auth/password`. Pensado para el aula: los alumnos no se registran y no
chocan con el límite de registros por IP.

### 4.10 Endpoints existentes

| Ruta | Acceso |
|---|---|
| `GET /estudiantes…`, `GET /estudiantes/estadisticas/…`, `/prediccion/*`, `GET /` | Público (sin cambios) |
| `POST /estudiantes/`, `PUT/DELETE /estudiantes/{id}` | Solo `admin` → `401` sin token, `403 permiso_denegado` con otro rol |
| `/productos`, `/categorias` | **Eliminados** (404) |

**`Authorization` en endpoints públicos: se ignora.** Los endpoints públicos
(los de la tabla de arriba, y `/auth/registro`, `/auth/login`,
`/auth/refresh`, `/auth/logout`) **no leen** la cabecera: con un token
válido, caducado, alterado o de otro esquema responden exactamente lo mismo
que sin ella, y **nunca dan 401 por la cabecera**. El cliente puede enviarla
siempre. Si algún día un endpoint usa la sesión de forma opcional (p. ej.
personalizar el campus con el progreso del jugador), este contrato lo marcará
como «autenticación opcional» y ahí un token inválido sí dará 401, para que el
cliente refresque.

---

## 5. Regla de concurrencia y reintentos del refresh (decisión final)

**Regla:** si llega un refresh que ya fue rotado, la API mira dos cosas:

1. ¿Se rotó hace **≤ 120 s**?
2. ¿Su sucesor **todavía no se ha usado**?

Si las dos son sí → `200` con **el mismo** refresh sucesor (no se crea uno
nuevo; el sucesor se deriva del token presentado con un HMAC de la clave del
servidor). Si alguna es no → reuso: se revoca la sesión entera y `401
sesion_revocada`.

Por qué así (frente a las dos propuestas iniciales):

| Caso real | «409 sin revocar, 20 s» | «Re-rotar, 30 s» | **Idempotente, 120 s** |
|---|---|---|---|
| Dos peticiones con el mismo refresh a la vez | La 2.ª recibe 409; la 1.ª trae el token: funciona | Cada una recibe un refresh distinto y uno de ellos queda revocado: si el cliente guarda el que llegó último y es el revocado, la sesión muere | Ambas reciben el mismo: funciona |
| La respuesta se pierde (timeout en arranque en frío) y el cliente reintenta | 409 durante 20 s y el cliente nunca recibe el token nuevo: tiene que volver a iniciar sesión | Funciona | Funciona |
| Un atacante con un refresh robado | Detectado | Detectado | Detectado: en cuanto alguien usa el sucesor, el token viejo deja de ser aceptable; y si el atacante lo usa primero, el cliente legítimo será el que dispare la detección más tarde |

**Por qué 120 s (v1.1).** Con 30 s no bastaba: si la API rota el refresh al
principio de la espera del cliente (60 s de timeout) y la respuesta se pierde,
el reintento llega a los ~60 s y se trataría como reuso, cerrando la sesión
de un alumno que no hizo nada raro. Con 120 s cabe el primer intento completo
(60 s) más un reintento (que encuentra el servidor ya despierto).

**Impacto en seguridad de ampliar la ventana.** La ventana solo cambia algo
cuando alguien presenta un refresh **ya rotado** cuyo sucesor **aún no se ha
usado**:

- No crea credenciales nuevas: quien presenta el refresh viejo recibe el mismo
  sucesor que ya tiene el cliente legítimo (más un access de 15 min).
- No impide la detección, la retrasa: en cuanto uno de los dos usa el
  sucesor, el otro, al usarlo después, dispara el reuso y se revoca la familia.
- Lo que se pierde: sin ventana, presentar un refresh viejo se detectaría al
  instante. Con 120 s, un atacante que robe un refresh **ya rotado** y lo use
  en menos de 2 minutos desde la rotación obtiene un access válido hasta
  15 min antes de que la detección corte la sesión (que no corta ese access:
  ver §4.4).
- Es un riesgo acotado (2 min de ventana, exige robar el token justo en ese
  intervalo) frente a uno seguro y frecuente en Render free (alumnos
  desconectados por arranques en frío). Por eso se amplía en vez de solo
  documentarlo.

Lo que **no** cambia: el cliente sigue debiendo hacer un solo refresh a la vez
(§6). La regla es una red de seguridad, no un permiso para refrescar en paralelo.

---

## 6. Qué debe hacer el cliente

### 6.1 Guardar

| Dato | Móvil / escritorio | Web |
|---|---|---|
| `access_token` | Memoria | Memoria |
| `refresh_token` | `flutter_secure_storage` | **Cookie** con el modo cookie (§6.3): la API la guarda y JavaScript no la ve. Sin él, **memoria** (recargar = volver a iniciar sesión). No usar `localStorage`; `sessionStorage` tampoco: al duplicar una pestaña se copia el refresh y las dos pestañas acabarían disparando la detección de reuso |
| `usuario` | Memoria (se puede cachear sin el token) | Memoria |

Al recibir un `TokensOut`, **guardar primero el `refresh_token` nuevo** y
después usar el access. Nunca volver a enviar un refresh ya sustituido.

### 6.2 Interceptor

1. Peticiones a `/auth/registro`, `/auth/login`, `/auth/refresh`,
   `/auth/logout`: **sin** interceptor (no se refresca ante sus errores).
2. Antes de cada petición protegida: si al access le quedan menos de 60 s
   (según `expires_in` y la hora en que se recibió), refrescar primero.
3. Si una petición protegida responde `401` con `no_autenticado`,
   `token_expirado`, `token_invalido` o `sesion_cerrada`: refrescar y
   **reintentar una sola vez**. Si vuelve a dar 401 → login.
4. **Un solo refresh en curso** (single-flight): si llega otra petición
   mientras se refresca, espera al mismo `Future` y usa su resultado.
5. Si el refresh falla **sin respuesta HTTP**, reintentar con el **mismo**
   refresh (nunca con otro):
   - por **timeout** (60 s): una vez más (el servidor ya estará despierto);
   - por **error de red inmediato**: hasta 2 veces más, enseguida.

   Ningún reintento puede salir más de **110 s** después del primer intento,
   para que entre en la ventana de 120 s. Si se agotan → error de conexión y
   se conservan los tokens; el siguiente intento, más tarde, puede acabar en
   `401 sesion_revocada` si la API llegó a rotar el refresh (→ login).
6. Si el refresh responde `401` (`refresh_invalido`/`sesion_revocada`) o
   `403 cuenta_desactivada` → borrar tokens y ir a login (con el mensaje que
   corresponda).
7. `403 permiso_denegado` y `429` no se reintentan automáticamente.

### 6.3 Web: refresh en cookie (opcional, v1.3)

**Para qué:** que la web recuerde la sesión al recargar la página sin guardar
el refresh donde JavaScript lo pueda leer. El APK y las apps de escritorio
**no** lo usan (no tienen cookies de navegador ni CORS).

**Cómo se pide:** la cabecera `X-Refresh-En-Cookie: 1` (exactamente `1`) en
`POST /auth/registro`, `/auth/login`, `/auth/refresh`, `/auth/logout`,
`/auth/logout-todo`, `PUT /auth/password` y `POST /passkeys/login/verificacion`,
y las peticiones con credenciales (`withCredentials: true` en Dart/`fetch(…, {credentials: "include"})`).

**Qué hace la API:**

| Endpoint | Sin la cabecera (APK, escritorio) | Con la cabecera (web) |
|---|---|---|
| registro, login, cambio de contraseña, login con passkey | `refresh_token` en el cuerpo | `refresh_token: null` y `Set-Cookie: __Host-refresh=…` |
| `POST /auth/refresh` | Cuerpo `{"refresh_token": …}` obligatorio | Cuerpo `{}`: lee la cookie; responde con la cookie nueva. Si lo rechaza (`401`/`403`), **borra la cookie** |
| `POST /auth/logout` | Cuerpo `{"refresh_token": …}` obligatorio | Cuerpo `{}`: cierra la sesión de la cookie y la borra (también `204` si no había cookie) |
| `POST /auth/logout-todo` | Igual | Además borra la cookie |

La cookie: `__Host-refresh=<refresh>; Max-Age=<refresh_expires_in>; Path=/;
Secure; HttpOnly; SameSite=None; Partitioned`. Va ligada a la página donde se
creó (`buelvasmanuel.github.io`): el navegador no la envía desde otra web.

Si la petición con la cabecera trae `refresh_token` en el cuerpo, se usa ese
(y la respuesta lo devuelve en cookie). Sin la cabecera, la cookie **se
ignora**.

**Defensas contra CSRF** (la cookie viaja sola en cada petición):

1. **Partitioned**: solo se envía cuando la pestaña es la web de la app.
2. La cabecera propia obliga al navegador a hacer un *preflight* de CORS, que
   solo aprueba los orígenes de `CORS_ORIGINS`.
3. La API comprueba `Origin` (debe ser uno de `CORS_ORIGINS`) → si no,
   `403 origen_no_permitido`. Con `CORS_ORIGINS="*"` el modo cookie se rechaza
   siempre.
4. Si la petición lleva cuerpo, debe ser `application/json` → si no,
   `415 json_obligatorio` (un formulario HTML no puede enviar JSON).

**Qué debe hacer el cliente web:**

- Enviar `X-Refresh-En-Cookie: 1` y credenciales en las peticiones de la
  tabla; en el resto no hace falta (las protegidas siguen con `Authorization`).
- Al arrancar la web: `POST /auth/refresh` con `{}`. `200` → sesión
  recuperada; `401 refresh_invalido` → no había sesión (pantalla de login,
  sin aviso de error).
- No guardar el refresh en ningún sitio: el cuerpo trae `null`.
- El interceptor no cambia (§6.2, reglas 1–7): el single-flight sigue siendo
  necesario, y los reintentos sin respuesta van con la misma cookie (si la
  primera respuesta se perdió, el navegador puede no haber guardado la nueva:
  la ventana de 120 s devuelve la misma).
- **Varias pestañas** comparten la cookie: si dos refrescan a la vez, las dos
  reciben el mismo sucesor (§5).
- Cerrar sesión: `POST /auth/logout` con `{}` y borrar el access de memoria.
- Si el navegador no guarda la cookie (bloqueo de cookies de terceros sin
  soporte de `Partitioned`): el refresh de arranque da `401 refresh_invalido`
  y la web se comporta como antes (iniciar sesión al recargar). Navegadores:
  ver la tabla de abajo.

**Navegadores** (cookie de `api-tienda-wjac.onrender.com` dentro de
`buelvasmanuel.github.io`, que para el navegador son sitios distintos):

| Navegador | `Partitioned` (CHIPS) |
|---|---|
| Chrome, Edge | 114+ |
| Firefox | 141+ |
| Safari | 26.2+ (según la página de CHIPS de Google; antes, ITP bloquea la cookie) |
| Brave | ver «Brave» abajo (comprobado a mano) |

**En local** (Docker, `compose.yaml`): `CORS_ORIGINS` tiene
`http://localhost:8080` y `http://127.0.0.1:8080` (`flutter run -d chrome
--web-port 8080`). Chrome y Firefox aceptan cookies `Secure` en
`http://localhost`. Con `CORS_ORIGINS=*` (el `.env.example` de la instalación
sin Docker) el modo cookie da `403`: hay que poner orígenes explícitos.

### 6.4 Cerrar sesión

Enviar `POST /auth/logout` con el refresh y **borrar los tokens locales sea
cual sea el resultado** (incluido un fallo de red). En modo cookie (§6.3), con
`{}`: la API borra la cookie (JavaScript no puede).

### 6.5 Tiempos de espera

Render free tarda ~50 s en despertar: usar 60 s de timeout para la primera
petición de la sesión y para `/auth/*`.

---

## 7. Límites de peticiones

En un aula toda la clase sale a internet por **la misma IP pública**. Por eso
los límites por IP son altos y la defensa principal contra la fuerza bruta es
el **bloqueo por cuenta** (§4.2), que vive en la base de datos y no depende
de la IP.

| Endpoint | Límite por IP | Además |
|---|---|---|
| `POST /auth/login` | 30 intentos **fallidos** / minuto (los correctos no cuentan) y 120 intentos / minuto en total | Bloqueo por cuenta (§4.2). Pasado el límite de fallos, la API responde 429 sin calcular ningún hash |
| `POST /auth/registro` | 60 / hora | Para una clase entera, mejor el alta en lote (§4.9) |
| `POST /auth/refresh` | 300 / minuto | Un refresh no se puede adivinar: el límite solo frena abusos de carga |
| `POST /usuarios/lote` | — (requiere docente o admin) | Máx. 40 cuentas por petición |

Ejemplo: 30 alumnos que inician sesión a la vez desde el colegio no tocan
ningún límite, aunque una decena se equivoque de contraseña.

Al superarlo: `429 demasiados_intentos` con `Retry-After` (la API la expone
por CORS, así que Flutter Web también puede leerla). Los contadores
por IP viven en memoria del servidor (se reinician si Render se duerme;
en Docker local van a Redis, `LIMITES_URI`); el bloqueo por cuenta vive en
la base de datos.

---

## 8. Detalles de implementación (informativo para el cliente)

- Access: claims `sub` (id del usuario, texto), `sid` (id de la sesión),
  `iat`, `exp`, `iss = "api-tienda"`, `aud = "app-estudiantes"`. El rol **no**
  va en el token: la API lee el usuario de la base en cada petición, así que
  un cambio de rol o una desactivación se aplica al instante.
- Refresh: se guarda solo su SHA-256. El primero de una sesión es aleatorio
  (256 bits); cada sucesor es `HMAC-SHA256(clave, refresh_anterior)`.
- Contraseñas: Argon2id (`t=2`, `m=19 MiB`, `p=1`, recomendación OWASP).
- Cambiar la clave del servidor invalida todos los access (el cliente
  refresca solo, sin notarlo) pero no los refresh.

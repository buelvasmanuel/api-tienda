# Arquitectura de `api-tienda`

> Estado verificado contra el código. Primera verificación: **2026-10-11**
> (commit `52ddbf5`, §10). Última actualización: **Fase 6 (pulido y
> auditoría final del backend)** del plan de `PROPUESTA_ESTUDIANTES_2.0.md`. Si el código
> cambia, este documento se actualiza en el mismo commit.
>
> Los contratos con la app Flutter están aparte:
> [CONTRATO_AUTH.md](CONTRATO_AUTH.md) (v1.1),
> [CONTRATO_LABORATORIO.md](CONTRATO_LABORATORIO.md) (v1.1) y
> [CONTRATO_JUEGO.md](CONTRATO_JUEGO.md) (v1).

---

## 1. Visión general

```
┌──────────────────────────┐  HTTPS/JSON  ┌──────────────────┐  HTTPS  ┌───────────────────────────┐  TLS (CA verificada)  ┌───────────────────┐
│ app_estudiantes (Flutter)│ ───────────► │ Cloudflare       │ ──────► │ api-tienda (FastAPI)      │ ────────────────────► │ MySQL 8.4 (Aiven) │
│ Web (GitHub Pages) + APK │ ◄─────────── │ (proxy de Render)│ ◄────── │ Render free · oregon      │                       │ DigitalOcean sfo  │
└──────────────────────────┘              └──────────────────┘         │ 1 proceso uvicorn         │                       └───────────────────┘
                                                                       │ 0,1 CPU · 512 MB          │
                                                                       │ modelo .joblib (del build)│
                                                                       └───────────────────────────┘
```

- URL pública: `https://api-tienda-wjac.onrender.com` (Swagger en `/docs`).
- Delante de uvicorn hay un proxy (cabeceras `server: cloudflare`,
  `x-render-origin-server: uvicorn`). uvicorn ya confía en sus cabeceras
  `X-Forwarded-*`: la redirección `307` de `/estudiantes` → `/estudiantes/`
  sale con `https://` (comprobado el 2026-10-11). Consecuencia para el límite
  por IP: ver §9.
- Local: MariaDB sin SSL; los tests usan la base `tienda_test`.

---

## 2. Estructura de carpetas

```
api-tienda/
├── app/
│   ├── main.py            # FastAPI, handler de 422 sin "input", límites, CORS, cabeceras de seguridad, routers, GET /
│   ├── config.py          # Settings: DB_*, DB_SSL_CA, CORS_ORIGINS, CLAVE_TOKENS, IP_CLIENTE, ARGON2_*
│   ├── database.py        # crear_engine(), collation por evento, NAMING_CONVENTION, Base, get_db()
│   ├── models.py          # Estudiante, Usuario, SesionRefresh, IntentoCaso, Rescate, InsigniaObtenida
│   ├── schemas.py         # Esquemas Pydantic (estudiantes, predicción, auth)
│   ├── ml.py              # Variables, pipeline y utilidades del modelo (compartido script/API)
│   ├── modelo_juego.py    # Modelo del juego: inferencia y explicación exacta desde el JSON
│   ├── modelo_juego.json  # Logística congelada (versión 2026-10-11), versionada en git
│   ├── laboratorio.py     # Reglas del rescate: intervenciones, óptimo, estrellas
│   ├── casos.py           # Catálogo de casos (Modo 2): opciones y respuestas desde los datos
│   ├── progreso.py        # XP, niveles, racha (UTC−5), insignias y ranking
│   ├── reloj.py           # reloj.ahora(): UTC sin zona, sustituible en tests
│   ├── seguridad.py       # Argon2id, PASETO v4.local, refresh opacos (sin BD)
│   ├── sesiones.py        # Familias de refresh en la BD: abrir, rotar, revocar, listar
│   ├── dependencias.py    # sesion_actual, usuario_actual, requiere_rol(...)
│   ├── errores.py         # {"detail": {"codigo", "mensaje"}}, 401 con WWW-Authenticate, 429
│   ├── limites.py         # Límite por IP (librería limits) e ip_cliente()
│   ├── middleware.py      # 413 por cuerpo > 64 KiB y 500 en JSON (dentro de CORS)
│   └── routers/
│       ├── auth.py        # /auth/*
│       ├── usuarios.py    # /usuarios
│       ├── estudiantes.py # escrituras solo admin
│       ├── estadisticas.py
│       ├── prediccion.py  # CargadorModelo (build) + explicación y lote (modelo del juego)
│       ├── laboratorio.py # /laboratorio (con sesión; guarda rescates y da XP)
│       └── juego.py       # /juego (con sesión)
├── alembic/versions/      # 7 migraciones (§3.2)
├── scripts/
│   ├── seed_estudiantes.py  # Faker("es_CO"), semilla 42, --cantidad, --reset
│   ├── graficas.py          # PNG por estadística (llama a la API con httpx2)
│   ├── entrenar_modelo.py   # CV estratificada 5 particiones, elige por F2, --sin-graficas
│   ├── congelar_modelo_juego.py  # regenera app/modelo_juego.json (con commit)
│   ├── generar_clave.py     # valor para CLAVE_TOKENS
│   └── crear_usuario.py     # alias + --rol; única forma de crear el primer admin
├── tests/                 # 423 tests (§8)
├── docs/                  # ARQUITECTURA.md (este archivo), CONTRATO_AUTH.md, CONTRATO_LABORATORIO.md
├── .github/workflows/tests.yml  # CI: pytest con mariadb:lts y mysql:8.4
├── alembic.ini, pytest.ini, render.yaml, .python-version (3.14.7)
├── requirements.txt, requirements-dev.txt, .env.example
├── README.md, INSTRUCCIONES_PROFESOR.md, PROPUESTA_ESTUDIANTES_2.0.md
└── (ignorados por git) .env, .env.aiven, modelos/, graficas/, venv/
```

---

## 3. Datos

### 3.1 Tablas

`Base` declara `MetaData(naming_convention=...)` (`ix_`, `uq_`, `ck_`, `fk_`,
`pk_`) para que Alembic pueda borrar restricciones en los downgrade. Las
fechas son `DATETIME(6)` (microsegundos) y **siempre UTC sin zona**: se
escriben y comparan con `reloj.ahora()`; al cliente se le envían con `Z`.

**`estudiantes`** — `Estudiante`

| Columna | Tipo | CHECK |
|---|---|---|
| `id` | INT | PK |
| `nombre`, `apellido` | VARCHAR(50) | — |
| `edad` | INT | 16–35 |
| `carrera` | VARCHAR(100) | — (texto libre) |
| `semestre` | INT | 1–10 |
| `promedio` | NUMERIC(3,2) | 0–5 |
| `ciudad` | VARCHAR(100) | — |
| `horas_estudio` | INT | 0–40 (horas/semana) |
| `asistencia` | INT | 0–100 (%) |

**`usuarios`** — `Usuario`

| Columna | Tipo | Restricciones |
|---|---|---|
| `id` | INT | PK |
| `alias` | VARCHAR(30) | UNIQUE (sin distinguir mayúsculas, por la collation); CHECK `REGEXP '^[A-Za-z0-9_]{3,30}$'` |
| `hash_password` | VARCHAR(255) | `$argon2id$…` |
| `rol` | VARCHAR(10) | CHECK IN (`jugador`, `docente`, `admin`); default `jugador` |
| `activo` | BOOL | default 1 |
| `sesiones_validas_desde` | DATETIME(6) NULL | Los access con `iat` anterior dejan de valer |
| `intentos_fallidos` | INT | CHECK ≥ 0 |
| `bloqueado_hasta` | DATETIME(6) NULL | Bloqueo por fuerza bruta |
| `creado_en`, `actualizado_en` | DATETIME(6) | |

**`sesiones_refresh`** — `SesionRefresh` (una fila por refresh emitido)

| Columna | Tipo | Restricciones |
|---|---|---|
| `id` | BIGINT | PK |
| `usuario_id` | INT | FK → `usuarios.id` `ON DELETE CASCADE`, índice |
| `familia_id` | VARCHAR(32) | uuid4 hex, índice. Un login = una familia |
| `hash_token` | VARCHAR(64) | SHA-256 hex del refresh, UNIQUE |
| `creado_en` | DATETIME(6) | |
| `expira_en` | DATETIME(6) | 14 días desde su emisión, sin pasar de `familia_expira_en` (CHECK) |
| `familia_expira_en` | DATETIME(6) | 30 días desde el login |
| `usado_en` | DATETIME(6) NULL | Cuándo se rotó |
| `revocado_en`, `motivo_revocacion` | NULL | Van juntos (CHECK); motivo IN (`logout`, `logout_todo`, `reuso`, `password`, `desactivada`) |
| `user_agent` | VARCHAR(200) NULL | |

**Juego (Fase 5).** No hay tabla de «progreso»: XP, nivel, racha e insignias
se calculan a partir de estas filas, así no pueden desincronizarse.

| Tabla | Columnas | Restricciones |
|---|---|---|
| `intentos_caso` | `id` BIGINT, `usuario_id`, `caso` VARCHAR(40), `respuesta`, `correcto`, `xp`, `creado_en` | FK usuario CASCADE; CHECK `xp >= 0`; índice (`usuario_id`, `caso`) |
| `rescates` | `id` BIGINT, `usuario_id`, `estudiante_id` NULL, copia de `horas_estudio_inicial` y `asistencia_inicial`, `estudio`, `tutoria`, `fichas`, `probabilidad_inicial/final` NUMERIC(5,4), `logrado`, `estrellas`, `xp`, `version_modelo`, `creado_en` | FK usuario CASCADE; FK estudiante **SET NULL** (el historial sobrevive); CHECK de estrellas 0–3, fichas 0–5, probabilidades 0–1, xp ≥ 0 |
| `usuarios_insignias` | `usuario_id`, `codigo`, `obtenida_en` | PK (`usuario_id`, `codigo`): una insignia no se repite. El catálogo vive en `app/progreso.py` |

No hay columna de sucesor: el sucesor de un refresh es `HMAC(clave, refresh)`
y se encuentra por su hash. Las familias que pasan sus 30 días se borran al
abrir cualquier sesión (no hay cron en Render free).

### 3.2 Migraciones (Alembic 1.20)

Cadena lineal; archivos con fecha en el nombre (`file_template`).
`tests/test_migraciones.py` comprueba que `upgrade head` produce exactamente
el esquema de los modelos.

| Revisión | Qué hace |
|---|---|
| `3adaaf027eed` | Crea `productos` (primera actividad) |
| `cc73e9874b06` | Crea `categorias` y la FK de `productos` |
| `81a56be44550` | Crea `estudiantes` con 5 CHECK |
| `1d65ce94be3f` | Collation `utf8mb4_unicode_ci` en la base y las tablas; downgrade no-op |
| `f9047f24dd9b` | **Borra `productos` y `categorias`**; el downgrade las recrea vacías |
| `5527590d242a` | Crea `usuarios` y `sesiones_refresh` |
| `7661ab474407` (head) | Crea `intentos_caso`, `rescates` y `usuarios_insignias` (Fase 5) |

### 3.3 Collation y conexión

- `database.COLLATION = "utf8mb4_unicode_ci"`, fijada con un listener
  `connect` porque SQLAlchemy reemplaza la que se pasa a PyMySQL. La comparten
  app, Alembic y tests vía `crear_engine()`.
- SSL: si `DB_SSL_CA` tiene valor (ruta o PEM pegado), se verifica certificado
  y nombre de host.
- `pool_pre_ping=True`; `SessionLocal(autoflush=False)`; `get_db()` abre y
  cierra una sesión por petición.
- **Aislamiento `READ COMMITTED`** (en `crear_engine`, para app, Alembic y
  tests). Con el REPEATABLE READ por defecto, una transacción lee la «foto» de
  su primera lectura aunque después espere un `FOR UPDATE`: dos respuestas
  simultáneas del mismo jugador daban la XP dos veces o un 500 por clave
  duplicada (comprobado en MySQL 8.4; MariaDB 11.6+ aborta con el error 1020).
  Login, refresh y XP bloquean antes la fila que decide.

---

## 4. Endpoints

Todos los endpoints son **síncronos** (`def`): FastAPI los ejecuta en su pool
de hilos. Las rutas de colección de estudiantes terminan en `/`; las de
`/auth` y `/usuarios`, no.

Formato de errores (unificado en la Fase 6):
- Todos: `{"detail": {"codigo", "mensaje", …campos extra}}` (códigos en
  CONTRATO_AUTH.md §2 y en los demás contratos). El 422 de carrera
  desconocida conserva `carreras_validas` (e `indices` en el lote).
- **422 de validación**: el estándar de FastAPI **sin el campo `input`** (no
  devuelve lo enviado, contraseñas incluidas). Handler en `main.py`.
- **500** no controlado: `{"codigo": "error_interno"}`, con CORS; el detalle
  solo va al log `app.errores`. **413** `cuerpo_demasiado_grande` por encima de
  64 KiB (`app/middleware.py`).

`Decimal` se serializa como **texto** (`"3.45"`): por eso Flutter usa `leerNum`.

### 4.1 Raíz

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/` | `{"mensaje": "API funcionando"}` — healthcheck de Render (no toca la BD) |

### 4.2 Autenticación y usuarios

Detalle completo (cuerpos, códigos, qué hace el cliente) en CONTRATO_AUTH.md §4.

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| POST | `/auth/registro` | público, 60/h por IP | 201 `TokensOut`; 409, 422, 429 |
| POST | `/auth/login` | público; por IP 30 fallos/min y 120/min en total; bloqueo por cuenta | `TokensOut`; 401, 403, 429 |
| POST | `/auth/refresh` | público, 300/min por IP | `TokensOut`; 401 `refresh_invalido`/`sesion_revocada`, 403, 429 |
| POST | `/auth/logout` | público | 204 siempre |
| POST | `/auth/logout-todo` | con token | 204 |
| GET | `/auth/yo` | con token | `UsuarioOut` |
| GET | `/auth/sesiones` | con token | `list[SesionOut]` |
| PUT | `/auth/password` | con token | `TokensOut`; 403 `password_actual_incorrecta`, 422, 429 |
| GET | `/usuarios` | docente, admin | `list[UsuarioAdminOut]` |
| PATCH | `/usuarios/{id}` | admin (no sobre sí mismo) | `UsuarioAdminOut`; 404, 409 |
| PUT | `/usuarios/{id}/password` | admin (no sobre sí mismo) | 204; 404, 409, 422 |
| POST | `/usuarios/lote` | docente, admin | 201 `LoteOut` (contraseñas generadas, todas o ninguna); 409 con `aliases`, 422 |

Todas las respuestas de `/auth` llevan `Cache-Control: no-store`.

### 4.3 Estudiantes (`/estudiantes`)

| Método | Ruta | Acceso | Respuesta | Errores |
|---|---|---|---|---|
| GET | `/estudiantes/` | público | `list[EstudianteOut]` por `id`, **sin paginación** | — |
| GET | `/estudiantes/{id}` | público | `EstudianteOut` | 404 |
| POST | `/estudiantes/` | **admin** | 201 `EstudianteOut` | 401, 403, 422 |
| PUT | `/estudiantes/{id}` | **admin** | `EstudianteOut` (reemplazo completo) | 401, 403, 404, 422 |
| DELETE | `/estudiantes/{id}` | **admin** | 204 | 401, 403, 404 |

La autorización se resuelve antes que el cuerpo: un anónimo recibe 401 aunque
el cuerpo sea inválido.

### 4.4 Estadísticas (`/estudiantes/estadisticas`, públicas)

`GET`, sin parámetros, `list[EstadisticaOut]` = `[{"etiqueta", "valor"}]`,
calculadas con `GROUP BY`.

| Ruta | Valor | Orden |
|---|---|---|
| `/por-carrera` | `COUNT(*)` | cantidad desc, carrera asc |
| `/por-edad` | `COUNT(*)` | edad asc |
| `/por-semestre` | `COUNT(*)` | semestre asc |
| `/por-ciudad` | `COUNT(*)`, **top 10** | cantidad desc, ciudad asc |
| `/promedio-por-carrera` | `ROUND(AVG(promedio), 2)` | media desc, carrera asc |
| `/riesgo-por-carrera` | `ROUND(100·AVG(promedio < 3), 2)` (%) | % desc, carrera asc |

### 4.5 Predicción (`/prediccion`, pública)

| Método | Ruta | Cuerpo | Respuesta | Errores |
|---|---|---|---|---|
| POST | `/prediccion/riesgo` | `PrediccionEntrada` | `PrediccionSalida` | 422 (validación o carrera desconocida con `detail = {"mensaje", "carreras_validas"}`), 503 |
| GET | `/prediccion/metricas` | — | `dict` sin esquema (`metricas.json` + `carreras_validas`) | 503 |

### 4.6 Modelo del juego y Laboratorio

Detalle en CONTRATO_LABORATORIO.md. Todas las respuestas llevan `modelo`
(nombre y versión) y `aviso` (estimación, correlación y no causalidad).

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| POST | `/prediccion/explicacion` | público | `ExplicacionOut` (base, logit, aportes con peso y efecto); 422 |
| POST | `/prediccion/riesgo/lote` | público | `LoteRiesgoOut` (1–50, mismo orden); 422 con `indices` si hay carreras desconocidas |
| GET | `/laboratorio/casos` | con sesión | `CasosOut` (riesgo ≥ 0.6 y rescatables con 5 fichas, del más grave al más leve) |
| GET | `/laboratorio/casos/{id}` | con sesión | `CasoOut` (sin promedio); 404 `caso_no_disponible` |
| POST | `/laboratorio/casos/{id}/resolver` | con sesión | `ResultadoRescateOut` (estrellas calculadas en el servidor, solución óptima); 404, 422 `presupuesto_superado` |

### 4.7 Juego (`/juego`, con sesión)

Detalle en CONTRATO_JUEGO.md.

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| GET | `/juego/casos` | con sesión | 9 casos con `resuelto`, `intentos`, `xp_ganada` |
| GET | `/juego/casos/{codigo}` | con sesión | Enunciado, opciones, gráfica; 404 `caso_no_encontrado`, 409 `sin_datos` |
| POST | `/juego/casos/{codigo}/responder` | con sesión | Corrección, `dato`, explicación o pista, XP, insignias nuevas, progreso; 422 `respuesta_invalida` |
| GET | `/juego/progreso` | con sesión | XP, nivel, siguiente nivel, racha, contadores, catálogo de insignias |
| GET | `/juego/ranking?periodo=semana\|siempre` | con sesión | Top 20 de jugadores activos por XP, con empates y la posición propia |
| GET | `/juego/jugadores` | docente, admin | Progreso de todos los jugadores |

Reglas: casos 20/40/60 XP por nivel × 1, 0,5 o 0,25 según el intento (solo la
primera vez); rescates 10 XP por estrella que mejore el récord del jugador con
ese estudiante; niveles en 0, 150, 400 y 800 XP; racha en hora de Colombia
(UTC−5 fijo, sin depender de la base de zonas horarias); 7 insignias. Las
respuestas y las XP las decide el servidor, y las escrituras de XP van
precedidas de un `FOR UPDATE` sobre el jugador.

### 4.8 Esquemas (Pydantic 2.14)

| Esquema | Campos | Notas |
|---|---|---|
| `EstudianteCreate` / `EstudianteOut` | nombre, apellido, edad, carrera, semestre, promedio, ciudad, horas_estudio, asistencia (+ `id`) | `str_strip_whitespace`; campos extra se ignoran |
| `EstadisticaOut` | `etiqueta`, `valor` | |
| `PrediccionEntrada` / `PrediccionSalida` | variables del modelo / `en_riesgo`, `probabilidad`, `modelo` | |
| `RegistroEntrada` | `alias` (`^[A-Za-z0-9_]{3,30}$`), `password` (10–128, ≠ alias) | Todos los de auth: `extra="forbid"` |
| `LoginEntrada` | `alias` (≤100), `password` (≤128) | Sin patrón: alias imposible = 401 |
| `RefreshEntrada` | `refresh_token` (≤512) | |
| `PasswordCambio` / `PasswordAdmin` | `password_actual`, `password_nueva` / `password_nueva` | |
| `UsuarioCambios` | `rol?`, `activo?` | |
| `TokensOut` | `access_token`, `token_type`, `expires_in`, `refresh_token`, `refresh_expires_in`, `usuario` | |
| `UsuarioOut` / `UsuarioAdminOut` | `id`, `alias`, `rol` (+ `activo`, `creado_en`) | |
| `SesionOut` | `id`, `iniciada_en`, `ultimo_uso`, `dispositivo`, `actual` | |

---

## 5. Seguridad

### 5.1 Piezas

| Pieza | Implementación |
|---|---|
| Contraseñas | Argon2id `t=2, m=19 MiB, p=1` (OWASP). Semáforo de 2 hashes simultáneos (`ARGON2_CONCURRENCIA`). ~50 ms en un portátil; en Render (0,1 CPU) del orden de 0,5 s. Rehash al hacer login si cambian los parámetros |
| Access | PASETO `v4.local`, 15 min. Claims `iss`, `aud`, `sub`, `sid`, `iat`, `exp`. Se descifra sin `deserializer` para que pyseto no valide con la hora real: la caducidad se comprueba con `reloj.ahora()` |
| Claves | `CLAVE_TOKENS` (32 bytes) → HKDF-SHA256 → una subclave para PASETO y otra para los sucesores de refresh |
| Refresh | 256 bits aleatorios; sucesor = `HMAC-SHA256(subclave, refresh)`; en BD solo SHA-256. Rotación con `SELECT … FOR UPDATE`. Ventana idempotente de 120 s (CONTRATO_AUTH.md §5) |
| Cada petición | Se carga el usuario de la BD: el rol se lee de ahí; cuenta desactivada → 403; `iat < sesiones_validas_desde` → 401 |
| Fuerza bruta | Por cuenta (BD): 5 fallos → 30 s, duplicando hasta 15 min; también cuenta en `PUT /auth/password`. Por IP (memoria): límites altos porque un aula comparte IP; en el login solo cuentan los fallos (contador propio con `limits`, comprobado antes de calcular ningún hash) y los techos totales, una dependencia `por_ip` sobre la misma librería (§4.2) |
| Enumeración | Mismo 401 para alias inexistente y contraseña mala; con alias inexistente se verifica un hash falso. (El registro revela si un alias existe con 409: es inherente, y por eso tiene el límite más estricto) |
| Asignación masiva | `extra="forbid"` en los cuerpos de auth |
| Respuestas | HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; `Cache-Control: no-store` en `/auth` y en el alta en lote; 422 sin `input`; 500 en JSON sin detalles internos y con CORS; 413 por encima de 64 KiB |
| Base de datos | `READ COMMITTED` + `FOR UPDATE` en lo que decide (login, refresh, XP); `hide_parameters`: los errores de SQL no llevan valores (hashes) a los logs |
| CORS | `CORS_ORIGINS` obligatoria; métodos y cabeceras explícitos; expone `Retry-After` |
| Logs | Logger `app.auth`: registros, bloqueos de cuenta, reuso de refresh, límites por IP superados (con `X-Forwarded-For`) y **acciones de admin** (cambios de rol o estado, restablecer contraseña, alta en lote). Logger `app.errores`: trazas de los 500 |

### 5.2 Ataques probados

| Ataque | Tests |
|---|---|
| Token con un byte cambiado, truncado, mal formado | `test_seguridad.py` |
| Token de otra clave, `v4.public` del atacante, `v3`, JWT `alg: none` | `test_seguridad.py` |
| Claims incorrectos o ausentes (`iss`, `aud`, `sub`, `iat` sin zona…) | `test_seguridad.py` |
| Token caducado (15 min exactos) | `test_seguridad.py` |
| Refresh usado como access y al revés | `test_seguridad.py`, `test_refresh.py` |
| Reuso de refresh fuera de la ventana; con el sucesor ya usado; robado y usado primero por el atacante | `test_refresh.py` |
| Dos refresh simultáneos (hilos con conexiones reales) | `test_refresh.py::test_dos_refresh_simultaneos_reciben_el_mismo_sucesor` |
| Fuerza bruta (bloqueo, duplicación, reinicio del contador) | `test_auth.py`, `test_refresh.py` (cambio de contraseña) |
| Enumeración de alias | `test_auth.py` |
| Escalada: jugador/docente escribe estudiantes, `"rol": "admin"` en el registro, rol cambiado en la BD, jugador crea cuentas | `test_auth.py`, `test_usuarios.py` |
| Sesiones tras logout, logout-todo, cambio o restablecimiento de contraseña, desactivación | `test_refresh.py`, `test_usuarios.py` |
| Secretos en la BD y en respuestas (hash, refresh, eco en 422) | `test_auth.py`, `test_refresh.py`, `test_usuarios.py` |
| Límite por IP (aula de 40 alumnos, fallos por IP sin calcular hash) y falsificación de `X-Forwarded-For` | `test_limites.py` |
| Token inválido en endpoints públicos (se ignora) | `test_auth.py` |
| Estrellas, XP o «correcto» enviados por el cliente | `test_laboratorio.py`, `test_juego.py` (422) |
| Dos respuestas simultáneas del mismo jugador (XP doble) | `test_juego.py::test_dos_respuestas_simultaneas_no_dan_la_xp_dos_veces` |

---

## 6. Modelo predictivo

- `app/ml.py`: `NUMERICAS = [horas_estudio, asistencia, semestre, edad]`,
  `CATEGORICAS = [carrera]`; objetivo `promedio < 3.0`; `SEMILLA = 42`.
- Pipeline = `ColumnTransformer` (`StandardScaler` solo en las logísticas,
  `OneHotEncoder(handle_unknown="ignore")`) + clasificador.
- Compiten `LogisticRegression(class_weight="balanced")` y
  `DecisionTreeClassifier(max_depth=4, min_samples_leaf=5, balanced)`; gana el
  mayor F2 medio en `StratifiedKFold(5)`. Falla con menos de 50 estudiantes.
- `CargadorModelo` carga el `.joblib` una vez y lo recarga si cambia su `mtime`.
- Último entrenamiento local (2026-10-10, 300 estudiantes, 18,3 % en riesgo):
  ganó la logística; en prueba accuracy 0,917, recall 0,727, F2 0,741.
- **Modelo del juego** (Fase 4): regresión logística congelada en
  `app/modelo_juego.json` (versión `2026-10-11`): intercepto, media,
  desviación y coeficiente de cada numérica, y coeficiente y frecuencia de
  cada carrera, más 8 muestras de control con la probabilidad de
  scikit-learn. Se congeló con los mismos datos (huella MD5 idéntica en local
  y Aiven), el mismo reparto (240/60, semilla 42) y el mismo pipeline que el
  modelo del build: hoy los dos coinciden (diferencia máxima 2·10⁻¹⁶). La
  inferencia es Python puro (sin pickle).
- Explicación exacta: `logit = base + Σ coef_j · (z_j − media_j)`, con
  `base` = logit del estudiante medio. Coincide con
  `shap.LinearExplainer` (diferencia 0 en los datos reales; los tests lo
  comprueban con datos sintéticos).
- Rescate: 12 combinaciones caben en 5 fichas; con los datos actuales hay
  70 estudiantes con riesgo ≥ 0.6, de los que 63 son rescatables (óptimos de
  1 a 5 fichas; 2 es lo más frecuente).
- Hallazgo para un caso «Experto»: el coeficiente de `edad` (−0,65) es un
  patrón espurio de esta muestra de 300 (el seed no hace depender el
  promedio de la edad).

---

## 7. Dependencias

Python **3.14.7** (`.python-version`). Fijadas a mano, sin `pip freeze`; una
instalación limpia de `requirements.txt` no deja ningún paquete sin versión
fijada (comprobado al cerrar la Fase 1).

**`requirements.txt`**

| Paquete | Versión | Para qué |
|---|---|---|
| fastapi | 0.143.0 | Framework (starlette 1.7.0, opentelemetry-api 1.45.1, annotated-doc 0.0.5) |
| uvicorn | 0.54.0 | Servidor (httptools 0.9.0, uvloop 0.23.0 salvo Windows, watchfiles 1.3.0, websockets 17.2, PyYAML 6.0.3) |
| pydantic / pydantic_core / pydantic-settings | 2.14.0 / 2.50.0 / 2.15.0 | Validación y configuración (python-dotenv 1.2.4) |
| SQLAlchemy / PyMySQL / alembic | 2.1.4 / 1.2.3 / 1.20.0 | BD y migraciones (Mako 1.4.3, MarkupSafe 3.0.4) |
| cryptography | 50.0.2 | TLS y `caching_sha2_password` de MySQL 8; HKDF |
| pyseto | 1.10.0 | PASETO (iso8601 2.1.0, pycryptodomex 3.24.0) |
| argon2-cffi | 25.1.0 | Argon2id (argon2-cffi-bindings 26.1.0) |
| limits | 5.8.0 | Límite por IP (Deprecated 3.0.0, wrapt 2.5.0, packaging 26.3). Sustituye a slowapi desde la Fase 6 |
| scikit-learn / pandas / joblib | 1.9.1 / 3.0.6 / 1.6.0 | Modelo (numpy 2.5.4, scipy 1.18.1, threadpoolctl 3.7.0, narwhals 2.27.1, cloudpickle 3.1.2, python-dateutil 2.9.0.post0, six 1.17.0) |
| otras | anyio 4.15.1, h11 0.16.0, idna 3.20, click 8.5.0, cffi 2.1.1, pycparser 3.11, annotated-types 0.8.0, typing_extensions 4.16.0, typing-inspection 0.4.4 | transitivas |

**`requirements-dev.txt`**: `-r requirements.txt` + Faker 40.43.0,
httpx2 2.13.1, matplotlib 3.11.2, pytest 9.1.1, shap 0.53.0 (slicer 0.0.8,
tqdm 4.70.1; sin numba). `shap` solo valida la explicación en los tests.

---

## 8. Configuración y tests

| Variable | Default | Uso |
|---|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | obligatorias | Conexión |
| `DB_HOST` / `DB_PORT` | `localhost` / `3306` | Conexión |
| `DB_SSL_CA` | `""` (sin SSL) | Ruta a `ca.pem` o PEM pegado |
| `CORS_ORIGINS` | **obligatoria** | `*` en local; `https://buelvasmanuel.github.io` en Render |
| `CLAVE_TOKENS` | **obligatoria** | 32 bytes base64url (`scripts.generar_clave`); se valida al arrancar |
| `IP_CLIENTE` | `conexion` | `conexion`, `x-forwarded-for:N` (N desde la derecha) o nombre de cabecera; `x-forwarded-for` a secas se rechaza |
| `ARGON2_TIEMPO`, `ARGON2_MEMORIA_KIB`, `ARGON2_PARALELISMO`, `ARGON2_CONCURRENCIA` | 2, 19456, 1, 2 | Coste de Argon2id |

Un error de configuración (variable que falta o con formato inválido) detiene
el arranque y el build con un mensaje que nombra la variable **sin mostrar
ningún valor** (`hide_input_in_errors`): antes, pydantic imprimía el valor
inválido o, si faltaba una variable, el resto de variables, y eso acababa en
los logs de Render.

**Tests** (423; `pytest -q` en ~15 s; también pasan contra MySQL 8.4): auth 67,
juego 62, estudiantes 42, límites 40, laboratorio 35, seguridad 33, refresh 33,
usuarios 29, predicción 22, estadísticas 20, predicción del juego 15, modelo de
usuarios 15, modelo del juego 9, migraciones 1.

- `conftest.py` fija antes de importar la app una `CLAVE_TOKENS` de pruebas,
  Argon2 barato (`t=1, m=64 KiB`), `CORS_ORIGINS` e `IP_CLIENTE`.
- Base `tienda_test` (con `assert`), `upgrade head` al empezar y `downgrade
  base` al terminar; cada test en una transacción con savepoints que se deshace.
- Fixtures: `client` (anónimo), `admin_client`, `crear_usuario(...)`, `reloj`
  (adelanta `reloj.ahora()`), `limite_por_ip` (el límite por IP está apagado
  salvo en los tests que lo piden).
- El test de concurrencia usa conexiones reales con commit y limpia al final.
- CI: `.github/workflows/tests.yml` contra `mariadb:lts` y `mysql:8.4`.

---

## 9. Despliegue

**Render** (Blueprint `render.yaml`): servicio web `api-tienda`, plan free,
región `oregon`.

```
build:  pip install -r requirements.txt && alembic upgrade head && python -m scripts.entrenar_modelo --sin-graficas
start:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
health: GET /
```

- Variables `DB_*`, `DB_SSL_CA` y `CLAVE_TOKENS` con `sync: false` (se
  escriben en el panel); `CORS_ORIGINS` e `IP_CLIENTE` fijas en el YAML.
- **`CLAVE_TOKENS` debe existir antes del deploy**: `alembic` importa la
  configuración, así que sin ella falla ya el build (y Render mantiene el
  deploy anterior).
- **El build aplica las migraciones en Aiven**: el primer deploy de la Fase 1
  borra `productos` y `categorias` (`f9047f24dd9b`) y crea las tablas de
  usuarios.
- No hay auto-deploy (repo conectado como público): cada push requiere
  *Manual Deploy*, que además **reentrena** el modelo.
- El primer admin se crea desde la máquina local contra Aiven:
  `set -a; source .env.aiven; set +a; python -m scripts.crear_usuario <alias> --rol admin`.

**IP del cliente detrás del proxy (verificado el 2026-10-11).** uvicorn,
con `FORWARDED_ALLOW_IPS=*`, toma la IP **más a la izquierda** de
`X-Forwarded-For`, que el cliente puede escribir: con `IP_CLIENTE=conexion`
el límite por IP se esquivaba cambiando esa cabecera (comprobado en
producción: con otra IP falsa, el contador empezaba de cero). La cabecera
real, vista en los logs de Render, es:

```
X-Forwarded-For: <lo que escriba el cliente>, <IP real>, <nodo de Cloudflare>, <10.x interna de Render>
```

El nodo de Cloudflare cambia entre peticiones (`162.158.x`, `104.22.x`…),
así que la IP real es la **3.ª por la derecha**: `IP_CLIENTE=x-forwarded-for:3`.
Si Render cambiara su cadena de proxies, la línea
`demasiados logins fallidos ip=… x-forwarded-for='…'` de los logs permite
volver a comprobarlo: lanzar 31 logins fallidos con un alias inexistente y
`X-Forwarded-For: 1.2.3.4`, y repetir con otra IP falsa (debe dar 429).

**Aiven**: MySQL 8.4 free, DigitalOcean `sfo`. Datos cargados desde la máquina
local con `.env.aiven`.

**Web**: Flutter en GitHub Pages (`https://buelvasmanuel.github.io/app-estudiantes/`).

---

## 10. Diferencias con la sección 1 de la propuesta (verificación inicial)

Registro histórico de la verificación del 2026-10-11, antes de la Fase 1.

| Propuesta dice | Realidad entonces | Importancia |
|---|---|---|
| CRUD de `/productos`, `/categorias`, `/estudiantes` | Correcto, además de `GET /categorias/{id}/productos` y `GET /` | Baja (hoy, Producto y Categoría eliminados) |
| «~133 tests» | Exactamente 133, todos en verde | — |
| Aiven «1 CPU, 1 GB RAM, 1 GB disco» | No verificable desde el repo | Revisar en el panel de Aiven |
| Render free: «se duerme, sin disco» | Correcto; faltaba **0,1 CPU y 512 MB**, un solo proceso | Alta (Argon2, SHAP) |
| Diagrama Flutter → API directo | Hay un proxy Cloudflare de Render en medio | Media (IP para el límite) |
| «Lo que NO tiene: usuarios, auth…» | Y por tanto **cualquiera podía hacer `DELETE /estudiantes/{id}`** | Alta (resuelto en la Fase 1) |
| WebSockets «a añadir» | `websockets` 17.2 ya venía con uvicorn | Baja |
| Sin versión de Python | 3.14.7 | Media: cada librería nueva necesita wheels para 3.14 |
| `promedio` «como texto» en Flutter | Pydantic serializa `Decimal` como texto | Baja |
| Tests con «httpx2» | `httpx2` solo en `scripts/graficas.py`; los tests usan `fastapi.testclient` | — |

---

## 11. Deuda técnica

Ordenada por impacto. Resueltas: escrituras públicas, CORS con `*` por defecto,
dependencias sin fijar, falta de CI, convención de fechas, cabeceras de
seguridad, logs de seguridad, `extra="forbid"` en auth (Fase 1); IP del cliente
detrás del proxy (verificada); umbral de riesgo duplicado, `response_model` de
`/prediccion/metricas`, formato de error mixto y slowapi (Fase 6).

1. **El bloqueo por cuenta se puede usar para bloquear a otro** (hallazgo A1
   de la auditoría, §12): pendiente de decisión.
2. **El modelo de la pestaña Predicción cambia en cada deploy** (reentrenar
   en el build puede cambiar métricas, coeficientes e incluso el tipo de
   modelo sin un commit). El juego no depende de eso (modelo congelado), pero
   si cambian los datos o gana el árbol, Predicción y Laboratorio darán
   probabilidades distintas para la misma entrada. `congelar_modelo_juego`
   avisa si el modelo del build deja de ser la logística.
3. **Tras un logout o un reuso detectado, el access sigue valiendo hasta
   15 min** (decisión de diseño: solo `logout-todo`, cambio de contraseña y
   desactivación cortan los access al instante).
4. **Contadores por IP en memoria**: se reinician si Render duerme el servicio
   y no se comparten entre procesos (hoy hay uno).
5. **Cadena de proxies de Render fijada a mano** (`x-forwarded-for:3`, §9).
6. **Sin paginación** en `GET /estudiantes/` (aplazada; con 300 filas no molesta).
7. **El healthcheck (`GET /`) no toca la base**: si Aiven cae, Render sigue
   viendo el servicio sano (A5).

---

## 12. Auditoría final del backend (Fase 6, 2026-10-11)

Revisión de todo el código, la configuración y el despliegue, con el foco en
la seguridad. Dependencias: `pip-audit` sin vulnerabilidades conocidas
(producción y desarrollo). Historial de git sin secretos (solo valores de
prueba). 423 tests en verde en MariaDB y MySQL 8.4.

**Corregido en la Fase 6** (sin cambiar el comportamiento que usa el cliente):

| # | Hallazgo | Arreglo |
|---|---|---|
| C1 | Dos respuestas o rescates simultáneos del mismo jugador daban la XP dos veces o un 500 (REPEATABLE READ) | `READ COMMITTED` en el engine; test de concurrencia real |
| C2 | Los errores de SQL incluían los parámetros (p. ej. un hash de contraseña) y acababan en los logs | `hide_parameters=True`; test |
| C3 | Un 500 salía sin cabeceras CORS: Flutter Web veía un error de CORS en lugar del 500 | Middleware dentro de CORS: 500 en JSON con código; test |
| C4 | Sin límite de tamaño de cuerpo: una petición enorme podía agotar los 512 MB | 413 por encima de 64 KiB (con o sin Content-Length); tests |
| C5 | Las acciones de admin no dejaban rastro | Log de cambios de rol/estado y de restablecer contraseña; test |
| C6 | `/juego/jugadores` hacía ~6 consultas por jugador (segundos con 40 alumnos) | Consultas agrupadas; test que lo compara con `/juego/progreso` |
| C7 | Opciones en orden de texto («Semestre 10» antes que «Semestre 2») | Orden natural (contrato del juego actualizado) |
| C8 | slowapi con una API de asyncio que desaparece en Python 3.16 | Dependencia propia sobre `limits` |
| C9 | Formato de error mixto | `{"codigo", "mensaje"}` en toda la API, compatible con el cliente |

**Pendientes de decisión** (cambios de comportamiento o de diseño: no se tocan
sin confirmación):

| # | Hallazgo | Riesgo | Propuesta |
|---|---|---|---|
| A1 | El bloqueo por cuenta (5 fallos → hasta 15 min) permite a cualquiera **bloquear la cuenta de otro**, y el ranking publica los alias de los jugadores. Una vez en 15 min, un intento fallido basta para mantenerla bloqueada | Medio: un alumno puede dejar sin jugar a sus compañeros en clase | Bajar el tope del bloqueo a 1 min y apoyarse en el límite de fallos por IP, o contar los fallos por (alias, IP) para que un tercero no bloquee al dueño. Siempre se puede desbloquear con `PUT /usuarios/{id}/password` |
| A2 | `/auth/registro` está abierto a cualquiera en internet (60/h por IP) | Bajo: cuentas basura en Aiven y en el ranking | Variable `REGISTRO_ABIERTO` (por defecto sí); en clase, el docente usa el alta en lote |
| A3 | `/docs` y `/openapi.json` son públicos en producción | Bajo: describen la API, no dan acceso | Mantener (el profesor usa Swagger) o ocultarlos con una variable |
| A4 | Las Actions de GitHub van fijadas por etiqueta (`@v7`), no por hash | Bajo (cadena de suministro) | Fijar por SHA |
| A5 | El healthcheck no comprueba la base | Bajo | `GET /salud` con `SELECT 1` como `healthCheckPath` |
| A6 | Los backups de `~/backups` contienen los hashes Argon2 de los usuarios | Bajo (Argon2id) | Mantenerlos con permisos 600 o cifrarlos |

**Pendientes de fin de semana** (los hace el usuario):

- [ ] Desactivar `Prueba_Frontend_1011` (id 2) cuando el frontend termine sus
      pruebas: `PATCH /usuarios/2 {"activo": false}` con el admin.
- [ ] Revocar la API key de Render guardada en `~/.config/render/api_key`
      (panel de Render → Account Settings → API Keys) y borrar el archivo.

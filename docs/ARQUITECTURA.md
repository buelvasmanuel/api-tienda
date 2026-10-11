# Arquitectura de `api-tienda`

> Estado verificado contra el código. Primera verificación: **2026-10-11**
> (commit `52ddbf5`, §10). Última actualización: fin de la **Fase 1
> (seguridad)** del plan de `PROPUESTA_ESTUDIANTES_2.0.md`. Si el código
> cambia, este documento se actualiza en el mismo commit.
>
> El contrato de autenticación con la app Flutter está aparte, en
> [CONTRATO_AUTH.md](CONTRATO_AUTH.md).

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
│   ├── models.py          # Estudiante, Usuario, SesionRefresh
│   ├── schemas.py         # Esquemas Pydantic (estudiantes, predicción, auth)
│   ├── ml.py              # Variables, pipeline y utilidades del modelo (compartido script/API)
│   ├── reloj.py           # reloj.ahora(): UTC sin zona, sustituible en tests
│   ├── seguridad.py       # Argon2id, PASETO v4.local, refresh opacos (sin BD)
│   ├── sesiones.py        # Familias de refresh en la BD: abrir, rotar, revocar, listar
│   ├── dependencias.py    # sesion_actual, usuario_actual, requiere_rol(...)
│   ├── errores.py         # {"detail": {"codigo", "mensaje"}}, 401 con WWW-Authenticate, 429
│   ├── limites.py         # slowapi: límite por IP e ip_cliente()
│   └── routers/
│       ├── auth.py        # /auth/*
│       ├── usuarios.py    # /usuarios
│       ├── estudiantes.py # escrituras solo admin
│       ├── estadisticas.py
│       └── prediccion.py  # CargadorModelo (recarga por mtime, con lock)
├── alembic/versions/      # 6 migraciones (§3.2)
├── scripts/
│   ├── seed_estudiantes.py  # Faker("es_CO"), semilla 42, --cantidad, --reset
│   ├── graficas.py          # PNG por estadística (llama a la API con httpx2)
│   ├── entrenar_modelo.py   # CV estratificada 5 particiones, elige por F2, --sin-graficas
│   ├── generar_clave.py     # valor para CLAVE_TOKENS
│   └── crear_usuario.py     # alias + --rol; única forma de crear el primer admin
├── tests/                 # 263 tests (§8)
├── docs/                  # ARQUITECTURA.md (este archivo), CONTRATO_AUTH.md
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
| `5527590d242a` (head) | Crea `usuarios` y `sesiones_refresh` |

### 3.3 Collation y conexión

- `database.COLLATION = "utf8mb4_unicode_ci"`, fijada con un listener
  `connect` porque SQLAlchemy reemplaza la que se pasa a PyMySQL. La comparten
  app, Alembic y tests vía `crear_engine()`.
- SSL: si `DB_SSL_CA` tiene valor (ruta o PEM pegado), se verifica certificado
  y nombre de host.
- `pool_pre_ping=True`; `SessionLocal(autoflush=False)`; `get_db()` abre y
  cierra una sesión por petición.

---

## 4. Endpoints

Todos los endpoints son **síncronos** (`def`): FastAPI los ejecuta en su pool
de hilos. Las rutas de colección de estudiantes terminan en `/`; las de
`/auth` y `/usuarios`, no.

Formato de errores:
- Errores de `/auth`, `/usuarios` y los 401/403 de autorización:
  `{"detail": {"codigo", "mensaje"}}` (códigos en CONTRATO_AUTH.md §2).
- 404 de estudiantes y 422 de predicción: formato anterior (`detail` texto u objeto).
- **422 de validación**: el estándar de FastAPI **sin el campo `input`** (no
  devuelve lo enviado, contraseñas incluidas). Handler en `main.py`.

`Decimal` se serializa como **texto** (`"3.45"`): por eso Flutter usa `leerNum`.

### 4.1 Raíz

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/` | `{"mensaje": "API funcionando"}` — healthcheck de Render (no toca la BD) |

### 4.2 Autenticación y usuarios

Detalle completo (cuerpos, códigos, qué hace el cliente) en CONTRATO_AUTH.md §4.

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| POST | `/auth/registro` | público, 5/h por IP | 201 `TokensOut`; 409, 422, 429 |
| POST | `/auth/login` | público, 10/min por IP + bloqueo por cuenta | `TokensOut`; 401, 403, 429 |
| POST | `/auth/refresh` | público, 30/min por IP | `TokensOut`; 401 `refresh_invalido`/`sesion_revocada`, 403, 429 |
| POST | `/auth/logout` | público | 204 siempre |
| POST | `/auth/logout-todo` | con token | 204 |
| GET | `/auth/yo` | con token | `UsuarioOut` |
| GET | `/auth/sesiones` | con token | `list[SesionOut]` |
| PUT | `/auth/password` | con token | `TokensOut`; 403 `password_actual_incorrecta`, 422, 429 |
| GET | `/usuarios` | docente, admin | `list[UsuarioAdminOut]` |
| PATCH | `/usuarios/{id}` | admin (no sobre sí mismo) | `UsuarioAdminOut`; 404, 409 |
| PUT | `/usuarios/{id}/password` | admin (no sobre sí mismo) | 204; 404, 409, 422 |

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

### 4.6 Esquemas (Pydantic 2.14)

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
| Refresh | 256 bits aleatorios; sucesor = `HMAC-SHA256(subclave, refresh)`; en BD solo SHA-256. Rotación con `SELECT … FOR UPDATE`. Ventana idempotente de 30 s (CONTRATO_AUTH.md §5) |
| Cada petición | Se carga el usuario de la BD: el rol se lee de ahí; cuenta desactivada → 403; `iat < sesiones_validas_desde` → 401 |
| Fuerza bruta | Por cuenta (BD): 5 fallos → 30 s, duplicando hasta 15 min; también cuenta en `PUT /auth/password`. Por IP (memoria, slowapi): ver §4.2 |
| Enumeración | Mismo 401 para alias inexistente y contraseña mala; con alias inexistente se verifica un hash falso. (El registro revela si un alias existe con 409: es inherente, y por eso tiene el límite más estricto) |
| Asignación masiva | `extra="forbid"` en los cuerpos de auth |
| Respuestas | HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; `Cache-Control: no-store` en `/auth`; 422 sin `input` |
| CORS | `CORS_ORIGINS` obligatoria; métodos y cabeceras explícitos; expone `Retry-After` |
| Logs | Logger `app.auth`: registros, bloqueos de cuenta, reuso de refresh, límites por IP superados (con `X-Forwarded-For`) |

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
| Escalada: jugador/docente escribe estudiantes, `"rol": "admin"` en el registro, rol cambiado en la BD | `test_auth.py`, `test_usuarios.py` |
| Sesiones tras logout, logout-todo, cambio o restablecimiento de contraseña, desactivación | `test_refresh.py`, `test_usuarios.py` |
| Secretos en la BD y en respuestas (hash, refresh, eco en 422) | `test_auth.py`, `test_refresh.py`, `test_usuarios.py` |
| Límite por IP y falsificación de `X-Forwarded-For` | `test_limites.py` |

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
- Decisión 2026-10-11: el modelo **del juego** se congela como regresión
  logística (pendiente: Fase 4).

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
| slowapi | 0.1.10 | Límite por IP (limits 5.8.0, Deprecated 3.0.0, wrapt 2.5.0, packaging 26.3) |
| scikit-learn / pandas / joblib | 1.9.1 / 3.0.6 / 1.6.0 | Modelo (numpy 2.5.4, scipy 1.18.1, threadpoolctl 3.7.0, narwhals 2.27.1, cloudpickle 3.1.2, python-dateutil 2.9.0.post0, six 1.17.0) |
| otras | anyio 4.15.1, h11 0.16.0, idna 3.20, click 8.5.0, cffi 2.1.1, pycparser 3.11, annotated-types 0.8.0, typing_extensions 4.16.0, typing-inspection 0.4.4 | transitivas |

**`requirements-dev.txt`**: `-r requirements.txt` + Faker 40.43.0,
httpx2 2.13.1, matplotlib 3.11.2, pytest 9.1.1.

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

**Tests** (263; `pytest -q` en ~6 s): auth 51, estudiantes 42, seguridad 33,
refresh 33, límites 28, prediccion 22, estadísticas 20, usuarios 19, modelo de
usuarios 14, migraciones 1.

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

**IP del cliente detrás del proxy (pendiente de verificar).** uvicorn, con
`FORWARDED_ALLOW_IPS=*`, toma la IP **más a la izquierda** de
`X-Forwarded-For`, que el cliente puede escribir. Por eso el límite por IP
lee la IP según `IP_CLIENTE`. Hasta verificar la cadena real de proxies queda
en `conexion` (funciona, pero se puede esquivar falsificando la cabecera; el
bloqueo por cuenta no depende de esto). Para verificarlo:

1. Desde tu máquina, 11 logins seguidos **con un alias que no exista** (así
   no se bloquea ninguna cuenta) y `-H "X-Forwarded-For: 1.2.3.4"`:
   `for i in $(seq 11); do curl -s -o /dev/null -w "%{http_code} " -X POST
   https://api-tienda-wjac.onrender.com/auth/login -H "Content-Type: application/json"
   -H "X-Forwarded-For: 1.2.3.4" -d '{"alias":"no_existe_xff","password":"x"}'; done`
   (el último debe dar `429`).
2. En los logs de Render, la línea `límite por IP superado ... x-forwarded-for='…'`
   muestra la cabecera completa. Tu IP pública real tendrá una posición fija
   contando desde la derecha.
3. Poner `IP_CLIENTE=x-forwarded-for:N` con esa posición (o `cf-connecting-ip`
   si Render pasa esa cabecera de Cloudflare y coincide con tu IP).

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

Ordenada por impacto. Resueltas en la Fase 1: escrituras públicas, `CORS_ORIGINS`
con `*` por defecto, dependencias transitivas sin fijar, falta de CI, falta de
convención de fechas, falta de cabeceras de seguridad y de logs de seguridad,
esquemas sin `extra="forbid"` en auth.

1. **IP del cliente sin verificar en producción** (§9): hasta entonces, el
   límite por IP se puede esquivar.
2. **El modelo cambia en cada deploy**: reentrenar en el build puede cambiar
   métricas, coeficientes e incluso el tipo de modelo sin un commit. Se aborda
   en la Fase 4 (modelo del juego congelado como logística).
3. **Tras un logout o un reuso detectado, el access sigue valiendo hasta
   15 min** (decisión de diseño: solo `logout-todo`, cambio de contraseña y
   desactivación cortan los access al instante).
4. **slowapi 0.1.10** usa `asyncio.iscoroutinefunction`, obsoleto en Python
   3.14 y eliminado en 3.16 (aviso silenciado en `pytest.ini`). Revisar antes
   de subir de Python, o sustituirlo por un contador propio.
5. **Contadores por IP en memoria**: se reinician si Render duerme el servicio
   y no se comparten entre procesos (hoy hay uno).
6. **Umbral duplicado**: `estadisticas.RIESGO = 3` y `ml.UMBRAL_RIESGO = 3.0`.
7. **`GET /prediccion/metricas` sin `response_model`**.
8. **Formato de error mixto**: los endpoints anteriores usan `detail` texto;
   los nuevos, `{"codigo", "mensaje"}`.
9. **Sin paginación** en `GET /estudiantes/` (aplazada; con 300 filas no molesta).
10. `INSTRUCCIONES_PROFESOR.md` dice estar repetido en los dos repos: la copia
    de `app_estudiantes` debe actualizarse con los cambios de este (clave de
    tokens, `CORS_ORIGINS`, admin).

# Arquitectura de `api-tienda` (estado verificado)

> Verificado contra el código el **2026-10-11** (commit `52ddbf5`, rama `main`).
> Actualizado: Producto y Categoría eliminados (migración `f9047f24dd9b`).
> Es la versión exacta de la sección 1 de `PROPUESTA_ESTUDIANTES_2.0.md` para
> el backend. Si el código cambia, este documento se actualiza en el mismo commit.

---

## 1. Visión general

```
┌──────────────────────────┐  HTTPS/JSON  ┌──────────────────┐  HTTPS  ┌───────────────────────────┐  TLS (CA verificada)  ┌───────────────────┐
│ app_estudiantes (Flutter)│ ───────────► │ Cloudflare       │ ──────► │ api-tienda (FastAPI)      │ ────────────────────► │ MySQL 8.4 (Aiven) │
│ Web (GitHub Pages) + APK │ ◄─────────── │ (proxy de Render)│ ◄────── │ Render free · oregon      │                       │ DigitalOcean sfo  │
└──────────────────────────┘              └──────────────────┘         │ 1 proceso uvicorn         │                       └───────────────────┘
                                                                       │ modelo .joblib (del build)│
                                                                       └───────────────────────────┘
```

- URL pública: `https://api-tienda-wjac.onrender.com` (Swagger en `/docs`).
- Delante de uvicorn hay un proxy (cabeceras `server: cloudflare`,
  `x-render-origin-server: uvicorn`). uvicorn ya confía en sus cabeceras
  `X-Forwarded-*`: la redirección `307` de `/estudiantes` → `/estudiantes/`
  sale con `https://` (comprobado el 2026-10-11).
- Local: MariaDB sin SSL; los tests usan la base `tienda_test`.

---

## 2. Estructura de carpetas

```
api-tienda/
├── app/
│   ├── main.py            # FastAPI(title="API Tienda"), CORS, registro de routers, GET /
│   ├── config.py          # Settings (pydantic-settings): DB_*, DB_SSL_CA, CORS_ORIGINS
│   ├── database.py        # crear_engine(), collation por evento, NAMING_CONVENTION, Base, get_db()
│   ├── models.py          # Estudiante
│   ├── schemas.py         # Esquemas Pydantic y tipos Annotated compartidos
│   ├── ml.py              # Variables, pipeline y utilidades del modelo (compartido script/API)
│   └── routers/
│       ├── estudiantes.py
│       ├── estadisticas.py
│       └── prediccion.py  # CargadorModelo (recarga por mtime, con lock)
├── alembic/
│   ├── env.py             # Usa crear_engine(); acepta una conexión externa (tests)
│   └── versions/          # 5 migraciones (ver §3.2)
├── scripts/
│   ├── seed_estudiantes.py  # Faker("es_CO"), semilla 42, --cantidad, --reset
│   ├── graficas.py          # PNG por estadística (llama a la API con httpx2)
│   └── entrenar_modelo.py   # CV estratificada 5 particiones, elige por F2, --sin-graficas
├── tests/                 # pytest, base tienda_test
├── docs/                  # ARQUITECTURA.md (este archivo), CONTRATO_AUTH.md
├── .github/workflows/     # tests.yml: CI con MariaDB y MySQL 8.4
├── alembic.ini, pytest.ini, render.yaml, .python-version (3.14.7)
├── requirements.txt, requirements-dev.txt, .env.example
├── README.md, INSTRUCCIONES_PROFESOR.md
└── (ignorados por git) .env, .env.aiven, modelos/, graficas/, venv/
```

---

## 3. Datos

### 3.1 Modelos (SQLAlchemy 2.1, `Mapped` + `mapped_column`)

`Base` declara `MetaData(naming_convention=...)` con nombres `ix_`, `uq_`,
`ck_`, `fk_`, `pk_` para que Alembic pueda borrar restricciones en los downgrade.

**`estudiantes`** — `Estudiante`

| Columna | Tipo | CHECK |
|---|---|---|
| `id` | INT | PK |
| `nombre`, `apellido` | VARCHAR(50) | — |
| `edad` | INT | `ck_estudiantes_edad`: 16–35 |
| `carrera` | VARCHAR(100) | — (texto libre; no es FK ni enum) |
| `semestre` | INT | `ck_estudiantes_semestre`: 1–10 |
| `promedio` | NUMERIC(3,2) | `ck_estudiantes_promedio`: 0–5 |
| `ciudad` | VARCHAR(100) | — |
| `horas_estudio` | INT | `ck_estudiantes_horas_estudio`: 0–40 (horas/semana) |
| `asistencia` | INT | `ck_estudiantes_asistencia`: 0–100 (%) |

Todas las columnas son NOT NULL. No hay índices secundarios ni columnas de
auditoría (`creado_en`, etc.).

### 3.2 Migraciones (Alembic 1.20)

Cadena lineal (las tres primeras crearon las tablas de la tienda, hoy eliminadas); los archivos llevan fecha en el nombre (`file_template` en `alembic.ini`).

| Revisión | Archivo | Qué hace |
|---|---|---|
| `3adaaf027eed` | `2026_10_10_1620-…_crear_tabla_productos` | Crea `productos` |
| `cc73e9874b06` | `2026_10_10_1626-…_crear_tabla_categorias` | Crea `categorias` y la FK de `productos` |
| `81a56be44550` | `2026_10_10_1643-…_crear_tabla_estudiantes` | Crea `estudiantes` con 5 CHECK |
| `1d65ce94be3f` | `2026_10_10_2132-…_collation_utf8mb4_unicode_ci` | `ALTER DATABASE` + `CONVERT TO` en las 3 tablas; downgrade no-op a propósito |
| `f9047f24dd9b` (head) | `2026_10_11_0147-…_eliminar_productos_y_categorias` | Borra `productos` y `categorias` (legado de la primera actividad); el downgrade las recrea vacías |

### 3.3 Collation y conexión

- `database.COLLATION = "utf8mb4_unicode_ci"`, fijada con un listener `connect`
  (`SET NAMES utf8mb4 COLLATE …`) porque SQLAlchemy reemplaza la collation que
  se pasa a PyMySQL. La comparten app, Alembic y tests vía `crear_engine()`.
- SSL: si `DB_SSL_CA` tiene valor (ruta o PEM pegado), se crea un
  `ssl.create_default_context` que verifica certificado y nombre de host.
- Engine de la app con `pool_pre_ping=True`; `SessionLocal(autoflush=False)`;
  `get_db()` abre y cierra una sesión por petición.

---

## 4. Endpoints

Todos los endpoints son **síncronos** (`def`): FastAPI los ejecuta en su pool
de hilos. **Ninguno requiere autenticación**, incluidas las escrituras.

Las rutas de colección terminan en `/` (`/estudiantes/`); sin la barra
responden `307` hacia la versión con barra.

`Decimal` se serializa en JSON como **texto** (`"3.45"`, `"1200.00"`): afecta
a `promedio`. Por eso Flutter usa `leerNum`.

### 4.1 Raíz

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/` | `{"mensaje": "API funcionando"}` — también es el `healthCheckPath` de Render (no toca la BD) |

### 4.2 Estudiantes (`/estudiantes`, tag «Estudiantes»)

| Método | Ruta | Cuerpo | Respuesta | Errores |
|---|---|---|---|---|
| GET | `/estudiantes/` | — | `list[EstudianteOut]` ordenada por `id`, **sin paginación** | — |
| GET | `/estudiantes/{estudiante_id}` | — | `EstudianteOut` | 404 |
| POST | `/estudiantes/` | `EstudianteCreate` | 201 `EstudianteOut` | 422 |
| PUT | `/estudiantes/{estudiante_id}` | `EstudianteCreate` (reemplazo completo, no hay PATCH) | `EstudianteOut` | 404, 422 |
| DELETE | `/estudiantes/{estudiante_id}` | — | 204 | 404 |

### 4.3 Estadísticas (`/estudiantes/estadisticas`, tag «Estadísticas»)

Todas: `GET`, sin parámetros, respuesta `list[EstadisticaOut]` =
`[{"etiqueta": str, "valor": int | float}]`. El cálculo lo hace SQL (`GROUP BY`).

| Ruta | Valor | Orden |
|---|---|---|
| `/por-carrera` | `COUNT(*)` | cantidad desc, carrera asc |
| `/por-edad` | `COUNT(*)` | edad asc |
| `/por-semestre` | `COUNT(*)` | semestre asc |
| `/por-ciudad` | `COUNT(*)`, **top 10** | cantidad desc, ciudad asc |
| `/promedio-por-carrera` | `ROUND(AVG(promedio), 2)` | media desc, carrera asc |
| `/riesgo-por-carrera` | `ROUND(100·AVG(promedio < 3 ? 1 : 0), 2)` (%) | % desc, carrera asc |

### 4.4 Predicción (`/prediccion`, tag «Predicción»)

| Método | Ruta | Cuerpo | Respuesta | Errores |
|---|---|---|---|---|
| POST | `/prediccion/riesgo` | `PrediccionEntrada` | `PrediccionSalida` | 422 (validación, o carrera desconocida con `detail = {"mensaje", "carreras_validas"}`), 503 (sin modelo) |
| GET | `/prediccion/metricas` | — | `dict` sin esquema: contenido de `modelos/metricas.json` + `carreras_validas` | 503 |

`en_riesgo` se decide con la probabilidad sin redondear (`>= 0.5`); `probabilidad`
se devuelve redondeada a 2 decimales.

### 4.5 Esquemas (Pydantic 2.14)

Tipos compartidos (`Annotated`): `Edad` 16–35, `Carrera` 1–100 caracteres,
`Semestre` 1–10, `HorasEstudio` 0–40, `Asistencia` 0–100.

| Esquema | Campos | Notas |
|---|---|---|
| `EstudianteCreate` | `nombre`, `apellido` (1–50), `edad`, `carrera`, `semestre`, `promedio: Decimal` (0–5, 3,2), `ciudad` (1–100), `horas_estudio`, `asistencia` | `str_strip_whitespace` |
| `EstudianteOut` | + `id` | |
| `EstadisticaOut` | `etiqueta: str`, `valor: int \| float` | |
| `PrediccionEntrada` | `horas_estudio`, `asistencia`, `semestre`, `edad`, `carrera` | sin `promedio` (es lo que se predice); ejemplo en el esquema |
| `PrediccionSalida` | `en_riesgo: bool`, `probabilidad: float` (0–1), `modelo: str` | |

Ningún esquema usa `extra="forbid"`: los campos desconocidos se ignoran.

---

## 5. Modelo predictivo

- `app/ml.py`: variables `NUMERICAS = [horas_estudio, asistencia, semestre, edad]`,
  `CATEGORICAS = [carrera]`; objetivo `promedio < 3.0` (`UMBRAL_RIESGO`);
  `SEMILLA = 42`.
- Pipeline = `ColumnTransformer` (`StandardScaler` solo para las logísticas,
  `OneHotEncoder(handle_unknown="ignore")`) + clasificador.
- Candidatos que compiten: `LogisticRegression(class_weight="balanced")` y
  `DecisionTreeClassifier(max_depth=4, min_samples_leaf=5, balanced)`. La
  logística sin balancear solo se muestra como comparación.
- Selección: mayor F2 medio en `StratifiedKFold(5, shuffle, 42)`; evaluación
  final en un 20 % de prueba. Falla si hay menos de 50 estudiantes.
- Salidas: `modelos/riesgo.joblib` y `modelos/metricas.json` (ignorados por git).
- En la API, `CargadorModelo` carga el `.joblib` una vez y lo recarga si cambia
  su `mtime`; protegido con `threading.Lock`. Se inyecta con
  `Depends(obtener_cargador)` para poder sustituirlo en los tests.
- Último entrenamiento local (2026-10-10, 300 estudiantes, 18,3 % en riesgo):
  ganó la Regresión Logística; en prueba accuracy 0,917, recall 0,727, F2 0,741.

---

## 6. Dependencias

Python **3.14.7** (`.python-version`, la usa Render). Versiones fijadas a mano
(no hay `pip freeze`).

**`requirements.txt` (producción)**

| Paquete | Versión | Para qué |
|---|---|---|
| fastapi | 0.143.0 | Framework (arrastra starlette 1.7.0, opentelemetry-api 1.45.1, annotated-doc) |
| uvicorn | 0.54.0 | Servidor ASGI, con httptools 0.9.0, uvloop 0.23.0 (no en Windows), watchfiles 1.3.0, websockets 17.2 |
| pydantic / pydantic_core | 2.14.0 / 2.50.0 | Validación |
| pydantic-settings | 2.15.0 | `Settings` desde `.env` (con python-dotenv 1.2.4) |
| SQLAlchemy | 2.1.4 | ORM |
| PyMySQL | 1.2.3 | Driver MySQL/MariaDB |
| cryptography | 50.0.2 | Autenticación `caching_sha2_password` y TLS de MySQL 8 |
| alembic | 1.20.0 | Migraciones (con Mako 1.4.3, MarkupSafe 3.0.4) |
| scikit-learn | 1.9.1 | Modelo |
| pandas | 3.0.6 | DataFrames para el modelo |
| joblib | 1.6.0 | Serializar el modelo |
| PyYAML | 6.0.3 | — (dependencia de uvicorn[standard]) |
| otras fijadas | anyio 4.15.1, h11 0.16.0, idna 3.20, click 8.5.0, cffi 2.1.1, pycparser 3.11, annotated-types 0.8.0, typing_extensions 4.16.0, typing-inspection 0.4.4 | transitivas |

**Transitivas de producción sin fijar** (en el venv local): numpy 2.5.4,
scipy 1.18.1, threadpoolctl 3.7.0, python-dateutil 2.9.0.post0, six 1.17.0,
narwhals 2.27.1, packaging 26.3, cloudpickle 3.1.2.

**`requirements-dev.txt`**: `-r requirements.txt` + Faker 40.43.0,
httpx2 2.13.1, matplotlib 3.11.2, pytest 9.1.1.

---

## 7. Configuración

| Variable | Default | Uso |
|---|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | obligatorias | Conexión |
| `DB_HOST` / `DB_PORT` | `localhost` / `3306` | Conexión |
| `DB_SSL_CA` | `""` (sin SSL) | Ruta a `ca.pem` o PEM pegado |
| `CORS_ORIGINS` | `"*"` | Lista separada por comas; en Render `https://buelvasmanuel.github.io` |

CORS: `allow_methods=["*"]`, `allow_headers=["*"]`, sin `allow_credentials`.

---

## 8. Tests

- En la verificación inicial (2026-10-11) había 133 tests en verde; 49 eran de
  productos y categorías y se fueron con ellas. `test_migraciones.py` comprueba
  que `alembic upgrade head` produce exactamente el esquema de los modelos.
- `conftest.py`: misma URL del `.env` pero base `tienda_test` (con `assert`
  de seguridad); migra a `head` al inicio de la sesión y hace `downgrade base`
  al final; cada test corre en una transacción con
  `join_transaction_mode="create_savepoint"` que se deshace; `client` sustituye
  `get_db` con `dependency_overrides`. Requiere MariaDB local en marcha.
- CI: `.github/workflows/tests.yml` ejecuta `pytest` contra `mariadb:lts` y `mysql:8.4`.

---

## 9. Despliegue

**Render** (Blueprint `render.yaml`): servicio web `api-tienda`, runtime python,
plan free, región `oregon`.

```
build:  pip install -r requirements.txt && alembic upgrade head && python -m scripts.entrenar_modelo --sin-graficas
start:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
health: GET /
```

- Variables `DB_*` y `DB_SSL_CA` con `sync: false` (se escriben en el panel);
  `CORS_ORIGINS` fija en el YAML.
- El repo está conectado como público: **no hay auto-deploy**. Cada push
  requiere *Manual Deploy*, que además aplica migraciones y **reentrena** el
  modelo con los datos que haya en Aiven en ese momento.
- Plan free: se duerme a los ~15 min sin tráfico, arranque en frío ~50 s,
  disco efímero (el `.joblib` sobrevive porque se genera en el build).

**Aiven**: MySQL 8.4 free, DigitalOcean `sfo`. Los datos se cargan desde la
máquina local (`seed_estudiantes`) con las credenciales de `.env.aiven`.

**Web**: el cliente Flutter está en GitHub Pages
(`https://buelvasmanuel.github.io/app-estudiantes/`); ese repo tiene su propio
workflow de Actions.

---

## 10. Diferencias con la sección 1 de la propuesta

| Propuesta dice | Realidad | Importancia |
|---|---|---|
| CRUD de `/productos`, `/categorias`, `/estudiantes` | Correcto, y además existen `GET /categorias/{id}/productos` y `GET /` (healthcheck) | Baja |
| «~133 tests» | Exactamente 133, todos en verde | — |
| Aiven «1 CPU, 1 GB RAM, 1 GB disco» | No se puede verificar desde el repo | Revisar en el panel de Aiven |
| Render free: «se duerme, sin disco» | Correcto; falta lo que más condiciona la Fase 1: **0,1 CPU y 512 MB de RAM**, un solo proceso | **Alta** (Argon2, SHAP) |
| Diagrama: Flutter → API directo | Hay un proxy Cloudflare de Render en medio | Media (IP real del cliente para rate limiting) |
| «Lo que NO tiene: usuarios, auth…» | Correcto, y la consecuencia es concreta: **cualquiera en internet puede hacer `DELETE /estudiantes/{id}`** contra la base de Aiven | **Alta** |
| WebSockets «a añadir» | La librería `websockets` 17.2 ya está instalada (viene con uvicorn) | Baja |
| No menciona la versión de Python | 3.14.7 | Media: cada librería nueva necesita wheels para 3.14 |
| `promedio` «como texto» en Flutter | Viene de que Pydantic serializa `Decimal` como texto; afecta también a `precio` | Baja |
| Tests con «httpx2» | Correcto: `httpx2` solo se usa en `scripts/graficas.py`; los tests usan `fastapi.testclient` | — |

El resto de la sección 1.2 (SQLAlchemy 2.x con `Mapped`/`select()`, Pydantic v2
con `Annotated` y `str_strip_whitespace`, collation por evento `connect`,
4 migraciones con `naming_convention`, CHECK en `estudiantes`, logística
balanceada elegida por F2 con CV estratificada, seed con Faker `es_CO` y semilla
fija, entrenamiento en el build, sin auto-deploy) coincide con el código.

---

## 11. Deuda técnica

Ordenada por impacto.

1. **Escrituras públicas en producción.** `POST/PUT/DELETE` de las tres
   entidades no tienen autenticación. CORS no protege nada aquí: solo lo
   aplican los navegadores; `curl` puede borrar los 300 estudiantes. La app
   Flutter solo hace lecturas y `POST /prediccion/riesgo`, así que protegerlas
   no la rompe.
2. **`CORS_ORIGINS` tiene `"*"` por defecto.** Si falta la variable en un
   despliegue, se abre a cualquier origen sin avisar. Además, `"*"` no se puede
   combinar con `allow_credentials=True` (cookies), que la Fase 1 podría necesitar.
3. ~~Transitivas sin fijar~~ — resuelto: numpy, scipy, threadpoolctl y demás
   quedan fijadas en `requirements.txt`.
4. **El modelo cambia en cada deploy.** Reentrenar en el build con los datos
   del momento hace que métricas, coeficientes e incluso el tipo de modelo
   (logística ↔ árbol, se elige por F2) puedan cambiar sin un commit. Para un
   juego que explica el modelo («la asistencia pesó 3 veces más»), eso es frágil.
5. **Umbral duplicado**: `estadisticas.RIESGO = 3` y `ml.UMBRAL_RIESGO = 3.0`.
6. ~~Sin CI en el backend~~ — resuelto (`.github/workflows/tests.yml`).
7. **Sin columnas de tiempo ni convención de zona horaria.** Las tablas nuevas
   (sesiones, rachas) la necesitan: MySQL `DATETIME` no guarda zona.
8. **`GET /prediccion/metricas` sin `response_model`**: el contrato no aparece en
   OpenAPI.
9. **Sin cabeceras de seguridad** (no hay `Strict-Transport-Security` ni
   `X-Content-Type-Options`, comprobado en producción) ni logs de aplicación.
10. **Sin paginación** en `GET /estudiantes/` (con 300 filas no molesta;
    quedó aplazada).
11. Menores: formato de error mixto (`detail`
    es texto en unos casos y objeto en el 422 de predicción); ningún esquema
    rechaza campos desconocidos (`extra="forbid"`), algo que importará en
    `/auth/registro`.

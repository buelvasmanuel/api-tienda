# API Tienda

API REST de estudiantes (CRUD, estadísticas y un modelo predictivo de riesgo
académico), construida con **FastAPI**, **SQLAlchemy 2.x** y **MariaDB** (a
través de PyMySQL). Es el backend de la app Flutter `app_estudiantes`.

> El nombre «tienda» viene de la primera actividad del curso (productos y
> categorías); esas tablas se eliminaron el 2026-10-11 (migración `f9047f24dd9b`).

> **Para ejecutarlo desde cero en Windows o Linux:** ver
> [INSTRUCCIONES_PROFESOR.md](INSTRUCCIONES_PROFESOR.md).

## Requisitos

- Python 3.11 o superior (desarrollado con 3.14; 3.10 no sirve: pandas y scikit-learn piden 3.11+)
- MariaDB (o MySQL)

### Arch Linux

```bash
sudo pacman -S python mariadb
sudo mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql
sudo systemctl enable --now mariadb
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip mariadb-server
sudo systemctl enable --now mariadb
```

> Si tu distribución no trae Python 3.11 o superior en sus repositorios, puedes
> instalarlo con [pyenv](https://github.com/pyenv/pyenv) o con
> [uv](https://docs.astral.sh/uv/).

### Windows

- Instala Python desde <https://www.python.org/downloads/> y marca
  **"Add python.exe to PATH"** durante la instalación.
- Instala MariaDB desde <https://mariadb.org/download/>. El instalador
  configura el servicio y te pide la contraseña de `root`.
- En los comandos de este README, sustituye:
  - `python` → `py`
  - `source venv/bin/activate` → `venv\Scripts\activate`
  - `cp` → `copy`
- Como alternativa, puedes usar WSL2 con Ubuntu y seguir los pasos de
  Ubuntu/Debian.

## Instalación

```bash
git clone <url-del-repositorio> api-tienda
cd api-tienda

python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt   # en un servidor: pip install -r requirements.txt
```

### Dependencias: `requirements.txt` frente a `requirements-dev.txt`

| Archivo                | Qué contiene                                          | Dónde se instala |
|------------------------|-------------------------------------------------------|------------------|
| `requirements.txt`     | Lo que la API necesita para **funcionar** (FastAPI, SQLAlchemy, PyMySQL, Alembic, scikit-learn, pandas, joblib...) | Producción y desarrollo |
| `requirements-dev.txt` | Empieza con `-r requirements.txt` (incluye todo lo anterior) y añade herramientas de **desarrollo** (pytest, httpx2, Faker, matplotlib) | Solo en tu máquina / CI |

Así el servidor no instala herramientas que nunca usa. Las dependencias se
añaden **a mano**, con versión fijada (`paquete==X.Y.Z`), en el archivo que
corresponda; no se regeneran con `pip freeze`, que mezclaría las de desarrollo
con las de producción.

## Crear la base de datos en MariaDB

Entra en la consola de MariaDB como administrador:

```bash
sudo mariadb
```

Y ejecuta lo siguiente (elige una contraseña tuya):

```sql
CREATE DATABASE tienda_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tienda_user'@'localhost' IDENTIFIED BY 'cambia_esta_contraseña';
GRANT ALL PRIVILEGES ON tienda_db.* TO 'tienda_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

## Configurar `.env`

Las credenciales se leen de un archivo `.env` en la raíz del proyecto
(mediante `pydantic-settings`, en `app/config.py`). Parte de la plantilla:

```bash
cp .env.example .env
```

Y rellena los valores:

| Variable      | Descripción                         | Ejemplo        |
|---------------|-------------------------------------|----------------|
| `DB_USER`     | Usuario de MariaDB                  | `tienda_user`  |
| `DB_PASSWORD` | Contraseña de ese usuario           | *(la tuya)*    |
| `DB_HOST`     | Servidor de la base de datos        | `localhost`    |
| `DB_PORT`     | Puerto de MariaDB                   | `3306`         |
| `DB_NAME`     | Nombre de la base de datos          | `tienda_db`    |
| `CLAVE_TOKENS` | Clave de las sesiones (32 bytes en base64url). **Obligatoria** | salida de `python -m scripts.generar_clave` |
| `CORS_ORIGINS` | Orígenes web permitidos, separados por comas. **Obligatoria** | `*` en local |
| `IP_CLIENTE`  | De dónde sale la IP para el límite por IP (opcional) | `conexion` (por defecto) |

> ⚠️ **Nunca subas `.env` a Git.** Ya está incluido en `.gitignore`.
> Comparte solo `.env.example`, que no lleva valores reales.

## Migraciones (Alembic)

Las tablas **no** se crean al arrancar la app: se gestionan con migraciones de
[Alembic](https://alembic.sqlalchemy.org/). Alembic toma la URL de la base de
`app/config.py`, así que usa el mismo `.env` que la API.

Crear/actualizar las tablas a la última versión:

```bash
alembic upgrade head
```

Tras cambiar un modelo en `app/models.py`, genera una migración nueva,
**revísala** en `alembic/versions/` y aplícala:

```bash
alembic revision --autogenerate -m "describe el cambio"
alembic upgrade head
```

Otros comandos útiles:

```bash
alembic current      # versión en la que está la base
alembic history      # lista de migraciones
alembic check        # ¿hay cambios en los modelos sin migración?
alembic downgrade -1 # deshace la última migración
```

Para trabajar contra otra base sin tocar `.env`, sobrescribe la variable en el
comando (las variables de entorno tienen prioridad sobre `.env`):

```bash
DB_NAME=tienda_test alembic upgrade head
```

## Ejecutar

Antes de la primera ejecución, aplica las migraciones (`alembic upgrade head`).

Desde la **raíz del proyecto** (para que se encuentre el `.env`) y con el
entorno virtual activado:

```bash
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Documentación interactiva (Swagger UI): <http://127.0.0.1:8000/docs>

Si el puerto 8000 está ocupado, puedes usar otro con `--port 8001`.

## Datos de prueba: estudiantes

`scripts/seed_estudiantes.py` llena la tabla `estudiantes` (de la base del
`.env`) con datos realistas generados con Faker (`es_CO`) y una semilla fija:
siempre salen los mismos estudiantes.

```bash
python -m scripts.seed_estudiantes                 # 300 estudiantes
python -m scripts.seed_estudiantes --cantidad 500  # otra cantidad
python -m scripts.seed_estudiantes --reset         # borra los existentes y genera de nuevo
```

Si ya hay estudiantes y no usas `--reset`, el script no inserta nada (para no
duplicar). Se ejecuta con `python -m` desde la raíz para que encuentre el
paquete `app`.

Los datos **no son independientes**: están pensados para minería de datos
(predecir el riesgo académico, promedio < 3.0). El promedio se calcula así:

```
promedio = 3.40
         + 0.035 × (horas_estudio − 14)     # más horas → mejor promedio
         + 0.02  × (asistencia − 82)        # más asistencia → mejor promedio
         + efecto de la carrera             # entre −0.15 (Medicina) y +0.12 (Diseño Gráfico)
         + ruido ~ Normal(0, 0.30)          # lo que ningún modelo puede explicar
```

recortado a [0.00, 5.00]. Con 300 estudiantes, alrededor del 18-21 % queda en
riesgo. La edad depende del semestre, y la asistencia está algo relacionada
con las horas de estudio; la edad y la ciudad **no** influyen en el promedio.

## Gráficas

Con la API en marcha y datos cargados, `scripts/graficas.py` consulta los
endpoints de estadísticas y guarda un PNG por cada uno en `graficas/`
(carpeta ignorada por Git):

```bash
python -m scripts.graficas                               # API en http://127.0.0.1:8000
python -m scripts.graficas --url http://127.0.0.1:8001   # otra URL
```

## Usuarios y roles

Hay tres roles: `jugador` (lo crea el registro público), `docente` y `admin`.
El primer admin se crea por consola, contra la base del `.env`:

```bash
python -m scripts.crear_usuario Profe_Admin --rol admin   # pide la contraseña
```

Las contraseñas se guardan con **Argon2id**; los refresh tokens, solo como
SHA-256. Detalles y decisiones en [docs/CONTRATO_AUTH.md](docs/CONTRATO_AUTH.md).

## Conectar desde Flutter

1. Arranca la API escuchando en **todas** las interfaces de red (no solo en
   `127.0.0.1`, que únicamente acepta conexiones del propio PC):

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0
   ```

2. Usa la URL base según dónde corra la app:

   | Dónde corre Flutter     | URL base                     |
   |-------------------------|------------------------------|
   | Emulador de Android     | `http://10.0.2.2:8000`       |
   | Celular real (misma WiFi que el PC) | `http://<IP-del-PC>:8000` |
   | Flutter Web / escritorio en el mismo PC | `http://127.0.0.1:8000` |

   `10.0.2.2` es una dirección especial del emulador de Android que apunta al
   PC anfitrión (dentro del emulador, `127.0.0.1` es el propio emulador).

3. Para ver la IP del PC en Linux:

   ```bash
   ip -4 addr
   ```

   Busca la interfaz de la WiFi (suele llamarse `wlan0` o `wlp…`) y la línea
   `inet 192.168.x.y/24`: la IP es `192.168.x.y` (sin el `/24`).

**Problemas típicos:**

- **Android bloquea `http://` (sin s)** desde Android 9. Para desarrollo, en
  `android/app/src/main/AndroidManifest.xml` añade al `<application>`:
  `android:usesCleartextTraffic="true"`.
- **Permiso de Internet:** en el mismo manifest, dentro de `<manifest>`:
  `<uses-permission android:name="android.permission.INTERNET"/>` (en modo
  debug ya viene, pero en *release* hace falta).
- **Cortafuegos:** si el celular no conecta pero el PC sí, abre el puerto
  (`sudo ufw allow 8000/tcp`, o con firewalld:
  `sudo firewall-cmd --add-port=8000/tcp`).
- **CORS** solo afecta a **Flutter Web** (lo aplican los navegadores, no las
  apps nativas). Los orígenes permitidos salen de la variable `CORS_ORIGINS`
  (separados por comas), que es obligatoria: `*` (cualquier origen) en
  desarrollo y solo el dominio de la app web en producción.

## Minería de datos: riesgo académico

Un modelo de clasificación predice si un estudiante está **en riesgo
académico** (`promedio < 3.0`) a partir de `horas_estudio`, `asistencia`,
`semestre`, `edad` y `carrera`.

### Entrenar

Con estudiantes en la base (ver *Datos de prueba*):

```bash
python -m scripts.entrenar_modelo
```

El script:

1. Lee los estudiantes de la base del `.env` (solo lectura) con SQLAlchemy y pandas.
2. Separa 80 % entrenamiento / 20 % prueba, **estratificado** (misma
   proporción de estudiantes en riesgo en ambos) y con semilla fija.
3. Compara, con **validación cruzada estratificada de 5 particiones** sobre
   el entrenamiento, una **Regresión Logística** (con `StandardScaler`) y un
   **Árbol de Decisión** (`max_depth=4`), ambos con `class_weight="balanced"`.
   Como referencia muestra también la Regresión Logística **sin balancear**
   (no compite).
4. Elige el mejor por **F2** (como F1, pero el recall pesa el doble que la precision).
5. Evalúa los modelos en el conjunto de prueba y muestra la importancia de las variables.
6. Guarda el Pipeline completo en `modelos/riesgo.joblib` y las métricas en
   `modelos/metricas.json` (carpeta ignorada por Git: se regenera con el
   script), más `graficas/matriz_confusion.png` y `graficas/importancia_variables.png`.

`promedio` **no** es una variable de entrada: el objetivo se calcula a partir
de él, así que usarlo sería *fuga de datos* (el modelo "vería la respuesta").

### Métricas obtenidas (300 estudiantes del seed, conjunto de prueba: 60, 11 en riesgo)

| Modelo                               | Accuracy | Precision | Recall | F1    | F2    |
|--------------------------------------|---------:|----------:|-------:|------:|------:|
| **Regresión Logística** (elegido)    | 0.917    | 0.800     | 0.727  | 0.762 | 0.741 |
| Árbol de Decisión                    | 0.867    | 0.600     | 0.818  | 0.692 | 0.763 |
| Regresión Logística sin balancear    | 0.900    | 1.000     | 0.455  | 0.625 | 0.510 |

En validación cruzada (que es con lo que se elige) la Regresión Logística
obtuvo F2 = 0.672 ± 0.232 frente a 0.594 ± 0.146 del árbol. Con tan pocos
estudiantes en riesgo en la prueba (11), **un solo acierto o fallo cambia el
recall en ~9 puntos**: las cifras son orientativas.

Variables más influyentes: **asistencia** y **horas de estudio** (en ambos
modelos). El semestre prácticamente no influye; la edad tampoco en el árbol,
aunque la Regresión Logística le da algo de peso por una casualidad de esta
muestra concreta (con más datos desaparece).

### Por qué importa más el recall que la accuracy

- **Recall** = de los estudiantes que de verdad están en riesgo, ¿a cuántos detecta?
- **Precision** = de los que el modelo marca en riesgo, ¿cuántos lo están de verdad?
- **Accuracy** = aciertos totales.

Solo ~18 % está en riesgo: un modelo que diga "nadie está en riesgo" acierta
el 82 % (accuracy alta) y no sirve para nada. Además, los errores no cuestan
lo mismo: no detectar a un estudiante en riesgo (falso negativo) puede
acabar en una materia perdida o en deserción, mientras que alertar de más
(falso positivo) solo cuesta una tutoría innecesaria.

### Predecir: `POST /prediccion/riesgo`

```json
// petición
{"horas_estudio": 6, "asistencia": 70, "semestre": 2, "edad": 18, "carrera": "Medicina"}
// respuesta
{"en_riesgo": true, "probabilidad": 0.98, "modelo": "Regresión Logística"}
```

- `probabilidad` (0-1) es la estimación del modelo de que el estudiante esté
  en riesgo; `en_riesgo` es `true` si es ≥ 0.5.
- Como se entrenó con `class_weight="balanced"`, las probabilidades están
  **infladas** hacia el riesgo (por diseño, para no dejar escapar casos): son
  útiles para **ordenar** estudiantes de más a menos riesgo, no como
  porcentaje literal. Por ejemplo, un estudiante "típico" (14 h, 82 %) sale
  con ~0.31 aunque solo el 18 % de los estudiantes esté en riesgo.
- Es una **alerta temprana**, no un diagnóstico: sirve para priorizar a quién
  ofrecer tutorías.
- La carrera tiene que ser una de las que el modelo conoce; si no, responde
  **422** con la lista `carreras_validas`.
- Si no hay modelo entrenado, responde **503** indicando cómo entrenarlo.
- La API carga el modelo una sola vez y, si se reentrena, lo recarga sola
  (sin reiniciar).

`GET /prediccion/metricas` devuelve `modelos/metricas.json` más
`carreras_validas` (para llenar el desplegable del formulario en Flutter).

> ⚠️ `modelos/riesgo.joblib` usa *pickle*: carga solo modelos generados por
> ti. Además, un `.joblib` solo está garantizado con la misma versión de
> scikit-learn con la que se entrenó (`version_sklearn` en `metricas.json`).

## Despliegue (Aiven + Render)

En producción la base es un **MySQL 8 gratis en [Aiven](https://aiven.io)** y la
API corre en el **plan free de [Render](https://render.com)**, desplegada desde
GitHub con el Blueprint `render.yaml`. Todo se configura con variables de
entorno, así que en local no cambia nada:

| Variable       | Local (`.env`)        | Render                                    |
|----------------|-----------------------|-------------------------------------------|
| `DB_*`         | MariaDB local         | Datos de conexión de Aiven                |
| `DB_SSL_CA`    | vacía (sin SSL)       | Contenido del `ca.pem` de Aiven (o una ruta al archivo) |
| `CORS_ORIGINS` | `*`                   | `https://buelvasmanuel.github.io`         |
| `CLAVE_TOKENS` | la de tu `.env`       | Otra distinta, de `python -m scripts.generar_clave` (crearla **antes** del deploy) |
| `IP_CLIENTE`   | `conexion`            | `conexion` hasta verificar el proxy (ver docs/ARQUITECTURA.md §9) |

- **Origen de CORS** = esquema + dominio (+ puerto), **sin ruta**: la app de
  `https://buelvasmanuel.github.io/app-estudiantes/` envía la cabecera
  `Origin: https://buelvasmanuel.github.io`.
- **Collation:** la conexión y las tablas usan `utf8mb4_unicode_ci` en los dos
  motores (migración `1d65ce94be3f`). Sin esto, MySQL 8 usaría
  `utf8mb4_0900_ai_ci`, que compara distinto (no ignora espacios finales).
- **El modelo se entrena en el build.** Render no guarda los archivos que se
  escriben mientras la API corre (se pierden al reiniciar), pero sí lo
  generado en el build. Por eso el build hace `pip install` →
  `alembic upgrade head` → `python -m scripts.entrenar_modelo --sin-graficas`.
  Así el modelo usa la misma versión de scikit-learn con la que se carga, y no
  hay binarios en el repo. Consecuencias:
  - Aiven tiene que tener los estudiantes **antes** del primer deploy (si hay
    menos de 50, el entrenamiento falla y Render mantiene el deploy anterior).
  - Para reentrenar con datos nuevos: *Manual Deploy* en Render.
- **Plan free:** la API se duerme tras ~15 min sin peticiones; la primera
  petición después tarda ~1 minuto en responder.

### Cargar datos en Aiven desde tu máquina

Crea `.env.aiven` (está en `.gitignore`, igual que los `*.pem`) con los datos
de Aiven y `DB_SSL_CA=ca.pem` (ruta al certificado descargado). Las variables
de entorno tienen prioridad sobre `.env`, así que basta con exportarlas:

```bash
set -a; source .env.aiven; set +a     # solo en esta terminal
alembic upgrade head
python -m scripts.seed_estudiantes    # 300 estudiantes
```

Cierra esa terminal al acabar: en otra nueva vuelves a usar tu `.env` local.

## Estructura del proyecto

```
api-tienda/
├── app/
│   ├── main.py          # Crea la app FastAPI y registra los routers
│   ├── config.py        # Configuración leída del .env (pydantic-settings)
│   ├── database.py      # Engine, sesión y dependencia get_db()
│   ├── models.py        # Modelos de SQLAlchemy (tablas)
│   ├── schemas.py       # Esquemas de Pydantic (validación y respuesta)
│   ├── ml.py            # Variables y Pipeline del modelo (compartido script/API)
│   ├── reloj.py         # Hora UTC en un solo sitio (los tests la adelantan)
│   ├── seguridad.py     # Argon2id, PASETO v4.local y refresh tokens (sin BD)
│   ├── sesiones.py      # Familias de refresh tokens en la BD
│   ├── dependencias.py  # usuario_actual, requiere_rol(...)
│   ├── errores.py       # Errores con código: {"detail": {"codigo", "mensaje"}}
│   ├── limites.py       # Límite de peticiones por IP (slowapi) e IP del cliente
│   └── routers/
│       ├── auth.py        # /auth: registro, login, refresh, sesiones
│       ├── usuarios.py    # /usuarios: gestión por docentes y admins
│       ├── estudiantes.py # Endpoints CRUD de /estudiantes
│       ├── estadisticas.py # Estadísticas de estudiantes (GROUP BY)
│       └── prediccion.py  # Predicción de riesgo académico
├── scripts/
│   ├── seed_estudiantes.py # Genera estudiantes de prueba (Faker)
│   ├── graficas.py      # PNG de las estadísticas (matplotlib)
│   ├── entrenar_modelo.py # Entrena el modelo de riesgo (scikit-learn)
│   ├── generar_clave.py # Clave nueva para CLAVE_TOKENS
│   └── crear_usuario.py # Crea usuarios con rol (el primer admin)
├── alembic/
│   ├── env.py           # Conecta Alembic con la config y los modelos
│   └── versions/        # Archivos de migración
├── alembic.ini          # Configuración de Alembic (sin credenciales)
├── tests/
│   ├── conftest.py      # Base tienda_test, migraciones, rollback por test
│   ├── ayudas.py        # Utilidades comunes (cabeceras con token, PASSWORD)
│   ├── test_migraciones.py # Las migraciones coinciden con los modelos
│   ├── test_modelo_usuarios.py # CHECK y UNIQUE de usuarios y sesiones
│   ├── test_seguridad.py # Ataques al access token
│   ├── test_auth.py     # Registro, login, bloqueo y permisos por rol
│   ├── test_refresh.py  # Rotación, reintentos, reuso, concurrencia, logout
│   ├── test_usuarios.py # Gestión de usuarios
│   ├── test_limites.py  # Límite por IP, cabeceras de seguridad, CORS, configuración
│   ├── test_estudiantes.py
│   ├── test_estadisticas.py
│   └── test_prediccion.py
├── docs/
│   ├── ARQUITECTURA.md  # Estado verificado del backend y deuda técnica
│   └── CONTRATO_AUTH.md # Contrato de autenticación con la app Flutter
├── .github/workflows/tests.yml # CI: pytest con MariaDB y MySQL 8.4
├── pytest.ini           # Configuración de pytest
├── .env.example         # Plantilla de variables de entorno
├── render.yaml          # Despliegue en Render (build, start y variables)
├── .python-version      # Versión de Python que usa Render
├── requirements.txt     # Dependencias de la app
└── requirements-dev.txt # Dependencias de desarrollo (tests, seed, gráficas)
```

## Tests

Los tests usan una base de datos **separada**, `tienda_test`, para no tocar
los datos reales. Créala una vez (en `sudo mariadb`):

```sql
CREATE DATABASE tienda_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON tienda_test.* TO 'tienda_user'@'localhost';
FLUSH PRIVILEGES;
```

Y ejecuta, con las dependencias de desarrollo instaladas:

```bash
pytest          # resumen
pytest -v       # un renglón por test
```

Cómo funcionan (`tests/conftest.py`):

- Usan las credenciales del `.env` pero **siempre** la base `tienda_test`; si el
  nombre no terminara en `_test`, los tests se niegan a ejecutarse.
- Al empezar se aplican las migraciones (`alembic upgrade head`) y al terminar
  se deshacen (`downgrade base`): se prueba el mismo esquema que en producción.
- Cada test corre dentro de una transacción que se deshace al final
  (rollback), así que todos empiezan con la base vacía y no dependen del orden.

## Endpoints

### Autenticación

El contrato completo (cuerpos, códigos de error, duración de los tokens y qué
debe hacer el cliente en cada caso) está en
[docs/CONTRATO_AUTH.md](docs/CONTRATO_AUTH.md). Resumen:

| Método | Ruta              | Acceso      | Descripción |
|--------|-------------------|-------------|-------------|
| `POST` | `/auth/registro`  | público     | Crea una cuenta de jugador y abre sesión |
| `POST` | `/auth/login`     | público     | Abre una sesión (bloqueo temporal tras 5 fallos) |
| `POST` | `/auth/refresh`   | público     | Cambia el refresh por uno nuevo (rotación) y da un access nuevo |
| `POST` | `/auth/logout`    | público     | Cierra la sesión de ese refresh |
| `POST` | `/auth/logout-todo` | con token | Cierra todas las sesiones del usuario |
| `GET`  | `/auth/yo`        | con token   | Usuario de la sesión |
| `GET`  | `/auth/sesiones`  | con token   | Sesiones abiertas (dispositivo, último uso) |
| `PUT`  | `/auth/password`  | con token   | Cambia la contraseña y cierra las demás sesiones |
| `GET`  | `/usuarios`       | docente, admin | Lista de usuarios |
| `PATCH`| `/usuarios/{id}`  | admin       | Cambia rol o activa/desactiva |
| `PUT`  | `/usuarios/{id}/password` | admin | Restablece la contraseña (no hay email) |

Las peticiones protegidas llevan `Authorization: Bearer <access_token>`. En
Swagger (`/docs`): botón **Authorize** y pegar el `access_token` del login.

### Raíz

| Método   | Ruta | Descripción                   | Respuesta |
|----------|------|-------------------------------|-----------|
| `GET`    | `/`  | Comprobar que la API funciona | 200       |

### Estudiantes

| Método   | Ruta                  | Descripción                         | Respuesta |
|----------|-----------------------|-------------------------------------|-----------|
| `GET`    | `/estudiantes/`       | Listar estudiantes (ordenados por id) | 200     |
| `GET`    | `/estudiantes/{id}`   | Obtener un estudiante               | 200 / 404 |
| `POST`   | `/estudiantes/`       | Crear un estudiante (**admin**)     | 201 / 401 / 403 / 422 |
| `PUT`    | `/estudiantes/{id}`   | Actualizar un estudiante (**admin**) | 200 / 401 / 403 / 404 / 422 |
| `DELETE` | `/estudiantes/{id}`   | Eliminar un estudiante (**admin**)  | 204 / 401 / 403 / 404 |

Leer es público; crear, modificar y borrar exige un token de un usuario con
rol `admin` (sin token: 401; con otro rol: 403).

Rangos válidos (los comprueba la API con 422 y también la base con `CHECK`):
edad 16-35, semestre 1-10, promedio 0.00-5.00, horas_estudio 0-40 (por
semana), asistencia 0-100 (%).

### Estadísticas (para gráficas)

Todas responden con el mismo formato, pensado para consumirlo fácilmente
desde Flutter: `[{"etiqueta": "Medicina", "valor": 30}, ...]`. Los conteos
son enteros y los promedios/porcentajes, decimales. Sin datos, devuelven `[]`.

| Ruta (`GET`)                                       | Valor                                   | Orden |
|----------------------------------------------------|-----------------------------------------|-------|
| `/estudiantes/estadisticas/por-carrera`            | cantidad de estudiantes                 | mayor a menor |
| `/estudiantes/estadisticas/por-edad`               | cantidad de estudiantes                 | por edad |
| `/estudiantes/estadisticas/por-semestre`           | cantidad de estudiantes                 | por semestre |
| `/estudiantes/estadisticas/por-ciudad`             | cantidad (solo las 10 primeras)         | mayor a menor |
| `/estudiantes/estadisticas/promedio-por-carrera`   | promedio medio, redondeado a 2 decimales | mayor a menor |
| `/estudiantes/estadisticas/riesgo-por-carrera`     | % de estudiantes con promedio < 3.0     | mayor a menor |

En caso de empate se ordena alfabéticamente, para que el resultado sea siempre el mismo.

### Predicción

| Método | Ruta                    | Descripción                                   | Respuesta |
|--------|-------------------------|-----------------------------------------------|-----------|
| `POST` | `/prediccion/riesgo`    | Predice si un estudiante está en riesgo       | 200 / 422 / 503 |
| `GET`  | `/prediccion/metricas`  | Métricas del modelo y carreras válidas        | 200 / 503 |

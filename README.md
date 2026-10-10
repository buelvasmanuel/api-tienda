# API Tienda

API REST para gestionar los productos de una tienda, construida con
**FastAPI**, **SQLAlchemy 2.x** y **MariaDB** (a través de PyMySQL).

## Requisitos

- Python 3.14
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

> Si tu distribución no trae Python 3.14 en sus repositorios, puedes
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

> **Base creada antes de usar Alembic:** si la tabla `productos` ya existía
> (creada con `create_all`), no ejecutes `upgrade` (fallaría al intentar
> crearla de nuevo). Marca la base como actualizada sin tocar los datos con
> `alembic stamp 3adaaf027eed` y a partir de ahí usa `upgrade` con normalidad.

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
  apps nativas). La API ya permite cualquier origen (`allow_origins=["*"]` en
  `app/main.py`), **solo para desarrollo**: en producción hay que listar los
  orígenes permitidos.

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
│   └── routers/
│       ├── productos.py   # Endpoints CRUD de /productos
│       ├── categorias.py  # Endpoints CRUD de /categorias
│       ├── estudiantes.py # Endpoints CRUD de /estudiantes
│       ├── estadisticas.py # Estadísticas de estudiantes (GROUP BY)
│       └── prediccion.py  # Predicción de riesgo académico
├── scripts/
│   ├── seed_estudiantes.py # Genera estudiantes de prueba (Faker)
│   ├── graficas.py      # PNG de las estadísticas (matplotlib)
│   └── entrenar_modelo.py # Entrena el modelo de riesgo (scikit-learn)
├── alembic/
│   ├── env.py           # Conecta Alembic con la config y los modelos
│   └── versions/        # Archivos de migración
├── alembic.ini          # Configuración de Alembic (sin credenciales)
├── tests/
│   ├── conftest.py      # Base tienda_test, migraciones, rollback por test
│   ├── test_productos.py
│   ├── test_categorias.py
│   ├── test_estudiantes.py
│   ├── test_estadisticas.py
│   └── test_prediccion.py
├── pytest.ini           # Configuración de pytest
├── .env.example         # Plantilla de variables de entorno
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

### Productos

| Método   | Ruta                     | Descripción              | Respuesta |
|----------|--------------------------|--------------------------|-----------|
| `GET`    | `/`                      | Comprobar que la API funciona | 200  |
| `GET`    | `/productos/`            | Listar productos         | 200       |
| `GET`    | `/productos/{id}`        | Obtener un producto      | 200 / 404 |
| `POST`   | `/productos/`            | Crear un producto        | 201 / 422 |
| `PUT`    | `/productos/{id}`        | Actualizar un producto   | 200 / 404 / 422 |
| `DELETE` | `/productos/{id}`        | Eliminar un producto     | 204 / 404 |

Un producto puede pertenecer a una categoría (`categoria_id`, opcional). Si se
indica una categoría que no existe, la API responde **422**.

### Categorías

| Método   | Ruta                          | Descripción                    | Respuesta |
|----------|-------------------------------|--------------------------------|-----------|
| `GET`    | `/categorias/`                | Listar categorías              | 200       |
| `GET`    | `/categorias/{id}`            | Obtener una categoría          | 200 / 404 |
| `GET`    | `/categorias/{id}/productos`  | Productos de una categoría     | 200 / 404 |
| `POST`   | `/categorias/`                | Crear una categoría            | 201 / 409 / 422 |
| `PUT`    | `/categorias/{id}`            | Actualizar una categoría       | 200 / 404 / 409 / 422 |
| `DELETE` | `/categorias/{id}`            | Eliminar una categoría         | 204 / 404 |

- El nombre de la categoría es **único** y la comparación no distingue
  mayúsculas ni acentos (collation `utf8mb4_unicode_ci`): si existe "Ropa",
  crear "ropa" o "RÓPA" devuelve **409 Conflict**.
- Los espacios al inicio y al final de los nombres se eliminan.
- Al borrar una categoría, sus productos **no** se borran: quedan con
  `categoria_id = null` (`ON DELETE SET NULL`).

### Estudiantes

| Método   | Ruta                  | Descripción                         | Respuesta |
|----------|-----------------------|-------------------------------------|-----------|
| `GET`    | `/estudiantes/`       | Listar estudiantes (ordenados por id) | 200     |
| `GET`    | `/estudiantes/{id}`   | Obtener un estudiante               | 200 / 404 |
| `POST`   | `/estudiantes/`       | Crear un estudiante                 | 201 / 422 |
| `PUT`    | `/estudiantes/{id}`   | Actualizar un estudiante            | 200 / 404 / 422 |
| `DELETE` | `/estudiantes/{id}`   | Eliminar un estudiante              | 204 / 404 |

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

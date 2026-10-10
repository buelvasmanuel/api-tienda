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
pip install -r requirements.txt
```

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

## Estructura del proyecto

```
api-tienda/
├── app/
│   ├── main.py          # Crea la app FastAPI y registra los routers
│   ├── config.py        # Configuración leída del .env (pydantic-settings)
│   ├── database.py      # Engine, sesión y dependencia get_db()
│   ├── models.py        # Modelos de SQLAlchemy (tablas)
│   ├── schemas.py       # Esquemas de Pydantic (validación y respuesta)
│   └── routers/
│       ├── productos.py # Endpoints CRUD de /productos
│       └── categorias.py # Endpoints CRUD de /categorias
├── alembic/
│   ├── env.py           # Conecta Alembic con la config y los modelos
│   └── versions/        # Archivos de migración
├── alembic.ini          # Configuración de Alembic (sin credenciales)
├── .env.example         # Plantilla de variables de entorno
└── requirements.txt     # Dependencias
```

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

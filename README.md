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

## Ejecutar

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
│       └── productos.py # Endpoints CRUD de /productos
├── .env.example         # Plantilla de variables de entorno
└── requirements.txt     # Dependencias
```

## Endpoints

| Método   | Ruta                     | Descripción              | Respuesta |
|----------|--------------------------|--------------------------|-----------|
| `GET`    | `/`                      | Comprobar que la API funciona | 200  |
| `GET`    | `/productos/`            | Listar productos         | 200       |
| `GET`    | `/productos/{id}`        | Obtener un producto      | 200 / 404 |
| `POST`   | `/productos/`            | Crear un producto        | 201 / 422 |
| `PUT`    | `/productos/{id}`        | Actualizar un producto   | 200 / 404 / 422 |
| `DELETE` | `/productos/{id}`        | Eliminar un producto     | 204 / 404 |

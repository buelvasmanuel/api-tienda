# Instrucciones para ejecutar el proyecto

El proyecto tiene dos partes:

- **`api-tienda`**: API REST (FastAPI + MariaDB/MySQL + modelo de scikit-learn).
- **`app_estudiantes`**: app Flutter que consume la API (Android, web, Windows o Linux).

Tres formas de probarlo:

1. **[Opción rápida: versión en la nube](#opción-rápida-versión-en-la-nube)**: sin instalar nada.
2. **[Opción Docker: la API en su PC con un comando](#opción-docker-la-api-en-su-pc-con-un-comando)**: solo hace falta Docker (sin instalar Python ni MariaDB).
3. **[Opción alternativa: instalación local](#opción-alternativa-instalación-local)**: todo a mano en su PC ([Windows](#windows) o [Linux](#linux)).

> Este archivo está en los dos repositorios (es el mismo).

---

## Opción rápida: versión en la nube

| | Enlace |
|---|---|
| **App web** | <https://buelvasmanuel.github.io/app-estudiantes/> |
| **APK Android** | <https://github.com/buelvasmanuel/app-estudiantes/releases/latest/download/Estudiantes.apk> (Android 7.0 o superior) |
| **API (Swagger)** | <https://api-tienda-wjac.onrender.com/docs> |

> ⏱️ La API está en el plan gratuito de Render, que **se apaga tras ~15 min sin
> uso**. La primera carga puede tardar **hasta un minuto**: la app muestra
> *"Despertando el servidor, puede tardar hasta un minuto…"*. Después responde
> al instante.

**Instalar el APK en un celular Android:**

1. Abrir el enlace del APK **desde el celular** y descargar `Estudiantes.apk`.
2. Abrir el archivo descargado. Si Android lo pide, permitir **"Instalar apps
   desconocidas"** para el navegador o el gestor de archivos.
3. Si aparece Play Protect ("app no reconocida"): **Más detalles → Instalar de
   todas formas** (es una app de clase, no está en Google Play).
4. Abrir **Estudiantes**.

Qué debe verse: ver [Verificación rápida](#verificación-rápida).

---

## Opción Docker: la API en su PC con un comando

Docker levanta la API y una base MariaDB en contenedores, sin instalar Python
ni MariaDB. El primer arranque crea las tablas, inserta los 300 estudiantes de
prueba, entrena el modelo y genera la clave de las sesiones: no hay que editar
ningún `.env`. La app Flutter se ejecuta igual que en la instalación local
(paso 6), contra `http://127.0.0.1:8000`.

### 1. Instalar Docker

**Windows 10 (22H2) u 11:** instalar **Docker Desktop**
(<https://docs.docker.com/desktop/setup/install/windows-install/>) con la
opción por defecto **"Use WSL 2"**. Reiniciar si lo pide y abrir Docker Desktop
una vez (debe quedar "Engine running", abajo a la izquierda).

**Linux:**

```bash
# Ubuntu 24.04
sudo apt update && sudo apt install docker.io docker-compose-v2 git
# Debian 13
sudo apt update && sudo apt install docker.io docker-compose git
# Arch Linux
sudo pacman -S docker docker-compose git

sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # para usar docker sin sudo; luego cerrar sesión y volver a entrar
```

(Otras distribuciones o versiones: <https://docs.docker.com/engine/install/>.)

Comprobar (en Windows, en CMD o PowerShell):

```bash
docker --version
docker compose version
```

### 2. Arrancar

Igual en Windows (CMD o PowerShell) y en Linux:

```bash
git clone https://github.com/buelvasmanuel/api-tienda.git
cd api-tienda
docker compose up --build
```

La primera vez descarga e instala todo (unos minutos, según la conexión). Está
lista cuando aparece `Application startup complete`. Abrir
<http://localhost:8000/docs> → Swagger UI. **Dejar esa terminal abierta** (o
usar `docker compose up --build -d` para que corra en segundo plano; entonces
`docker compose ps` muestra `healthy` cuando está lista y `docker compose logs -f api`
enseña los logs).

Opcional: usuario **admin** (pide la contraseña dos veces; no se ve al escribir):

```bash
docker compose exec api python -m scripts.crear_usuario Profe_Admin --rol admin
```

### 3. App Flutter

Como en el paso 6 de la instalación local ([Windows](#6-app-flutter) o
[Linux](#6-app-flutter-1)): `flutter run -d windows`, `-d linux` o `-d chrome`.
El emulador de Android y el celular por USB (`adb reverse tcp:8000 tcp:8000`)
también funcionan.

### Parar, actualizar y empezar de cero

| Para | Comando |
|---|---|
| Parar (los datos se conservan) | `Ctrl+C` en la terminal, o `docker compose down` |
| Volver a arrancar | `docker compose up` |
| Actualizar el código | `git pull` y `docker compose up --build` |
| **Borrar todo** (base, modelo, cuentas y sesiones) y empezar de cero | `docker compose down -v` |

### Problemas frecuentes con Docker

| Síntoma | Solución |
|---|---|
| Windows: `error during connect` / `open //./pipe/dockerDesktopLinuxEngine` | Docker Desktop no está abierto: abrirlo y esperar a "Engine running" |
| Windows: `WSL 2 installation is incomplete` o `WSL update required` | En PowerShell: `wsl --update` y reiniciar Docker Desktop |
| Windows: `Virtualization support not detected` | Activar la virtualización (Intel VT-x / AMD-V, "SVM") en la BIOS/UEFI |
| Linux: `permission denied ... /var/run/docker.sock` | Falta el grupo `docker`: `sudo usermod -aG docker $USER` y cerrar sesión; o anteponer `sudo` |
| `Bind for 127.0.0.1:8000 failed: port is already allocated` | Otra API ocupa el puerto 8000 (p. ej. la de la instalación local con `uvicorn`): pararla. O cambiar en `compose.yaml` `"127.0.0.1:8000:8000"` por `"127.0.0.1:8001:8000"` y usar `--dart-define=API_URL=http://127.0.0.1:8001` en la app |
| Celular en la **misma wifi** (sin cable) no conecta | Por seguridad la API solo escucha en este PC. En `compose.yaml`, cambiar `"127.0.0.1:8000:8000"` por `"8000:8000"`, `docker compose up -d` y usar `--dart-define=API_URL=http://<IP-del-PC>:8000` |
| La API se reinicia en bucle | Ver el error con `docker compose logs api`. Si se cortó el primer arranque a la mitad: `docker compose down -v` y `docker compose up --build` |

Los **tests** de la API no se ejecutan en Docker: necesitan la instalación
local (más abajo).

---

## Opción alternativa: instalación local

Orden: **instalar → base de datos → API → app**. La API debe quedar corriendo
en una terminal mientras se usa la app en otra.

---

## WINDOWS

### 1. Instalar

| Programa | Enlace | Notas |
|---|---|---|
| **Python 3.11 o superior** (recomendado 3.13) | <https://www.python.org/downloads/windows/> | Marcar **"Add python.exe to PATH"**. 3.10 o anterior **no** sirve. |
| **MariaDB** (11.4 LTS o superior) | <https://mariadb.org/download/> | El instalador pide una **contraseña de root**: anótala. Dejar marcado "Install as service" y el puerto **3306**. |
| **Git** | <https://git-scm.com/download/win> | Opciones por defecto. |
| **Flutter** | <https://docs.flutter.dev/get-started/install/windows/desktop> | Añadir `flutter\bin` al PATH. |
| **Visual Studio 2022 Community** | <https://visualstudio.microsoft.com/es/downloads/> | Solo para la app de escritorio Windows: carga de trabajo **"Desarrollo para el escritorio con C++"**. (No es VS Code.) |

Comprobar (en una terminal nueva):

```bat
py --version
git --version
flutter doctor
```

En `flutter doctor` debe salir ✓ en **Visual Studio** (para `-d windows`) o en **Chrome** (para `-d chrome`).

Los sonidos (opcionales, apagados por defecto) no necesitan nada en Windows:
Media Foundation reproduce MP3. Solo en las ediciones **N** de Windows hay que
instalar el *Media Feature Pack* (Configuración → Aplicaciones → Características
opcionales).

### 2. Base de datos

Abrir **"Command Prompt (MariaDB 11.x)"** desde el menú Inicio (o cualquier CMD si `mariadb` está en el PATH):

```bat
mariadb -u root -p
```

Escribir la contraseña de root que se puso al instalar y pegar:

```sql
CREATE DATABASE tienda_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tienda_user'@'localhost' IDENTIFIED BY 'tienda_pass_123';
GRANT ALL PRIVILEGES ON tienda_db.* TO 'tienda_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 3. Backend (API)

**CMD:**

```bat
cd ruta\a\api-tienda
py -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
copy .env.example .env
notepad .env
```

**PowerShell:**

```powershell
cd ruta\a\api-tienda
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
Copy-Item .env.example .env
notepad .env
```

Si PowerShell dice *"la ejecución de scripts está deshabilitada en este sistema"*:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

(o, solo para esa ventana: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`) y repetir `.\venv\Scripts\Activate.ps1`.

Con el venv activado el prompt empieza por **`(venv)`**.

En `.env` hay que revisar dos cosas:

1. `DB_PASSWORD` debe coincidir con la del `CREATE USER` (si se usó la del
   ejemplo, no hay que cambiarla).
2. `CLAVE_TOKENS` (la clave de las sesiones de usuario) debe tener una clave
   propia. Se genera con este comando y se pega en lugar del texto de ejemplo:

   ```bash
   python -m scripts.generar_clave
   ```

```ini
DB_USER=tienda_user
DB_PASSWORD=tienda_pass_123
DB_HOST=localhost
DB_PORT=3306
DB_NAME=tienda_db
CLAVE_TOKENS=(la salida de generar_clave)
CORS_ORIGINS=*
```

Si falta o es incorrecta, la API no arranca y dice
`CLAVE_TOKENS debe tener 32 bytes (python -m scripts.generar_clave)`.

### 4. Preparar los datos

Con el venv activado, en la carpeta `api-tienda`:

```bat
alembic upgrade head
python -m scripts.seed_estudiantes
python -m scripts.entrenar_modelo
```

1. Crea las tablas. 2. Inserta 300 estudiantes de prueba. 3. Entrena el modelo de riesgo (`modelos/riesgo.joblib`).

Opcional: para crear, modificar o borrar estudiantes desde `/docs` hace falta
un usuario **admin** (leer no lo necesita). Se crea con
`python -m scripts.crear_usuario Profe_Admin --rol admin` (pide la contraseña);
después, en `/docs`: `POST /auth/login` → copiar `access_token` → botón
**Authorize**.

### 5. Arrancar la API

```bat
uvicorn app.main:app --reload --host 0.0.0.0
```

Si el Firewall de Windows pregunta, **Permitir acceso** (redes privadas).
Abrir <http://127.0.0.1:8000/docs> → debe verse Swagger UI.
**Dejar esta terminal abierta.**

### 6. App Flutter

En **otra** terminal:

```bat
cd ruta\a\app_estudiantes
flutter pub get
flutter run -d windows
```

Alternativa en el navegador: `flutter run -d chrome`.

Al abrir la app, pulse **«¿No tienes cuenta? Regístrate»** y cree un alias y
una contraseña (mínimo 10 caracteres).

**Celular Android por USB** (depuración USB activada; `adb` viene con Android Studio, en `%LOCALAPPDATA%\Android\Sdk\platform-tools`):

```bat
adb reverse tcp:8000 tcp:8000
flutter devices
flutter run -d <id-del-celular> --dart-define=API_URL=http://127.0.0.1:8000
```

---

## LINUX

### 1. Instalar

| Programa | Enlace |
|---|---|
| **Python 3.11 o superior** | <https://www.python.org/downloads/> (normalmente ya viene; ver abajo) |
| **MariaDB** | <https://mariadb.org/download/> (o el gestor de paquetes) |
| **Git** | <https://git-scm.com/download/linux> |
| **Flutter** | <https://docs.flutter.dev/get-started/install/linux/desktop> |

**Ubuntu / Debian:**

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip git mariadb-server \
  clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libstdc++-12-dev \
  libsecret-1-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
sudo systemctl enable --now mariadb
```

**Arch Linux:**

```bash
sudo pacman -S python git mariadb clang cmake ninja pkgconf gtk3 libsecret \
  gstreamer gst-plugins-base gst-plugins-good
sudo mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql
sudo systemctl enable --now mariadb
```

Comprobar:

```bash
python3 --version     # 3.11 o superior
flutter doctor        # ✓ en "Linux toolchain" (para -d linux) o en Chrome
```

> Ubuntu 22.04 trae Python 3.10, que **no** sirve. Ubuntu 24.04 (3.12) y Debian 12 (3.11) sí.
> En 22.04: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv`
> y en el paso 3 usar `python3.12 -m venv venv`.

### 2. Base de datos

En Linux se entra como administrador con `sudo` (sin contraseña de root):

```bash
sudo mariadb
```

```sql
CREATE DATABASE tienda_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tienda_user'@'localhost' IDENTIFIED BY 'tienda_pass_123';
GRANT ALL PRIVILEGES ON tienda_db.* TO 'tienda_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 3. Backend (API)

```bash
cd ruta/a/api-tienda
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
python -m scripts.generar_clave   # copia la salida...
nano .env        # ...en CLAVE_TOKENS; DB_PASSWORD debe coincidir con la del CREATE USER
```

### 4. Preparar los datos

```bash
alembic upgrade head
python -m scripts.seed_estudiantes
python -m scripts.entrenar_modelo
```

### 5. Arrancar la API

```bash
uvicorn app.main:app --reload --host 0.0.0.0
```

Abrir <http://127.0.0.1:8000/docs>. **Dejar esta terminal abierta.**

### 6. App Flutter

En otra terminal:

```bash
cd ruta/a/app_estudiantes
flutter pub get
flutter run -d linux        # o: flutter run -d chrome
```

Al abrir la app, pulse **«¿No tienes cuenta? Regístrate»** y cree un alias y
una contraseña (mínimo 10 caracteres). En Linux la sesión se guarda en el
llavero del sistema (`libsecret`).

**Celular Android por USB:**

```bash
adb reverse tcp:8000 tcp:8000
flutter devices
flutter run -d <id-del-celular> --dart-define=API_URL=http://127.0.0.1:8000
```

---

## Verificación rápida

| Dónde | Qué debe verse |
|---|---|
| `/docs` de la API (local: <http://127.0.0.1:8000/docs> · nube: <https://api-tienda-wjac.onrender.com/docs>) | Swagger con los grupos Autenticación, Usuarios, Estudiantes, Estadísticas y Predicción. Crear, modificar o borrar estudiantes pide iniciar sesión como admin (sin sesión: **401**) |
| App → **Estudiantes** | "300 de 300" y el filtro **"En riesgo (55)"**; promedios < 3.0 en rojo. Tocar uno abre el detalle |
| App → **Gráficas** | "Estudiantes por carrera": Derecho es la más alta (**49**). El desplegable cambia entre 6 estadísticas |
| App → **Predicción** | Elegir **Psicología** y pulsar Predecir → tarjeta **verde** "Sin riesgo". Elegir **Medicina**, horas en **0** y asistencia en **30 %** → tarjeta **roja** "En riesgo". Abajo: métricas del modelo (recall 73 %) |

Tests de la API (opcional; usan una base aparte, `tienda_test`):

```sql
-- en mariadb (Windows: mariadb -u root -p · Linux: sudo mariadb)
CREATE DATABASE tienda_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON tienda_test.* TO 'tienda_user'@'localhost';
FLUSH PRIVILEGES;
```

```bash
pytest           # en api-tienda, con el venv activado → 563 passed
flutter test     # en app_estudiantes → 220 tests passed
```

---

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| **Puerto 8000 ocupado** (`[Errno 10048]` en Windows, `address already in use` en Linux) | Usar otro puerto: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8001` y en la app `flutter run -d windows --dart-define=API_URL=http://127.0.0.1:8001`. Ver quién lo usa: Windows `netstat -ano \| findstr :8000` → `taskkill /PID <pid> /F`; Linux `sudo ss -ltnp \| grep :8000` |
| **MariaDB no arranca** / `Can't connect to MySQL server` (2003) | Windows (CMD como administrador): `net start MariaDB` (o `services.msc` → MariaDB → Iniciar). Linux: `sudo systemctl start mariadb` y ver el error con `sudo journalctl -u mariadb -n 30` |
| `Access denied for user 'tienda_user'` (1045) | `DB_PASSWORD` en `.env` no coincide con la del `CREATE USER`. Evitar `@ : / #` en la contraseña |
| **venv no activado**: `No module named 'fastapi'`, `"uvicorn" no se reconoce...`, `alembic: command not found` | Activar el venv (Windows CMD `venv\Scripts\activate`, PowerShell `.\venv\Scripts\Activate.ps1`, Linux `source venv/bin/activate`). El prompt debe empezar por `(venv)` |
| `Table 'tienda_db.estudiantes' doesn't exist` | `alembic upgrade head` |
| **503** "Modelo no entrenado" (pestaña Predicción) | `python -m scripts.entrenar_modelo` (no hace falta reiniciar la API) |
| **Lista de estudiantes vacía** | `python -m scripts.seed_estudiantes` y en la app pulsar Actualizar |
| App: "Sin conexión con la API" | La API no está corriendo, o está en otro puerto. Comprobar <http://127.0.0.1:8000/docs> y pulsar **Reintentar** |
| `pip install` falla con "requires a different Python" / `pandas==3.0.6` | Python es 3.10 o anterior: instalar 3.11+ y recrear el venv (`py -3.13 -m venv venv` en Windows) |
| Windows: `Unable to find suitable Visual Studio toolchain` | Instalar Visual Studio 2022 con "Desarrollo para el escritorio con C++", o usar `flutter run -d chrome` |
| Windows: `Building with plugins requires symlink support` | Activar el Modo de desarrollador: `start ms-settings:developers` |
| Celular: la app no conecta | Repetir `adb reverse tcp:8000 tcp:8000` (se pierde al desconectar el cable) |

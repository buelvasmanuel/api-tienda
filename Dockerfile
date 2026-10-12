# Imagen de la API para desarrollo local con docker compose (compose.yaml).
# Producción (Render) no la usa: allí se despliega con render.yaml.
FROM python:3.14.7-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias primero: esta capa se reutiliza mientras no cambien los
# requirements. Faker hace falta para los 300 estudiantes de prueba; su versión
# sale de requirements-dev.txt (el resto de dev, como shap, no se instala).
# tr quita el \r que Git para Windows añade a los archivos (CRLF).
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt "$(grep -i '^faker==' requirements-dev.txt | tr -d '\r')"

COPY . .

# Sin root: el usuario "api" es dueño solo de lo que escribe (modelo y clave).
# /app/.env apunta al volumen: ahí guarda scripts/arranque_docker.py la clave.
RUN useradd --create-home --uid 1000 api \
    && mkdir -p /app/modelos /datos \
    && chown api:api /app/modelos /datos \
    && ln -s /datos/.env /app/.env
USER api

EXPOSE 8000
CMD ["python", "-m", "scripts.arranque_docker"]

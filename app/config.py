import base64
import binascii
import re
import ssl

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str

    # Certificado de la CA para conectar por SSL (p. ej. MySQL de Aiven). Puede
    # ser la ruta a un ca.pem o el contenido del certificado pegado tal cual.
    # Vacío = sin SSL (MariaDB local).
    DB_SSL_CA: str = ""

    # Orígenes permitidos por CORS, separados por comas. Un origen es solo
    # esquema + dominio (+ puerto), sin ruta: https://usuario.github.io
    # Obligatoria: en local "*"; en producción, solo el dominio de la app web.
    # (Sin valor por defecto para que un despliegue sin ella no quede abierto.)
    CORS_ORIGINS: str

    # De dónde sale la IP del cliente para el límite de peticiones por IP:
    # - "conexion": la que ve uvicorn (en local, la real).
    # - "x-forwarded-for:N": la N-ésima empezando por la derecha de
    #   X-Forwarded-For (la que añadió el proxy N, contando desde el servidor).
    #   La primera por la izquierda NO sirve: la puede escribir el cliente.
    # - Otro texto: nombre de una cabecera que pone el proxy (cf-connecting-ip).
    IP_CLIENTE: str = "conexion"

    # Si es False, POST /auth/registro responde 403 registro_cerrado y las
    # cuentas solo las crea un docente (alta en lote) o un admin.
    REGISTRO_ABIERTO: bool = True

    # Passkeys (docs/CONTRATO_PASSKEYS.md): el dominio (RP ID) al que quedan
    # ligadas y los orígenes web desde los que se aceptan, separados por comas.
    # En local, para probar con Flutter Web: localhost y http://localhost:<puerto>.
    PASSKEYS_RP_ID: str = "buelvasmanuel.github.io"
    PASSKEYS_RP_NOMBRE: str = "Estudiantes"
    PASSKEYS_ORIGENES: str = "https://buelvasmanuel.github.io"

    # Dónde viven los contadores del límite por IP (app/limites.py):
    # "memory://" (en el proceso; producción, con una sola instancia) o
    # "redis://host:6379" (compartidos entre procesos; Docker local, para
    # aprender). Cualquier URI que acepte la librería `limits`.
    LIMITES_URI: str = "memory://"

    # Clave maestra de los tokens: 32 bytes en base64url. De ella se derivan la
    # clave de PASETO y la de los refresh (app/seguridad.py). Sin valor por
    # defecto: si falta, la app no arranca. Generar con:
    #   python -m scripts.generar_clave
    CLAVE_TOKENS: str

    # Coste de Argon2id. Por defecto, el mínimo que recomienda OWASP
    # (t=2, m=19 MiB, p=1). Los tests lo bajan para ir rápido.
    ARGON2_TIEMPO: int = 2
    ARGON2_MEMORIA_KIB: int = 19 * 1024
    ARGON2_PARALELISMO: int = 1
    # Hashes simultáneos como máximo: cada uno reserva ARGON2_MEMORIA_KIB y
    # Render free tiene 512 MB y 0,1 CPU.
    ARGON2_CONCURRENCIA: int = 2

    # utf-8 explícito: en Windows la codificación por defecto es cp1252 y una
    # contraseña con "ñ" o tildes se leería mal.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Un error de configuración no debe imprimir valores: sin esto, pydantic
        # pone en el mensaje (y en los logs de Render) el valor inválido o, si
        # falta una variable, todas las demás (contraseña de la base incluida).
        hide_input_in_errors=True,
    )

    @field_validator("CLAVE_TOKENS")
    @classmethod
    def _clave_de_32_bytes(cls, valor: str) -> str:
        try:
            clave = base64.urlsafe_b64decode(valor + "=" * (-len(valor) % 4))
        except (binascii.Error, ValueError):
            raise ValueError("CLAVE_TOKENS no es base64url válido")
        if len(clave) != 32:
            raise ValueError("CLAVE_TOKENS debe tener 32 bytes (python -m scripts.generar_clave)")
        return valor

    @field_validator("IP_CLIENTE")
    @classmethod
    def _modo_ip_valido(cls, valor: str) -> str:
        valor = valor.strip().lower()
        # "x-forwarded-for" a secas no vale como nombre de cabecera: es una
        # lista y su primer elemento lo controla el cliente.
        valido = re.fullmatch(r"conexion|x-forwarded-for:[1-9]|[a-z0-9-]+", valor)
        if not valido or valor == "x-forwarded-for":
            raise ValueError('IP_CLIENTE: "conexion", "x-forwarded-for:N" o un nombre de cabecera')
        return valor

    @property
    def clave_tokens(self) -> bytes:
        return base64.urlsafe_b64decode(self.CLAVE_TOKENS + "=" * (-len(self.CLAVE_TOKENS) % 4))

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def connect_args(self) -> dict:
        """Argumentos de PyMySQL comunes a la app, Alembic y los tests."""
        args: dict = {"charset": "utf8mb4"}
        if self.DB_SSL_CA:
            ca = self.DB_SSL_CA.strip()
            if ca.startswith("-----BEGIN"):
                contexto = ssl.create_default_context(cadata=ca)
            else:
                contexto = ssl.create_default_context(cafile=ca)
            args["ssl"] = contexto  # verifica el certificado y el nombre del host
        return args

    @property
    def passkeys_origenes(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.PASSKEYS_ORIGENES.split(",") if o.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def origenes_con_credenciales(self) -> list[str]:
        """Orígenes a los que se permiten cookies (refresh en cookie, CORS con
        credenciales). Ninguno si CORS_ORIGINS contiene "*": abrir las cookies
        a cualquier web sería un CSRF servido en bandeja."""
        return [] if "*" in self.cors_origins else self.cors_origins


settings = Settings()

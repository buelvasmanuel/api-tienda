import ssl

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
    CORS_ORIGINS: str = "*"

    # utf-8 explícito: en Windows la codificación por defecto es cp1252 y una
    # contraseña con "ñ" o tildes se leería mal.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()

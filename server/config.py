import os
from pathlib import Path
from dotenv import load_dotenv

# Procura o .env na raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    API_TOKEN_SECRET: str = os.getenv("API_TOKEN_SECRET", "chave_insegura_default")
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    SCRCPY_VERSION: str = os.getenv("SCRCPY_VERSION", "4.1")
    SCRCPY_MAX_SIZE: int = int(os.getenv("SCRCPY_MAX_SIZE", "1024"))
    MADLAB_APK: str = os.getenv("MADLAB_APK", "")

settings = Settings()

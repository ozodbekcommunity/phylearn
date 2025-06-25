from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Ma'lumotlar bazasi
    DATABASE_URL: str = "sqlite:///phylearn.db"
    
    # Xavfsizlik
    SECRET_KEY: str = "phylearn-super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # AI integratsiyasi
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    
    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    
    # Fayl yuklash
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_EXTENSIONS: set = {".pdf", ".doc", ".docx", ".txt", ".jpg", ".png"}
    
    # Ilova sozlamalari
    APP_NAME: str = "PhyLearn"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"

settings = Settings()
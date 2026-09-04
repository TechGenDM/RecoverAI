from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Execution Mode
    MODE: Literal["LIVE", "SIMULATED"] = "LIVE"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://recoverai:recoverai_password@localhost:5432/recoverai_dev"
    )

    # Razorpay Credentials (Test Mode) - Protected from exposure
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: SecretStr = Field(default=SecretStr(""))
    RAZORPAY_WEBHOOK_SECRET: SecretStr = Field(default=SecretStr(""))

    # LLM Configuration
    LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = "gemini"
    LLM_MODEL: str = "gemini-2.5-flash"
    GEMINI_API_KEY: SecretStr = Field(default=SecretStr(""))
    OPENAI_API_KEY: SecretStr = Field(default=SecretStr(""))
    ANTHROPIC_API_KEY: SecretStr = Field(default=SecretStr(""))

    # Recovery Policy Defaults (Bounded)
    RECOVERY_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    RECOVERY_MAX_WINDOW_HOURS: int = Field(default=72, ge=1, le=168)
    RECOVERY_LINK_EXPIRY_HOURS: int = Field(default=24, ge=1, le=72)

    # Agent / Scheduler constraints
    LLM_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    SCHEDULER_BATCH_SIZE: int = Field(default=10, ge=1, le=100)

    # M3 Executor Configuration
    RAZORPAY_BASE_URL: str = "https://api.razorpay.com"
    EXECUTOR_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    PAYMENT_LINK_MIN_VALIDITY_MINUTES: int = Field(default=15, ge=5, le=60)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def safe_dict(self) -> dict:
        """Returns non-sensitive configuration parameters for logging and inspection."""
        return {
            "MODE": self.MODE,
            "DATABASE_URL_SCHEME": self.DATABASE_URL.split("://")[0]
            if "://" in self.DATABASE_URL
            else "unknown",
            "RAZORPAY_KEY_ID_PRESENT": bool(self.RAZORPAY_KEY_ID),
            "RAZORPAY_KEY_SECRET_SET": bool(
                self.RAZORPAY_KEY_SECRET.get_secret_value()
            ),
            "RAZORPAY_WEBHOOK_SECRET_SET": bool(
                self.RAZORPAY_WEBHOOK_SECRET.get_secret_value()
            ),
            "LLM_PROVIDER": self.LLM_PROVIDER,
            "LLM_MODEL": self.LLM_MODEL,
            "GEMINI_API_KEY_SET": bool(self.GEMINI_API_KEY.get_secret_value()),
            "RECOVERY_MAX_ATTEMPTS": self.RECOVERY_MAX_ATTEMPTS,
            "RECOVERY_MAX_WINDOW_HOURS": self.RECOVERY_MAX_WINDOW_HOURS,
            "RECOVERY_LINK_EXPIRY_HOURS": self.RECOVERY_LINK_EXPIRY_HOURS,
            "LLM_TIMEOUT_SECONDS": self.LLM_TIMEOUT_SECONDS,
            "SCHEDULER_BATCH_SIZE": self.SCHEDULER_BATCH_SIZE,
        }


settings = Settings()

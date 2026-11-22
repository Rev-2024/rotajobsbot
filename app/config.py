import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> set[str]:
    return {p.strip() for p in value.split(",") if p.strip()}


class Settings:
    def __init__(self) -> None:
        url = os.getenv("DATABASE_URL", "sqlite:///./rotary_jobs.db")
        # Hosts (Neon, Render, Heroku...) hand out "postgres://" or "postgresql://" URLs. Pin the driver we
        # ship (psycopg 3) so it works the same whichever SQLAlchemy version gets installed.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
                break
        self.database_url = url
        self.wa_token = os.getenv("WHATSAPP_TOKEN", "")
        self.wa_phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.wa_verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        self.wa_app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
        self.graph_version = os.getenv("GRAPH_API_VERSION", "v21.0")
        self.admin_phones = _csv(os.getenv("ADMIN_PHONES", ""))
        self.admin_token = os.getenv("ADMIN_TOKEN", "")
        self.dry_run = os.getenv("DRY_RUN", "") == "1"
        # Website chat
        secret = os.getenv("SESSION_SECRET", "")
        self.session_secret_is_set = bool(secret)
        self.session_secret = secret or secrets.token_hex(32)  # random per start if unset: logins reset on restart
        self.cookie_secure = os.getenv("COOKIE_SECURE", "") == "1"  # set to 1 once the site is served over HTTPS
        self.invite_code = os.getenv("INVITE_CODE", "").strip()  # optional club code required to create an account


settings = Settings()

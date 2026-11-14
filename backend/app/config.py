import os

DATABASE_URL = os.environ.get("FIOLE_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg2://app:app@localhost:5432/statsapp"
# Managed hosts often give "postgres://"; SQLAlchemy needs "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
SESSION_COOKIE = "fiole_session"
SESSION_DAYS = int(os.environ.get("FIOLE_SESSION_DAYS", "30"))
COOKIE_SECURE = os.environ.get("FIOLE_COOKIE_SECURE", "0") == "1"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

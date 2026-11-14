import os

DATABASE_URL = os.environ.get("FIOLE_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg2://app:app@localhost:5432/statsapp"
# Managed hosts give "postgres://" or "postgresql://" without a driver; pin psycopg2
# (SQLAlchemy 2.1+ would otherwise default to psycopg v3, which is not installed).
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(_prefix):]
        break

SESSION_COOKIE = "fiole_session"
SESSION_DAYS = int(os.environ.get("FIOLE_SESSION_DAYS", "30"))
COOKIE_SECURE = os.environ.get("FIOLE_COOKIE_SECURE", "0") == "1"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Accounts allowed to see the admin page (user counts, CSV of donation-appeal opt-ins). Comma-separated.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("FIOLE_ADMIN_EMAILS", "").split(",") if e.strip()}

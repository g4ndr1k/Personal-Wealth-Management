"""Seed default categories and the single assistant user."""

import secrets
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

from api.db import get_db, init_db

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

CATEGORIES = [
    ("groceries",          "Belanja Harian",          1),
    ("meals",              "Makanan & Minuman",       2),
    ("snacks",             "Jajan / Camilan",         3),
    ("gas_lpg",            "Gas LPG",                 4),
    ("electricity_token",  "Token Listrik",           5),
    ("water",              "Air (Galon / PDAM)",      6),
    ("transport",          "Transportasi",            7),
    ("household_supplies", "Peralatan Rumah Tangga",  8),
    ("laundry",            "Laundry",                 9),
    ("cleaning",           "Kebersihan",              10),
    ("medical",            "Kesehatan / Obat",        11),
    ("children",           "Anak-anak",               12),
    ("donation",           "Sedekah / Donasi",        13),
    ("staff_salary",       "Gaji ART / Driver",       14),
    ("other",              "Lainnya",                 99),
]

DEFAULT_USERNAME = "asisten"
DEFAULT_PASSWORD = "rumah123"
DEFAULT_DISPLAY_NAME = "Asisten"


def seed_all(db_path: str | None = None) -> None:
    from api.db import DB_PATH as _default
    path = db_path or _default
    init_db(path)
    now = datetime.now(timezone.utc).isoformat()

    with get_db(path) as conn:
        # Seed categories (INSERT OR IGNORE to preserve user edits)
        conn.executemany(
            "INSERT OR IGNORE INTO household_categories (code, label_id, sort_order) VALUES (?, ?, ?)",
            CATEGORIES,
        )

        # Seed default user only if table is empty
        row = conn.execute("SELECT COUNT(*) FROM app_users").fetchone()
        if row[0] == 0:
            conn.execute(
                "INSERT INTO app_users (id, username, display_name, password_hash, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (
                    str(uuid.uuid4()),
                    DEFAULT_USERNAME,
                    DEFAULT_DISPLAY_NAME,
                    pwd_ctx.hash(DEFAULT_PASSWORD),
                    now,
                ),
            )
            print(f"[seed] Created default user '{DEFAULT_USERNAME}' with password '{DEFAULT_PASSWORD}'")
        else:
            print("[seed] Users already exist — skipping user seed")

    print("[seed] Categories verified")

"""
PostgreSQL Reporting Database Client.
Connects using the `database` section inside `credential_tools.json`.
"""

from __future__ import annotations

from typing import Any
import psycopg2

from scripts.core.cli import print_error, print_info, print_success
from scripts.core.config import get_db_config


def get_db_connection() -> Any:
    """Open and return a PostgreSQL connection using credential_tools.json."""
    try:
        db_cfg = get_db_config()
    except Exception as e:
        print_error(f"Gagal membaca konfigurasi database: {e}")
        return None

    host = db_cfg["host"]
    port = db_cfg["port"]
    dbname = db_cfg["dbname"]
    user = db_cfg["user"]
    password = db_cfg["password"]

    missing = [k for k in ["host", "dbname", "user", "password"] if not db_cfg.get(k)]
    if missing:
        print_error(
            f"Konfigurasi database di credential_tools.json belum lengkap. Field kosong: {', '.join(missing)}"
        )
        return None

    print_info(f"Menghubungkan ke PostgreSQL database '{dbname}' di {host}:{port} sebagai '{user}'...")
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=15,
        )
        print_success("Berhasil terhubung ke database PostgreSQL.")
        return conn
    except Exception as e:
        print_error(f"Gagal terhubung ke database PostgreSQL: {e}")
        return None

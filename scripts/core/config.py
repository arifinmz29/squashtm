"""
Configuration & Credential Loader.
Reads all credentials from `credential_tools.json` at the project root.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Project Directory Structure
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(CORE_DIR)
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)

CREDENTIAL_FILE = os.path.join(PROJECT_ROOT, "credential_tools.json")
CREDENTIAL_EXAMPLE_FILE = os.path.join(PROJECT_ROOT, "credential_tools.example.json")

DATA_DIR = os.path.join(PROJECT_ROOT, "data_files")
CSV_DIR = os.path.join(DATA_DIR, "csv")
JSON_DIR = os.path.join(DATA_DIR, "json")
XLS_DIR = os.path.join(DATA_DIR, "xls")
TEMP_XLS_DIR = os.path.join(XLS_DIR, "temp")
SCHEMAS_DIR = os.path.join(DATA_DIR, "schemas")

COOKIE_FILE = os.path.join(PROJECT_ROOT, "cookies.txt")


def ensure_data_dirs() -> None:
    """Ensure all standard output/input data directories exist."""
    for folder in [DATA_DIR, CSV_DIR, JSON_DIR, XLS_DIR, TEMP_XLS_DIR, SCHEMAS_DIR]:
        os.makedirs(folder, exist_ok=True)


def load_credentials(cred_path: str | None = None) -> dict[str, Any]:
    """Load credentials dictionary from credential_tools.json."""
    target_path = cred_path or CREDENTIAL_FILE
    if not os.path.exists(target_path):
        raise FileNotFoundError(
            f"File credential tidak ditemukan di: {target_path}\n"
            f"Silakan salin '{CREDENTIAL_EXAMPLE_FILE}' menjadi '{CREDENTIAL_FILE}' "
            "dan lengkapi nilai credential di dalamnya."
        )

    with open(target_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_squash_config() -> dict[str, str]:
    """Return SquashTM credential dictionary."""
    creds = load_credentials()
    sq = creds.get("squashtm", {})
    url = str(sq.get("url") or "").strip().rstrip("/")
    username = str(sq.get("username") or "").strip()
    password = str(sq.get("password") or "").strip()
    dimension_type_default = str(sq.get("dimension_type_default") or "").strip()

    return {
        "url": url,
        "username": username,
        "password": password,
        "dimension_type_default": dimension_type_default,
    }


def get_db_config() -> dict[str, Any]:
    """Return PostgreSQL Reporting DB credential dictionary."""
    creds = load_credentials()
    db = creds.get("database", {})
    return {
        "host": str(db.get("host") or "").strip(),
        "port": int(db.get("port") or 5432),
        "dbname": str(db.get("dbname") or "").strip(),
        "user": str(db.get("user") or "").strip(),
        "password": str(db.get("password") or "").strip(),
    }


def get_gsheet_config() -> dict[str, Any]:
    """Return Google Service Account dictionary with normalized private_key."""
    creds = load_credentials()
    gsa = dict(creds.get("google_service_account", {}))
    if "private_key" in gsa and isinstance(gsa["private_key"], str):
        gsa["private_key"] = gsa["private_key"].replace("\\n", "\n")
    return gsa

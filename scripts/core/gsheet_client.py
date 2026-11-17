"""
Google Sheets API Client.
Authenticates using the `google_service_account` section inside `credential_tools.json`.
"""

from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from scripts.core.cli import print_error, print_info, print_success
from scripts.core.config import get_gsheet_config


def get_google_sheet_client() -> gspread.Client | None:
    """Authenticate and return a gspread Client using credential_tools.json."""
    print_info("Mengautentikasi Google Sheets API...")
    try:
        creds_data = get_gsheet_config()
    except Exception as e:
        print_error(f"Gagal membaca konfigurasi Google Service Account: {e}")
        return None

    private_key = creds_data.get("private_key", "").strip()
    client_email = creds_data.get("client_email", "").strip()

    if not private_key or "YOUR_PRIVATE_KEY" in private_key:
        print_error(
            "Field 'google_service_account.private_key' belum diisi dengan benar di credential_tools.json."
        )
        return None

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        client = gspread.authorize(creds)
        print_success(f"Autentikasi Google API berhasil ({client_email}).")
        return client
    except Exception as e:
        print_error(f"Autentikasi Google Sheets gagal: {e}")
        return None

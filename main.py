#!/usr/bin/env python3
"""
SquashTM & Regression Automation CLI Application.
Entry point interaktif yang menggabungkan seluruh modul tools SquashTM.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts import (
    delete_from_testrun_by_testcaseid,
    export_test_cases_by_folder,
    manual_regression_preparation,
    update_custom_field_test_case,
    update_status_test_run_sqtm_multi,
    update_status_test_run_sqtm_multi_v2,
)
from scripts.core.cli import (
    Color,
    print_banner,
    print_error,
    print_info,
    print_section,
    print_success,
    print_warn,
    prompt_bool,
    prompt_choice,
)
from scripts.core.config import (
    CREDENTIAL_FILE,
    ensure_data_dirs,
    get_db_config,
    get_gsheet_config,
    get_squash_config,
)
from scripts.core.db_client import get_db_connection
from scripts.core.gsheet_client import get_google_sheet_client
from scripts.core.squash_client import cleanup_cookies, get_squash_session


def check_credentials_and_connections() -> int:
    """Memeriksa kelengkapan file credential_tools.json dan menguji koneksi."""
    print_banner(
        "CEK CREDENTIAL & TEST KONEKSI",
        f"Lokasi file: {CREDENTIAL_FILE}",
    )

    if not os.path.exists(CREDENTIAL_FILE):
        print_error(f"File '{CREDENTIAL_FILE}' belum ditemukan!")
        print_info(
            "Silakan salin 'credential_tools.example.json' menjadi 'credential_tools.json' terlebih dahulu."
        )
        return 1

    try:
        sq = get_squash_config()
        db = get_db_config()
        gs = get_gsheet_config()
    except Exception as e:
        print_error(f"Gagal membaca credential_tools.json: {e}")
        return 1

    print_section("1. Ringkasan Konfigurasi di credential_tools.json")
    print(f"  [SquashTM] URL              : {sq.get('url') or '<KOSONG>'}")
    print(f"  [SquashTM] Username         : {sq.get('username') or '<KOSONG>'}")
    print(f"  [SquashTM] Password         : {'********' if sq.get('password') else '<KOSONG>'}")
    print(f"  [Database] Host:Port/DB     : {db.get('host')}:{db.get('port')}/{db.get('dbname')}")
    print(f"  [Database] User             : {db.get('user') or '<KOSONG>'}")
    print(f"  [GSheet]   Service Account  : {gs.get('client_email') or '<KOSONG>'}")
    has_pk = bool(gs.get("private_key") and "BEGIN PRIVATE KEY" in gs.get("private_key", ""))
    print(f"  [GSheet]   Private Key      : {'Valid (Terisi)' if has_pk else '<BELUM DIISI / TIDAK VALID>'}")

    if not prompt_bool("Lakukan test koneksi langsung ke SquashTM, Database, dan Google API?", default=True):
        return 0

    print_section("2. Menguji Koneksi SquashTM")
    session, _ = get_squash_session()
    if session:
        cleanup_cookies()

    print_section("3. Menguji Koneksi PostgreSQL Database")
    conn = get_db_connection()
    if conn:
        conn.close()

    print_section("4. Menguji Autentikasi Google Sheets API")
    get_google_sheet_client()

    return 0


def show_main_menu() -> str:
    """Menampilkan menu utama interaktif dan mengembalikan pilihan user."""
    print_banner(
        "SQUASHTM & REGRESSION AUTOMATION CLI",
        "Pilih fitur yang ingin dijalankan (gunakan angka 1-7 atau 0 untuk keluar)",
    )

    menu_items = [
        (
            "1",
            "Delete Test Cases from Test Run (by TestCaseID)",
            "Hapus test case dari Iteration Test Plan via CSV / daftar TC ID",
        ),
        (
            "2",
            "Export Test Cases by Folder (.xlsx)",
            "Export & gabungkan seluruh test case dalam folder SquashTM ke Excel",
        ),
        (
            "3",
            "Manual Regression Preparation (DB -> Google Sheet)",
            "Query hasil regression terbaru dari PostgreSQL ke tab {release_tag}_report",
        ),
        (
            "4",
            "Update Custom Field Test Case",
            "Update custom field (automationStatus, automationPlatform, dll.) dari Google Sheet",
        ),
        (
            "5",
            "Update Status Test Run Multi-Iteration (V1 - Single Status)",
            "Mass-update satu status (mis: SUCCESS) dari daftar TC ID di Kolom A Google Sheet",
        ),
        (
            "6",
            "Update Status Test Run Multi-Iteration (V2 - Dynamic Mapping)",
            "Mass-update status berdasarkan pemetaan kolom ID & Status Rerun di Google Sheet",
        ),
        (
            "7",
            "Cek Credential & Test Koneksi",
            "Verifikasi konfigurasi credential_tools.json (SquashTM, DB, Google Sheets)",
        ),
        (
            "0",
            "Keluar (Exit)",
            "Tutup aplikasi CLI",
        ),
    ]

    for code, title, desc in menu_items:
        color = Color.YELLOW if code == "0" else Color.CYAN
        print(f"  {color}{Color.BOLD}[{code}]{Color.RESET} {Color.BOLD}{title}{Color.RESET}")
        print(f"      {Color.DIM}{desc}{Color.RESET}")

    print()
    while True:
        choice = input(f"{Color.BOLD}Masukkan pilihan menu [0-7]: {Color.RESET}").strip()
        if choice in {item[0] for item in menu_items}:
            return choice
        print_warn("Pilihan tidak valid. Silakan masukkan angka 0 sampai 7.")


def main() -> int:
    ensure_data_dirs()

    handlers = {
        "1": delete_from_testrun_by_testcaseid.run_interactive,
        "2": export_test_cases_by_folder.run_interactive,
        "3": manual_regression_preparation.run_interactive,
        "4": update_custom_field_test_case.run_interactive,
        "5": update_status_test_run_sqtm_multi.run_interactive,
        "6": update_status_test_run_sqtm_multi_v2.run_interactive,
        "7": check_credentials_and_connections,
    }

    try:
        while True:
            choice = show_main_menu()
            if choice == "0":
                print_info("Terima kasih! Menutup aplikasi SquashTM CLI.")
                return 0

            handler = handlers.get(choice)
            if handler:
                try:
                    handler()
                except KeyboardInterrupt:
                    print()
                    print_warn("Operasi dibatalkan oleh pengguna (Ctrl+C).")
                except Exception as e:
                    print_error(f"Terjadi kesalahan tak terduga: {e}")

            print()
            if not prompt_bool("Kembali ke Menu Utama?", default=True):
                print_info("Terima kasih! Menutup aplikasi SquashTM CLI.")
                return 0
    except KeyboardInterrupt:
        print()
        print_info("Aplikasi dihentikan oleh pengguna (Ctrl+C).")
        return 0
    finally:
        cleanup_cookies()


if __name__ == "__main__":
    sys.exit(main())

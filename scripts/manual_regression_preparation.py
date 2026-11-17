#!/usr/bin/env python3
"""
Fitur 3: Manual Regression Preparation (PostgreSQL Reporting DB -> Google Sheet).

Alur Proses:
  1. Melakukan koneksi ke database PostgreSQL Reporting menggunakan credential di `credential_tools.json`.
  2. Menjalankan query hasil eksekusi test terbaru (dedup per `tr_tc_test_id` dengan `ROW_NUMBER()`)
     berdasarkan `release_tag` dan daftar `platform_names`.
  3. Mengautentikasi Google Sheets API menggunakan `google_service_account` di `credential_tools.json`.
  4. Membuat atau memperbarui sheet `{release_tag}_report` di Google Spreadsheet yang dituju.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Sequence

import gspread

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.core.cli import (
    print_banner,
    print_error,
    print_info,
    print_section,
    print_success,
    print_warn,
    prompt_bool,
    prompt_str_list,
    prompt_text,
)
from scripts.core.config import get_gsheet_config
from scripts.core.db_client import get_db_connection
from scripts.core.gsheet_client import get_google_sheet_client

DEFAULT_RELEASE_TAG = "2026-11-12"
DEFAULT_PLATFORMS = [
    "FE-Playwright",
    "FE-Robotframework",
    "BE-Playwright",
    "BE-Robotframework",
]
DEFAULT_SPREADSHEET_NAME = "Worksheet Regression - Q4 2026 - Robot Framework & Playwright"


def fetch_regression_data(
    conn: Any,
    release_tag: str,
    platform_names: Sequence[str],
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Menjalankan query SQL dengan parameter release_tag dan platform_names."""
    print_info(
        f"Menjalankan query untuk release_tag='{release_tag}' dan platforms={list(platform_names)}..."
    )

    placeholders = ", ".join(["%s"] * len(platform_names))
    query = f"""
WITH LatestResults AS (
  SELECT
    tr_tc_test_id,
    tr_env_name,
    tr_test_result,
    tr_release_tag,
    tr_platform_name,
    tr_update_at,
    tr_time_execution,
    tr_error_message,
    tr_ec_name,
    tr_ec_name_main,
    ROW_NUMBER() OVER (
      PARTITION BY tr_tc_test_id, tr_release_tag 
      ORDER BY tr_update_at DESC
    ) AS rn
  FROM test_result
)
SELECT
  tc.tc_test_id,
  tc.tc_name,
  tc.tc_fp_name,
  tc.tc_tag,
  lr.tr_platform_name,
  lr.tr_test_result,
  lr.tr_release_tag,
  lr.tr_time_execution,
  lr.tr_error_message,
  lr.tr_ec_name,
  lr.tr_ec_name_main
FROM LatestResults lr
JOIN test_case tc ON lr.tr_tc_test_id = tc.tc_test_id
WHERE lr.rn = 1
  AND lr.tr_release_tag = %s
  AND lr.tr_platform_name IN ({placeholders});
"""
    params = [release_tag] + list(platform_names)

    with conn.cursor() as cursor:
        cursor.execute(query, params)
        headers = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        print_success(f"Query berhasil dieksekusi. Ditemukan {len(rows)} baris data.")
        return headers, rows


def format_cell_value(val: Any) -> Any:
    """Format nilai sel agar valid untuk serialisasi JSON gspread."""
    if val is None:
        return ""
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        return str(val)
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return val


def export_to_google_sheet(
    client: gspread.Client,
    spreadsheet_name: str,
    target_sheet_name: str,
    headers: list[str],
    rows: Sequence[Sequence[Any]],
) -> bool:
    """Membuat atau memperbarui sheet target dan menulis seluruh data ke Google Sheet."""
    print_section(f"Ekspor Data ke Google Spreadsheet: '{spreadsheet_name}'")

    try:
        spreadsheet = client.open(spreadsheet_name)
    except Exception as e:
        client_email = get_gsheet_config().get(
            "client_email", "YOUR_SERVICE_ACCOUNT_EMAIL@YOUR_PROJECT_ID.iam.gserviceaccount.com"
        )
        print_error(f"Gagal membuka spreadsheet '{spreadsheet_name}': {e}")
        print_info(f"Pastikan Anda telah membagikan akses Editor spreadsheet ke: {client_email}")
        return False

    formatted_rows = [[format_cell_value(cell) for cell in row] for row in rows]
    all_data = [headers] + formatted_rows

    total_rows = len(all_data)
    total_cols = len(headers)

    try:
        worksheet = spreadsheet.worksheet(target_sheet_name)
        print_info(f"Worksheet '{target_sheet_name}' sudah ada. Membersihkan data lama...")
        worksheet.clear()

        if worksheet.row_count < total_rows:
            worksheet.add_rows(total_rows - worksheet.row_count + 10)
        if worksheet.col_count < total_cols:
            worksheet.add_cols(total_cols - worksheet.col_count + 2)
    except gspread.exceptions.WorksheetNotFound:
        print_info(f"Membuat worksheet baru: '{target_sheet_name}'...")
        initial_rows = max(total_rows + 20, 100)
        initial_cols = max(total_cols + 5, 20)
        worksheet = spreadsheet.add_worksheet(
            title=target_sheet_name,
            rows=initial_rows,
            cols=initial_cols,
        )

    print_info(
        f"Mengirim {total_rows} baris data (termasuk header) ke sheet '{target_sheet_name}'..."
    )
    try:
        worksheet.update(values=all_data, range_name="A1")
    except TypeError:
        worksheet.update("A1", all_data)

    print_success(
        f"Data berhasil ditulis ke sheet '{target_sheet_name}' pada spreadsheet '{spreadsheet_name}'."
    )
    return True


def execute(
    release_tag: str = DEFAULT_RELEASE_TAG,
    platform_names: Sequence[str] = DEFAULT_PLATFORMS,
    spreadsheet_name: str = DEFAULT_SPREADSHEET_NAME,
    target_sheet_name: str | None = None,
) -> int:
    """Jalankan alur Manual Regression Preparation."""
    sheet_name = target_sheet_name or f"{release_tag}_report"

    print_section("Ringkasan Eksekusi")
    print(f"  Release Tag      : {release_tag}")
    print(f"  Platforms        : {list(platform_names)}")
    print(f"  Spreadsheet Name : {spreadsheet_name}")
    print(f"  Target Sheet     : {sheet_name}")

    conn = get_db_connection()
    if not conn:
        return 1

    try:
        headers, rows = fetch_regression_data(conn, release_tag, platform_names)
    except Exception as e:
        print_error(f"Terjadi kesalahan saat mengambil data dari database: {e}")
        return 1
    finally:
        conn.close()
        print_info("Koneksi database ditutup.")

    if not rows:
        print_warn(
            "Tidak ada baris data yang ditemukan untuk parameter tersebut. "
            "Pembuatan sheet tetap dilanjutkan (hanya baris header)."
        )

    client = get_google_sheet_client()
    if not client:
        return 1

    ok = export_to_google_sheet(client, spreadsheet_name, sheet_name, headers, rows)
    return 0 if ok else 1


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Manual Regression Preparation."""
    print_banner(
        "MANUAL REGRESSION PREPARATION",
        "Query hasil test dari DB PostgreSQL Reporting dan ekspor ke Google Spreadsheet",
    )

    release_tag = prompt_text("Masukkan Release Tag (contoh: 2026-11-12)", default=DEFAULT_RELEASE_TAG)
    platform_names = prompt_str_list(
        "Masukkan daftar Platform Name",
        default_list=DEFAULT_PLATFORMS,
    )
    spreadsheet_name = prompt_text(
        "Masukkan Nama Google Spreadsheet",
        default=DEFAULT_SPREADSHEET_NAME,
    )
    default_sheet = f"{release_tag}_report"
    target_sheet_name = prompt_text(
        "Masukkan Nama Worksheet / Tab tujuan",
        default=default_sheet,
    )

    print_section("Konfirmasi Parameter")
    print(f"  Release Tag      : {release_tag}")
    print(f"  Platforms        : {', '.join(platform_names)}")
    print(f"  Spreadsheet Name : {spreadsheet_name}")
    print(f"  Target Sheet     : {target_sheet_name}")

    if not prompt_bool("Lanjutkan query database dan ekspor ke Google Sheet?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(
        release_tag=release_tag,
        platform_names=platform_names,
        spreadsheet_name=spreadsheet_name,
        target_sheet_name=target_sheet_name,
    )


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Query data regression database dan ekspor ke Google Spreadsheet."
    )
    parser.add_argument(
        "--release-tag",
        default=DEFAULT_RELEASE_TAG,
        help=f"Release tag untuk query (default: {DEFAULT_RELEASE_TAG})",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=DEFAULT_PLATFORMS,
        help="Daftar platform name",
    )
    parser.add_argument(
        "--spreadsheet",
        default=DEFAULT_SPREADSHEET_NAME,
        help="Nama Google Spreadsheet",
    )
    parser.add_argument(
        "--sheet-name",
        default=None,
        help="Nama worksheet tujuan (default: {release_tag}_report)",
    )
    args = parser.parse_args()

    return execute(
        release_tag=args.release_tag,
        platform_names=args.platforms,
        spreadsheet_name=args.spreadsheet,
        target_sheet_name=args.sheet_name,
    )


if __name__ == "__main__":
    sys.exit(main())

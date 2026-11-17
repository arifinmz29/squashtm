#!/usr/bin/env python3
"""
Fitur 4: Update Custom Field Test Case dari Google Sheet.

Alur Proses:
  1. Membaca 2 kolom dari Google Sheet (`TEST_CASE_ID` di Kolom A, `VALUE` di Kolom B; Baris 1 dianggap header).
  2. Login ke SquashTM menggunakan credential dari `credential_tools.json`.
  3. Untuk setiap Test Case ID:
     - GET `/api/rest/latest/test-cases/{tc_id}`
     - Menyusun ulang daftar `custom_fields` dengan mengubah nilai pada `target_field_code`
       serta menjaga validitas custom field lainnya.
     - PATCH `/api/rest/latest/test-cases/{tc_id}`
  4. Menyimpan laporan hasil update ke `data_files/csv/report_update_{field}_{timestamp}.csv`.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import os
import sys
import time
from typing import Any

import gspread
import requests

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
    prompt_choice,
    prompt_float,
    prompt_text,
)
from scripts.core.config import CSV_DIR, ensure_data_dirs, get_squash_config
from scripts.core.gsheet_client import get_google_sheet_client
from scripts.core.squash_client import cleanup_cookies, get_squash_session, refresh_xsrf

DEFAULT_SPREADSHEET_NAME = "Worksheet Regression - Q4 2026 - Robot Framework & Playwright"
DEFAULT_SHEET_NAME = "updateField"
DEFAULT_TARGET_FIELD_CODE = "automationStatus"
DEFAULT_DELAY = 0.3

NUMERIC_FIELDS = ["TotalFailed", "TotalPassed", "TotalSkipped", "TotalTests"]
CREATOR_PATH_DRIVER_APPS = (
    "/Genesis/Master Test Case/New Regression Testing/Driver Apps/"
)
CREATOR_PATH_GENESIS_WEB_MIDDLE_MILE = (
    "/Genesis/Master Test Case/New Regression Testing/Genesis Web/Middle Mile/"
)


def is_unset_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed == "" or trimmed == "-"
    return False


def resolve_creator_value(current_value: Any, test_case_path: str | None) -> Any:
    if current_value != "QA Lead":
        return current_value
    path = test_case_path or ""
    if path.startswith(CREATOR_PATH_DRIVER_APPS):
        return "Akbar"
    if path.startswith(CREATOR_PATH_GENESIS_WEB_MIDDLE_MILE):
        return "Fakhrin"
    return "Wildan"


def resolve_prioritization_feature_value(current_value: Any) -> Any:
    if current_value is None or not isinstance(current_value, str):
        return current_value
    normalized = current_value.strip()
    if normalized == "monetery_lost":
        return "Monetery Lost"
    if normalized == "core_feature":
        return "Core Feature"
    return current_value


def resolve_custom_field_value(
    field: dict[str, Any],
    current_data: dict[str, Any],
    target_field_code: str,
    new_target_value: str | None = None,
    dimension_type_default: str = "",
) -> Any:
    code = field.get("code")
    curr_val = field.get("value")

    if code == target_field_code and new_target_value is not None:
        return new_target_value
    if code == "assigneeTesting":
        return curr_val if curr_val is not None else "Unassignee"
    if code == "flowProcess":
        return curr_val if curr_val is not None else "Unassignee"
    if code == "tipe_testCase":
        return curr_val if curr_val is not None else "Unassignee"
    if code == "Dimension_Type":
        return dimension_type_default if is_unset_value(curr_val) else curr_val
    if code == "creator":
        return resolve_creator_value(curr_val, current_data.get("path"))
    if code == "prioritizationFeature":
        return resolve_prioritization_feature_value(curr_val)
    if code in NUMERIC_FIELDS:
        if is_unset_value(curr_val):
            return 0
        try:
            n = float(curr_val)
            return n if n == n and abs(n) != float("inf") else 0
        except (TypeError, ValueError):
            return 0
    return curr_val


def should_include_custom_field(
    field: dict[str, Any],
    target_field_code: str,
    dimension_type_default: str = "",
) -> bool:
    code = field.get("code")
    curr_val = field.get("value")

    always_include_fields = {
        target_field_code,
        "assigneeTesting",
        "flowProcess",
        "tipe_testCase",
        *NUMERIC_FIELDS,
    }
    if dimension_type_default:
        always_include_fields.add("Dimension_Type")

    if code in always_include_fields:
        return True
    if is_unset_value(curr_val):
        return False
    return True


def fetch_updates_from_sheet(
    client: gspread.Client,
    spreadsheet_name: str,
    sheet_name: str,
) -> list[dict[str, str]]:
    """
    Membaca 2 kolom dari Google Sheet: Kolom A = TEST_CASE_ID, Kolom B = VALUE.
    Baris pertama dianggap sebagai header dan dilewati.
    """
    print_info(f"Membaca worksheet '{sheet_name}' dari '{spreadsheet_name}'...")
    try:
        sheet = client.open(spreadsheet_name)
        worksheet = sheet.worksheet(sheet_name)
        all_rows = worksheet.get_all_values()

        updates: list[dict[str, str]] = []
        for i, row in enumerate(all_rows):
            if i == 0:
                continue
            if len(row) >= 2:
                tc_id = str(row[0]).strip()
                val = str(row[1]).strip()
                if tc_id:
                    updates.append({"id": tc_id, "value": val})

        print_success(f"Berhasil memuat {len(updates)} baris data untuk di-update.")
        return updates
    except Exception as e:
        print_error(f"Gagal membaca Google Sheet: {e}")
        return []


def update_single_case(
    session: requests.Session,
    base_url: str,
    tc_id: str,
    target_field_code: str,
    new_value: str,
    dimension_type_default: str = "",
) -> str:
    """Update custom field untuk satu Test Case ID via REST API SquashTM."""
    refresh_xsrf(session)
    url = f"{base_url}/api/rest/latest/test-cases/{tc_id}"

    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200:
            print_warn(f"  [SKIP] Test Case {tc_id} tidak ditemukan atau error (HTTP {resp.status_code})")
            return "NOT_FOUND"

        current_data = resp.json()
        custom_fields = current_data.get("custom_fields", [])
        field_found = any(f.get("code") == target_field_code for f in custom_fields)

        new_custom_fields = []
        for field in custom_fields:
            if not should_include_custom_field(
                field, target_field_code, dimension_type_default
            ):
                continue

            new_custom_fields.append(
                {
                    "code": field.get("code"),
                    "value": resolve_custom_field_value(
                        field,
                        current_data,
                        target_field_code,
                        new_value,
                        dimension_type_default,
                    ),
                }
            )

        if not field_found:
            print_warn(
                f"  [WARN] Field '{target_field_code}' tidak ditemukan pada TC {tc_id}."
            )

        payload = {
            "_type": current_data.get("_type"),
            "custom_fields": new_custom_fields,
        }

        patch_resp = session.patch(url, json=payload, timeout=20)
        if patch_resp.status_code == 200:
            print(f"  [OK] TC {tc_id} updated: {target_field_code} -> {new_value}")
            return "SUCCESS"
        else:
            print_error(
                f"  [ERR] Gagal update TC {tc_id}. HTTP {patch_resp.status_code}: {patch_resp.text[:150]}"
            )
            return "FAILED"
    except Exception as e:
        print_error(f"  [ERR] Exception saat update TC {tc_id}: {e}")
        return "FAILED"


def write_report_csv(target_field_code: str, results: list[dict[str, str]]) -> str:
    """Simpan laporan hasil update custom field ke CSV."""
    os.makedirs(CSV_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"report_update_{target_field_code}_{timestamp}.csv"
    output_path = os.path.join(CSV_DIR, filename)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["TEST_CASE_ID", "VALUE", "STATUS"])
        for row in results:
            writer.writerow([row["id"], row.get("value", ""), row["status"]])

    print_info(f"Laporan disimpan ke: {output_path}")
    return output_path


def execute(
    spreadsheet_name: str = DEFAULT_SPREADSHEET_NAME,
    sheet_name: str = DEFAULT_SHEET_NAME,
    target_field_code: str = DEFAULT_TARGET_FIELD_CODE,
    delay: float = DEFAULT_DELAY,
) -> int:
    """Jalankan alur Update Custom Field Test Case."""
    ensure_data_dirs()
    sq_cfg = get_squash_config()
    dimension_type_default = sq_cfg.get("dimension_type_default", "")

    client = get_google_sheet_client()
    if not client:
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        updates = fetch_updates_from_sheet(client, spreadsheet_name, sheet_name)
        if not updates:
            print_warn("Tidak ada data yang ditemukan untuk di-update.")
            return 0

        print_section(f"Mengupdate {len(updates)} Test Case (Field: {target_field_code})")
        success_count = 0
        fail_count = 0
        report_rows: list[dict[str, str]] = []

        for idx, item in enumerate(updates, start=1):
            tc_id = item["id"]
            val = item["value"]
            print(f"[{idx}/{len(updates)}] Memproses TC {tc_id} -> '{val}'...")
            status = update_single_case(
                session,
                base_url,
                tc_id,
                target_field_code,
                val,
                dimension_type_default,
            )
            report_rows.append({"id": tc_id, "value": val, "status": status})

            if status == "SUCCESS":
                success_count += 1
            else:
                fail_count += 1

            if delay > 0:
                time.sleep(delay)

        write_report_csv(target_field_code, report_rows)

        print_section("Ringkasan Update Custom Field")
        print(f"  Total Diproses : {len(updates)}")
        print(f"  Berhasil       : {success_count}")
        print(f"  Gagal/Skip     : {fail_count}")
        return 0 if fail_count == 0 else 2
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Update Custom Field Test Case."""
    print_banner(
        "UPDATE CUSTOM FIELD TEST CASE",
        "Mengupdate nilai Custom Field Test Case di SquashTM berdasarkan Google Sheet (Kolom A: ID, Kolom B: Value)",
    )

    spreadsheet_name = prompt_text(
        "Masukkan Nama Google Spreadsheet",
        default=DEFAULT_SPREADSHEET_NAME,
    )
    sheet_name = prompt_text(
        "Masukkan Nama Worksheet / Tab",
        default=DEFAULT_SHEET_NAME,
    )

    field_choice = prompt_choice(
        "Pilih Target Custom Field Code yang ingin di-update",
        [
            ("automationStatus", "Status Automation (mis: Done, Can Not Create Automation, None, Maintenance)"),
            ("automationPlatform", "Platform Automation (mis: Robot Framework, Playwright)"),
            ("AssignAutomation", "Assignee Automation (mis: Arifin)"),
            ("sectionRegression", "Section Regression (mis: Section 01, Section 02)"),
            ("Custom", "Input kode custom field lainnya secara manual"),
        ],
        default_index=1,
    )
    if field_choice == "Custom":
        target_field_code = prompt_text(
            "Masukkan kode Custom Field SquashTM",
            default=DEFAULT_TARGET_FIELD_CODE,
        )
    else:
        target_field_code = field_choice

    delay = prompt_float("Delay antar request update dalam detik", default=DEFAULT_DELAY)

    print_section("Konfirmasi Parameter")
    print(f"  Spreadsheet Name  : {spreadsheet_name}")
    print(f"  Worksheet / Tab   : {sheet_name}")
    print(f"  Target Field Code : {target_field_code}")
    print(f"  Delay Request     : {delay}s")

    if not prompt_bool("Lanjutkan proses update custom field?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(
        spreadsheet_name=spreadsheet_name,
        sheet_name=sheet_name,
        target_field_code=target_field_code,
        delay=delay,
    )


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Update SquashTM test case custom field from Google Sheet."
    )
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET_NAME, help="Spreadsheet name")
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET_NAME, help="Worksheet tab name")
    parser.add_argument("--field-code", default=DEFAULT_TARGET_FIELD_CODE, help="Custom field code")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between requests")
    args = parser.parse_args()

    return execute(
        spreadsheet_name=args.spreadsheet,
        sheet_name=args.sheet_name,
        target_field_code=args.field_code,
        delay=args.delay,
    )


if __name__ == "__main__":
    sys.exit(main())

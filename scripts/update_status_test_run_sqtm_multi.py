#!/usr/bin/env python3
"""
Fitur 5: Update Status Test Run Multi-Iteration V1 (Single Target Status / Daftar ID di Kolom A).

Alur Proses:
  1. Autentikasi ke Google Sheets API dan SquashTM menggunakan `credential_tools.json`.
  2. Menentukan daftar Iteration ID dari Campaign ID (via statistics API) atau input manual.
  3. Membaca daftar Test Case ID dari Kolom A pada worksheet Google Sheet (contoh: 'Success Test IDs').
  4. Mengambil Test Plan dari seluruh Iteration dan menyimpannya ke `data_files/csv/test_id_from_iterations_multi.csv`.
  5. Mencocokkan Test Case ID dan melakukan mass-update ke satu status target (`SUCCESS`, `FAILURE`, `UNTESTABLE`, `BLOCKED`).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import Any, Sequence

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
    prompt_int,
    prompt_int_list,
    prompt_text,
)
from scripts.core.config import CSV_DIR, ensure_data_dirs
from scripts.core.gsheet_client import get_google_sheet_client
from scripts.core.squash_client import (
    cleanup_cookies,
    fetch_iteration_ids_for_campaign,
    get_squash_session,
    refresh_xsrf,
    save_iteration_ids_csv,
)

DEFAULT_SPREADSHEET_NAME = "Worksheet Regression - Q4 2026 - Robot Framework & Playwright"
DEFAULT_SHEET_NAME = "Success Test IDs"
DEFAULT_UPDATE_STATUS = "SUCCESS"
DEFAULT_CAMPAIGN_ID = 31558
DEFAULT_ITERATION_IDS = [16540, 16528, 16530, 16531]
DEFAULT_BATCH_SIZE = 20


def fetch_target_ids_from_sheet(
    client: gspread.Client,
    spreadsheet_name: str,
    sheet_name: str,
) -> set[str]:
    """Membaca daftar test_case_id dari Kolom A pada Google Sheet."""
    print_section("Step 1: Membaca Daftar Test Case ID dari Google Sheet")
    print_info(f"Spreadsheet : {spreadsheet_name}")
    print_info(f"Sheet (Tab) : {sheet_name}")

    try:
        sheet = client.open(spreadsheet_name)
        worksheet = sheet.worksheet(sheet_name)
        ids = worksheet.col_values(1)

        clean_ids: set[str] = set()
        for raw_val in ids:
            val = str(raw_val).strip()
            if val.endswith(".0") and val[:-2].isdigit():
                val = val[:-2]
            if val and val.upper() not in ["TEST_CASE_ID", "TC_TEST_ID", "TC_ID", "ID"]:
                clean_ids.add(val)

        print_success(f"Berhasil memuat {len(clean_ids)} Test Case ID unik dari Google Sheet.")
        return clean_ids
    except Exception as e:
        print_error(f"Gagal membaca Google Sheet: {e}")
        return set()


def fetch_iteration_test_plan(
    session: requests.Session,
    base_url: str,
    iteration_id: int,
) -> dict[str, Any] | None:
    """Mengambil test plan dari SquashTM untuk satu iteration."""
    print_info(f"Mengambil Test Plan Iteration {iteration_id}...")
    refresh_xsrf(session)
    url = f"{base_url}/backend/iteration/{iteration_id}/test-plan"

    payload = {
        "gridRequest": 1,
        "size": 2000,
        "page": 0,
        "allowedPageSizes": [10, 25, 50, 100],
        "active": True,
        "fixedPaginationSize": False,
        "showAll": False,
        "sort": [],
        "filterValues": [],
        "scope": [],
        "extendedHighLvlReqScope": True,
        "simplifiedColumnDisplayGridIds": [],
        "gridId": "iteration-test-plan",
        "searchOnMultiColumns": False,
    }

    try:
        resp = session.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            print_error(
                f"Gagal mengambil test plan untuk iteration {iteration_id}. HTTP {resp.status_code}"
            )
            return None
        return resp.json()
    except Exception as e:
        print_error(f"Exception saat mengambil Test Plan iteration {iteration_id}: {e}")
        return None


def process_all_iterations(
    session: requests.Session,
    base_url: str,
    iteration_ids: Sequence[int],
) -> str:
    """Mengambil test plan dari semua iteration dan menyimpannya ke file CSV gabungan."""
    print_section(f"Step 2: Mengambil Test Plan dari {len(iteration_ids)} Iteration")
    os.makedirs(CSV_DIR, exist_ok=True)
    output_csv = os.path.join(CSV_DIR, "test_id_from_iterations_multi.csv")

    all_test_cases: dict[str, list[str]] = {}

    for iteration_id in iteration_ids:
        data = fetch_iteration_test_plan(session, base_url, iteration_id)
        if not data:
            print_warn(f"Melewati iteration {iteration_id} karena gagal diambil.")
            continue

        count = 0
        for row in data.get("dataRows", []):
            raw_tc_id = row.get("data", {}).get("testCaseId")
            plan_id = row.get("id")
            if raw_tc_id and plan_id:
                tc_id = str(raw_tc_id).strip()
                if tc_id.endswith(".0") and tc_id[:-2].isdigit():
                    tc_id = tc_id[:-2]
                all_test_cases.setdefault(tc_id, []).append(str(plan_id))
                count += 1

        print_info(f"  -> Memuat {count} item dari Iteration {iteration_id}")

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["TEST_CASE_ID", "IT_TEST_PLAN_IDS"])
        for tc_id, plan_ids in all_test_cases.items():
            writer.writerow([tc_id, ",".join(plan_ids)])

    print_success(f"Menyimpan {len(all_test_cases)} test case unik ke {output_csv}")
    return output_csv


def update_test_cases(
    session: requests.Session,
    base_url: str,
    target_ids: set[str],
    csv_file: str,
    update_status: str = DEFAULT_UPDATE_STATUS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    """Update status eksekusi pada test plan item yang cocok secara batch."""
    print_section(f"Step 3: Mengupdate Status ke '{update_status}'")

    if not os.path.exists(csv_file):
        print_error(f"File input CSV tidak ditemukan: {csv_file}")
        return

    plan_ids_to_update: list[str] = []
    try:
        with open(csv_file, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    tc_id = row[0].strip()
                    plan_ids_str = row[1].strip()
                    if tc_id in target_ids:
                        plan_ids = [pid.strip() for pid in plan_ids_str.split(",") if pid.strip()]
                        plan_ids_to_update.extend(plan_ids)
    except Exception as e:
        print_error(f"Gagal membaca CSV: {e}")
        return

    print_info(f"Ditemukan {len(plan_ids_to_update)} Test Plan Item yang cocok untuk di-update.")
    if not plan_ids_to_update:
        print_warn("Tidak ada item yang cocok untuk di-update.")
        return

    total_batches = (len(plan_ids_to_update) + batch_size - 1) // batch_size
    for i in range(0, len(plan_ids_to_update), batch_size):
        batch = plan_ids_to_update[i : i + batch_size]
        batch_num = i // batch_size + 1
        ids_string = ",".join(batch)
        refresh_xsrf(session)
        url = f"{base_url}/backend/iteration/test-plan/{ids_string}/mass-update"
        payload = {"executionStatus": update_status}

        try:
            resp = session.post(url, json=payload, timeout=30)
            if resp.status_code in [200, 204]:
                print(
                    f"  [OK] Batch {batch_num}/{total_batches} berhasil di-update ke {update_status} ({len(batch)} item)."
                )
            else:
                print_error(
                    f"  [ERR] Batch {batch_num}/{total_batches} gagal. HTTP {resp.status_code}: {resp.text[:150]}"
                )
        except Exception as e:
            print_error(f"  [ERR] Exception pada Batch {batch_num}/{total_batches}: {e}")

        time.sleep(0.2)

    print_success("Seluruh proses update selesai.")


def execute(
    campaign_id: int | None,
    iteration_ids: Sequence[int],
    spreadsheet_name: str = DEFAULT_SPREADSHEET_NAME,
    sheet_name: str = DEFAULT_SHEET_NAME,
    update_status: str = DEFAULT_UPDATE_STATUS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Jalankan alur Update Status Test Run Multi-Iteration V1."""
    ensure_data_dirs()

    if campaign_id is None and not iteration_ids:
        print_error("Harap tentukan Campaign ID atau daftar Iteration ID.")
        return 1

    client = get_google_sheet_client()
    if not client:
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        if campaign_id is not None:
            resolved_iterations = fetch_iteration_ids_for_campaign(session, campaign_id, base_url)
            if not resolved_iterations:
                print_error("Tidak ada Iteration ID yang diperoleh dari Campaign Statistics.")
                return 1
            save_iteration_ids_csv(resolved_iterations, campaign_id, CSV_DIR)
        else:
            resolved_iterations = list(iteration_ids)

        print_info(f"Memproses {len(resolved_iterations)} Iteration: {resolved_iterations}")

        target_ids = fetch_target_ids_from_sheet(client, spreadsheet_name, sheet_name)
        if not target_ids:
            print_warn("Tidak ada Test Case ID yang ditemukan pada sheet.")
            return 0

        csv_file = process_all_iterations(session, base_url, resolved_iterations)
        update_test_cases(
            session,
            base_url,
            target_ids,
            csv_file,
            update_status=update_status,
            batch_size=batch_size,
        )
        return 0
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Update Status Test Run Multi-Iteration V1."""
    print_banner(
        "UPDATE STATUS TEST RUN MULTI-ITERATION (V1 - SINGLE STATUS)",
        "Update status eksekusi Test Run dari daftar Test Case ID di Kolom A Google Sheet",
    )

    mode = prompt_choice(
        "Pilih cara menentukan Iteration",
        [
            ("campaign", "Berdasarkan Campaign ID (otomatis via Campaign Statistics API)"),
            ("manual", "Input daftar Iteration ID secara manual"),
        ],
        default_index=1,
    )

    campaign_id: int | None = None
    iteration_ids: list[int] = []
    if mode == "campaign":
        campaign_id = prompt_int("Masukkan Campaign ID", default=DEFAULT_CAMPAIGN_ID, required=True)
    else:
        iteration_ids = prompt_int_list(
            "Masukkan daftar Iteration ID",
            default_list=DEFAULT_ITERATION_IDS,
        )

    spreadsheet_name = prompt_text(
        "Masukkan Nama Google Spreadsheet",
        default=DEFAULT_SPREADSHEET_NAME,
    )
    sheet_name = prompt_text(
        "Masukkan Nama Worksheet / Tab (Kolom A berisi Test Case ID)",
        default=DEFAULT_SHEET_NAME,
    )

    update_status = prompt_choice(
        "Pilih Status Eksekusi yang akan diterapkan",
        [
            ("SUCCESS", "Pass / Berhasil"),
            ("FAILURE", "Fail / Gagal"),
            ("UNTESTABLE", "Untestable"),
            ("BLOCKED", "Blocked"),
        ],
        default_index=1,
    )

    batch_size = prompt_int("Masukkan ukuran Batch (Batch Size)", default=DEFAULT_BATCH_SIZE) or 20

    print_section("Konfirmasi Parameter")
    print(f"  Target Mode      : {'Campaign ID = ' + str(campaign_id) if campaign_id else 'Iteration IDs = ' + str(iteration_ids)}")
    print(f"  Spreadsheet Name : {spreadsheet_name}")
    print(f"  Worksheet / Tab  : {sheet_name}")
    print(f"  Target Status    : {update_status}")
    print(f"  Batch Size       : {batch_size}")

    if not prompt_bool("Lanjutkan proses mass-update status?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(
        campaign_id=campaign_id,
        iteration_ids=iteration_ids,
        spreadsheet_name=spreadsheet_name,
        sheet_name=sheet_name,
        update_status=update_status,
        batch_size=batch_size,
    )


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Update SquashTM test run status (V1 single status) across multiple iterations."
    )
    parser.add_argument("--campaign-id", type=int, default=DEFAULT_CAMPAIGN_ID, help="Campaign ID")
    parser.add_argument("--iteration-ids", type=str, default=None, help="Comma-separated iteration IDs")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET_NAME, help="Spreadsheet name")
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET_NAME, help="Worksheet tab name")
    parser.add_argument("--status", default=DEFAULT_UPDATE_STATUS, help="Execution status (SUCCESS, FAILURE, etc.)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size")
    args = parser.parse_args()

    iter_ids: list[int] = []
    camp_id: int | None = args.campaign_id
    if args.iteration_ids:
        iter_ids = [int(x.strip()) for x in args.iteration_ids.split(",") if x.strip().isdigit()]
        camp_id = None

    return execute(
        campaign_id=camp_id,
        iteration_ids=iter_ids,
        spreadsheet_name=args.spreadsheet,
        sheet_name=args.sheet_name,
        update_status=args.status,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    sys.exit(main())

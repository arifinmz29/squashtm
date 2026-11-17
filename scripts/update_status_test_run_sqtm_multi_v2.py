#!/usr/bin/env python3
"""
Fitur 6: Update Status Test Run Multi-Iteration V2 (Dynamic Mapping dari Kolom Header Google Sheet).

Alur Proses:
  1. Autentikasi ke Google Sheets API dan SquashTM menggunakan `credential_tools.json`.
  2. Menentukan daftar Iteration ID dari Campaign ID (via statistics API) atau input manual.
  3. Membaca kolom `HEADER_TEST_ID` (mis: `tc_test_id`) dan `HEADER_STATUS` (mis: `Status Rerun` atau `tr_test_result`)
     dari Google Sheet, mendeteksi distribusi nilai, dan memetakan status ke `SUCCESS` atau `FAILURE`.
  4. Mengambil Test Plan dari seluruh Iteration dan menyimpannya ke `data_files/csv/test_id_from_iterations_multi_v2.csv`.
  5. Melakukan mass-update secara berkelompok (`SUCCESS` dan `FAILURE`) ke SquashTM.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

DEFAULT_CAMPAIGN_ID = 47022
DEFAULT_ITERATION_IDS = [16540, 16528, 16530, 16531]
DEFAULT_SPREADSHEET_NAME = "Worksheet Regression - Q4 2026 - Robot Framework & Playwright"
DEFAULT_SHEET_NAME = "Run_Manual_20261008"
DEFAULT_HEADER_TEST_ID = "tc_test_id"
DEFAULT_HEADER_STATUS = "Status Rerun"
DEFAULT_SKIP_EMPTY_STATUS = True
DEFAULT_BATCH_SIZE = 20

PASS_STATUS_VALUES = [
    "PASS",
    "Pass AT Without Adjustment",
    "Pass AT With Adjustment",
    "Pass Manual Testing",
]

EMPTY_STATUS_VALUES = {"", "#N/A", "N/A", "-", "SKIP", "NONE", "NULL"}


def map_status(raw_status: str) -> str:
    """
    Memetakan nilai status dari Google Sheet ke executionStatus SquashTM:
    - PASS / Pass AT Without Adjustment / Pass AT With Adjustment / Pass Manual Testing -> SUCCESS
    - Lainnya -> FAILURE
    """
    if not raw_status:
        return "FAILURE"

    val_clean = raw_status.strip().lower()
    for pass_val in PASS_STATUS_VALUES:
        if val_clean == pass_val.strip().lower():
            return "SUCCESS"

    if val_clean in ["success", "pass", "passed"]:
        return "SUCCESS"

    return "FAILURE"


def clean_tc_id(val: str) -> str:
    """Membersihkan string Test Case ID dan menghapus akhiran .0 jika ada."""
    s = str(val).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def fetch_test_ids_and_statuses(
    client: gspread.Client,
    spreadsheet_name: str,
    sheet_name: str,
    header_test_id: str,
    header_status: str,
    skip_empty_status: bool = True,
) -> dict[str, str]:
    """Membaca kolom Test Case ID dan Status dari Google Sheet dan mengembalikan {tc_id: mapped_status}."""
    print_section("Step 1: Membaca Test ID dan Status dari Google Sheet")
    print(f"  Spreadsheet  : {spreadsheet_name}")
    print(f"  Sheet (Tab)  : {sheet_name}")
    print(f"  Header TC ID : {header_test_id}")
    print(f"  Header Status: {header_status}")

    try:
        sheet = client.open(spreadsheet_name)
        worksheet = sheet.worksheet(sheet_name)
        all_values = worksheet.get_all_values()

        if not all_values:
            print_error(f"Sheet '{sheet_name}' kosong.")
            return {}

        headers = [str(h).strip() for h in all_values[0]]
        print_info(f"Total kolom ditemukan: {len(headers)}")

        header_id_clean = header_test_id.strip().lower()
        header_status_clean = header_status.strip().lower()

        matching_id_cols = [
            idx for idx, h in enumerate(headers) if h.strip().lower() == header_id_clean
        ]
        matching_status_cols = [
            idx for idx, h in enumerate(headers) if h.strip().lower() == header_status_clean
        ]

        if not matching_id_cols:
            print_error(f"Header Test ID '{header_test_id}' tidak ditemukan pada sheet '{sheet_name}'.")
            print_info(f"Header yang tersedia: {headers}")
            return {}

        if not matching_status_cols:
            print_error(f"Header Status '{header_status}' tidak ditemukan pada sheet '{sheet_name}'.")
            print_info(f"Header yang tersedia: {headers}")
            return {}

        id_col_idx = matching_id_cols[0]

        if len(matching_status_cols) > 1:
            print_warn(
                f"Ditemukan {len(matching_status_cols)} kolom dengan nama header '{header_status}':"
            )
            col_data_counts = []
            for c_idx in matching_status_cols:
                filled = sum(
                    1
                    for r in all_values[1:]
                    if c_idx < len(r)
                    and r[c_idx].strip()
                    and r[c_idx].strip().upper() not in EMPTY_STATUS_VALUES
                )
                col_data_counts.append((c_idx, filled))
                print(f"  - Kolom {c_idx + 1} ('{headers[c_idx]}'): {filled} baris terisi")

            best_col_idx, max_filled = max(col_data_counts, key=lambda x: x[1])
            status_col_idx = best_col_idx
            print_info(
                f"Otomatis memilih Kolom {status_col_idx + 1} dengan {max_filled} baris terisi."
            )
        else:
            status_col_idx = matching_status_cols[0]

        print_info(f"Menggunakan '{header_test_id}' pada Kolom {id_col_idx + 1}")
        print_info(f"Menggunakan '{header_status}' pada Kolom {status_col_idx + 1}")

        status_value_counts: Counter[str] = Counter()
        for r in all_values[1:]:
            val = r[status_col_idx].strip() if status_col_idx < len(r) else ""
            if not val or val.upper() in EMPTY_STATUS_VALUES:
                val_disp = "<EMPTY / N/A>"
            else:
                val_disp = val
            status_value_counts[val_disp] += 1

        print_info(f"Distribusi nilai pada '{header_status}' (Kolom {status_col_idx + 1}):")
        for val, count in status_value_counts.most_common():
            mapped = "SKIPPED" if (val == "<EMPTY / N/A>" and skip_empty_status) else map_status(val)
            print(f"  - '{val}': {count} baris (Dipetakan ke: {mapped})")

        tc_status_map: dict[str, str] = {}
        skipped_empty_count = 0
        total_rows = len(all_values) - 1

        for row in all_values[1:]:
            raw_id = row[id_col_idx].strip() if id_col_idx < len(row) else ""
            raw_status = row[status_col_idx].strip() if status_col_idx < len(row) else ""

            tc_id = clean_tc_id(raw_id)
            if not tc_id or tc_id.lower() in [header_id_clean, "test_case_id", "tc_id", "id"]:
                continue

            if skip_empty_status and (not raw_status or raw_status.upper() in EMPTY_STATUS_VALUES):
                skipped_empty_count += 1
                continue

            tc_status_map[tc_id] = map_status(raw_status)

        success_count = sum(1 for s in tc_status_map.values() if s == "SUCCESS")
        failure_count = sum(1 for s in tc_status_map.values() if s == "FAILURE")

        print_section(f"Ringkasan Pembacaan Sheet (dari {total_rows} baris data)")
        print(f"  - Total Test Case akan di-update : {len(tc_status_map)}")
        print(f"  - Dipetakan ke SUCCESS           : {success_count}")
        print(f"  - Dipetakan ke FAILURE           : {failure_count}")
        if skipped_empty_count > 0:
            print(f"  - Dilewati (status kosong/N/A)   : {skipped_empty_count}")

        return tc_status_map
    except Exception as e:
        print_error(f"Gagal membaca Google Sheet: {e}")
        return {}


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
    output_csv = os.path.join(CSV_DIR, "test_id_from_iterations_multi_v2.csv")

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
                tc_id = clean_tc_id(str(raw_tc_id))
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
    tc_status_map: dict[str, str],
    csv_file: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    """Update status eksekusi di SquashTM dikelompokkan berdasarkan status (SUCCESS / FAILURE)."""
    print_section("Step 3: Mengupdate Status di SquashTM")

    if not os.path.exists(csv_file):
        print_error(f"File input CSV tidak ditemukan: {csv_file}")
        return

    plan_ids_by_status: dict[str, list[str]] = {
        "SUCCESS": [],
        "FAILURE": [],
    }
    matched_tc_ids: set[str] = set()

    try:
        with open(csv_file, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    tc_id = clean_tc_id(row[0])
                    plan_ids_str = row[1].strip()
                    if tc_id in tc_status_map:
                        matched_tc_ids.add(tc_id)
                        target_status = tc_status_map[tc_id]
                        plan_ids = [pid.strip() for pid in plan_ids_str.split(",") if pid.strip()]
                        plan_ids_by_status.setdefault(target_status, []).extend(plan_ids)
    except Exception as e:
        print_error(f"Gagal membaca CSV: {e}")
        return

    unmatched_tc_ids = set(tc_status_map.keys()) - matched_tc_ids
    print_info(f"Target TC ID dari Sheet        : {len(tc_status_map)}")
    print_info(f"TC ID cocok di Iteration Plans : {len(matched_tc_ids)}")
    if unmatched_tc_ids:
        print_warn(
            f"{len(unmatched_tc_ids)} TC ID dari sheet tidak ditemukan pada Iteration Test Plan "
            f"(contoh: {list(unmatched_tc_ids)[:10]})"
        )

    total_plan_items = sum(len(ids) for ids in plan_ids_by_status.values())
    if total_plan_items == 0:
        print_warn("Tidak ada Test Plan Item yang cocok untuk di-update.")
        return

    for status, plan_ids in plan_ids_by_status.items():
        if not plan_ids:
            print_info(f"Tidak ada item untuk di-update ke status '{status}'.")
            continue

        total_batches = (len(plan_ids) + batch_size - 1) // batch_size
        print_section(
            f"Mengupdate {len(plan_ids)} item ke status '{status}' ({total_batches} batch)"
        )

        for i in range(0, len(plan_ids), batch_size):
            batch = plan_ids[i : i + batch_size]
            batch_num = i // batch_size + 1
            ids_string = ",".join(batch)
            refresh_xsrf(session)
            url = f"{base_url}/backend/iteration/test-plan/{ids_string}/mass-update"
            payload = {"executionStatus": status}

            try:
                resp = session.post(url, json=payload, timeout=30)
                if resp.status_code in [200, 204]:
                    print(
                        f"  [OK] Batch {batch_num}/{total_batches} updated to {status} ({len(batch)} items)."
                    )
                else:
                    print_error(
                        f"  [ERR] Batch {batch_num}/{total_batches} gagal. HTTP {resp.status_code}: {resp.text[:150]}"
                    )
            except Exception as e:
                print_error(f"  [ERR] Batch {batch_num}/{total_batches} exception: {e}")

            time.sleep(0.2)

    print_success("Seluruh proses update selesai.")


def execute(
    campaign_id: int | None,
    iteration_ids: Sequence[int],
    spreadsheet_name: str = DEFAULT_SPREADSHEET_NAME,
    sheet_name: str = DEFAULT_SHEET_NAME,
    header_test_id: str = DEFAULT_HEADER_TEST_ID,
    header_status: str = DEFAULT_HEADER_STATUS,
    skip_empty_status: bool = DEFAULT_SKIP_EMPTY_STATUS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Jalankan alur Update Status Test Run Multi-Iteration V2."""
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

        tc_status_map = fetch_test_ids_and_statuses(
            client=client,
            spreadsheet_name=spreadsheet_name,
            sheet_name=sheet_name,
            header_test_id=header_test_id,
            header_status=header_status,
            skip_empty_status=skip_empty_status,
        )
        if not tc_status_map:
            print_warn("Tidak ada test case yang siap di-update.")
            return 0

        csv_file = process_all_iterations(session, base_url, resolved_iterations)
        update_test_cases(
            session,
            base_url,
            tc_status_map,
            csv_file,
            batch_size=batch_size,
        )
        return 0
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Update Status Test Run Multi-Iteration V2."""
    print_banner(
        "UPDATE STATUS TEST RUN MULTI-ITERATION (V2 - DYNAMIC MAPPING)",
        "Update status eksekusi Test Run berdasarkan pemetaan kolom ID & Status di Google Sheet",
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
        "Masukkan Nama Worksheet / Tab (mis: Run_Manual_20261008 atau 2026-11-12_report)",
        default=DEFAULT_SHEET_NAME,
    )
    header_test_id = prompt_text(
        "Masukkan Nama Header Kolom Test Case ID",
        default=DEFAULT_HEADER_TEST_ID,
    )

    status_col_choice = prompt_choice(
        "Pilih Nama Header Kolom Status pada Google Sheet",
        [
            ("Status Rerun", "Kolom hasil rerun manual (Pass AT Without Adjustment, Pass Manual Testing, dll.)"),
            ("tr_test_result", "Kolom hasil reporting automation (PASS / FAIL)"),
            ("Custom", "Input nama header kolom status lainnya secara manual"),
        ],
        default_index=1,
    )
    if status_col_choice == "Custom":
        header_status = prompt_text("Masukkan Nama Header Kolom Status", default=DEFAULT_HEADER_STATUS)
    else:
        header_status = status_col_choice

    skip_empty = prompt_bool(
        "Skip baris yang kolom statusnya kosong / N/A? (Jika Tidak, akan dianggap FAILURE)",
        default=DEFAULT_SKIP_EMPTY_STATUS,
    )
    batch_size = prompt_int("Masukkan ukuran Batch (Batch Size)", default=DEFAULT_BATCH_SIZE) or 20

    print_section("Konfirmasi Parameter")
    print(f"  Target Mode        : {'Campaign ID = ' + str(campaign_id) if campaign_id else 'Iteration IDs = ' + str(iteration_ids)}")
    print(f"  Spreadsheet Name   : {spreadsheet_name}")
    print(f"  Worksheet / Tab    : {sheet_name}")
    print(f"  Header Kolom TC ID : {header_test_id}")
    print(f"  Header Kolom Status: {header_status}")
    print(f"  Skip Status Kosong : {skip_empty}")
    print(f"  Batch Size         : {batch_size}")

    if not prompt_bool("Lanjutkan proses mass-update status (V2)?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(
        campaign_id=campaign_id,
        iteration_ids=iteration_ids,
        spreadsheet_name=spreadsheet_name,
        sheet_name=sheet_name,
        header_test_id=header_test_id,
        header_status=header_status,
        skip_empty_status=skip_empty,
        batch_size=batch_size,
    )


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Update SquashTM test run statuses across multiple iterations (V2 dynamic mapping)."
    )
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET_NAME, help="Spreadsheet name")
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET_NAME, help="Worksheet tab name")
    parser.add_argument("--header-test-id", default=DEFAULT_HEADER_TEST_ID, help="Test ID header")
    parser.add_argument("--header-status", default=DEFAULT_HEADER_STATUS, help="Status header")
    parser.add_argument("--campaign-id", type=int, default=DEFAULT_CAMPAIGN_ID, help="Campaign ID")
    parser.add_argument("--iteration-ids", type=str, default=None, help="Comma-separated iteration IDs")
    parser.add_argument(
        "--skip-empty-status",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_SKIP_EMPTY_STATUS,
        help="Skip rows with empty/N/A status",
    )
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
        header_test_id=args.header_test_id,
        header_status=args.header_status,
        skip_empty_status=args.skip_empty_status,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    sys.exit(main())

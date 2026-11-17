#!/usr/bin/env python3
"""
Fitur 1: Delete Test Cases from SquashTM Test Run (Iteration Test Plan) by TestCaseID.

Alur Proses:
  1. Login ke SquashTM menggunakan credential dari `credential_tools.json`.
  2. Mengambil daftar ITERATION_ID dari Campaign ID (via campaign-tree atau fallback ke statistics)
     atau menggunakan daftar Iteration ID yang diinput langsung.
  3. Mengambil daftar testPlanItemId dari setiap Iteration (`POST /backend/iteration/{id}/test-plan`),
     lalu mencocokkan `testCaseId` dengan daftar target (dari CSV atau input manual).
  4. Menghapus test case dari iteration (`DELETE /backend/iteration/{id}/test-plan/{testPlanItemId}`).
  5. Menyimpan laporan hasil penghapusan ke `data_files/csv/delete_result_{timestamp}.csv`.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import os
import re
import sys
import time
from typing import Any

import requests

# Pastikan PROJECT_ROOT terdaftar di sys.path jika dieksekusi langsung
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.core.cli import (
    parse_tc_id_list,
    print_banner,
    print_error,
    print_info,
    print_section,
    print_success,
    print_warn,
    prompt_bool,
    prompt_choice,
    prompt_float,
    prompt_int,
    prompt_int_list,
    prompt_text,
)
from scripts.core.config import CSV_DIR, ensure_data_dirs
from scripts.core.squash_client import (
    cleanup_cookies,
    fetch_iteration_ids_from_campaign_tree,
    get_origin,
    get_squash_session,
    get_squash_workspace_url,
    refresh_xsrf,
)

DEFAULT_CSV_PATH = os.path.join(CSV_DIR, "delete_testrun.csv")
DEFAULT_DELAY = 0.15


def fetch_iteration_test_plan(
    session: requests.Session,
    base_url: str,
    iteration_id: int,
) -> list[dict[str, str]]:
    """
    POST /backend/iteration/{iteration_id}/test-plan (paginated).
    Returns list of dicts: [{"plan_item_id": "...", "test_case_id": "...", "name": "..."}, ...]
    """
    print_info(f"Mengambil Test Plan untuk Iteration {iteration_id}...")
    refresh_xsrf(session)

    url = f"{base_url}/backend/iteration/{iteration_id}/test-plan"
    workspace_url = get_squash_workspace_url(base_url)
    origin = get_origin(base_url)

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": origin,
        "referer": f"{workspace_url}/campaign-workspace/iteration/{iteration_id}/test-plan?anchor=plan-exec",
        "x-xsrf-token": session.cookies.get("XSRF-TOKEN", ""),
    }

    all_items: list[dict[str, str]] = []
    page = 0
    page_size = 2000

    while True:
        payload = {
            "gridRequest": 1,
            "size": page_size,
            "page": page,
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
            r = session.post(url, json=payload, headers=headers, timeout=30)
            if r.status_code != 200:
                print_error(f"Gagal mengambil test plan (page {page}): HTTP {r.status_code}")
                break

            data = r.json()
            rows = data.get("dataRows", [])
            if not rows:
                break

            for row in rows:
                row_data = row.get("data", {})
                tc_id = str(row_data.get("testCaseId") or "").strip()
                plan_id = str(row.get("id") or row_data.get("testPlanItemId") or "").strip()
                name = str(row_data.get("name") or row_data.get("testCaseName") or "").strip()

                if plan_id and tc_id:
                    all_items.append(
                        {
                            "plan_item_id": plan_id,
                            "test_case_id": tc_id,
                            "name": name,
                        }
                    )

            total_count = data.get("count") or data.get("total")
            if total_count and len(all_items) >= int(total_count):
                break

            if len(rows) < page_size:
                break

            page += 1
        except Exception as e:
            print_error(f"Exception saat mengambil test plan iteration {iteration_id}: {e}")
            break

    print_info(f"  -> Berhasil memuat {len(all_items)} item test plan dari Iteration {iteration_id}.")
    return all_items


def read_test_case_ids_from_csv(csv_path: str) -> list[str]:
    """
    Membaca daftar Test Case ID dari file CSV.
    Mendukung CSV dengan header ('test_case_id', 'testcaseid', 'tc_id', 'id')
    maupun 1 kolom tanpa header.
    """
    print_info(f"Membaca Test Case ID dari file CSV: {csv_path}")

    if not os.path.exists(csv_path):
        print_error(f"File CSV tidak ditemukan: {csv_path}")
        return []

    test_case_ids: list[str] = []
    seen: set[str] = set()

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header_checked = False
        target_col_idx = 0

        for row in reader:
            if not row or not any(field.strip() for field in row):
                continue

            if not header_checked:
                header_checked = True
                found_header = False
                for idx, col_name in enumerate(row):
                    col_clean = col_name.strip().lower().replace(" ", "_").replace("-", "_")
                    if col_clean in ["test_case_id", "testcaseid", "testcase_id", "tc_id", "id"]:
                        target_col_idx = idx
                        found_header = True
                        break
                if found_header:
                    continue

            if len(row) > target_col_idx:
                raw_val = row[target_col_idx].strip()
                match = re.search(r"\d+", raw_val)
                if match:
                    tc_id = match.group()
                    if tc_id not in seen:
                        seen.add(tc_id)
                        test_case_ids.append(tc_id)

    print_success(f"Ditemukan {len(test_case_ids)} Test Case ID unik untuk diproses.")
    return test_case_ids


def delete_test_plan_item(
    session: requests.Session,
    base_url: str,
    iteration_id: int,
    plan_item_id: str,
    dry_run: bool = False,
) -> tuple[bool, int, str]:
    """DELETE /backend/iteration/{iteration_id}/test-plan/{plan_item_id}."""
    if dry_run:
        return True, 200, "DRY-RUN (Simulasi, tidak dihapus)"

    refresh_xsrf(session)
    url = f"{base_url}/backend/iteration/{iteration_id}/test-plan/{plan_item_id}"
    workspace_url = get_squash_workspace_url(base_url)
    origin = get_origin(base_url)

    headers = {
        "accept": "application/json, text/plain, */*",
        "origin": origin,
        "referer": f"{workspace_url}/campaign-workspace/iteration/{iteration_id}/test-plan?anchor=plan-exec",
        "x-xsrf-token": session.cookies.get("XSRF-TOKEN", ""),
    }

    try:
        r = session.delete(url, headers=headers, timeout=20)
        if r.status_code in [200, 204]:
            return True, r.status_code, "SUCCESS"
        return False, r.status_code, f"HTTP {r.status_code} - {r.text[:100]}"
    except Exception as e:
        return False, 500, f"Exception: {e}"


def execute(
    campaign_id: int | None,
    iteration_ids: list[int],
    target_tc_ids: list[str],
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY,
) -> int:
    """Jalankan proses penghapusan Test Case dari Test Run (Iteration)."""
    ensure_data_dirs()

    if not target_tc_ids:
        print_error("Daftar Target Test Case ID kosong. Proses dibatalkan.")
        return 1

    if campaign_id is None and not iteration_ids:
        print_error("Harap berikan Campaign ID atau daftar Iteration ID.")
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        # 2. Resolve Iteration IDs
        resolved_iteration_ids: list[int] = []
        if campaign_id is not None:
            print_section(f"Mengambil Iteration ID dari Campaign {campaign_id}")
            resolved_iteration_ids = fetch_iteration_ids_from_campaign_tree(
                session, campaign_id, base_url
            )
            if not resolved_iteration_ids:
                print_error(f"Tidak ditemukan iteration untuk Campaign {campaign_id}.")
                return 1
        else:
            resolved_iteration_ids = list(iteration_ids)

        print_info(f"Memproses {len(resolved_iteration_ids)} Iteration: {resolved_iteration_ids}")
        print_info(f"Target Test Case ID yang dicari: {len(target_tc_ids)} ID")

        target_tc_set = set(target_tc_ids)
        matched_items: list[dict[str, Any]] = []

        # 3. Query test plan per iteration
        print_section("Mencari Test Plan Item yang Cocok")
        for iid in resolved_iteration_ids:
            plan_items = fetch_iteration_test_plan(session, base_url, iid)
            matched_in_iter = 0
            for item in plan_items:
                if item["test_case_id"] in target_tc_set:
                    matched_items.append(
                        {
                            "iteration_id": iid,
                            "plan_item_id": item["plan_item_id"],
                            "test_case_id": item["test_case_id"],
                            "name": item.get("name", ""),
                        }
                    )
                    matched_in_iter += 1
            print_info(f"  -> Cocok {matched_in_iter} item di Iteration {iid}.")

        print_info(f"Total Test Plan Item yang cocok untuk dihapus: {len(matched_items)}")
        if not matched_items:
            print_warn("Tidak ada Test Case ID yang ditemukan pada Test Plan Iteration tersebut.")
            return 0

        # 4. Perform Deletion
        print_section("Menghapus Item dari Iteration Test Plan")
        if dry_run:
            print_warn("MODE DRY-RUN AKTIF: Tidak ada item yang benar-benar dihapus di SquashTM.")

        results_report: list[dict[str, Any]] = []
        success_count = 0
        fail_count = 0

        for idx, item in enumerate(matched_items, start=1):
            iid = item["iteration_id"]
            plan_id = item["plan_item_id"]
            tc_id = item["test_case_id"]
            name = item.get("name", "")

            prefix = f"[{idx}/{len(matched_items)}]"
            print(
                f"{prefix} Iteration {iid} | TC: {tc_id} | PlanItemId: {plan_id} ({name}) ...",
                end=" ",
                flush=True,
            )

            ok, status_code, message = delete_test_plan_item(
                session, base_url, iid, plan_id, dry_run=dry_run
            )

            if ok:
                success_count += 1
                print(f"[OK] {message}")
            else:
                fail_count += 1
                print(f"[FAIL] {message}")

            results_report.append(
                {
                    "iteration_id": iid,
                    "test_case_id": tc_id,
                    "test_plan_item_id": plan_id,
                    "name": name,
                    "status": "SUCCESS" if ok else "FAILED",
                    "http_code": status_code,
                    "message": message,
                }
            )

            if not dry_run and delay > 0:
                time.sleep(delay)

        # 5. Save Report CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filepath = os.path.join(CSV_DIR, f"delete_result_{timestamp}.csv")

        with open(report_filepath, mode="w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "iteration_id",
                "test_case_id",
                "test_plan_item_id",
                "name",
                "status",
                "http_code",
                "message",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results_report)

        found_tc_ids = {item["test_case_id"] for item in matched_items}
        not_found_tc_ids = target_tc_set - found_tc_ids

        print("\n==================================================")
        print("                RINGKASAN PENGHAPUSAN             ")
        print("==================================================")
        print(f"Total Target Test Cases       : {len(target_tc_set)}")
        print(f"Total Test Plan Items Cocok   : {len(matched_items)}")
        print(f"Berhasil Dihapus              : {success_count}")
        print(f"Gagal Dihapus                 : {fail_count}")
        if not_found_tc_ids:
            print(
                f"TC Tidak Ditemukan di Plan    : {len(not_found_tc_ids)} "
                f"(contoh: {sorted(list(not_found_tc_ids))[:10]})"
            )
        print(f"Laporan CSV Disimpan di       : {report_filepath}")
        print("==================================================\n")

        return 0 if fail_count == 0 else 2
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Delete Test Cases dari Test Run."""
    print_banner(
        "DELETE TEST CASES FROM TEST RUN (ITERATION)",
        "Menghapus Test Case dari Iteration Test Plan berdasarkan Test Case ID",
    )

    # 1. Pilih sumber Iteration (Campaign ID vs Manual Iteration IDs)
    target_mode = prompt_choice(
        "Pilih cara menentukan target Iteration",
        [
            ("campaign", "Berdasarkan Campaign ID (otomatis cari semua Iteration di bawah Campaign)"),
            ("iteration", "Input daftar Iteration ID secara manual"),
        ],
        default_index=1,
    )

    campaign_id: int | None = None
    iteration_ids: list[int] = []
    if target_mode == "campaign":
        campaign_id = prompt_int("Masukkan Campaign ID SquashTM (contoh: 42683)", required=True)
    else:
        iteration_ids = prompt_int_list("Masukkan Iteration ID (contoh: 62649, 62650)")

    # 2. Pilih sumber daftar Test Case ID
    tc_source = prompt_choice(
        "Pilih sumber daftar Test Case ID yang akan dihapus",
        [
            ("csv", f"Baca dari file CSV (default: {DEFAULT_CSV_PATH})"),
            ("manual", "Input langsung daftar Test Case ID via terminal (pisahkan dengan koma)"),
        ],
        default_index=1,
    )

    target_tc_ids: list[str] = []
    if tc_source == "csv":
        while True:
            csv_path = prompt_text("Path file CSV", default=DEFAULT_CSV_PATH)
            if os.path.exists(csv_path):
                target_tc_ids = read_test_case_ids_from_csv(csv_path)
                if target_tc_ids:
                    break
                print_warn("File CSV tidak memiliki Test Case ID yang valid.")
            else:
                print_warn(f"File tidak ditemukan di '{csv_path}'. Pastikan path benar.")
                if not prompt_bool("Coba masukkan path CSV lagi?", default=True):
                    return 1
    else:
        while True:
            raw_tcs = prompt_text("Masukkan Test Case ID (contoh: 113501, 113502)")
            target_tc_ids = parse_tc_id_list(raw_tcs)
            if target_tc_ids:
                break
            print_warn("Tidak ada Test Case ID angka yang valid.")

    # 3. Opsi Dry Run & Delay
    dry_run = prompt_bool(
        "Aktifkan mode Dry-Run? (Hanya simulasi/preview tanpa menghapus data)",
        default=False,
    )
    delay = prompt_float("Delay antar request hapus dalam detik", default=DEFAULT_DELAY)

    print_section("Konfirmasi Parameter")
    print(f"  Target Mode      : {'Campaign ID = ' + str(campaign_id) if campaign_id else 'Iteration IDs = ' + str(iteration_ids)}")
    print(f"  Jumlah Target TC : {len(target_tc_ids)} Test Case ID")
    print(f"  Dry-Run Mode     : {dry_run}")
    print(f"  Delay Request    : {delay}s")

    if not prompt_bool("Lanjutkan eksekusi?", default=True):
        print_warn("Eksekusi dibatalkan oleh pengguna.")
        return 0

    return execute(
        campaign_id=campaign_id,
        iteration_ids=iteration_ids,
        target_tc_ids=target_tc_ids,
        dry_run=dry_run,
        delay=delay,
    )


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Delete test cases from SquashTM test runs by Test Case ID."
    )
    parser.add_argument("-c", "--campaign-id", type=int, default=None, help="SquashTM Campaign ID.")
    parser.add_argument(
        "-i", "--iteration-ids", type=str, default=None, help="Comma-separated iteration IDs."
    )
    parser.add_argument(
        "-f", "--csv", type=str, default=None, help=f"Path to CSV file (default: {DEFAULT_CSV_PATH})."
    )
    parser.add_argument(
        "--tc", "--testcase-ids", type=str, default=None, help="Comma-separated Test Case IDs."
    )
    parser.add_argument("--dry-run", action="store_true", default=False, help="Preview without deleting.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between deletes.")
    args = parser.parse_args()

    target_tc_ids: list[str] = []
    if args.tc:
        target_tc_ids = parse_tc_id_list(args.tc)
    else:
        csv_file = args.csv or DEFAULT_CSV_PATH
        target_tc_ids = read_test_case_ids_from_csv(csv_file)

    iteration_ids: list[int] = []
    if args.iteration_ids:
        iteration_ids = [int(x.strip()) for x in args.iteration_ids.split(",") if x.strip().isdigit()]

    return execute(
        campaign_id=args.campaign_id,
        iteration_ids=iteration_ids,
        target_tc_ids=target_tc_ids,
        dry_run=args.dry_run,
        delay=args.delay,
    )


if __name__ == "__main__":
    sys.exit(main())

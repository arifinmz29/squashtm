#!/usr/bin/env python3
"""
Fitur Tambahan: Export Test Case by Project Schema (`/backend/search/test-case`).
Refactored dari script lama agar terintegrasi dengan `credential_tools.json` dan CLI interaktif.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
import re
import sys
from typing import Any

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
    prompt_text,
)
from scripts.core.config import CSV_DIR, SCHEMAS_DIR, ensure_data_dirs
from scripts.core.squash_client import (
    cleanup_cookies,
    get_origin,
    get_squash_session,
    refresh_xsrf,
)

DEFAULT_SCHEMA_FILE = os.path.join(SCHEMAS_DIR, "search_tc_schema.json")
DEFAULT_MAPPING_MAIN = os.path.join(CSV_DIR, "search_tc_mapping_main_f.csv")
DEFAULT_MAPPING_CUF = os.path.join(CSV_DIR, "search_tc_mapping_cuf.csv")


def get_nested_value(data: dict[str, Any], path: str) -> Any:
    """Ambil nilai dari dictionary bersarang menggunakan notasi seperti 'x.dataRows[0].data.id'."""
    clean_path = path.replace("x.dataRows[0].", "")
    try:
        current: Any = data
        pattern = re.compile(r'\.|\["|"]|\[\'|\']')
        parts = [p for p in pattern.split(clean_path) if p]

        for part in parts:
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return ""
            elif isinstance(current, list):
                idx = int(part)
                if idx < len(current):
                    current = current[idx]
                else:
                    return ""
            else:
                return ""
        return current
    except Exception:
        return ""


def execute(
    schema_file: str = DEFAULT_SCHEMA_FILE,
    mapping_main_file: str = DEFAULT_MAPPING_MAIN,
    mapping_cuf_file: str = DEFAULT_MAPPING_CUF,
) -> int:
    """Jalankan export test case by project berdasarkan JSON schema."""
    ensure_data_dirs()

    if not os.path.exists(schema_file):
        print_error(f"File schema tidak ditemukan: {schema_file}")
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        refresh_xsrf(session)
        with open(schema_file, "r", encoding="utf-8") as f:
            request_payload = json.load(f)

        project_scope = request_payload.get("scope", [])
        project_name = project_scope[0] if project_scope else "UnknownProject"

        if request_payload.get("size", 0) < 10:
            print_warn(
                f"Nilai 'size' pada schema diatur ke {request_payload.get('size')}. Hasil akan dibatasi."
            )

        search_url = f"{base_url}/backend/search/test-case"
        headers = {
            "Content-Type": "application/json",
            "X-XSRF-TOKEN": session.cookies.get("XSRF-TOKEN", ""),
            "Origin": get_origin(base_url),
            "Referer": f"{base_url}/search/test-case",
        }

        print_info(f"Mengambil test cases untuk project scope: {project_name}...")
        response = session.post(search_url, headers=headers, json=request_payload, timeout=60)
        if response.status_code != 200:
            print_error(f"Gagal mengambil test cases: HTTP {response.status_code}")
            return 1

        result_data = response.json()
        data_rows = result_data.get("dataRows", [])
        print_success(f"Berhasil mengambil {len(data_rows)} test cases.")

        csv_headers: list[str] = []
        mapping_keys: list[str] = []

        for map_path in [mapping_main_file, mapping_cuf_file]:
            if os.path.exists(map_path):
                with open(map_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 2:
                            mapping_keys.append(row[0].strip())
                            csv_headers.append(row[1].strip())
                        elif len(row) == 1:
                            key = row[0].strip()
                            mapping_keys.append(key)
                            csv_headers.append(key)

        today_date = datetime.now().strftime("%Y%m%d")
        output_filename = f"exported_test_case_{project_name}_{today_date}.csv"
        output_path = os.path.join(CSV_DIR, output_filename)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_headers)
            for item in data_rows:
                row_data = [get_nested_value(item, key) for key in mapping_keys]
                writer.writerow(row_data)

        print_success(f"Export selesai! File disimpan di: {output_path}")
        return 0
    except Exception as e:
        print_error(f"Exception saat export test cases by project: {e}")
        return 1
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    print_banner(
        "EXPORT TEST CASE BY PROJECT SCHEMA",
        "Export Test Case menggunakan endpoint /backend/search/test-case & schema JSON",
    )
    schema_file = prompt_text("Path file JSON Schema", default=DEFAULT_SCHEMA_FILE)
    print_section("Konfirmasi Parameter")
    print(f"  Schema File : {schema_file}")
    print(f"  Output Dir  : {CSV_DIR}")

    if not prompt_bool("Lanjutkan proses export?", default=True):
        return 0
    return execute(schema_file=schema_file)


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()
    parser = argparse.ArgumentParser(description="Export test cases by project schema.")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA_FILE, help="Path to schema JSON")
    args = parser.parse_args()
    return execute(schema_file=args.schema)


if __name__ == "__main__":
    sys.exit(main())

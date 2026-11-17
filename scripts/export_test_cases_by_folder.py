#!/usr/bin/env python3
"""
Fitur 2: Export Test Cases by Folder ke Excel (.xlsx).

Alur Proses:
  1. Login ke SquashTM menggunakan credential dari `credential_tools.json`.
  2. Mengambil struktur folder dari `/backend/test-case-tree` berdasarkan Library Name
     ("Genesis", "Customer Apps", "Website").
  3. Mencari Node ID dari nama folder yang dituju, me-refresh tree view, dan mengekstrak
     daftar Node ID di dalamnya.
  4. Mengekspor masing-masing node ke file `.xls` sementara (`/backend/test-case/export/content/xls`).
  5. Menggabungkan (merge) seluruh file `.xls` menjadi satu file `.xlsx` di `data_files/xls/`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time

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
    prompt_text,
)
from scripts.core.config import JSON_DIR, TEMP_XLS_DIR, XLS_DIR, ensure_data_dirs
from scripts.core.squash_client import cleanup_cookies, get_squash_session, refresh_xsrf

DEFAULT_LIBRARY_NAME = "Genesis"
DEFAULT_FOLDER_NAME = "New Regression Testing"

LIBRARY_OPENED_NODES: dict[str, list[str]] = {
    "Genesis": [
        "TestCaseLibrary-1",
        "TestCaseFolder-7",
        "TestCaseFolder-10",
        "TestCaseFolder-11115",
        "TestCaseFolder-15182",
    ],
    "Customer Apps": [
        "TestCaseLibrary-2",
        "TestCaseFolder-15464",
        "TestCaseFolder-19456",
    ],
    "Website": [
        "TestCaseLibrary-3",
        "TestCaseFolder-15468",
        "TestCaseFolder-15466",
    ],
}


def get_tree_view(
    session: requests.Session,
    base_url: str,
    library_name: str,
    additional_opened_nodes: list[str] | str | None = None,
) -> bool:
    """POST /backend/test-case-tree and save response to data_files/json/folder_tree.json."""
    print_info(f"Mengambil Tree View untuk Library '{library_name}'...")
    refresh_xsrf(session)
    url = f"{base_url}/backend/test-case-tree"

    opened_nodes = list(
        LIBRARY_OPENED_NODES.get(library_name, LIBRARY_OPENED_NODES["Genesis"])
    )
    if library_name not in LIBRARY_OPENED_NODES:
        print_warn(f"Library '{library_name}' tidak dikenal, menggunakan default 'Genesis'.")

    if additional_opened_nodes:
        if isinstance(additional_opened_nodes, list):
            opened_nodes.extend(additional_opened_nodes)
        else:
            opened_nodes.append(additional_opened_nodes)

    payload = {
        "openedNodes": opened_nodes,
        "selectedNodes": ["TestCaseFolder-7"],
    }

    try:
        resp = session.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            print_error(f"Gagal mengambil tree view. HTTP {resp.status_code}")
            return False

        os.makedirs(JSON_DIR, exist_ok=True)
        json_path = os.path.join(JSON_DIR, "folder_tree.json")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(resp.json(), f, indent=4)

        print_success(f"Tree view disimpan ke {json_path}")
        return True
    except Exception as e:
        print_error(f"Exception saat mengambil Tree View: {e}")
        return False


def get_folder_id_by_name(folder_name: str) -> str | None:
    """Cari ID folder (mis. 'TestCaseFolder-15182') berdasarkan nama folder di folder_tree.json."""
    json_path = os.path.join(JSON_DIR, "folder_tree.json")
    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for row in data.get("dataRows", []):
            row_data = row.get("data", {})
            node_name = row_data.get("name") or row_data.get("NAME") or row_data.get("Name")
            if node_name == folder_name:
                return row.get("id")
        return None
    except Exception as e:
        print_error(f"Gagal membaca folder_tree.json: {e}")
        return None


def extract_node_ids_by_folder_name(folder_name: str) -> list[str]:
    """Ekstrak daftar child node ID angka dari folder yang dipilih."""
    json_path = os.path.join(JSON_DIR, "folder_tree.json")
    if not os.path.exists(json_path):
        print_error(f"File {json_path} tidak ditemukan.")
        return []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        found = False
        target_children = []
        matched_row_id = None

        for row in data.get("dataRows", []):
            row_data = row.get("data", {})
            node_name = row_data.get("name") or row_data.get("NAME") or row_data.get("Name")
            if node_name == folder_name:
                found = True
                matched_row_id = row.get("id")
                target_children = row.get("children", [])
                break

        if not found:
            print_error(f"Folder '{folder_name}' tidak ditemukan pada folder_tree.json.")
            return []

        node_ids: list[str] = []
        for child in target_children:
            match = re.search(r"(\d+)", str(child))
            if match:
                node_ids.append(match.group(1))

        # Fallback: jika folder tidak memiliki sub-folder tetapi punya ID sendiri
        if not node_ids and matched_row_id:
            match = re.search(r"(\d+)", str(matched_row_id))
            if match:
                print_info(
                    f"Folder '{folder_name}' tidak memiliki sub-folder, mengekspor node folder langsung ({match.group(1)})."
                )
                node_ids.append(match.group(1))

        print_info(f"Berhasil mengekstrak {len(node_ids)} node ID: {node_ids}")
        return node_ids
    except Exception as e:
        print_error(f"Gagal memproses folder_tree.json: {e}")
        return []


def export_test_cases(
    session: requests.Session,
    base_url: str,
    folder_name: str,
    node_ids: list[str],
) -> list[str]:
    """Export setiap node ID menjadi file .xls di dalam TEMP_XLS_DIR."""
    print_section("Mengekspor Test Case per Node")
    if not node_ids:
        print_warn("Tidak ada node ID untuk diekspor.")
        return []

    os.makedirs(TEMP_XLS_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    exported_files: list[str] = []
    safe_folder = re.sub(r'[\\/*?:"<>|]', "_", folder_name)

    for idx, node_id in enumerate(node_ids, start=1):
        print_info(f"Exporting Node ID: {node_id} ({idx}/{len(node_ids)})...")
        refresh_xsrf(session)

        url = f"{base_url}/backend/test-case/export/content/xls"
        params = {
            "filename": f"export-the-test-cases_{timestamp}",
            "calls": "false",
            "keep-rte-format": "true",
        }
        payload = {
            "nodeIds": [int(node_id)],
            "libraryIds": [],
        }

        try:
            resp = session.post(url, params=params, json=payload, timeout=60)
            if resp.status_code == 200:
                filename = f"{safe_folder}_{node_id}.xls"
                filepath = os.path.join(TEMP_XLS_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                print_success(f"  -> Tersimpan di {filepath}")
                exported_files.append(filepath)
            else:
                print_error(f"  -> Gagal export node {node_id}. HTTP {resp.status_code}")
        except Exception as e:
            print_error(f"  -> Exception saat export node {node_id}: {e}")

        time.sleep(1.0)

    return exported_files


def merge_xls_files(folder_name: str, exported_files: list[str]) -> str | None:
    """Gabungkan semua file .xls hasil export menjadi satu file .xlsx."""
    print_section("Menggabungkan File Excel (.xlsx)")
    if not exported_files:
        print_warn("Tidak ada file yang dapat digabungkan.")
        return None

    os.makedirs(XLS_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_folder = re.sub(r'[\\/*?:"<>|]', "_", folder_name)
    final_output = os.path.join(XLS_DIR, f"export_by_folder_{safe_folder}_{timestamp}.xlsx")

    try:
        import pandas as pd

        print_info("Menggabungkan sheet menggunakan pandas...")
        merged_sheets: dict[str, list] = {}

        for file in exported_files:
            try:
                try:
                    if file.lower().endswith(".xls"):
                        file_data = pd.read_excel(
                            file, sheet_name=None, engine="xlrd", keep_default_na=False
                        )
                    else:
                        file_data = pd.read_excel(
                            file, sheet_name=None, keep_default_na=False
                        )

                    if isinstance(file_data, dict):
                        for sheet_name, df in file_data.items():
                            merged_sheets.setdefault(sheet_name, []).append(df)
                    else:
                        merged_sheets.setdefault("Sheet1", []).append(file_data)
                except Exception as ex_excel:
                    print_warn(f"  Pembacaan Excel standar gagal ({ex_excel}), mencoba mode HTML...")
                    dfs = pd.read_html(file, keep_default_na=False)
                    for i, df in enumerate(dfs):
                        sheet_name = f"HTML_Table_{i + 1}"
                        merged_sheets.setdefault(sheet_name, []).append(df)
            except Exception as e:
                print_warn(f"  Tidak dapat membaca file {file}: {e}")

        if merged_sheets:
            with pd.ExcelWriter(final_output, engine="openpyxl") as writer:
                for sheet_name, df_list in merged_sheets.items():
                    if df_list:
                        combined_df = pd.concat(df_list, ignore_index=True)
                        combined_df.to_excel(writer, sheet_name=sheet_name, index=False)

            print_success(f"File gabungan berhasil disimpan ke: {final_output}")
            return final_output
        else:
            print_error("Tidak ada data yang berhasil dibaca untuk digabungkan.")
            return None
    except ImportError:
        print_warn(
            "Library 'pandas' / 'openpyxl' / 'xlrd' belum terinstall. "
            f"File sementara tetap tersedia di: {TEMP_XLS_DIR}"
        )
        return None
    except Exception as e:
        print_error(f"Proses penggabungan file gagal: {e}")
        return None


def execute(library_name: str = DEFAULT_LIBRARY_NAME, folder_name: str = DEFAULT_FOLDER_NAME) -> int:
    """Jalankan alur Export Test Cases by Folder."""
    ensure_data_dirs()
    clean_folder = folder_name.strip()
    if not clean_folder:
        print_error("Nama folder tidak boleh kosong.")
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        if not get_tree_view(session, base_url, library_name):
            return 1

        folder_node_id = get_folder_id_by_name(clean_folder)
        if folder_node_id:
            print_info(f"Node ID untuk folder '{clean_folder}' ditemukan: {folder_node_id}. Refreshing tree...")
            if not get_tree_view(
                session, base_url, library_name, additional_opened_nodes=[folder_node_id]
            ):
                return 1
        else:
            print_warn(
                f"Folder '{clean_folder}' belum ditemukan pada root tree. Mencoba ekstraksi..."
            )

        node_ids = extract_node_ids_by_folder_name(clean_folder)
        if not node_ids:
            print_error("Tidak ada node ID yang ditemukan pada folder tersebut.")
            return 1

        exported_files = export_test_cases(session, base_url, clean_folder, node_ids)
        merge_xls_files(clean_folder, exported_files)
        return 0
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Export Test Cases by Folder."""
    print_banner(
        "EXPORT TEST CASES BY FOLDER",
        "Mengekspor seluruh Test Case di dalam folder SquashTM menjadi file Excel (.xlsx)",
    )

    lib_choice = prompt_choice(
        "Pilih Library SquashTM",
        [
            ("Genesis", "Library utama Genesis"),
            ("Customer Apps", "Library Customer Apps"),
            ("Website", "Library Website"),
            ("Custom", "Input nama library lainnya secara manual"),
        ],
        default_index=1,
    )
    if lib_choice == "Custom":
        library_name = prompt_text("Masukkan nama Library", default=DEFAULT_LIBRARY_NAME)
    else:
        library_name = lib_choice

    folder_name = prompt_text("Masukkan Nama Folder yang ingin di-export", default=DEFAULT_FOLDER_NAME)

    print_section("Konfirmasi Parameter")
    print(f"  Library Name : {library_name}")
    print(f"  Folder Name  : {folder_name}")
    print(f"  Output Dir   : {XLS_DIR}")

    if not prompt_bool("Lanjutkan proses export?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(library_name=library_name, folder_name=folder_name)


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(description="Export SquashTM test cases by folder to Excel.")
    parser.add_argument(
        "-l", "--library", default=DEFAULT_LIBRARY_NAME, help="Library name (Genesis, Customer Apps, Website)"
    )
    parser.add_argument(
        "-f", "--folder", default=DEFAULT_FOLDER_NAME, help="Target folder name in SquashTM"
    )
    args = parser.parse_args()
    return execute(library_name=args.library, folder_name=args.folder)


if __name__ == "__main__":
    sys.exit(main())

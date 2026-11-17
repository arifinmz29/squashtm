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
    prompt_int,
    prompt_text,
)
from scripts.core.config import JSON_DIR, TEMP_XLS_DIR, XLS_DIR, ensure_data_dirs
from scripts.core.squash_client import cleanup_cookies, get_squash_session, refresh_xsrf

DEFAULT_LIBRARY_NAME = "Genesis"
DEFAULT_FOLDER_NAME = "New Regression Testing"
DEFAULT_EXPORT_DEPTH = 2

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


def parse_folder_id_from_input(folder_input: str) -> str | None:
    """
    Jika input berupa URL SquashTM (.../test-case-folder/10/...), 'TestCaseFolder-10',
    atau angka murni '10', kembalikan 'TestCaseFolder-10'. Jika nama folder biasa, kembalikan None.
    """
    clean = folder_input.strip()
    url_match = re.search(r"test-case-folder/(\d+)", clean, re.IGNORECASE)
    if url_match:
        return f"TestCaseFolder-{url_match.group(1)}"

    node_match = re.match(r"^TestCaseFolder-(\d+)$", clean, re.IGNORECASE)
    if node_match:
        return f"TestCaseFolder-{node_match.group(1)}"

    if clean.isdigit():
        return f"TestCaseFolder-{clean}"

    return None


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
            for node in additional_opened_nodes:
                if node not in opened_nodes:
                    opened_nodes.append(node)
        else:
            if additional_opened_nodes not in opened_nodes:
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


def _load_tree_rows_by_id() -> dict[str, dict]:
    """Baca folder_tree.json dan kembalikan mapping {row_id: row_dict}."""
    json_path = os.path.join(JSON_DIR, "folder_tree.json")
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            str(row.get("id")): row
            for row in data.get("dataRows", [])
            if row.get("id")
        }
    except Exception as e:
        print_error(f"Gagal membaca folder_tree.json: {e}")
        return {}


def _get_row_name(row: dict) -> str:
    row_data = row.get("data", {})
    return str(row_data.get("name") or row_data.get("NAME") or row_data.get("Name") or "")


def get_folder_id_by_name(folder_name: str) -> str | None:
    """Cari ID folder (mis. 'TestCaseFolder-10') berdasarkan nama folder atau ID/URL di folder_tree.json."""
    direct_id = parse_folder_id_from_input(folder_name)
    if direct_id:
        return direct_id

    rows_by_id = _load_tree_rows_by_id()
    for row_id, row in rows_by_id.items():
        if _get_row_name(row) == folder_name:
            return row_id
    return None


def expand_tree_to_depth(
    session: requests.Session,
    base_url: str,
    library_name: str,
    target_folder_id: str,
    depth: int = DEFAULT_EXPORT_DEPTH,
) -> bool:
    """
    Membuka (expand) tree secara bertahap dari target_folder_id hingga kedalaman `depth` level
    agar seluruh sub-folder pada level tersebut termuat di folder_tree.json.
    """
    opened_accum: list[str] = [target_folder_id]
    current_frontier: list[str] = [target_folder_id]

    for current_lvl in range(1, max(1, depth) + 1):
        print_info(
            f"Membuka struktur sub-folder level {current_lvl}/{depth} "
            f"({len(current_frontier)} folder)..."
        )
        if not get_tree_view(
            session,
            base_url,
            library_name,
            additional_opened_nodes=opened_accum,
        ):
            return False

        if current_lvl >= depth:
            break

        rows_by_id = _load_tree_rows_by_id()
        next_frontier: list[str] = []
        for parent_id in current_frontier:
            row = rows_by_id.get(parent_id, {})
            for child_id in row.get("children", []):
                child_str = str(child_id)
                if child_str.startswith("TestCaseFolder-"):
                    if child_str not in opened_accum:
                        opened_accum.append(child_str)
                    next_frontier.append(child_str)

        if not next_frontier:
            break
        current_frontier = next_frontier

    return True


def extract_node_ids_by_folder_name(
    folder_name: str,
    depth: int = DEFAULT_EXPORT_DEPTH,
) -> list[str]:
    """
    Ekstrak daftar node ID angka dari folder yang dipilih hingga kedalaman `depth` level di bawahnya.
    Jika suatu sub-folder pada level < depth tidak memiliki sub-folder lagi (leaf folder),
    maka node folder tersebut langsung dimasukkan agar tidak ada test case yang terlewat.
    """
    rows_by_id = _load_tree_rows_by_id()
    if not rows_by_id:
        print_error("Data folder_tree.json kosong atau tidak ditemukan.")
        return []

    target_row_id = parse_folder_id_from_input(folder_name)
    if not target_row_id or target_row_id not in rows_by_id:
        target_row_id = None
        for row_id, row in rows_by_id.items():
            if _get_row_name(row) == folder_name:
                target_row_id = row_id
                break

    if not target_row_id or target_row_id not in rows_by_id:
        print_error(f"Folder '{folder_name}' tidak ditemukan pada folder_tree.json.")
        return []

    collected_raw_ids: list[str] = []

    def _collect(row_id: str, current_depth: int) -> None:
        row = rows_by_id.get(row_id)
        if not row:
            collected_raw_ids.append(row_id)
            return

        if current_depth >= depth:
            collected_raw_ids.append(row_id)
            return

        children = [str(c) for c in row.get("children", [])]
        folder_children = [c for c in children if c.startswith("TestCaseFolder-")]
        non_folder_children = [c for c in children if not c.startswith("TestCaseFolder-")]

        if not folder_children:
            # Folder ini tidak memiliki sub-folder lagi (leaf folder atau berisi TestCase langsung)
            if current_depth == 0 and non_folder_children:
                collected_raw_ids.extend(non_folder_children)
            else:
                collected_raw_ids.append(row_id)
            return

        # Jika ada TestCase langsung di level menengah (di luar sub-folder), tetap ikutkan
        if non_folder_children:
            collected_raw_ids.extend(non_folder_children)

        for fc in folder_children:
            if current_depth + 1 >= depth:
                collected_raw_ids.append(fc)
            else:
                _collect(fc, current_depth + 1)

    _collect(target_row_id, 0)

    node_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in collected_raw_ids:
        match = re.search(r"(\d+)", str(raw_id))
        if match:
            nid = match.group(1)
            if nid not in seen:
                seen.add(nid)
                node_ids.append(nid)

    print_info(
        f"Berhasil mengekstrak {len(node_ids)} node ID (depth={depth} level di bawah '{folder_name}'): {node_ids}"
    )
    return node_ids


def _get_child_node_ids_for_fallback(
    session: requests.Session,
    base_url: str,
    library_name: str,
    failed_node_id: str,
) -> list[str]:
    """
    Jika export sebuah TestCaseFolder gagal (mis. HTTP 500 karena >65.536 baris STEPS pada format .xls
    atau timeout), buka sub-folder di bawahnya dan kembalikan daftar child node ID-nya.
    """
    folder_row_id = f"TestCaseFolder-{failed_node_id}"
    rows_by_id = _load_tree_rows_by_id()
    row = rows_by_id.get(folder_row_id)

    # Jika belum terbuka atau children masih kosong tetapi CHILD_COUNT > 0, refresh tree
    child_count = (row or {}).get("data", {}).get("CHILD_COUNT", 0)
    children = (row or {}).get("children", [])
    if not children and child_count > 0:
        existing_opened = list(rows_by_id.keys())
        existing_opened.append(folder_row_id)
        if get_tree_view(session, base_url, library_name, additional_opened_nodes=existing_opened):
            rows_by_id = _load_tree_rows_by_id()
            row = rows_by_id.get(folder_row_id)
            children = (row or {}).get("children", [])

    sub_ids: list[str] = []
    for child in children:
        m = re.search(r"(\d+)", str(child))
        if m:
            sub_ids.append(m.group(1))
    return sub_ids


def export_test_cases(
    session: requests.Session,
    base_url: str,
    folder_name: str,
    node_ids: list[str],
    library_name: str = DEFAULT_LIBRARY_NAME,
) -> list[str]:
    """
    Export setiap node ID menjadi file .xls di dalam TEMP_XLS_DIR.
    Dilengkapi mekanisme Auto-Fallback: jika suatu node gagal di-export (HTTP 500 karena
    limit 65.536 baris .xls atau Timeout), otomatis memecah export ke sub-folder di bawahnya.
    """
    print_section("Mengekspor Test Case per Node")
    if not node_ids:
        print_warn("Tidak ada node ID untuk diekspor.")
        return []

    os.makedirs(TEMP_XLS_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    exported_files: list[str] = []
    safe_folder = re.sub(r'[\\/*?:"<>|]', "_", folder_name)
    rows_by_id = _load_tree_rows_by_id()

    def _export_single_or_split(node_id: str, label_prefix: str = "") -> None:
        nonlocal rows_by_id
        folder_row = rows_by_id.get(f"TestCaseFolder-{node_id}", {})
        node_title = _get_row_name(folder_row)
        title_info = f" ({node_title})" if node_title else ""
        print_info(f"{label_prefix}Exporting Node ID: {node_id}{title_info}...")
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

        export_ok = False
        try:
            resp = session.post(url, params=params, json=payload, timeout=120)
            if resp.status_code == 200:
                filename = f"{safe_folder}_{node_id}.xls"
                filepath = os.path.join(TEMP_XLS_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                print_success(f"  -> Tersimpan di {filepath} ({len(resp.content):,} bytes)")
                exported_files.append(filepath)
                export_ok = True
            else:
                print_warn(
                    f"  -> Gagal export node {node_id}{title_info} (HTTP {resp.status_code})."
                )
        except Exception as e:
            print_warn(f"  -> Exception/Timeout saat export node {node_id}{title_info}: {e}")

        if not export_ok:
            sub_ids = _get_child_node_ids_for_fallback(
                session, base_url, library_name, node_id
            )
            rows_by_id = _load_tree_rows_by_id()
            if sub_ids:
                print_info(
                    f"  -> Auto-Fallback: Memecah node {node_id}{title_info} menjadi "
                    f"{len(sub_ids)} sub-node di bawahnya: {sub_ids}"
                )
                for sub_idx, sub_id in enumerate(sub_ids, start=1):
                    _export_single_or_split(
                        sub_id, label_prefix=f"  [Sub {sub_idx}/{len(sub_ids)}] "
                    )
                    time.sleep(0.5)
            else:
                print_error(
                    f"  -> Node {node_id}{title_info} gagal diekspor dan tidak memiliki sub-folder untuk dipecah."
                )

    for idx, node_id in enumerate(node_ids, start=1):
        _export_single_or_split(node_id, label_prefix=f"[{idx}/{len(node_ids)}] ")
        time.sleep(0.5)

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
                        if not combined_df.empty:
                            combined_df = combined_df.where(combined_df.notna(), "")
                        combined_df.to_excel(writer, sheet_name=sheet_name, index=False)
                        print_info(f"  -> Sheet '{sheet_name}': {len(combined_df):,} baris")

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


def execute(
    library_name: str = DEFAULT_LIBRARY_NAME,
    folder_name: str = DEFAULT_FOLDER_NAME,
    depth: int = DEFAULT_EXPORT_DEPTH,
) -> int:
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
            print_info(
                f"Node ID untuk folder '{clean_folder}' ditemukan: {folder_node_id}. "
                f"Membuka sub-folder hingga {depth} level di bawahnya..."
            )
            if not expand_tree_to_depth(
                session,
                base_url,
                library_name,
                target_folder_id=folder_node_id,
                depth=depth,
            ):
                return 1
        else:
            print_warn(
                f"Folder '{clean_folder}' belum ditemukan pada root tree. Mencoba ekstraksi..."
            )

        # Gunakan nama folder sebenarnya jika input berupa URL atau ID
        resolved_display_name = clean_folder
        if folder_node_id:
            rows_by_id = _load_tree_rows_by_id()
            actual_name = _get_row_name(rows_by_id.get(folder_node_id, {}))
            if actual_name:
                resolved_display_name = actual_name

        node_ids = extract_node_ids_by_folder_name(clean_folder, depth=depth)
        if not node_ids:
            print_error("Tidak ada node ID yang ditemukan pada folder tersebut.")
            return 1

        exported_files = export_test_cases(
            session,
            base_url,
            resolved_display_name,
            node_ids,
            library_name=library_name,
        )
        merge_xls_files(resolved_display_name, exported_files)
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

    folder_name = prompt_text(
        "Masukkan Nama Folder / ID / URL yang ingin di-export",
        default=DEFAULT_FOLDER_NAME,
    )
    depth = prompt_int(
        "Kedalaman sub-folder untuk di-export (depth level di bawah folder utama)",
        default=DEFAULT_EXPORT_DEPTH,
    ) or DEFAULT_EXPORT_DEPTH

    print_section("Konfirmasi Parameter")
    print(f"  Library Name : {library_name}")
    print(f"  Folder Target: {folder_name}")
    print(f"  Export Depth : {depth} level di bawah folder utama (+ Auto-Fallback jika >65k baris)")
    print(f"  Output Dir   : {XLS_DIR}")

    if not prompt_bool("Lanjutkan proses export?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(library_name=library_name, folder_name=folder_name, depth=depth)


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(description="Export SquashTM test cases by folder to Excel.")
    parser.add_argument(
        "-l", "--library", default=DEFAULT_LIBRARY_NAME, help="Library name (Genesis, Customer Apps, Website)"
    )
    parser.add_argument(
        "-f", "--folder", default=DEFAULT_FOLDER_NAME, help="Target folder name, ID, or URL in SquashTM"
    )
    parser.add_argument(
        "-d",
        "--depth",
        type=int,
        default=DEFAULT_EXPORT_DEPTH,
        help=f"Sub-folder depth level below target folder to export (default: {DEFAULT_EXPORT_DEPTH})",
    )
    args = parser.parse_args()
    return execute(library_name=args.library, folder_name=args.folder, depth=args.depth)


if __name__ == "__main__":
    sys.exit(main())

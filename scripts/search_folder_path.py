#!/usr/bin/env python3
"""
Fitur 7: Search Folder Path di SquashTM (hingga sub-folder paling dalam).

Alur Proses:
  1. Login ke SquashTM menggunakan credential dari `credential_tools.json`.
  2. Resolve Library Name (Genesis, Customer App, Website, All, atau ID/URL library)
     melalui REST API `/api/rest/latest/projects` (dengan fallback ke `/backend/test-case-tree`).
  3. Mengambil seluruh daftar folder hingga sub-folder terdalam:
     - Metode Utama (cepat): `GET /api/rest/latest/test-case-folders` (paginated, fields=id,name,project),
       filter folder yang namanya mengandung teks input user (case-insensitive), lalu mengambil
       path lengkap masing-masing folder yang cocok (`GET /api/rest/latest/test-case-folders/{id}`).
     - Metode Fallback otomatis: BFS bertahap pada `POST /backend/test-case-tree` apabila
       REST API tidak tersedia / tidak memiliki izin akses.
  4. Menampilkan daftar path folder hasil pencarian di terminal.
  5. Menyimpan salinan hasil pencarian ke file CSV di `data_files/csv/`.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime
import os
import re
import sys
import threading
from typing import Any

import requests

# Pastikan PROJECT_ROOT terdaftar di sys.path jika dieksekusi langsung
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

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
    prompt_text,
)
from scripts.core.config import CSV_DIR, ensure_data_dirs
from scripts.core.squash_client import (
    cleanup_cookies,
    get_squash_session,
    get_squash_workspace_url,
    refresh_xsrf,
)

DEFAULT_LIBRARY_NAME = "Website"
REST_PAGE_SIZE = 2000
DETAIL_FETCH_WORKERS = 5
TREE_BFS_BATCH_SIZE = 100


def _normalize_label(text: str) -> str:
    """Normalisasi nama library untuk pencocokan fleksibel (mis. 'Customer Apps' -> 'customer app')."""
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    return cleaned


def parse_library_id_from_input(library_input: str) -> int | None:
    """
    Jika input berupa URL library SquashTM (.../test-case-library/3/...),
    'TestCaseLibrary-3', atau angka murni '3', kembalikan int ID (3).
    """
    clean = library_input.strip()
    url_match = re.search(r"test-case-library/(\d+)", clean, re.IGNORECASE)
    if url_match:
        return int(url_match.group(1))

    node_match = re.match(r"^TestCaseLibrary-(\d+)$", clean, re.IGNORECASE)
    if node_match:
        return int(node_match.group(1))

    if clean.isdigit():
        return int(clean)

    return None


def fetch_libraries_rest(session: requests.Session, base_url: str) -> dict[int, str] | None:
    """
    Ambil daftar project/library dari `GET /api/rest/latest/projects`.
    Mengembalikan dict {project_id: project_name}, atau None jika endpoint gagal.
    """
    refresh_xsrf(session)
    projects: dict[int, str] = {}
    page = 0

    try:
        while True:
            resp = session.get(
                f"{base_url}/api/rest/latest/projects",
                params={"page": page, "size": 100},
                timeout=30,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            items = data.get("_embedded", {}).get("projects", [])
            for item in items:
                pid = item.get("id")
                pname = str(item.get("name") or "").strip()
                if pid is not None and pname:
                    projects[int(pid)] = pname

            page_info = data.get("page", {})
            total_pages = int(page_info.get("totalPages", 1))
            page += 1
            if page >= total_pages or not items:
                break

        return projects
    except Exception:
        return None


def fetch_libraries_tree(session: requests.Session, base_url: str) -> dict[int, str]:
    """Ambil daftar library dari root `POST /backend/test-case-tree`."""
    refresh_xsrf(session)
    libraries: dict[int, str] = {}
    try:
        resp = session.post(
            f"{base_url}/backend/test-case-tree",
            json={"openedNodes": [], "selectedNodes": []},
            timeout=30,
        )
        if resp.status_code != 200:
            return libraries

        for row in resp.json().get("dataRows", []):
            rid = str(row.get("id") or "")
            m = re.match(r"^TestCaseLibrary-(\d+)$", rid)
            if m:
                row_data = row.get("data") or {}
                name = str(
                    row_data.get("NAME") or row_data.get("name") or row_data.get("Name") or ""
                ).strip()
                if name:
                    libraries[int(m.group(1))] = name
    except Exception as e:
        print_warn(f"Gagal mengambil daftar library dari test-case-tree: {e}")

    return libraries


def resolve_target_libraries(
    library_input: str,
    available_libraries: dict[int, str],
) -> dict[int, str]:
    """
    Mencocokkan input `library_name` dari user terhadap daftar library SquashTM yang tersedia.
    Mendukung:
      - 'all' / '*' / 'semua' -> seluruh library
      - ID angka / 'TestCaseLibrary-3' / URL SquashTM library
      - Nama exact (case-insensitive)
      - Nama dengan variasi plural/singular (mis. 'Customer Apps' -> 'Customer App')
      - Substring tunggal yang tidak ambigu
    """
    clean = library_input.strip()
    if not clean:
        return {}

    norm = _normalize_label(clean)
    if norm in {"all", "*", "semua"}:
        return dict(available_libraries)

    # 1. Cek berdasarkan ID / URL
    direct_id = parse_library_id_from_input(clean)
    if direct_id is not None:
        if direct_id in available_libraries:
            return {direct_id: available_libraries[direct_id]}
        return {}

    # 2. Exact match (case-insensitive)
    for lib_id, lib_name in available_libraries.items():
        if _normalize_label(lib_name) == norm:
            return {lib_id: lib_name}

    # 3. Singular/plural normalization (mis. "Customer Apps" <-> "Customer App")
    norm_no_s = norm.rstrip("s")
    for lib_id, lib_name in available_libraries.items():
        if _normalize_label(lib_name).rstrip("s") == norm_no_s:
            return {lib_id: lib_name}

    # 4. Partial substring match jika hanya 1 library yang cocok
    partial_matches = {
        lib_id: lib_name
        for lib_id, lib_name in available_libraries.items()
        if norm in _normalize_label(lib_name)
    }
    if len(partial_matches) == 1:
        return partial_matches

    return {}


def _build_folder_url(workspace_url: str, folder_id: int) -> str:
    return f"{workspace_url}/test-case-workspace/test-case-folder/{folder_id}/content?anchor=dashboard"


def _search_via_rest_api(
    session: requests.Session,
    base_url: str,
    target_libraries: dict[int, str],
    folder_query: str,
) -> list[dict[str, Any]] | None:
    """
    Cari folder menggunakan SquashTM REST API (`/api/rest/latest/test-case-folders`).
    Mengembalikan list hasil jika sukses, atau None jika REST API gagal (untuk memicu fallback).
    """
    refresh_xsrf(session)
    query_lower = folder_query.strip().lower()
    target_ids = set(target_libraries.keys())
    workspace_url = get_squash_workspace_url(base_url)

    print_info("Mengambil indeks seluruh folder dari SquashTM REST API...")
    matched_stubs: list[dict[str, Any]] = []
    total_scanned = 0
    total_in_library = 0
    page = 0

    try:
        while True:
            resp = session.get(
                f"{base_url}/api/rest/latest/test-case-folders",
                params={
                    "page": page,
                    "size": REST_PAGE_SIZE,
                    "fields": "id,name,project",
                },
                timeout=60,
            )
            if resp.status_code != 200:
                print_warn(
                    f"REST API test-case-folders mengembalikan HTTP {resp.status_code}. "
                    "Beralih ke metode fallback (test-case-tree BFS)..."
                )
                return None

            data = resp.json()
            items = data.get("_embedded", {}).get("test-case-folders", [])
            page_info = data.get("page", {})
            total_pages = int(page_info.get("totalPages", 1))

            for item in items:
                total_scanned += 1
                proj = item.get("project") or {}
                proj_id = proj.get("id")
                if proj_id is None or int(proj_id) not in target_ids:
                    continue

                total_in_library += 1
                fname = str(item.get("name") or "")
                if query_lower in fname.lower():
                    matched_stubs.append(
                        {
                            "folder_id": int(item["id"]),
                            "folder_name": fname,
                            "library_id": int(proj_id),
                            "library_name": target_libraries.get(int(proj_id), ""),
                        }
                    )

            page += 1
            if page >= total_pages or not items:
                break
    except Exception as e:
        print_warn(f"Exception saat akses REST API test-case-folders: {e}. Beralih ke fallback...")
        return None

    lib_label = ", ".join(target_libraries.values())
    print_info(
        f"Selesai memindai {total_in_library:,} folder pada library [{lib_label}] "
        f"(total indeks global: {total_scanned:,}). Ditemukan {len(matched_stubs)} folder cocok."
    )

    if not matched_stubs:
        return []

    print_info(f"Mengambil full path untuk {len(matched_stubs)} folder yang cocok...")
    results: list[dict[str, Any]] = []

    def _fetch_single_detail(stub: dict[str, Any], worker_sess: requests.Session) -> dict[str, Any]:
        fid = stub["folder_id"]
        path_val = ""
        last_err = ""
        for _attempt in range(2):
            try:
                r = worker_sess.get(
                    f"{base_url}/api/rest/latest/test-case-folders/{fid}",
                    params={"fields": "id,name,path"},
                    timeout=30,
                )
                if r.status_code == 200:
                    fd = r.json()
                    path_val = str(fd.get("path") or "").strip()
                    if path_val and not path_val.startswith("/"):
                        path_val = "/" + path_val
                    break
                last_err = f"HTTP {r.status_code}"
            except Exception as ex:
                last_err = str(ex)

        if not path_val:
            path_val = f"/{stub['library_name']}/.../{stub['folder_name']} (gagal ambil path: {last_err})"

        return {
            "library_name": stub["library_name"],
            "folder_id": fid,
            "folder_name": stub["folder_name"],
            "folder_path": path_val,
            "folder_url": _build_folder_url(workspace_url, fid),
        }

    if len(matched_stubs) <= 15:
        for stub in matched_stubs:
            results.append(_fetch_single_detail(stub, session))
    else:
        thread_local = threading.local()
        created_sessions: list[requests.Session] = []
        sessions_lock = threading.Lock()
        base_cookies = session.cookies.copy()
        base_headers = dict(session.headers)

        def _get_worker_session() -> requests.Session:
            sess = getattr(thread_local, "session", None)
            if sess is None:
                sess = requests.Session()
                sess.headers.update(base_headers)
                sess.cookies.update(base_cookies)
                thread_local.session = sess
                with sessions_lock:
                    created_sessions.append(sess)
            return sess

        def _worker_task(stub: dict[str, Any]) -> dict[str, Any]:
            return _fetch_single_detail(stub, _get_worker_session())

        completed = 0
        try:
            with ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as executor:
                futures = [
                    executor.submit(_worker_task, stub)
                    for stub in matched_stubs
                ]
                for fut in as_completed(futures):
                    results.append(fut.result())
                    completed += 1
                    if completed % 50 == 0 or completed == len(matched_stubs):
                        print_info(f"  Progress ambil path: {completed}/{len(matched_stubs)}...")
        finally:
            for s in created_sessions:
                try:
                    s.close()
                except Exception:
                    pass

    return results


def _search_via_tree_bfs(
    session: requests.Session,
    base_url: str,
    target_libraries: dict[int, str],
    folder_query: str,
) -> list[dict[str, Any]]:
    """
    Fallback: telusuri seluruh struktur folder hingga level terdalam menggunakan
    BFS bertahap pada `POST /backend/test-case-tree`.
    """
    refresh_xsrf(session)
    query_lower = folder_query.strip().lower()
    workspace_url = get_squash_workspace_url(base_url)
    results: list[dict[str, Any]] = []

    for lib_id, lib_name in target_libraries.items():
        lib_row_id = f"TestCaseLibrary-{lib_id}"
        print_info(f"Menelusuri seluruh sub-folder pada Library '{lib_name}' ({lib_row_id}) via BFS...")

        all_nodes: dict[str, dict[str, Any]] = {
            lib_row_id: {"name": lib_name, "parent": None, "child_count": 0}
        }

        resp0 = session.post(
            f"{base_url}/backend/test-case-tree",
            json={"openedNodes": [lib_row_id], "selectedNodes": []},
            timeout=60,
        )
        if resp0.status_code != 200:
            print_error(f"Gagal membuka root library '{lib_name}': HTTP {resp0.status_code}")
            continue

        frontier: list[str] = []
        for row in resp0.json().get("dataRows", []):
            rid = str(row.get("id") or "")
            pid = row.get("parentRowId")
            if rid.startswith("TestCaseFolder-") and pid == lib_row_id:
                rdata = row.get("data") or {}
                cc = int(rdata.get("CHILD_COUNT") or 0)
                all_nodes[rid] = {
                    "name": str(rdata.get("NAME") or rdata.get("name") or ""),
                    "parent": pid,
                    "child_count": cc,
                }
                if cc > 0:
                    frontier.append(rid)

        level = 1
        while frontier:
            level += 1
            frontier.sort(key=lambda x: str(all_nodes[x]["parent"] or ""))
            next_frontier: list[str] = []

            for i in range(0, len(frontier), TREE_BFS_BATCH_SIZE):
                batch = frontier[i : i + TREE_BFS_BATCH_SIZE]
                parents = list({all_nodes[f]["parent"] for f in batch if all_nodes[f]["parent"]})
                opened = list(dict.fromkeys(parents + batch))
                refresh_xsrf(session)
                resp = session.post(
                    f"{base_url}/backend/test-case-tree",
                    json={"openedNodes": opened, "selectedNodes": []},
                    timeout=60,
                )
                if resp.status_code != 200:
                    print_warn(f"  HTTP {resp.status_code} saat membuka batch level {level}, melanjutkan...")
                    continue

                batch_set = set(batch)
                for row in resp.json().get("dataRows", []):
                    rid = str(row.get("id") or "")
                    pid = row.get("parentRowId")
                    if rid.startswith("TestCaseFolder-") and pid in batch_set and rid not in all_nodes:
                        rdata = row.get("data") or {}
                        cc = int(rdata.get("CHILD_COUNT") or 0)
                        all_nodes[rid] = {
                            "name": str(rdata.get("NAME") or rdata.get("name") or ""),
                            "parent": pid,
                            "child_count": cc,
                        }
                        if cc > 0:
                            next_frontier.append(rid)

            frontier = next_frontier

        def _reconstruct_path(node_id: str) -> str:
            parts: list[str] = []
            cur: str | None = node_id
            visited: set[str] = set()
            while cur and cur in all_nodes and cur not in visited:
                visited.add(cur)
                parts.append(all_nodes[cur]["name"])
                cur = all_nodes[cur]["parent"]
            return "/" + "/".join(reversed(parts))

        folder_count = 0
        for rid, meta in all_nodes.items():
            if not rid.startswith("TestCaseFolder-"):
                continue
            folder_count += 1
            fname = meta["name"]
            if query_lower in fname.lower():
                m = re.search(r"(\d+)", rid)
                fid = int(m.group(1)) if m else 0
                results.append(
                    {
                        "library_name": lib_name,
                        "folder_id": fid,
                        "folder_name": fname,
                        "folder_path": _reconstruct_path(rid),
                        "folder_url": _build_folder_url(workspace_url, fid),
                    }
                )

        print_info(
            f"Selesai menelusuri Library '{lib_name}': {folder_count:,} folder (kedalaman maks: {level})."
        )

    return results


def search_folder_paths(
    session: requests.Session,
    base_url: str,
    library_name: str,
    folder_name: str,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """
    Cari semua folder di dalam `library_name` (hingga sub-folder paling dalam)
    yang namanya sesuai atau mengandung `folder_name` (case-insensitive).

    Returns:
        (sorted_results, resolved_target_libraries)
    """
    available_libraries = fetch_libraries_rest(session, base_url)
    if not available_libraries:
        available_libraries = fetch_libraries_tree(session, base_url)

    if not available_libraries:
        raise RuntimeError("Tidak dapat mengambil daftar Library/Project dari SquashTM.")

    target_libraries = resolve_target_libraries(library_name, available_libraries)
    if not target_libraries:
        avail_str = ", ".join(
            f"{name} (ID={lid})" for lid, name in sorted(available_libraries.items())
        )
        raise ValueError(
            f"Library '{library_name}' tidak ditemukan di SquashTM. "
            f"Library yang tersedia: {avail_str}"
        )

    # Metode utama: REST API (sangat cepat), fallback otomatis ke BFS tree view
    results = _search_via_rest_api(session, base_url, target_libraries, folder_name)
    if results is None:
        results = _search_via_tree_bfs(session, base_url, target_libraries, folder_name)

    # Urutkan: exact match nama folder terlebih dahulu, lalu berdasarkan path (alphabetical)
    query_lower = folder_name.strip().lower()
    results.sort(
        key=lambda r: (
            0 if r["folder_name"].strip().lower() == query_lower else 1,
            r["folder_path"].lower(),
            r["folder_id"],
        )
    )
    return results, target_libraries


def save_results_to_csv(
    results: list[dict[str, Any]],
    library_label: str,
    folder_query: str,
    output_path: str | None = None,
) -> str:
    """Simpan hasil pencarian path folder ke file CSV di dalam `data_files/csv/`."""
    os.makedirs(CSV_DIR, exist_ok=True)

    if output_path:
        csv_path = output_path
        parent_dir = os.path.dirname(os.path.abspath(csv_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_lib = re.sub(r"[^A-Za-z0-9._-]+", "_", library_label.strip()).strip("_")[:30] or "all"
        safe_query = re.sub(r"[^A-Za-z0-9._-]+", "_", folder_query.strip()).strip("_")[:40] or "query"
        filename = f"search_folder_path_{safe_lib}_{safe_query}_{timestamp}.csv"
        csv_path = os.path.join(CSV_DIR, filename)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["no", "library_name", "folder_id", "folder_name", "folder_path", "folder_url"]
        )
        for idx, item in enumerate(results, start=1):
            writer.writerow(
                [
                    idx,
                    item["library_name"],
                    item["folder_id"],
                    item["folder_name"],
                    item["folder_path"],
                    item["folder_url"],
                ]
            )

    return csv_path


def print_search_results(
    results: list[dict[str, Any]],
    library_label: str,
    folder_query: str,
) -> None:
    """Tampilkan daftar path folder hasil pencarian secara rapi di terminal."""
    print_section(
        f"Hasil Pencarian Folder ('{folder_query}' di Library: {library_label})"
    )
    if not results:
        print_warn(
            f"Tidak ditemukan folder yang sesuai atau mengandung '{folder_query}' "
            f"pada Library '{library_label}'."
        )
        return

    print_success(f"Ditemukan {len(results)} folder yang sesuai:\n")
    for idx, item in enumerate(results, start=1):
        print(
            f"  {Color.CYAN}{Color.BOLD}[{idx}]{Color.RESET} "
            f"{Color.BOLD}{item['folder_path']}{Color.RESET}"
        )
        print(
            f"      {Color.DIM}ID: {item['folder_id']} | URL: {item['folder_url']}{Color.RESET}"
        )


def execute(
    library_name: str = DEFAULT_LIBRARY_NAME,
    folder_name: str = "",
    output_path: str | None = None,
) -> int:
    """Jalankan pencarian path folder dan simpan salinannya ke file CSV."""
    ensure_data_dirs()
    clean_library = library_name.strip()
    clean_folder = folder_name.strip()

    if not clean_library:
        print_error("Nama library tidak boleh kosong.")
        return 1
    if not clean_folder:
        print_error("Nama folder / kata kunci pencarian tidak boleh kosong.")
        return 1

    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        results, resolved_libs = search_folder_paths(
            session=session,
            base_url=base_url,
            library_name=clean_library,
            folder_name=clean_folder,
        )

        lib_label = (
            "All"
            if len(resolved_libs) > 1
            else next(iter(resolved_libs.values()), clean_library)
        )
        print_search_results(results, lib_label, clean_folder)

        csv_path = save_results_to_csv(
            results=results,
            library_label=lib_label,
            folder_query=clean_folder,
            output_path=output_path,
        )
        print()
        print_success(f"Salinan hasil pencarian disimpan ke CSV: {csv_path}")
        return 0
    except ValueError as e:
        print_error(str(e))
        return 1
    except Exception as e:
        print_error(f"Gagal mencari path folder: {e}")
        return 1
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    """Antarmuka CLI interaktif untuk Fitur Search Folder Path."""
    print_banner(
        "SEARCH FOLDER PATH (DEEP SUB-FOLDER SEARCH)",
        "Mencari path lengkap folder SquashTM berdasarkan sebagian/seluruh nama folder",
    )

    lib_choice = prompt_choice(
        "Pilih Library SquashTM",
        [
            ("Genesis", "Library utama Genesis"),
            ("Customer App", "Library Customer App"),
            ("Website", "Library Website"),
            ("All", "Cari di semua Library"),
            ("Custom", "Input nama / ID / URL library lainnya secara manual"),
        ],
        default_index=3,
    )
    if lib_choice == "Custom":
        library_name = prompt_text(
            "Masukkan nama Library / ID / URL Library",
            default=DEFAULT_LIBRARY_NAME,
        )
    else:
        library_name = lib_choice

    folder_name = prompt_text(
        "Masukkan nama folder (sebagian atau seluruh nama folder yang dicari)",
        required=True,
    )

    print_section("Konfirmasi Parameter")
    print(f"  Library Name : {library_name}")
    print(f"  Folder Name  : {folder_name}")
    print(f"  Output CSV   : {CSV_DIR}")

    if not prompt_bool("Lanjutkan pencarian folder?", default=True):
        print_warn("Proses dibatalkan oleh pengguna.")
        return 0

    return execute(library_name=library_name, folder_name=folder_name)


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()

    parser = argparse.ArgumentParser(
        description="Search SquashTM folder paths recursively down to the deepest sub-folder."
    )
    parser.add_argument(
        "-l",
        "--library",
        default=DEFAULT_LIBRARY_NAME,
        help="Library name (Genesis, Customer App, Website, All), library ID, or library URL",
    )
    parser.add_argument(
        "-f",
        "--folder",
        required=True,
        help="Partial or full folder name to search for (case-insensitive)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help=f"Optional custom output CSV path (default: auto-generated in {CSV_DIR})",
    )
    args = parser.parse_args()
    return execute(
        library_name=args.library,
        folder_name=args.folder,
        output_path=args.output,
    )


if __name__ == "__main__":
    sys.exit(main())

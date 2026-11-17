"""
SquashTM Client & Shared Campaign/Iteration Helpers.
Handles authentication, session cookies, XSRF token synchronization,
and campaign/iteration test plan queries.
"""

from __future__ import annotations

import atexit
import csv
import http.cookiejar
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

from scripts.core.cli import print_error, print_info, print_success, print_warn
from scripts.core.config import COOKIE_FILE, CSV_DIR, get_squash_config


def get_origin(base_url: str) -> str:
    """Extract scheme + host:port for Origin header."""
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "http://172.16.80.47:8090"


def get_squash_workspace_url(base_url: str) -> str:
    """Return base path containing /squash for UI referer URLs."""
    clean = base_url.rstrip("/")
    if clean.endswith("/squash"):
        return clean
    return f"{clean}/squash"


def refresh_xsrf(session: requests.Session) -> None:
    """Keep x-xsrf-token header in sync with current session cookies."""
    token = session.cookies.get("XSRF-TOKEN")
    if token:
        session.headers.update({"x-xsrf-token": token})


def cleanup_cookies() -> None:
    """Remove temporary cookie files on exit."""
    for path in {COOKIE_FILE, "cookies.txt", "cookies_export.txt", "cookies_custom_field.txt"}:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


atexit.register(cleanup_cookies)


def login_squash(session: requests.Session, sq_cfg: dict[str, str] | None = None) -> bool:
    """
    Authenticate against SquashTM:
    1. GET {BASE_URL}/login to obtain initial XSRF-TOKEN cookie
    2. POST {BASE_URL}/backend/login with username & password
    """
    cfg = sq_cfg or get_squash_config()
    base_url = cfg["url"]
    username = cfg["username"]
    password = cfg["password"]

    print_info(f"Menghubungkan ke SquashTM ({base_url}) sebagai '{username}'...")

    if not base_url or not username or not password:
        print_error(
            "Konfigurasi SquashTM (url, username, password) belum lengkap di credential_tools.json."
        )
        return False

    # 1. Handshake
    try:
        r = session.get(f"{base_url}/login", timeout=20)
        if r.status_code != 200:
            print_error(f"Handshake gagal: HTTP {r.status_code}")
            return False
    except Exception as e:
        print_error(f"Gagal mengakses halaman login SquashTM: {e}")
        return False

    token = session.cookies.get("XSRF-TOKEN")
    if not token:
        print_error("Tidak dapat memperoleh XSRF-TOKEN dari handshake awal.")
        return False

    # 2. Login POST
    headers = {
        "accept": "application/json, text/plain, */*",
        "x-xsrf-token": token,
        "content-type": "application/x-www-form-urlencoded",
    }
    data = {
        "username": username,
        "password": password,
    }

    try:
        r = session.post(f"{base_url}/backend/login", headers=headers, data=data, timeout=20)
        if r.status_code in [200, 204]:
            print_success(f"Login SquashTM berhasil sebagai '{username}'.")
            try:
                cj = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
                for c in session.cookies:
                    cj.set_cookie(c)
                cj.save(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
            return True
        print_error(f"Login SquashTM gagal: HTTP {r.status_code} - {r.text[:200]}")
        return False
    except Exception as e:
        print_error(f"Exception saat POST login SquashTM: {e}")
        return False


def get_squash_session() -> tuple[requests.Session | None, str]:
    """
    Create a fresh requests.Session, authenticate against SquashTM,
    and return (session, base_url).
    """
    sq_cfg = get_squash_config()
    base_url = sq_cfg["url"]

    session = requests.Session()
    session.headers.update(
        {
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            )
        }
    )

    cleanup_cookies()

    if login_squash(session, sq_cfg):
        origin = get_origin(base_url)
        session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": origin,
                "x-xsrf-token": session.cookies.get("XSRF-TOKEN", ""),
            }
        )
        return session, base_url
    return None, base_url


# =============================================================================
# CAMPAIGN -> ITERATION ID HELPERS
# =============================================================================
def parse_iteration_ids_from_statistics_payload(data: dict[str, Any]) -> list[int]:
    """Extract iteration IDs from progressionStatistics.scheduledIterations[*].id."""
    prog = data.get("progressionStatistics")
    if prog is None:
        raise ValueError(
            "Key 'progressionStatistics' tidak ditemukan pada response statistics. "
            f"Keys tersedia: {list(data.keys())}"
        )
    scheduled = prog.get("scheduledIterations")
    if not isinstance(scheduled, list):
        raise ValueError("'scheduledIterations' tidak ditemukan atau bukan list.")

    ids: list[int] = []
    for item in scheduled:
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(int(item["id"]))
    return ids


def fetch_iteration_ids_for_campaign(
    session: requests.Session,
    campaign_id: int,
    base_url: str | None = None,
) -> list[int]:
    """POST /backend/campaign-view/{campaign_id}/statistics to get scheduled iteration IDs."""
    resolved_base_url = base_url or get_squash_config()["url"]
    refresh_xsrf(session)
    origin = get_origin(resolved_base_url)
    workspace_url = get_squash_workspace_url(resolved_base_url)

    url = f"{resolved_base_url}/backend/campaign-view/{campaign_id}/statistics"
    payload = {"lastExecutionScope": True}
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": origin,
        "referer": f"{workspace_url}/campaign-workspace/campaign/{campaign_id}/dashboard?anchor=dashboard",
        "x-xsrf-token": session.cookies.get("XSRF-TOKEN") or "",
    }

    try:
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            print_error(f"Request campaign statistics gagal: HTTP {resp.status_code}")
            return []
        data = resp.json()
        ids = parse_iteration_ids_from_statistics_payload(data)
        if ids:
            print_success(f"Ditemukan {len(ids)} iteration dari statistics Campaign {campaign_id}: {ids}")
        return ids
    except Exception as e:
        print_error(f"Gagal mengambil iteration IDs dari statistics Campaign {campaign_id}: {e}")
        return []


def _extract_iteration_ids_recursive(node: Any) -> list[int]:
    """Recursively search for iteration IDs in SquashTM campaign tree response."""
    results: list[int] = []
    if isinstance(node, list):
        for item in node:
            results.extend(_extract_iteration_ids_recursive(item))
    elif isinstance(node, dict):
        node_id = node.get("id")
        node_type = str(node.get("type") or node.get("nodeType") or "").lower()

        if node_id is not None:
            node_id_str = str(node_id)
            if "iteration" in node_id_str.lower() or node_type == "iteration":
                m = re.search(r"\d+", node_id_str)
                if m:
                    results.append(int(m.group()))

        for key in ["children", "content", "nodes", "dataRows", "data"]:
            if key in node and isinstance(node[key], (list, dict)):
                results.extend(_extract_iteration_ids_recursive(node[key]))
    return results


def fetch_iteration_ids_from_campaign_tree(
    session: requests.Session,
    campaign_id: int,
    base_url: str | None = None,
) -> list[int]:
    """
    GET /backend/campaign-tree/Campaign-{campaign_id}/content
    with automatic fallback to campaign statistics API.
    """
    resolved_base_url = base_url or get_squash_config()["url"]
    refresh_xsrf(session)
    workspace_url = get_squash_workspace_url(resolved_base_url)

    url = f"{resolved_base_url}/backend/campaign-tree/Campaign-{campaign_id}/content"
    headers = {
        "accept": "application/json, text/plain, */*",
        "referer": f"{workspace_url}/campaign-workspace/campaign/{campaign_id}/content?anchor=information",
        "x-xsrf-token": session.cookies.get("XSRF-TOKEN", ""),
    }

    try:
        r = session.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            raw_ids = _extract_iteration_ids_recursive(data)
            iteration_ids: list[int] = []
            for iid in raw_ids:
                if iid not in iteration_ids:
                    iteration_ids.append(iid)
            if iteration_ids:
                print_success(
                    f"Ditemukan {len(iteration_ids)} iteration via campaign-tree: {iteration_ids}"
                )
                return iteration_ids
        else:
            print_warn(f"campaign-tree mengembalikan HTTP {r.status_code}")
    except Exception as e:
        print_warn(f"Error saat akses campaign-tree: {e}")

    print_info("Mencoba fallback ke endpoint campaign statistics...")
    return fetch_iteration_ids_for_campaign(session, campaign_id, resolved_base_url)


def save_iteration_ids_csv(
    iteration_ids: list[int],
    campaign_id: int,
    output_dir: str | None = None,
) -> str:
    """Save list_iteration_ids_{campaign_id}.csv into output_dir."""
    out_dir = output_dir or CSV_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"list_iteration_ids_{campaign_id}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iteration_id"])
        for iid in iteration_ids:
            w.writerow([iid])
    print_info(f"Daftar Iteration ID disimpan ke: {path}")
    return path

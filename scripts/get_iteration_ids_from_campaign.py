#!/usr/bin/env python3
"""
Helper Utility: Fetch Iteration IDs from SquashTM Campaign Statistics API.
Menyimpan daftar Iteration ID dari sebuah Campaign ke `data_files/csv/list_iteration_ids_{campaign_id}.csv`.
"""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.core.cli import (
    print_banner,
    print_error,
    print_info,
    print_warn,
    prompt_bool,
    prompt_int,
)
from scripts.core.config import CSV_DIR, ensure_data_dirs
from scripts.core.squash_client import (
    cleanup_cookies,
    fetch_iteration_ids_for_campaign,
    get_squash_session,
    save_iteration_ids_csv,
)

DEFAULT_CAMPAIGN_ID = 31558


def execute(campaign_id: int) -> int:
    ensure_data_dirs()
    session, base_url = get_squash_session()
    if not session:
        return 1

    try:
        ids = fetch_iteration_ids_for_campaign(session, campaign_id, base_url)
        if not ids:
            print_error(f"Tidak ada Iteration ID yang ditemukan untuk Campaign {campaign_id}.")
            return 1
        print_info(f"Iteration IDs ({len(ids)}): {ids}")
        save_iteration_ids_csv(ids, campaign_id, CSV_DIR)
        return 0
    finally:
        cleanup_cookies()


def run_interactive() -> int:
    print_banner(
        "FETCH ITERATION IDS FROM CAMPAIGN",
        "Mengambil daftar Iteration ID dari Campaign SquashTM dan menyimpannya ke CSV",
    )
    campaign_id = prompt_int("Masukkan Campaign ID SquashTM", default=DEFAULT_CAMPAIGN_ID, required=True)
    if not campaign_id:
        return 1
    if not prompt_bool(f"Ambil daftar Iteration ID untuk Campaign {campaign_id}?", default=True):
        print_warn("Dibatalkan.")
        return 0
    return execute(campaign_id=campaign_id)


def main() -> int:
    if len(sys.argv) == 1:
        return run_interactive()
    parser = argparse.ArgumentParser(description="Fetch iteration IDs from SquashTM campaign.")
    parser.add_argument("-c", "--campaign-id", type=int, required=True, help="Campaign ID")
    args = parser.parse_args()
    return execute(campaign_id=args.campaign_id)


if __name__ == "__main__":
    sys.exit(main())

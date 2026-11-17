"""
Interactive CLI Helper Utilities.
Provides styled prompts, menus, and console messages with default values.
"""

from __future__ import annotations

import re
from typing import Any, Sequence


# ANSI Color Codes
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"


def print_banner(title: str, subtitle: str = "") -> None:
    line = "=" * 68
    print(f"\n{Color.CYAN}{Color.BOLD}{line}{Color.RESET}")
    print(f"{Color.CYAN}{Color.BOLD}  {title}{Color.RESET}")
    if subtitle:
        print(f"{Color.DIM}  {subtitle}{Color.RESET}")
    print(f"{Color.CYAN}{Color.BOLD}{line}{Color.RESET}")


def print_section(title: str) -> None:
    print(f"\n{Color.BLUE}{Color.BOLD}--- {title} ---{Color.RESET}")


def print_info(msg: str) -> None:
    print(f"{Color.CYAN}[INFO]{Color.RESET} {msg}")


def print_success(msg: str) -> None:
    print(f"{Color.GREEN}[SUCCESS]{Color.RESET} {msg}")


def print_warn(msg: str) -> None:
    print(f"{Color.YELLOW}[WARN]{Color.RESET} {msg}")


def print_error(msg: str) -> None:
    print(f"{Color.RED}[ERROR]{Color.RESET} {msg}")


def prompt_text(
    label: str,
    default: str | None = None,
    required: bool = True,
) -> str:
    """Prompt user for a text string with optional default value."""
    while True:
        default_hint = f" {Color.DIM}[{default}]{Color.RESET}" if default is not None and default != "" else ""
        raw = input(f"{Color.BOLD}? {label}{default_hint}: {Color.RESET}").strip()
        if not raw and default is not None:
            return str(default)
        if raw:
            return raw
        if not required:
            return ""
        print_warn("Input tidak boleh kosong. Silakan isi kembali.")


def prompt_int(
    label: str,
    default: int | None = None,
    required: bool = True,
) -> int | None:
    """Prompt user for an integer value."""
    while True:
        default_hint = f" {Color.DIM}[{default}]{Color.RESET}" if default is not None else ""
        raw = input(f"{Color.BOLD}? {label}{default_hint}: {Color.RESET}").strip()
        if not raw:
            if default is not None:
                return default
            if not required:
                return None
            print_warn("Masukkan angka yang valid.")
            continue
        if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
            return int(raw)
        print_warn(f"'{raw}' bukan angka bulat (integer) yang valid.")


def prompt_float(
    label: str,
    default: float = 0.15,
) -> float:
    """Prompt user for a float value."""
    while True:
        default_hint = f" {Color.DIM}[{default}]{Color.RESET}"
        raw = input(f"{Color.BOLD}? {label}{default_hint}: {Color.RESET}").strip()
        if not raw:
            return default
        try:
            val = float(raw)
            if val < 0:
                print_warn("Nilai tidak boleh negatif.")
                continue
            return val
        except ValueError:
            print_warn(f"'{raw}' bukan angka desimal yang valid.")


def prompt_bool(
    label: str,
    default: bool = True,
) -> bool:
    """Prompt user for a Yes/No boolean confirmation."""
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{Color.BOLD}? {label} {Color.DIM}[{hint}]{Color.RESET}: ").strip().lower()
        if not raw:
            return default
        if raw in ["y", "yes", "ya", "true", "1"]:
            return True
        if raw in ["n", "no", "tidak", "false", "0"]:
            return False
        print_warn("Masukkan 'y' (Ya) atau 'n' (Tidak).")


def prompt_choice(
    label: str,
    options: Sequence[tuple[str, str]],
    default_index: int = 1,
) -> str:
    """
    Display a numbered list of (value, description) options and return the chosen value.
    `default_index` is 1-based.
    """
    print(f"\n{Color.BOLD}{label}:{Color.RESET}")
    for idx, (val, desc) in enumerate(options, start=1):
        marker = f"{Color.GREEN}*{Color.RESET}" if idx == default_index else " "
        if desc and desc != val:
            print(f"  {marker} {Color.CYAN}{idx}.{Color.RESET} {val} {Color.DIM}— {desc}{Color.RESET}")
        else:
            print(f"  {marker} {Color.CYAN}{idx}.{Color.RESET} {val}")

    while True:
        raw = input(
            f"{Color.BOLD}Pilih nomor [1-{len(options)}] {Color.DIM}(default: {default_index}){Color.RESET}: "
        ).strip()
        if not raw:
            return options[default_index - 1][0]
        if raw.isdigit():
            choice_idx = int(raw)
            if 1 <= choice_idx <= len(options):
                return options[choice_idx - 1][0]
        print_warn(f"Pilihan tidak valid. Masukkan angka antara 1 sampai {len(options)}.")


def prompt_int_list(
    label: str,
    default_list: Sequence[int] | None = None,
) -> list[int]:
    """Prompt user for comma-separated integers (e.g. Iteration IDs)."""
    default_str = ", ".join(str(x) for x in default_list) if default_list else ""
    while True:
        hint = f" {Color.DIM}[{default_str}]{Color.RESET}" if default_str else ""
        raw = input(f"{Color.BOLD}? {label} (pisahkan dengan koma){hint}: {Color.RESET}").strip()
        if not raw and default_list:
            return list(default_list)
        items: list[int] = []
        for part in raw.split(","):
            clean = part.strip()
            if clean.isdigit():
                items.append(int(clean))
        if items:
            return items
        print_warn("Masukkan minimal satu ID angka yang valid (contoh: 16540, 16528).")


def prompt_str_list(
    label: str,
    default_list: Sequence[str] | None = None,
) -> list[str]:
    """Prompt user for comma-separated strings (e.g. Platform Names or TC IDs)."""
    default_str = ", ".join(default_list) if default_list else ""
    while True:
        hint = f" {Color.DIM}[{default_str}]{Color.RESET}" if default_str else ""
        raw = input(f"{Color.BOLD}? {label} (pisahkan dengan koma){hint}: {Color.RESET}").strip()
        if not raw and default_list:
            return list(default_list)
        items = [p.strip() for p in raw.split(",") if p.strip()]
        if items:
            return items
        print_warn("Masukkan minimal satu nilai.")


def parse_tc_id_list(raw_input: str) -> list[str]:
    """Extract numeric Test Case IDs from comma-separated string."""
    result: list[str] = []
    seen: set[str] = set()
    for part in raw_input.split(","):
        val = part.strip()
        if val:
            m = re.search(r"\d+", val)
            if m:
                tc_id = m.group()
                if tc_id not in seen:
                    seen.add(tc_id)
                    result.append(tc_id)
    return result

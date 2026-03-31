#!/usr/bin/env python3
"""Fetch full legislation.gov.uk instrument sources for the current rac-uk corpus.

The fetch targets are derived from the checked-in wave manifests:
- official source cases contribute their own point-in-time date
- slice source cases contribute the editorial-note date embedded in the slice

For each instrument we keep one full-instrument snapshot at the latest date used
by the current corpus under:
    sources/official/{type}/{year}/{number}/{date}/source.akn
    sources/official/{type}/{year}/{number}/{date}/source.xml
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
WAVES_DIR = ROOT / "waves"
OFFICIAL_ROOT = ROOT / "sources" / "official"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def extract_slice_date(path: Path) -> str:
    text = path.read_text()
    match = re.search(r"valid from\s+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not determine point-in-time date from {path}")
    return match.group(1)


def collect_targets() -> dict[tuple[str, str, str], str]:
    targets: dict[tuple[str, str, str], str] = {}
    for manifest_path in sorted(WAVES_DIR.glob("*/manifest.json")):
        data = load_json(manifest_path)
        for case in data["cases"]:
            repo_parts = Path(case["repo_rac_path"]).parts
            instrument = (repo_parts[1], repo_parts[2], repo_parts[3])
            source_path = case["source_path"]
            source_parts = Path(source_path).parts
            if source_path.startswith("sources/official/"):
                point_in_time = source_parts[-1]
            else:
                point_in_time = extract_slice_date(ROOT / source_path)
            current = targets.get(instrument)
            if current is None or point_in_time > current:
                targets[instrument] = point_in_time
    return targets


def fetch(url: str) -> str:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.text


def write_if_needed(path: Path, text: str, refresh: bool) -> None:
    if path.exists() and not refresh:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Re-fetch files even if they already exist")
    parser.add_argument("--dry-run", action="store_true", help="Print the fetch plan without downloading")
    args = parser.parse_args()

    targets = collect_targets()
    if args.dry_run:
        for (law_type, year, number), point_in_time in sorted(targets.items()):
            print(f"{law_type}/{year}/{number} @ {point_in_time}")
        return 0

    for (law_type, year, number), point_in_time in sorted(targets.items()):
        base_url = f"https://www.legislation.gov.uk/{law_type}/{year}/{number}/{point_in_time}"
        target_dir = OFFICIAL_ROOT / law_type / year / number / point_in_time
        print(f"Fetching {law_type}/{year}/{number} @ {point_in_time}")
        akn_text = fetch(base_url + "/data.akn")
        xml_text = fetch(base_url + "/data.xml")
        write_if_needed(target_dir / "source.akn", akn_text, args.refresh)
        write_if_needed(target_dir / "source.xml", xml_text, args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

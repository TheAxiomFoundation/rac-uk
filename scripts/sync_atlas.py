#!/usr/bin/env python3
"""Sync the rac-uk corpus into Atlas/Supabase.

This script upserts:
1. arch.rules rows for the current rac-uk hierarchy
2. public.encoding_runs rows for executable leaf encodings

It is intentionally repo-driven. The canonical source of what should appear in
Atlas is the rac-uk wave manifests plus the checked-in .rac files.

Managed UK archive nodes mirror the repo path under ``uk/legislation/...``.
Derived leaves remain in the same tree as official 1:1 legal nodes; callers can
distinguish them by ``source_path``:
- ``sources/official/...`` for official legislation.gov.uk material
- ``sources/slices/...`` for normalized row/slice leaves
"""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import quote
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import requests


ROOT = Path(__file__).resolve().parents[1]
WAVES_DIR = ROOT / "waves"
RAC_ROOT = ROOT / "legislation"
STRUCTURAL_TOKENS = {"regulation", "schedule", "paragraph", "part", "chapter", "article"}
INSTRUMENT_TITLES = {
    ("uksi", "2006", "965"): "The Child Benefit (Rates) Regulations 2006",
    ("uksi", "2002", "1792"): "The State Pension Credit Regulations 2002",
    ("ssi", "2020", "351"): "The Scottish Child Payment Regulations 2020",
    ("uksi", "2013", "376"): "The Universal Credit Regulations 2013",
    ("uksi", "2002", "2005"): "The Working Tax Credit (Entitlement and Maximum Rate) Regulations 2002",
}


def deterministic_id(citation_path: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"atlas:{citation_path}"))


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def extract_embedded_source(rac_text: str) -> str:
    match = re.match(r'\s*"""(.*?)"""\s*', rac_text, re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def extract_effective_date(text: str) -> str | None:
    match = re.search(r"Editorial note:.*?(?:from|valid from)\s+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def infer_source_url(source_path: str) -> str | None:
    parts = Path(source_path).parts
    if len(parts) < 5 or parts[0] != "sources" or parts[1] != "official":
        return None
    law_type, year, number = parts[2], parts[3], parts[4]
    if len(parts) > 6:
        provision = "/".join(parts[5:-1])
        return f"https://www.legislation.gov.uk/{law_type}/{year}/{number}/{provision}"
    return f"https://www.legislation.gov.uk/{law_type}/{year}/{number}"


def instrument_title(path_parts: list[str]) -> str:
    key = tuple(path_parts[1:4])
    return INSTRUMENT_TITLES.get(key, f"{path_parts[1].upper()} {path_parts[2]}/{path_parts[3]}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


@dataclass
class Case:
    wave: str
    repo_rac_path: str
    repo_test_path: str
    citation: str
    source_path: str
    backend: str
    model: str
    estimated_cost_usd: float | None
    duration_ms: int | None
    metrics: dict[str, Any]
    autorac_version: str

    @property
    def rac_file(self) -> Path:
        return ROOT / self.repo_rac_path

    @property
    def source_file(self) -> Path:
        return ROOT / self.source_path


def load_cases() -> list[Case]:
    cases_by_path: dict[str, Case] = {}
    for manifest_path in sorted(WAVES_DIR.glob("*/manifest.json")):
        data = load_json(manifest_path)
        seeded = data.get("seeded_from", {})
        autorac_version = seeded.get("autorac_version", "")
        wave = data["wave"]
        for raw in data["cases"]:
            case = Case(
                wave=wave,
                repo_rac_path=raw["repo_rac_path"],
                repo_test_path=raw["repo_test_path"],
                citation=raw["citation"],
                source_path=raw["source_path"],
                backend=raw["backend"],
                model=raw["model"],
                estimated_cost_usd=raw.get("estimated_cost_usd"),
                duration_ms=raw.get("duration_ms"),
                metrics=raw.get("metrics", {}),
                autorac_version=autorac_version,
            )
            cases_by_path[case.repo_rac_path] = case
    return sorted(cases_by_path.values(), key=lambda c: natural_key(c.repo_rac_path))


def node_label(parts: list[str], instrument_root_len: int) -> str:
    if len(parts) == instrument_root_len:
        return instrument_title(parts)

    tail = parts[instrument_root_len:]
    if len(tail) >= 2 and tail[-2] in STRUCTURAL_TOKENS:
        return f"{tail[-2].title()} {tail[-1]}"

    token = tail[-1]
    if "-" in token or "_" in token:
        return slug_to_title(token)
    return f"({token})"


def build_boundaries(repo_rac_path: str) -> list[list[str]]:
    parts = Path(repo_rac_path).with_suffix("").parts
    instrument_root = list(parts[:4])  # legislation/type/year/number
    tail = list(parts[4:])
    boundaries = [instrument_root]
    consumed = 0
    while consumed < len(tail):
        if consumed + 1 < len(tail) and tail[consumed] in STRUCTURAL_TOKENS:
            consumed += 2
        else:
            consumed += 1
        boundaries.append(instrument_root + tail[:consumed])
    return boundaries


def leaf_body(case: Case) -> str:
    return extract_embedded_source(case.rac_file.read_text())


def leaf_source_url(case: Case) -> str | None:
    return infer_source_url(case.source_path)


def build_rules(cases: list[Case]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str | None, set[str]] = defaultdict(set)

    for case in cases:
        boundaries = build_boundaries(case.repo_rac_path)
        parent_citation: str | None = None
        for i, boundary in enumerate(boundaries):
            citation_path = "uk/" + "/".join(boundary)
            is_leaf = i == len(boundaries) - 1
            path_key = citation_path
            parent_id = deterministic_id(parent_citation) if parent_citation else None
            label = node_label(boundary, instrument_root_len=4)

            rule = nodes.get(path_key)
            if not rule:
                rule = {
                    "id": deterministic_id(citation_path),
                    "jurisdiction": "uk",
                    "doc_type": "regulation",
                    "parent_id": parent_id,
                    "level": i,
                    "ordinal": None,
                    "heading": label,
                    "body": None,
                    "effective_date": None,
                    "repeal_date": None,
                    "source_url": None,
                    "source_path": None,
                    "rac_path": None,
                    "has_rac": False,
                    "citation_path": citation_path,
                    "line_count": 0,
                }
                nodes[path_key] = rule

            if is_leaf:
                body = leaf_body(case)
                rule["body"] = body
                rule["effective_date"] = extract_effective_date(body)
                rule["source_url"] = leaf_source_url(case)
                rule["source_path"] = case.source_path
                rule["rac_path"] = case.repo_rac_path
                rule["has_rac"] = True
                rule["line_count"] = len(body.splitlines()) if body else 0
            else:
                # Prefer instrument source_url on the root node if we can infer it.
                if i == 0 and not rule["source_url"]:
                    root_parts = boundary[1:4]
                    rule["source_url"] = (
                        f"https://www.legislation.gov.uk/{root_parts[0]}/{root_parts[1]}/{root_parts[2]}"
                    )

            children_by_parent[parent_citation].add(citation_path)
            parent_citation = citation_path

    # Assign ordinals deterministically by sibling order.
    for parent_citation, child_paths in children_by_parent.items():
        sorted_paths = sorted(child_paths, key=lambda p: natural_key(p.split("/")[-1]))
        for ordinal, child_path in enumerate(sorted_paths, start=1):
            nodes[child_path]["ordinal"] = ordinal

    return sorted(nodes.values(), key=lambda r: (r["level"], natural_key(r["citation_path"])))


def build_encoding_runs(cases: list[Case]) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    rows = []
    for case in cases:
        metrics = case.metrics
        compile_pass = metrics.get("compile_pass")
        ci_pass = metrics.get("ci_pass")
        ungrounded = metrics.get("ungrounded_numeric_count")
        pe_score = metrics.get("policyengine_score")
        has_issues = not bool(compile_pass and ci_pass and (ungrounded == 0))
        row_id = f"rac-uk:{case.repo_rac_path}"
        row = {
            "id": row_id,
            "timestamp": now,
            "citation": case.citation,
            "file_path": case.repo_rac_path,
            "complexity": {},
            "iterations": [],
            "total_duration_ms": case.duration_ms,
            "predicted_scores": None,
            "final_scores": {"policyengine_match": pe_score} if pe_score is not None else None,
            "agent_type": case.backend,
            "agent_model": case.model,
            "rac_content": case.rac_file.read_text(),
            "session_id": None,
            "synced_at": now,
            "data_source": "manual_estimate",
            "has_issues": has_issues,
            "note": f"Imported from rac-uk {case.wave}",
            "autorac_version": case.autorac_version,
        }
        rows.append(row)
    return rows


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def post_json(url: str, headers: dict[str, str], rows: list[dict[str, Any]]) -> None:
    response = requests.post(url, headers=headers, json=rows, timeout=180)
    response.raise_for_status()


def sync_rules(rules: list[dict[str, Any]], service_key: str, supabase_url: str, batch_size: int) -> None:
    url = supabase_url.rstrip("/") + "/rest/v1/rules"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Accept-Profile": "arch",
        "Content-Profile": "arch",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    for batch in chunked(rules, batch_size):
        post_json(url, headers, batch)


def sync_encoding_runs(rows: list[dict[str, Any]], service_key: str, supabase_url: str, batch_size: int) -> None:
    url = supabase_url.rstrip("/") + "/rest/v1/encoding_runs"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    for batch in chunked(rows, batch_size):
        post_json(url, headers, batch)


def delete_managed_rules(service_key: str, supabase_url: str) -> None:
    url = (
        supabase_url.rstrip("/")
        + "/rest/v1/rules?citation_path=like."
        + quote("uk/legislation/%", safe="")
    )
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept-Profile": "arch",
        "Content-Profile": "arch",
        "Prefer": "count=exact,return=minimal",
    }
    response = requests.delete(url, headers=headers, timeout=180)
    response.raise_for_status()


def delete_managed_encoding_runs(service_key: str, supabase_url: str) -> None:
    url = (
        supabase_url.rstrip("/")
        + "/rest/v1/encoding_runs?id=like."
        + quote("rac-uk:%", safe="")
    )
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Prefer": "count=exact,return=minimal",
    }
    response = requests.delete(url, headers=headers, timeout=180)
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-rules", action="store_true", help="Do not sync arch.rules")
    parser.add_argument("--skip-encodings", action="store_true", help="Do not sync encoding_runs")
    parser.add_argument(
        "--append-only",
        action="store_true",
        help="Skip managed-subtree replacement and only upsert current rows",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    supabase_url = os.environ.get("RAC_SUPABASE_URL")
    service_key = os.environ.get("RAC_SUPABASE_SECRET_KEY")
    if not supabase_url or not service_key:
        raise SystemExit("RAC_SUPABASE_URL and RAC_SUPABASE_SECRET_KEY are required")

    cases = load_cases()
    rules = build_rules(cases)
    encodings = build_encoding_runs(cases)

    print(f"Loaded {len(cases)} cases")
    print(f"Prepared {len(rules)} arch.rules rows")
    print(f"Prepared {len(encodings)} encoding_runs rows")

    if args.dry_run:
        sample = {
            "rule": rules[0],
            "encoding": {k: encodings[0][k] for k in ["id", "citation", "file_path", "data_source", "autorac_version"]},
            "replace_managed": not args.append_only,
        }
        print(json.dumps(sample, indent=2))
        return 0

    if not args.append_only:
        if not args.skip_rules:
            delete_managed_rules(service_key, supabase_url)
            print("Deleted managed uk/legislation arch.rules rows")
        if not args.skip_encodings:
            delete_managed_encoding_runs(service_key, supabase_url)
            print("Deleted managed rac-uk encoding_runs rows")

    if not args.skip_rules:
        sync_rules(rules, service_key, supabase_url, args.batch_size)
        print("Synced arch.rules")
    if not args.skip_encodings:
        sync_encoding_runs(encodings, service_key, supabase_url, args.batch_size)
        print("Synced encoding_runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

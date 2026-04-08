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
import xml.etree.ElementTree as ET
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
OFFICIAL_ROOT = ROOT / "sources" / "official"
STRUCTURAL_TOKENS = {"regulation", "schedule", "paragraph", "part", "chapter", "article"}
TYPE_LABELS = {
    "legislation": "Legislation",
    "uksi": "UK Statutory Instruments",
    "ssi": "Scottish Statutory Instruments",
    "ukpga": "UK Public General Acts",
}
OFFICIAL_PREFIX = "sources/official/"
SLICE_PREFIX = "sources/slices/"
SOURCE_PREFIXES = (OFFICIAL_PREFIX, SLICE_PREFIX)
PUBLISHABLE_ROOT_TOKENS = {"regulation", "schedule", "article", "section"}
AKN_NS = {
    "akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}
INSTRUMENT_TITLES = {
    ("uksi", "2006", "965"): "The Child Benefit (Rates) Regulations 2006",
    ("uksi", "2002", "1792"): "The State Pension Credit Regulations 2002",
    ("ssi", "2020", "351"): "The Scottish Child Payment Regulations 2020",
    ("uksi", "2013", "376"): "The Universal Credit Regulations 2013",
    ("uksi", "2002", "2005"): "The Working Tax Credit (Entitlement and Maximum Rate) Regulations 2002",
    ("ukpga", "2002", "16"): "State Pension Credit Act 2002",
}


def deterministic_id(citation_path: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"atlas:{citation_path}"))


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def iter_text_filtered(elem: ET.Element) -> list[str]:
    pieces: list[str] = []

    def visit(node: ET.Element) -> None:
        if local_name(node.tag) == "noteRef":
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)

    visit(elem)
    return pieces


def element_text(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    text = normalize_text(" ".join(iter_text_filtered(elem)))
    return text or None


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
    remainder = list(parts[5:-1])
    if remainder and re.fullmatch(r"\d{4}-\d{2}-\d{2}", remainder[0]):
        remainder = remainder[1:]
    if remainder:
        provision = "/".join(remainder)
        return f"https://www.legislation.gov.uk/{law_type}/{year}/{number}/{provision}"
    return f"https://www.legislation.gov.uk/{law_type}/{year}/{number}"


def instrument_title(path_parts: list[str]) -> str:
    key = tuple(path_parts[1:4])
    return INSTRUMENT_TITLES.get(key, f"{path_parts[1].upper()} {path_parts[2]}/{path_parts[3]}")


def validate_source_path(source_path: str) -> None:
    if not source_path.startswith(SOURCE_PREFIXES):
        raise ValueError(
            f"Unsupported source_path {source_path!r}; expected one of {SOURCE_PREFIXES}"
        )


def extract_dc_title(xml_text: str) -> str | None:
    match = re.search(r"<dc:title>(.*?)</dc:title>", xml_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


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
            validate_source_path(raw["source_path"])
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
            if not case.rac_file.exists():
                print(f"Skipping missing historical RAC file: {case.rac_file}")
                continue
            cases_by_path[case.repo_rac_path] = case
    return sorted(cases_by_path.values(), key=lambda c: natural_key(c.repo_rac_path))


def node_label(parts: list[str], instrument_root_len: int) -> str:
    if parts == ["legislation"]:
        return TYPE_LABELS["legislation"]
    if len(parts) == 2 and parts[0] == "legislation":
        return TYPE_LABELS.get(parts[1], parts[1].upper())
    if len(parts) == 3 and parts[0] == "legislation":
        return parts[2]
    if len(parts) == instrument_root_len:
        return instrument_title(parts)

    tail = parts[instrument_root_len:]
    if len(tail) >= 2 and tail[-2] in STRUCTURAL_TOKENS:
        return f"{tail[-2].title()} {tail[-1]}"

    token = tail[-1]
    if "-" in token or "_" in token:
        return slug_to_title(token)
    return f"({token})"


def build_citation_boundaries(instrument_root: list[str], tail: list[str]) -> list[list[str]]:
    boundaries = [instrument_root[:i] for i in range(1, len(instrument_root) + 1)]
    consumed = 0
    while consumed < len(tail):
        if tail[consumed] in STRUCTURAL_TOKENS:
            token_boundary = instrument_root + tail[:consumed + 1]
            if boundaries[-1] != token_boundary:
                boundaries.append(token_boundary)
            consumed += 1
            if consumed >= len(tail):
                break
        consumed += 1
        boundaries.append(instrument_root + tail[:consumed])
    return boundaries


def build_boundaries(repo_rac_path: str) -> list[list[str]]:
    parts = Path(repo_rac_path).with_suffix("").parts
    instrument_root = list(parts[:4])  # legislation/type/year/number
    tail = list(parts[4:])
    return build_citation_boundaries(instrument_root, tail)


def all_repo_rac_paths() -> list[str]:
    paths: list[str] = []
    for rac_file in sorted(RAC_ROOT.rglob("*.rac")):
        if rac_file.name.endswith(".rac.test"):
            continue
        paths.append(str(rac_file.relative_to(ROOT)))
    return paths


def repo_leaf_source_url(repo_rac_path: str) -> str | None:
    parts = Path(repo_rac_path).with_suffix("").parts
    if len(parts) < 4 or parts[0] != "legislation":
        return None
    base = "https://www.legislation.gov.uk/" + "/".join(parts[1:4])
    if len(parts) == 4:
        return base
    return base + "/" + "/".join(parts[4:])


def leaf_body(case: Case) -> str:
    return extract_embedded_source(case.rac_file.read_text())


def leaf_source_url(case: Case) -> str | None:
    return infer_source_url(case.source_path)


def official_source_roots() -> list[Path]:
    return sorted(OFFICIAL_ROOT.glob("*/*/*/*/source.akn"))


def official_title(root: ET.Element) -> str | None:
    title = root.find(".//akn:proprietary/dc:title", AKN_NS)
    if title is not None and element_text(title):
        return element_text(title)
    return None


def official_point_in_time(root: ET.Element) -> str | None:
    for frbr_date in root.findall(".//akn:FRBRExpression/akn:FRBRdate", AKN_NS):
        if frbr_date.get("name") == "point-in-time":
            return frbr_date.get("date")
    for frbr_date in root.findall(".//akn:FRBRExpression/akn:FRBRdate", AKN_NS):
        if frbr_date.get("name") == "validFrom":
            return frbr_date.get("date")
    return None


def official_element_body(elem: ET.Element) -> str | None:
    blocks: list[str] = []
    for child in elem:
        name = local_name(child.tag)
        if name in {"num", "heading"}:
            text = element_text(child)
            if text:
                blocks.append(text)
    for paragraph in elem.iter():
        if local_name(paragraph.tag) != "p":
            continue
        text = element_text(paragraph)
        if text:
            blocks.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        if block in seen:
            continue
        seen.add(block)
        deduped.append(block)
    if not deduped:
        return None
    return "\n\n".join(deduped)


def official_source_url(instrument_root: list[str], tail: list[str]) -> str:
    base = "https://www.legislation.gov.uk/" + "/".join(instrument_root[1:4])
    if not tail:
        return base
    return base + "/" + "/".join(tail)


def build_official_rules() -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str | None, set[str]] = defaultdict(set)

    for akn_path in official_source_roots():
        rel_dir = akn_path.parent.relative_to(ROOT)
        rel_parts = rel_dir.parts
        instrument_root = ["legislation", rel_parts[2], rel_parts[3], rel_parts[4]]
        root = ET.fromstring(akn_path.read_text())
        title = official_title(root) or instrument_title(instrument_root)
        effective_date = official_point_in_time(root)

        for boundary in [instrument_root[:i] for i in range(1, len(instrument_root) + 1)]:
            citation_path = "uk/" + "/".join(boundary)
            parent_citation = "uk/" + "/".join(boundary[:-1]) if len(boundary) > 1 else None
            rule = nodes.get(citation_path)
            if not rule:
                heading = (
                    title if len(boundary) == 4 else node_label(boundary, instrument_root_len=4)
                )
                nodes[citation_path] = {
                    "id": deterministic_id(citation_path),
                    "jurisdiction": "uk",
                    "doc_type": "regulation",
                    "parent_id": deterministic_id(parent_citation) if parent_citation else None,
                    "level": len(boundary) - 1,
                    "ordinal": None,
                    "heading": heading,
                    "body": None,
                    "effective_date": effective_date if len(boundary) == 4 else None,
                    "repeal_date": None,
                    "source_url": official_source_url(instrument_root, boundary[4:]) if len(boundary) >= 4 else None,
                    "source_path": str(rel_dir) if len(boundary) == 4 else None,
                    "rac_path": None,
                    "has_rac": False,
                    "citation_path": citation_path,
                    "line_count": 0,
                }
            children_by_parent[parent_citation].add(citation_path)

        body = root.find(".//akn:body", AKN_NS)
        if body is None:
            continue

        for elem in body.iter():
            e_id = elem.get("eId")
            if not e_id:
                continue
            tail = e_id.split("-")
            if tail[0] not in PUBLISHABLE_ROOT_TOKENS:
                continue
            boundaries = build_citation_boundaries(instrument_root, tail)

            parent_citation: str | None = None
            if len(boundaries) > 1:
                parent_citation = "uk/" + "/".join(boundaries[-2])
            citation_path = "uk/" + "/".join(instrument_root + tail)
            heading = element_text(next((child for child in elem if local_name(child.tag) == "heading"), None))
            body_text = official_element_body(elem)

            for i, boundary in enumerate(boundaries):
                path_key = "uk/" + "/".join(boundary)
                parent_key = "uk/" + "/".join(boundary[:-1]) if len(boundary) > 1 else None
                rule = nodes.get(path_key)
                if not rule:
                    rule = {
                        "id": deterministic_id(path_key),
                        "jurisdiction": "uk",
                        "doc_type": "regulation",
                        "parent_id": deterministic_id(parent_key) if parent_key else None,
                        "level": len(boundary) - 1,
                        "ordinal": None,
                        "heading": node_label(boundary, instrument_root_len=4),
                        "body": None,
                        "effective_date": None,
                        "repeal_date": None,
                        "source_url": official_source_url(instrument_root, boundary[4:]) if len(boundary) >= 4 else None,
                        "source_path": None,
                        "rac_path": None,
                        "has_rac": False,
                        "citation_path": path_key,
                        "line_count": 0,
                    }
                    nodes[path_key] = rule
                children_by_parent[parent_key].add(path_key)

            rule = nodes[citation_path]
            if heading:
                rule["heading"] = heading
            if body_text:
                rule["body"] = body_text
                rule["line_count"] = len(body_text.splitlines())
            rule["effective_date"] = effective_date
            rule["source_url"] = official_source_url(instrument_root, tail)
            rule["source_path"] = str(rel_dir)
            if parent_citation:
                children_by_parent[parent_citation].add(citation_path)

    for parent_citation, child_paths in children_by_parent.items():
        sorted_paths = sorted(child_paths, key=lambda p: natural_key(p.split("/")[-1]))
        for ordinal, child_path in enumerate(sorted_paths, start=1):
            nodes[child_path]["ordinal"] = ordinal

    return nodes


def build_instrument_title_map(cases: list[Case]) -> dict[tuple[str, str, str], str]:
    titles = dict(INSTRUMENT_TITLES)
    for akn_path in official_source_roots():
        rel_parts = akn_path.parent.relative_to(ROOT).parts
        xml_path = akn_path.with_name("source.xml")
        if not xml_path.exists():
            continue
        title = extract_dc_title(xml_path.read_text())
        if title:
            titles[(rel_parts[2], rel_parts[3], rel_parts[4])] = title
    for case in cases:
        source_parts = Path(case.source_path).parts
        if len(source_parts) < 5 or source_parts[0] != "sources" or source_parts[1] != "official":
            continue
        key = (source_parts[2], source_parts[3], source_parts[4])
        xml_path = ROOT / case.source_path / "source.xml"
        if not xml_path.exists():
            continue
        title = extract_dc_title(xml_path.read_text())
        if title:
            titles[key] = title
    return titles


def build_repo_rules(cases: list[Case]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str | None, set[str]] = defaultdict(set)
    titles = build_instrument_title_map(cases)
    cases_by_path = {case.repo_rac_path: case for case in cases}

    for repo_rac_path in all_repo_rac_paths():
        case = cases_by_path.get(repo_rac_path)
        rac_file = ROOT / repo_rac_path
        boundaries = build_boundaries(repo_rac_path)
        parent_citation: str | None = None
        for i, boundary in enumerate(boundaries):
            citation_path = "uk/" + "/".join(boundary)
            is_leaf = i == len(boundaries) - 1
            path_key = citation_path
            parent_id = deterministic_id(parent_citation) if parent_citation else None
            label = (
                titles.get(tuple(boundary[1:4]), instrument_title(boundary))
                if len(boundary) == 4
                else node_label(boundary, instrument_root_len=4)
            )

            rule = nodes.get(path_key)
            if not rule:
                rule = {
                    "id": deterministic_id(citation_path),
                    "jurisdiction": "uk",
                    "doc_type": "regulation",
                    "parent_id": parent_id,
                    "level": len(boundary) - 1,
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
                body = extract_embedded_source(rac_file.read_text())
                rule["body"] = body
                rule["effective_date"] = extract_effective_date(body)
                rule["source_url"] = leaf_source_url(case) if case else repo_leaf_source_url(repo_rac_path)
                rule["source_path"] = case.source_path if case else None
                rule["rac_path"] = repo_rac_path
                rule["has_rac"] = True
                rule["line_count"] = len(body.splitlines()) if body else 0
            else:
                # Prefer instrument source_url on the root node if we can infer it.
                if len(boundary) == 4 and not rule["source_url"]:
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


def merge_rules(official: dict[str, dict[str, Any]], repo_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = {path: dict(rule) for path, rule in official.items()}
    for repo_rule in repo_rules:
        citation_path = repo_rule["citation_path"]
        existing = nodes.get(citation_path)
        if not existing:
            nodes[citation_path] = dict(repo_rule)
            continue
        existing["has_rac"] = existing.get("has_rac", False) or repo_rule.get("has_rac", False)
        if repo_rule.get("rac_path"):
            existing["rac_path"] = repo_rule["rac_path"]

        source_path = repo_rule.get("source_path")
        if source_path and source_path.startswith(SLICE_PREFIX):
            for key in ("heading", "body", "effective_date", "source_url", "source_path", "line_count"):
                if repo_rule.get(key) is not None:
                    existing[key] = repo_rule[key]
        else:
            if source_path:
                existing["source_path"] = source_path
            if repo_rule.get("source_url") and not existing.get("source_url"):
                existing["source_url"] = repo_rule["source_url"]
            if repo_rule.get("effective_date") and not existing.get("effective_date"):
                existing["effective_date"] = repo_rule["effective_date"]
            if not existing.get("heading") and repo_rule.get("heading"):
                existing["heading"] = repo_rule["heading"]
            if not existing.get("body") and repo_rule.get("body"):
                existing["body"] = repo_rule["body"]
            existing["line_count"] = max(existing.get("line_count", 0), repo_rule.get("line_count", 0))

    children_by_parent: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes.values():
        children_by_parent[node["parent_id"]].append(node)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda node: natural_key(node["citation_path"].split("/")[-1]))
        for ordinal, node in enumerate(siblings, start=1):
            node["ordinal"] = ordinal

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
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rule in rules:
        grouped[int(rule["level"])].append(rule)
    for level in sorted(grouped):
        for batch in chunked(grouped[level], batch_size):
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


def fetch_rule_refs(
    service_key: str, supabase_url: str, query: str, page_size: int
) -> list[dict[str, Any]]:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept-Profile": "arch",
        "Content-Profile": "arch",
    }
    base_url = (
        supabase_url.rstrip("/")
        + "/rest/v1/rules?select=citation_path,level&order=level.desc,citation_path&"
        + query
    )
    rows_out: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = f"{base_url}&limit={page_size}&offset={offset}"
        response = requests.get(url, headers=headers, timeout=180)
        response.raise_for_status()
        rows = response.json()
        rows_out.extend(rows)
        if len(rows) < page_size:
            break
        offset += len(rows)
    return rows_out


def delete_rules_where(service_key: str, supabase_url: str, query: str) -> None:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept-Profile": "arch",
        "Content-Profile": "arch",
        "Prefer": "count=exact,return=minimal",
    }
    url = supabase_url.rstrip("/") + "/rest/v1/rules?" + query
    response = requests.delete(url, headers=headers, timeout=180)
    if response.status_code not in {200, 204, 404}:
        response.raise_for_status()


def delete_managed_rules(service_key: str, supabase_url: str, batch_size: int) -> None:
    instrument_refs = fetch_rule_refs(
        service_key,
        supabase_url,
        "level=eq.3&citation_path=like." + quote("uk/legislation/%", safe=""),
        page_size=batch_size,
    )
    for row in instrument_refs:
        citation_path = row["citation_path"]
        delete_rules_where(
            service_key,
            supabase_url,
            "citation_path=like." + quote(citation_path + "/%", safe=""),
        )
        delete_rules_where(
            service_key,
            supabase_url,
            "citation_path=eq." + quote(citation_path, safe=""),
        )

    scaffold_refs = fetch_rule_refs(
        service_key,
        supabase_url,
        "level=lt.3&citation_path=like." + quote("uk/legislation%", safe=""),
        page_size=batch_size,
    )
    grouped_paths: dict[int, list[str]] = defaultdict(list)
    for row in scaffold_refs:
        grouped_paths[int(row["level"])].append(row["citation_path"])
    for level in sorted(grouped_paths, reverse=True):
        for citation_path in grouped_paths[level]:
            delete_rules_where(
                service_key,
                supabase_url,
                "citation_path=eq." + quote(citation_path, safe=""),
            )


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
    rules = merge_rules(build_official_rules(), build_repo_rules(cases))
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
            delete_managed_rules(service_key, supabase_url, args.batch_size)
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

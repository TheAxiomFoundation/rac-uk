from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


BLOCK_HEADER = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")
FIELD_LINE = re.compile(r"^\s{4}([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


@dataclass
class VariableOccurrence:
    name: str
    file: str
    entity: str | None
    period: str | None
    dtype: str | None

    @property
    def signature(self) -> tuple[str | None, str | None, str | None]:
        return (self.entity, self.period, self.dtype)


def extract_variable_occurrences(rac_file: Path, root: Path) -> list[VariableOccurrence]:
    """Extract top-level RAC variable declarations from one file."""
    lines = rac_file.read_text().splitlines()
    occurrences: list[VariableOccurrence] = []
    index = 0
    while index < len(lines):
        match = BLOCK_HEADER.match(lines[index])
        if not match:
            index += 1
            continue

        name = match.group(1)
        fields: dict[str, str] = {}
        index += 1
        while index < len(lines):
            field_match = FIELD_LINE.match(lines[index])
            if field_match:
                fields[field_match.group(1)] = field_match.group(2).strip() or None
                index += 1
                continue
            if lines[index] and not lines[index].startswith(" "):
                break
            index += 1

        if any(fields.get(key) for key in ("entity", "period", "dtype")):
            occurrences.append(
                VariableOccurrence(
                    name=name,
                    file=str(rac_file.relative_to(root)),
                    entity=fields.get("entity"),
                    period=fields.get("period"),
                    dtype=fields.get("dtype"),
                )
            )

    return occurrences


def build_report(root: Path, include_singletons: bool = False) -> list[dict[str, object]]:
    """Aggregate variable declarations across the corpus."""
    grouped: dict[str, list[VariableOccurrence]] = defaultdict(list)
    for rac_file in sorted((root / "legislation").rglob("*.rac")):
        if rac_file.name.endswith(".rac.test"):
            continue
        for occurrence in extract_variable_occurrences(rac_file, root):
            grouped[occurrence.name].append(occurrence)

    rows: list[dict[str, object]] = []
    for name, occurrences in grouped.items():
        if not include_singletons and len(occurrences) < 2:
            continue
        signatures = sorted({occ.signature for occ in occurrences})
        rows.append(
            {
                "name": name,
                "count": len(occurrences),
                "consistent": len(signatures) == 1,
                "signatures": [
                    {
                        "entity": entity,
                        "period": period,
                        "dtype": dtype,
                    }
                    for entity, period, dtype in signatures
                ],
                "files": [occ.file for occ in occurrences],
            }
        )

    rows.sort(key=lambda row: (-int(row["count"]), row["name"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report repeated RAC variable declarations across rac-uk."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to rac-uk checkout).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include singleton declarations as well as repeated variables.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a text summary.",
    )
    args = parser.parse_args()

    rows = build_report(args.root, include_singletons=args.all)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No repeated variable declarations found.")
        return 0

    print("Repeated variable declarations:")
    for row in rows:
        status = "consistent" if row["consistent"] else "DIVERGENT"
        print(f"- {row['name']}: {row['count']} files ({status})")
        for signature in row["signatures"]:
            print(
                "  "
                f"entity={signature['entity']} period={signature['period']} dtype={signature['dtype']}"
            )
        preview_files = row["files"][:5]
        for file_path in preview_files:
            print(f"  {file_path}")
        remaining = len(row["files"]) - len(preview_files)
        if remaining > 0:
            print(f"  ... {remaining} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

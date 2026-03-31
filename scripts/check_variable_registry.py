from __future__ import annotations

import argparse
import fnmatch
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import report_variables


@dataclass
class RegistryEntry:
    name: str
    entity: str
    period: str
    dtype: str
    paths: list[str]
    description: str | None = None

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.entity, self.period, self.dtype)

    def matches(self, occurrence: report_variables.VariableOccurrence) -> bool:
        if occurrence.name != self.name:
            return False
        return any(fnmatch.fnmatch(occurrence.file, pattern) for pattern in self.paths)


def load_registry(path: Path) -> list[RegistryEntry]:
    payload = tomllib.loads(path.read_text())
    entries: list[RegistryEntry] = []
    for raw in payload.get("variable", []):
        entries.append(
            RegistryEntry(
                name=raw["name"],
                entity=raw["entity"],
                period=raw["period"],
                dtype=raw["dtype"],
                paths=list(raw["paths"]),
                description=raw.get("description"),
            )
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate scoped canonical variable declarations for rac-uk."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to rac-uk checkout).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Registry TOML path (defaults to <root>/variables.toml).",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    registry_path = (args.registry or root / "variables.toml").resolve()
    entries = load_registry(registry_path)

    occurrences: list[report_variables.VariableOccurrence] = []
    for rac_file in sorted((root / "legislation").rglob("*.rac")):
        if rac_file.name.endswith(".rac.test"):
            continue
        occurrences.extend(report_variables.extract_variable_occurrences(rac_file, root))

    drift_messages: list[str] = []
    unused_entries: list[RegistryEntry] = []
    for entry in entries:
        matched = [occ for occ in occurrences if entry.matches(occ)]
        if not matched:
            unused_entries.append(entry)
            continue
        for occurrence in matched:
            if occurrence.signature != entry.signature:
                drift_messages.append(
                    f"{occurrence.file}: {occurrence.name} has "
                    f"{occurrence.signature} but registry expects {entry.signature}"
                )

    if drift_messages:
        print("Canonical variable drift detected:")
        for message in drift_messages:
            print(f"- {message}")
        return 1

    print(
        "Canonical variable registry matches all scoped declarations "
        f"({len(entries)} entries)."
    )
    if unused_entries:
        print("Unused registry entries:")
        for entry in unused_entries:
            print(f"- {entry.name} ({', '.join(entry.paths)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

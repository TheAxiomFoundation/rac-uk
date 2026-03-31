from __future__ import annotations

import argparse
from pathlib import Path

import report_embedded_scalars


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail if a RAC file embeds substantive scalar literals inside formulas."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to rac-uk checkout).",
    )
    args = parser.parse_args()

    rows = report_embedded_scalars.build_report(args.root.resolve())
    if not rows:
        print("No embedded scalar literals found in formulas.")
        return 0

    print("Embedded scalar violations detected:")
    for row in rows:
        print(
            f"- {row['file']}:{row['line']} {row['variable']} embeds "
            f"{row['literal']} in `{row['expression']}`"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

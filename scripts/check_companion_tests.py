from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "legislation"
    missing: list[Path] = []

    for rac_file in sorted(root.rglob("*.rac")):
        if rac_file.name.endswith(".rac.test"):
            continue
        companion = rac_file.with_suffix(rac_file.suffix + ".test")
        if not companion.exists():
            missing.append(rac_file)

    if missing:
        print("Missing companion .rac.test files:", file=sys.stderr)
        for path in missing:
            print(path.relative_to(root.parent), file=sys.stderr)
        return 1

    print("All .rac files have companion .rac.test files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# rac-uk

UK tax-benefit policy encodings in RAC.

This repo currently contains:

- wave 1: `22` atomic UK leaves from the clean `autorac` expanded UK suite
- wave 2: `3` additional WTC schedule-2 atomic leaves promoted from clean `eval-source` runs
- wave 3: `4` additional UC child-disability and work-allowance leaves promoted from clean `eval-source` runs
- wave 4: `3` additional UC childcare-cap and housing non-dependant-deduction leaves promoted from clean `eval-source` runs
- wave 5: `6` additional Pension Credit atomic leaves promoted from the clean committed `autorac` UK expanded suite
- wave 6: `5` additional Pension Credit savings credit leaves promoted from the clean committed `autorac` UK expanded suite
- wave 7: `5` additional Pension Credit prisoner and earnings-disregard leaves promoted from clean committed `eval-suite` runs
- wave 8: `5` additional Pension Credit special-employment and shared-maximum leaves promoted from clean committed `eval-suite` runs
- wave 9: `12` additional official UK leaves promoted from the clean committed `wave9` seed suite
- wave 10: `2` additional UC taper-rate sibling leaves promoted from the clean committed split-clause suite
- current total: `66` `.rac` leaves with companion tests

Wave provenance is recorded in:

- [waves/2026-03-30-wave1/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave1/manifest.json)
- [waves/2026-03-30-wave2/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave2/manifest.json)
- [waves/2026-03-30-wave3/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave3/manifest.json)
- [waves/2026-03-30-wave4/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave4/manifest.json)
- [waves/2026-03-31-wave5/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-31-wave5/manifest.json)
- [waves/2026-03-31-wave6/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-31-wave6/manifest.json)
- [waves/2026-03-31-wave7/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-31-wave7/manifest.json)
- [waves/2026-03-31-wave8/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-31-wave8/manifest.json)
- [waves/2026-03-31-wave9/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-31-wave9/manifest.json)
- [waves/2026-04-01-wave10/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-04-01-wave10/manifest.json)

## Structure

The canonical tree is organized by legal citation, not by benchmark case name:

```text
rac-uk/
├── legislation/
│   ├── uksi/2013/376/regulation/80A/2/b/i.rac
│   ├── uksi/2013/376/regulation/36/3/single-under-25.rac
│   └── ssi/2020/351/regulation/20/1.rac
├── sources/
│   ├── official/   # AKN + CLML from legislation.gov.uk
│   └── slices/     # Normalized row/element source text used for derived leaves
└── waves/
    └── 2026-03-30-wave1/
```

## Source policy

- Before encoding leaves from an instrument, fetch and check in the full official point-in-time instrument source under `sources/official/{type}/{year}/{number}/{date}/`.
- Use `scripts/fetch_official_instruments.py` to derive those full-instrument fetch targets from the current wave manifests.
- Prefer official `legislation.gov.uk` AKN for executable encoding work.
- Preserve the corresponding `data.xml` CLML alongside it for provenance and fallback.
- For table rows or derived atomic leaves that are not directly addressable as official AKN nodes, keep the normalized text slice under `sources/slices/`.
- In Atlas syncs, `source_path` is the discriminator:
  `sources/official/...` means an official legal node, while `sources/slices/...` means a derived row/slice leaf under the same parent provision.

## Atlas sync policy

- The `rac-uk` sync publishes repo-managed UK nodes under `uk/legislation/...` in `arch.rules`.
- It syncs the full official AKN-derived instrument tree for the checked-in instrument snapshots, then overlays the repo's encoded leaves and derived row/slice leaves on top of that official structure.
- This is separate from any broader UK legislation ingest that may use `uk/statute/...` for act/section archives.
- The sync is replace-oriented, not append-only: it deletes the currently managed `uk/legislation/...` archive subtree and `rac-uk:*` encoding rows before reinserting the current repo state, so renames do not leave ghost nodes behind.
- The replacement step is operationally non-atomic across `arch.rules` and `encoding_runs`. If a sync is interrupted, rerun `scripts/sync_atlas.py` to restore the full managed subset. `--append-only` is reserved for topping off a partially inserted run without another delete pass.
- Instrument titles should come from checked-in official CLML when available. The hardcoded map in the sync script is only a fallback for slice-only instruments that do not yet have local official source files.

## Encoding policy

- Default to the most atomic subsection or row possible.
- Only encode a parent provision when the atomic children are insufficient and that decision is explicit.
- If text explicitly incorporates a definition from another legal source, import it or create the upstream stub rather than restating it locally.

## Validation

The repo includes a lightweight validation workflow that:

- runs `rac.validate` across `legislation/`
- verifies that every `.rac` file has a companion `.rac.test`
- checks the first scoped canonical variable registry in [variables.toml](/Users/maxghenis/TheAxiomFoundation/rac-uk/variables.toml)
- fails if a substantive scalar literal is embedded inside a formula or conditional branch instead of being declared as its own named variable
- enforces a tracked baseline for source-number coverage, so new or worsened gaps fail even though the existing UK corpus still has a small backlog of known misses

Run the full local repo validation with:

```bash
python3 scripts/validate_repo.py
```

## Variable inventory

To audit repeated local declarations before a larger bulk wave:

```bash
python3 scripts/report_variables.py
```

Use `--json` for machine-readable output or `--all` to include singleton declarations.

To enforce the current scoped canonical set:

```bash
python3 scripts/check_variable_registry.py
```

To report or fail on embedded scalar literals inside formulas:

```bash
python3 scripts/report_embedded_scalars.py
python3 scripts/check_embedded_scalars.py
```

`report_embedded_scalars.py --json` emits one row per violation with the file, line,
variable name, literal, and formula snippet.

To audit or check whether substantive source numbers are represented by named RAC
scalar definitions:

```bash
python3 scripts/report_numeric_occurrence_coverage.py
python3 scripts/check_numeric_occurrence_coverage.py
```

`report_numeric_occurrence_coverage.py --json` emits one row per missing source-number
occurrence with the file, numeric value, source occurrence count, named scalar count,
and remaining missing count.

`check_numeric_occurrence_coverage.py` is baseline-aware. It does not require the whole
existing corpus to be fixed at once; it only fails when a coverage gap is new or worse
than the tracked baseline in
[validation_baselines/numeric_occurrence_coverage.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/validation_baselines/numeric_occurrence_coverage.json).

The registry is intentionally narrow and path-scoped. It currently covers:

- Pension Credit `claimant_has_partner` declarations
- Benefit Cap claimant-status helpers under `80A`

Variables with the same name but different semantics across programs should stay out of
the registry until they are renamed or explicitly scoped.

This is still an early UK corpus, not a complete encoding set.

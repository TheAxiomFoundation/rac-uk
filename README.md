# rac-uk

UK tax-benefit policy encodings in RAC.

This repo currently contains:

- wave 1: `22` atomic UK leaves from the clean `autorac` expanded UK suite
- wave 2: `3` additional WTC schedule-2 atomic leaves promoted from clean `eval-source` runs
- wave 3: `4` additional UC child-disability and work-allowance leaves promoted from clean `eval-source` runs
- current total: `29` `.rac` leaves with companion tests

Wave provenance is recorded in:

- [waves/2026-03-30-wave1/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave1/manifest.json)
- [waves/2026-03-30-wave2/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave2/manifest.json)
- [waves/2026-03-30-wave3/manifest.json](/Users/maxghenis/TheAxiomFoundation/rac-uk/waves/2026-03-30-wave3/manifest.json)

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

- Prefer official `legislation.gov.uk` AKN for executable encoding work.
- Preserve the corresponding `data.xml` CLML alongside it for provenance and fallback.
- For table rows or derived atomic leaves that are not directly addressable as official AKN nodes, keep the normalized text slice under `sources/slices/`.

## Encoding policy

- Default to the most atomic subsection or row possible.
- Only encode a parent provision when the atomic children are insufficient and that decision is explicit.
- If text explicitly incorporates a definition from another legal source, import it or create the upstream stub rather than restating it locally.

## Validation

The repo includes a lightweight validation workflow that:

- runs `rac.validate` across `legislation/`
- verifies that every `.rac` file has a companion `.rac.test`

This is still an early UK corpus, not a complete encoding set.

# rac-uk

**THE home for UK tax-benefit law encodings.**

All UK-specific `.rac` files belong here, not in `rac`.

## Structure

Use legal citation as the primary path:

```text
legislation/<type>/<year>/<number>/<unit>/<unit-number>/...
```

Examples:

- `legislation/uksi/2006/965/regulation/2/1/a.rac`
- `legislation/uksi/2013/376/regulation/80A/2/b/i.rac`
- `legislation/ssi/2020/351/regulation/20/1.rac`

For derived atomic leaves from tables or schedules that are not directly addressable as official AKN nodes, keep them under the legislative location they come from:

- `legislation/uksi/2013/376/regulation/36/3/single-under-25.rac`
- `legislation/uksi/2002/2005/schedule/2/basic-element.rac`

## Source preservation

- Official legislation sources live under `sources/official/` and should include both `source.akn` and `source.xml`.
- Normalized row or paragraph slices live under `sources/slices/`.
- Wave-level provenance lives under `waves/`.

## UK encoding rules

- Default to the most atomic subsection or row possible.
- Do not flatten sibling leaves into one file just because the source filename would otherwise collide.
- If a provision explicitly points to another source for a definition, import it or create the upstream stub there.
- If a helper is only a leaf-local conjunction, keep it local.
- Do not invent parent-level abstractions when a leaf-specific branch variable is what the law states.

## Current waves

The current corpus includes:

- wave 1 from the clean `autorac` UK expanded suite run on March 30, 2026
- wave 2 from clean `autorac eval-source` WTC schedule-2 row runs on March 30, 2026
- wave 3 from clean `autorac eval-source` UC child-disability and work-allowance row runs on March 30, 2026

These waves are provenance-backed and benchmark-quality, but they are still an early corpus rather than a complete UK encoding set.

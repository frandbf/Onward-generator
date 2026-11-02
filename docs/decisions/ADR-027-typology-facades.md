# ADR-027 — The typology and the era tune the facade

Status: proposed · 1 November 2026. Fourth increment of the [facades, typology and triangle budget plan](../plans/2026-10-31-facades-typology-budget.md), first half. Builds on [ADR-024](ADR-024-building-typology.md).

## Context

ADR-024 recorded a typology and an era per building and changed nothing drawn. A nineteenth-century block and a 1960s slab still shared one grammar. The grammar already reads the rhythm, the windows and the balconies from the template, so the difference needs parameters, not new geometry.

## Decision

1. **`facades` in `config/typology.default.json`,** validated by its schema, with `bay_factor`, `window_width_factor`, `window_height_factor` (0.7–1.4), `balcony_share` and a wall `palette`, per typology and per era.
2. **The era goes over the typology,** because it is an observation and the typology is an estimate.
3. **A template copy per building** (`styled_template`). Layout's checks are unchanged, so an opening that does not fit still falls back.
4. **Palettes only for a residential use.** A shop, an office or a school keeps the palette of its use. Observed OSM colours and materials still win, and a landmark keeps its walls; its template has no windows to tune.
5. **Recorded:** `facade.typology.facade_style`; `scene_detail` and `detail_report` 0.4.0.

## Rejected alternatives

- **New templates per typology.** Most of a template does not change, and each would have to pass the template checks and the shop and door rules again.
- **An era from form.** Unknown stays unknown (ADR-024): a building with no era gets its typology's style.

## Not done

- **Atlas strips by era.** The trim atlas (ADR-020) still has one set of wall strips and window cells, so a 1960s window is wider but painted like any other. A new strip costs texture memory, which ASTC has only just freed and nobody has measured on the headset.
- **A budget by visibility.** The core already costs the same at any radius (ADR-017) and ADR-025 took about 12 % of the triangles, so it waits for a measured need.

## Consequences

- Facades differ by typology and era wherever those are known; on a map with no era, only by form.
- Triangles change with the bays and balconies; see the table in the pull request. Whether the styles look right is a headset question: the values are a first guess.

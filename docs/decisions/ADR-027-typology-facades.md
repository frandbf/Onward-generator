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

- **Atlas strips by era.** The trim atlas (ADR-020) still has one set of wall strips and window cells, so a 1960s window is wider but painted like any other. A new strip costs texture memory, which ASTC has only just freed and nobody has measured on the headset. *Partly done by the amendment of 8 November 2026 below, as looks rather than strips, and only for the regional atlases behind an option.*
- **A budget by visibility.** The core already costs the same at any radius (ADR-017) and ADR-025 took about 12 % of the triangles, so it waits for a measured need.

## Consequences

- Facades differ by typology and era wherever those are known; on a map with no era, only by form.
- Triangles change with the bays and balconies; see the table in the pull request. Whether the styles look right is a headset question: the values are a first guess.
- **Amendment (8 November 2026): era bands as looks.** Status: proposed until the headset session. Plan 2026-11-07, increment D1, with the ADR-020 amendment of the same day. An atlas may give `eras`: for a period of `detail/typology.py` (`pre_1900` … `2000_plus`), the tones a building may take, the window families it may take, and the dress shares (zocalo, cornice, string course) that change. No strip is added, so the texture memory does not change; the trade is fewer tones per era. The trim pass reads `facade.typology.era` of the building record and uses it only when its `origin` is `observed` and its `period` is a string. A building with no era, an era that is not observed, or a period with no band keeps exactly the look it had (ADR-024: unknown stays unknown). With a band the tone is drawn by `sha256` of the look key from the band's tones, the window family from the band's families, and the dress shares the band names override the style's. The era is observed; the band chosen from it is an estimate: `materials_report.json` `facades.era_bands` says `era_origin: observed`, `band_origin: estimated` and counts buildings per period, `no_band` and `unknown` (also `stats.eras`). Only the regional recipes (`config/facade_styles.regional.json`) declare eras; the mediterranean atlas has none, so Murcia and Madrid, which have observed eras, do not change. In those recipes older periods keep the traditional windows and full dress, and 1960 onwards takes the metal, uPVC or aluminium casement with little or no cornice and string course. On the Kraków bench map only 1 of 155 trimmed buildings has an observed era, so the effect there is small; whether the bands read right is a headset question.

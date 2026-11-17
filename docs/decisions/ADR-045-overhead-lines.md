# ADR-045: Overhead power lines

Status: proposed · 17 November 2026. The maintainer asked for it; plan [2026-11-10-atmosphere-advanced](../plans/2026-11-10-atmosphere-advanced.md), item C5.

## Context

The streets of Mogadishu, Fallujah or many Latin American cities carry overhead power lines: poles at the kerb, wires along the street and drops to every house. A modern European centre such as Murcia, Madrid or Paris has its lines underground, and poles there would read wrong. OSM rarely maps a pole in the wired cities: none of the local maps, Mogadishu and Fallujah included, has a single `power=pole`, `power=line` or `power=minor_line`, although the extract keeps every node and way. So the lines must be generated.

## Decision

1. **When.** `config/overhead_lines.json` (schema `config/overhead_lines.schema.json`) lists the facade styles (`config/facade_styles.json`) whose maps are wired by default: `east_african`, `arid` and `latin_american`. The style is the one materials and detail resolve: `map.json` `facades.style`, else the box of the map's centre. `map.json` `"overhead_lines": true` or `false` overrides it either way (`overgrow place --overhead-lines on|off`; `run` passes it only when the key is there). Any other value is refused with `MapError`. A map that is not wired builds exactly what it built before, and its report has no `overhead_lines` block.
2. **Poles.** Along each street centre line of a listed `highway` (`primary` to `service` and `track`, not footways), longest first, a pole every `spacing_m` (28 m), starting half a spacing in. It stands past the street's half width (the geo scene's `width_m`, else 3 m) by `kerb_m` (0.5 m), or nearer the axis when that would leave less than `back_m` (1.4 m) to the building behind; nearer the axis than `min_axis_m` (1.5 m) there is no room (`narrow`). The side is chosen by a hash of the seed and the street, else the other side. A pole is left out outside the zone, near a spawn, within `clear_m` + `back_m` of a door, within `clear_m` (1 m) of a street lamp, a pinned prop, a terrace or another prop's footprint, and within four tenths of the spacing of another pole (where two streets meet). At most `max_poles` (40).
3. **Wires.** Two poles of one street in a row are wired with three conductors resting on the crossarm when they stand at most `max_span_m` (40 m) apart and the span crosses no building for more than 5 cm. Each pole sends up to `drops_per_pole` (2, by hash: 0, 1 or 2) service drops to the nearest facades within `drop_reach_m` (14 m) whose building is tall enough: a drop lands 5 cm out of the wall, at most `drop_max_z_m` (5 m) up and 0.4 m under the roof, never under `drop_min_z_m` (3 m), and its way crosses no building. A wire sags `sag_ratio` (3 %) of its length at mid-span. A pole with neither a span nor a drop is left out (`unwired`): a real one always carries a wire.
4. **Geometry.** All drawn by code, with no download: a tapered eight-sided capped pole (8 m, 0.12 m at the foot) with a 1.4 m crossarm, 34 triangles; a wire is two crossed ribbons 3.5 cm wide, one flat and one upright, on eight segments, drawn both ways on their own vertices, 64 triangles. One generated material, `overhead_line`: the poles' weathered grey over the wires' near black. The Unity adapter lights it by light probes, as the awning cloth.
5. **Gameplay.** A pole writes a 0.3 m box collider its full height (`overgrow/collider/pole/<n>`); a wire never collides and hangs above head height. The poles' footprints keep the props `dress` places off them.
6. **Budget.** At most a quarter of overgrow's triangle headroom, as the terraces and the awnings: poles with their spans first, street by street, then the drops.
7. **Records.** Nodes `overgrow/power/pole/<n>` and `overgrow/power/wire/<n>`; `scene_detail.json` records each as an `overgrowth` object of kind `power_pole` (role `pole`, `size_m` 0.3 × 0.3 × 8) or `power_line` (role `span` or `drop`), origin `generated`. `overgrow_report.json` gains `overhead_lines`: style, the map's request, poles, spans, drops, triangles, colliders and why poles were left out.

## Consequences

- On Mogadishu (east_african, r 150 m) the rule stands 16 poles with 9 spans and 19 drops, 3 488 triangles; 55 spots fall outside the playable core, 4 near a spawn, 2 on streets too narrow, 2 near another pole, 1 on another prop and 4 poles carried no wire.
- A lane that OSM does not map as a `highway` gets no pole, so a dense block with unmapped alleys stays unwired inside.
- An observed `power=pole` or `power=line` is not read yet: when a wired city maps them, using them first, as `observed`, is the next step.
- Thin wires may shimmer on the Quest; a headset session decides whether 3.5 cm is right.
- Proposed until a headset session checks a wired map.

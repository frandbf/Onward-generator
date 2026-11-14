# ADR-040 — Library trees with alpha cut-out leaves

Status: proposed · 14 November 2026. Asked by the maintainer: use the *Horned Holly Tree* package's trees instead of the procedural ones. It stays proposed until a headset session has looked at the leaves (clipping, both faces, the bake) and measured the frame time on a map with them.

## Context

- `detail` stands a procedural tree on each OSM tree and along the streets: a lathed canopy on a trunk, one of three shapes, drawn opaque. The local *Horned Holly Tree* catalogue held only a rock: the exporter refused every model with an alpha cut-out material (`transparent_material`), and the library refuses any `alphaMode` other than `OPAQUE`.
- The SDK's `PP_Opaque_Simple_Lit` graph has alpha clipping built in: with the material keyword `ALPHACLIPPING_ON` its Alpha is the Base Map's alpha, clipped at `_AlphaClipping` (0.5 by default). The graph renders front faces only (`m_RenderFace: 2`). No shader is added and the graph is not edited.

## Decision

1. **Import.** The Asset Store exporter keeps an opaque alpha cut-out (alpha clip on, no blend surface or keyword, queue under Transparent): it writes the albedo with its alpha and an `onward_cutoff T` line from `_Cutoff`. `objglb` makes that material `alphaMode: MASK` with `alphaCutoff` T and writes each of its triangles twice, the second facing the other way, because the shader draws one face. The classifier names trees (`tree`, at least 2 m tall) and bushes (`bush`, `shrub`, `hedge`; a model's own bush word beats a package's tree word).
2. **Library.** A `MASK` material is accepted on a `vegetation` row with role `tree` or `bush` only; a cut-out fence or sign stays refused. A prop with cut-out parts is never decimated (clustering would merge the leaf cards and their two faces).
3. **Street trees.** `overgrow place` draws `detail`'s trees with the library's cut-out trees of at most 2 400 triangles and at least 3 m (`overgrow/trees.py`): the same node, name, extras, place and trunk collider; the trunk on the node's origin; a uniform scale in the node's matrix so the crown reaches as far from the trunk as the procedural one did, so a crown `detail` narrowed off a facade stays narrowed. A scale outside 0.6 to 1.6 keeps the procedural tree. The trees nearest the centre go first while they spend at most half of the triangle headroom left after the lamp posts; the street props keep the rest. Swapped and cut-out materials stay out of the prop atlas, which is RGB.
4. **Export.** The visual GLB lists the cut-out materials in `extras.materials.cutouts` (material to threshold); `signs place` and `overgrow place` carry it with the normal maps. `export package` writes `adapter_options.json` `cutout_materials: [{material, cutoff}]` and the manifest's `cutout_materials`, and refuses a name that is not a material of the GLB or a threshold outside (0, 1). The adapter turns on `ALPHACLIPPING_ON`, sets `_AlphaClipping` and puts the material in the alpha-test queue. A scene without the list gets no option and the same `package_id`.

## Consequences

- Alpha testing costs more on a tile-based GPU than opaque drawing: early depth rejection is lost on those pixels. With about 30 trees of 1 500 to 2 100 triangles in view it is to be measured on the headset (frame time) before the ADR is accepted.
- Triangles: Horned Holly Tree 2 and 3 cost 2 127 and 1 539 (both faces of the leaves), against 24 to 200 for a procedural tree; the share of the headroom bounds them.
- The lightmapper bakes the leaves as solid casters unless it reads the alpha; how the bake looks is part of the headset check.
- The procedural trees stay for a map without a cut-out tree catalogue, which includes every map built from the committed catalogues alone.

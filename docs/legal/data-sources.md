# Data sources and licences

An operational summary, not legal advice. The binding text is each licence.

## OpenStreetMap

| Field | Value |
| --- | --- |
| Provider | `openstreetmap` |
| Licence | Open Database License 1.0 (`ODbL-1.0`) |
| URL | https://www.openstreetmap.org/copyright |
| Required attribution | `© OpenStreetMap contributors` |

Obligations that apply to this project:

- **Attribution.** A derived product that is shown or distributed, including an Onward map, must attribute OpenStreetMap and its contributors in a reasonable place. For a map, the Workshop description or the credits are the candidates.
- **Share-alike.** If a derived database is distributed, for example a generated `geo_scene.json`, it must be offered under the ODbL. An image or a 3D model produced from the data is a Produced Work. That case is different, and the attribution obligation remains.
- **Keep the licence notice.** The pipeline copies provider, licence, URL, and attribution into `geo_scene.json` and `geo_report.json`.

The licence text is https://opendatacommons.org/licenses/odbl/1-0/.

`map create` makes one Overpass request for the map square plus its margin. The area comes from latitude, longitude, and radius, measured in the map CRS and converted to WGS84. `geo` and the later stages do not download OSM again. Repeating the request is `osm download --map DIRECTORY --overwrite`. The request asks for every node and way in the box and only the `type=multipolygon` relations; the `.source.json` records that in `relation_types`. The body is `data/raw/<id>.osm`, with provider, licence, query, relation types, bounding box, and SHA-256 in the `.source.json` beside it. Limit: 32 MiB, and a side of at most 4100 m. In central Madrid the answer at radius 150 m went from 3.6 MB with every relation to 0.6 MB with multipolygons only, with the same `geo_scene` features. The 32 MiB cap is therefore reached at about radius 1100 m in a dense centre, up from about 880 m before the filter; both are estimates from r300 and r500, and Overpass's own `[timeout:90]` may stop a large request first. The source file is never modified.

## What the pipeline records

Each source file is a `SourceDataset`:

- `provider`, `license`, `license_url`, and `attribution`.
- `path`, `sha256`, and `size_bytes`. The hash is checked again before and after processing. If the file changed, the pipeline stops.
- `retrieved_at`. If `--retrieved-at` is omitted, the file modification time is used and `retrieved_at_origin` is `"file_mtime"`.
- `data_timestamp`: the OSM file's own timestamp (`<meta osm_base>` from Overpass, or the `timestamp` attribute), when it exists.
- `registered_at`: when the record was written.

## What stays out of git

- `maps/*/data/` is gitignored. Large OSM extracts are not committed.
- `tests/fixtures/geo/` and `tests/fixtures/cadastre/` are synthetic data created for this project, under CC0-1.0. They are not OpenStreetMap and they are not Catastro. The README in the fixture folder says so.
- Do not commit credentials, private SDK files, or a downloaded GML.

## Catastro INSPIRE Buildings (BU)

Spain only, without Navarre and the Basque Country, which have their own foral cadastres and are not used. The peninsula, the Balearic and Canary Islands, Ceuta and Melilla: one provider, one licence (`docs/05-cadastre.md#coverage`).

| Field | Value |
| --- | --- |
| Provider | Dirección General del Catastro (Ministerio de Hacienda) |
| Dataset | INSPIRE Buildings (BU), object `BuildingPart`, field `numberOfFloorsAboveGround` |
| Service | WFS 2.0 `https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx`. It refuses curl's default user agent. The generator identifies itself as `onward-generator/<version>` |
| Licence | CC BY 4.0, as stated by the official catalogue (datos.gob.es, "Building INSPIRE Download Service - WFS") |
| Catastro's own licence | "Licencia de acceso y uso de los servicios y conjuntos de datos INSPIRE", v1.0, July 2016 |
| Attribution | `Dirección General del Catastro, INSPIRE Buildings (BU)` |

Obligations, from the Catastro licence:

- **Transformed information only.** Use of transformed information is allowed, including commercial use, in the sense of article 21 of the Spanish intellectual-property law. **Redistributing the original information is not allowed.** The downloaded GML stays in `maps/<id>/data/raw/`, outside git. A download of several tiles is merged into that one file, each part once; the sidecar records every request. What continues downstream is a storey count per OSM building, the overlap-weighted median of the cadastral parts, later turned into an estimated height. Since ADR-016, `cadastre split` also divides an OSM footprint into sub-footprints: each is a cadastral part outline clipped to the OSM outline, snapped to 1 mm, merged by parcel and storey count, with slivers dropped and gaps filled. That shape is a transformation of the cadastral geometry; the GML itself is never copied into a scene or a package.
- **No impersonation.** Derived products must not be presented as "cartografía catastral", "información catastral", or equivalent terms.
- **No warranty.** Catastro is not responsible for derived products, and the data can be out of date.
- **Attribution (CC BY 4.0).** `heights_overrides.catastro.json`, `heights.json`, `scene3d.json`, and the GLBs carry the source and the licence. The split scene `data/processed/cadastre/geo_scene.json` carries it in `subdivision.cadastre` and on every sub-building (`subdivision.license`, `attribution`, `retrieved_at`).

A scene that combines OSM and Catastro is a Produced Work from the OSM database. It keeps the OSM attribution and adds the Catastro attribution.

Sources checked on 2026-10-18:

- https://www.catastro.hacienda.gob.es/webinspire/index.html
- https://www.catastro.hacienda.gob.es/webinspire/documentos/Licencia.pdf
- https://datos.gob.es/es/catalogo/e0dat0002-spanish-general-directorate-for-cadastre-building-inspire-download-service-wfs1

The fixture `tests/fixtures/cadastre/synthetic_bu.gml` is synthetic (CC0-1.0) and contains no Catastro data.

## Source plugins

Datasets that only cover some countries are source plugins ([ADR-018](../decisions/ADR-018-source-plugins.md)). Each declares its licence (SPDX id, URL, attribution, obligations). `config/sources.json` `licence_allowlist` lists the licences a plugin may be fetched under: `CC-BY-4.0`, `etalab-2.0` (Licence Ouverte 2.0) and `ODbL-1.0`. A plugin whose licence is not on the list stops `run` before any request. `onward-generator source list` prints each plugin's licence and whether the list accepts it. When several plugins give a building a height, the merged overrides keep the winning source's licence and attribution, and name both when a second source lends the storey count.

## Generated geometry

Phase 04 does not use external models. Windows, balconies, roofs, street furniture, and interiors are procedural geometry from this code. `scene_detail.json` and `scene_visual.glb` inherit the base attributions (OpenStreetMap ODbL-1.0, and Catastro CC BY 4.0 when it was used) and state that the facades are generic templates, not a faithful reconstruction. They must not be presented as the real buildings.

`dress style` adds no third-party asset. Its atlas is generated in code (noise on the existing colours, 256 px tiles). It is not a photograph of a city and it is not an extract from Onward. Licence of that atlas: `project-generated`.

`signs place` paints the OSM `name` of the way on a generated plate. The name is ODbL data. The map keeps the OpenStreetMap attribution. The name is not translated and not invented. A way with no `name` gets no plate.

Shop signs and window vinyls use the OSM `name` of the shop POI the same way (ODbL, `name_origin: observed`). A POI with no `name` gets a generic Spanish word for its `shop`, `amenity`, `office` or `craft` value from `config/detail.default.json` (`name_origin: estimated`), for example `Farmacia` or `Panadería`. That word is a project assumption, not a brand or a real sign. The colours and layout are generated. The text uses the same vendored DejaVu font; no other font is added.

## Panoramax

Street photographs used by `panoramax apply`. The step is under development. It is off unless `map.json` has `"panoramax": true`.

| | |
| --- | --- |
| Provider | Panoramax (GeoVisio). Search is the metacatalogue `https://api.panoramax.xyz/api/search` |
| Licence | CC BY-SA 4.0. `https://spdx.org/licenses/CC-BY-SA-4.0.html` |
| What is stored | A picture whose `field_of_view` is 360, its JPEG, and a sidecar: provider, licence, licence URL, attribution, URL, author, SHA-256, `retrieved_at`. A flat picture is listed as `skipped: flat` and the JPEG is not downloaded |
| Where | JPEGs and sidecars: `cache/panoramax/<picture id>`, gitignored, shared by every map. Each map keeps its search index in `maps/<id>/data/raw/panoramax/` |
| On the building | One cropped facade per exposed face. The material stays `CC-BY-SA-4.0` with origin `observed`. It is not relicensed to CC0 and its pixels are not mixed into the catalogue atlas |

The Spanish instance `panoramax.openstreetmap.es` publishes under CC BY-SA 4.0. The extra grant that OSM-FR gives for non-photographic derivatives does not apply to pictures from the Spanish instance or from MapComplete. A facade crop keeps the picture's author and the CC BY-SA 4.0 attribution. The GLB extras and `panoramax_report.json` record the picture id, the author, the licence, and `observed`.

## Textures

A new map's plan is `maps/<id>/config/texture_plan.json`, written by `map create` from [config/dress/imported_library.json](../../config/dress/imported_library.json). There is no shared Murcia or generic template.

| Source | Licence | What is used |
| --- | --- | --- |
| ambientCG, [docs.ambientcg.com/license](https://docs.ambientcg.com/license/) | CC0 1.0 | The `Color` JPG at 1k only. Normal, roughness, displacement, blend, and USD are not copied |
| Kenney packs (City Kit Suburban, Modular Buildings, Retro Urban Kit, Retro Textures Fantasy), each pack's `License.txt` | CC0 1.0 | GLBs in `assets/kenney`. Texture-pack PNGs in `assets/textures/`. FBX and OBJ are not copied |
| Poly Haven `painted_plaster_wall` and `plastered_wall` by Amal Kumar, already in `assets/textures/` with their `.source.json` | CC0 1.0 | `Diffuse` map, `1k`, `jpg`. Listed in the imported library as `ph_painted_plaster_wall` and `ph_plastered_wall` (plaster family) since 2026-10-30 |
| Poly Haven `wood_planks` by Amal Kumar, already in `assets/textures/wood_planks/` with its `.source.json` | CC0 1.0 | `Diffuse` map, `1k`, `jpg`. Listed in the imported library as `ph_wood_planks` since 2026-10-30, with no texture family, so no plan slot draws it. It floors the bars and generic shops; `ph_plastered_wall` and ambientCG `Tiles139` cover the other shop walls and floors (`docs/07-detail.md`, *Shop interiors*) |
| Poly Haven, only when an older plan still names those ids and `--download` is passed | CC0 1.0 | `Diffuse` map, `1k`, `jpg`, from `api.polyhaven.com` / `dl.polyhaven.org`. HTML is not scraped. An explicit licence other than CC0 is refused. JPEG cache: `assets/textures/` |

The 71 Kenney meshes whose role was `building` (suburban houses, modular building pieces, and two cliff blocks) were removed on 2026-10-26. The installed GLBs were scaled to street metres on 2026-10-27; the factor is `street_scale` in `assets/kenney_review.json`. The removed meshes were not scaled. `assets/kenney_review.json` describes every Kenney mesh, including those. On 2026-10-29, 20 Retro Urban meshes whose material uses `alphaMode: BLEND` were also removed: the crossed-card trees, the balconies, scaffolding, see-through walls and fence, cables, and three barriers. The export material is opaque, so their transparent cards rendered solid. The same day the four Retro Urban grass tiles (`grass`, `grass-corner`, `grass-corner-inner`, `grass-hill`) were removed: they are 3.2 m terrain tiles, and the slopes stood in a plaza as a tilted triangle. Their review cards say why. The meshes that remain are listed in `assets/kenney/manifest.json` and do not replace OpenStreetMap footprints. `overgrow` includes that manifest with every other `assets/*/manifest.json`. Each GLB references `Textures/<name>.png` beside itself. Those PNGs are in `assets/kenney/<pack>/Textures/`. The shader uses albedo only: `_BaseColor × _BaseMap`. Cobble is applied only to `surface/pedestrian_cobble`.

CC0 does not require attribution. The report still records the author the source declares.

## Abandoned-city props

`overgrow place` adds grass tufts, vines, and trees marked `generated`. An OSM `natural=tree` stays `observed`. Rust and boarded windows that the code builds are project-generated. They are not photographs.

The placeable meshes are the 49 albedo GLBs in `assets/normalized`. Each card, including author and licence, is in [docs/abandoned-assets.md](../abandoned-assets.md). The licence review is `assets/normalized_review.json`.

| Source | What is allowed |
| --- | --- |
| Poly Haven models named in the catalogue | CC0. glTF albedo, decimated. Category index pages (`/models/nature`, `/plants`, `/trees`) are not downloads |
| Sketchfab | CC0 and CC BY 4.0 only, after the model page was read. The review is `assets/normalized_review.json`. CC-BY-NC, CC-BY-NC-SA, the Standard licence, a missing licence, a pack whose description says the photographs are from Textures.com, a retopology of a Standard scan, and models tagged as another game's assets are not requested, even when the page says CC BY |

A withdrawn model stays in the review with `usable: false` so it is not downloaded again. `modular_chainlink_fence` and `post_apocalyptic_buildings` were removed from the library.

## Street-plate font

`signs place` draws the street name with a vendored TrueType font, loaded by path so the plate does not depend on the fonts of the machine that runs it.

| Field | Value |
| --- | --- |
| Font | DejaVu Sans Bold (`DejaVuSans-Bold.ttf`), version 2.37 |
| Files | `assets/fonts/dejavu/DejaVuSans-Bold.ttf`, with the licence text in `assets/fonts/dejavu/LICENSE` |
| Source | `dejavu-fonts-ttf-2.37.tar.bz2` from the official release, https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37 |
| Licence | Bitstream Vera Fonts licence, with Arev additions under the same terms; DejaVu changes are public domain. Redistribution is allowed. The font may not be sold on its own, and a modified version must change its name |
| Archive SHA-256 | `fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7` |
| Archive SHA-512 | `bafa39321021097432777f0825d700190c23f917d754a4504722cd8946716c22c083836294dab7f3ae7cf20af63c4d0944f3423bf4aa25dbca562d1f30e00654` |
| `DejaVuSans-Bold.ttf` SHA-256 | `e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724` |
| `LICENSE` SHA-256 | `7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93` |

GitHub publishes no digest for that release. The archive's SHA-512 matches the one Alpine Linux pins for the same file (`main/font-dejavu/APKBUILD`). Downloaded and checked on 2026-10-29. The font file is unmodified. It covers Latin-1 and Latin Extended-A, so Spanish names (á, é, í, ó, ú, ñ, ü) draw as real glyphs. The font is only rasterised into the plate albedo and the shop-sign atlas; the font file is not part of the exported map.

## Asset Store

`config/props/catalog.json` records Unity Asset Store packs that were reviewed and not baked into the GLB. `props place` can write instances that have OSM evidence. The Unity adapter does not instantiate that list. Visible furniture in the exported scene is the generated furniture from `dress style`, plus the normalized library from `overgrow`.

Approved to import, and still not instantiated: **327313**, **71042** (furniture only), and **251403**. A pack whose licence or shader does not fit `PP_Opaque_Simple_Lit` stays out. Store shaders do not replace that shader.

## Quest measurement and export

Phase 05 and the export adapter add no extracted Onward assets. Test packages contain the generated geometry, the SDK `PP_` materials, and the prefabs the validator requires. They inherit the OpenStreetMap attribution and, when Catastro was used, the Catastro attribution. They are local. They have not been published. `device_verified` stays false until the headset checklist is filled in.

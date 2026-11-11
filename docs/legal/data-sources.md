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
| Dataset | INSPIRE Buildings (BU): object `BuildingPart`, field `numberOfFloorsAboveGround`; object `Building` (one per parcel), fields `currentUse`, `numberOfDwellings`, `numberOfBuildingUnits` (ADR-022) |
| Service | WFS 2.0 `https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx`. It refuses curl's default user agent. The generator identifies itself as `onward-generator/<version>` |
| Licence | CC BY 4.0, as stated by the official catalogue (datos.gob.es, "Building INSPIRE Download Service - WFS") |
| Catastro's own licence | "Licencia de acceso y uso de los servicios y conjuntos de datos INSPIRE", v1.0, July 2016 |
| Attribution | `Dirección General del Catastro, INSPIRE Buildings (BU)` |

Obligations, from the Catastro licence:

- **Transformed information only.** Use of transformed information is allowed, including commercial use, in the sense of article 21 of the Spanish intellectual-property law. **Redistributing the original information is not allowed.** The downloaded GML stays in `maps/<id>/data/raw/`, outside git. A download of several tiles is merged into that one file, each part once; the sidecar records every request. What continues downstream is a storey count per OSM building, the overlap-weighted median of the cadastral parts, later turned into an estimated height. Since ADR-016, `cadastre split` also divides an OSM footprint into sub-footprints: each is a cadastral part outline clipped to the OSM outline, snapped to 1 mm, merged by parcel and storey count, with slivers dropped and gaps filled. That shape is a transformation of the cadastral geometry; the GML itself is never copied into a scene or a package.
- **Current use (ADR-022).** The `bu:Building` features are downloaded beside the parts into `data/raw/catastro_buildings.gml`, outside git, with their own sidecar. Downstream only each building's parcel reference, its `currentUse` code and its two counts are copied, as `catastro_use` on the buildings of the split scene, with the provider, licence, attribution and retrieval date. `detail` turns an office or retail use into a generic ground-floor shopfront: the use is observed, the floor it is put on is estimated. Neither the Building geometry nor the GML is copied.
- **No impersonation.** Derived products must not be presented as "cartografía catastral", "información catastral", or equivalent terms.
- **No warranty.** Catastro is not responsible for derived products, and the data can be out of date.
- **Attribution (CC BY 4.0).** `heights_overrides.catastro.json`, `heights.json`, `scene3d.json`, and the GLBs carry the source and the licence. The split scene `data/processed/cadastre/geo_scene.json` carries it in `subdivision.cadastre` and on every sub-building (`subdivision.license`, `attribution`, `retrieved_at`).

A scene that combines OSM and Catastro is a Produced Work from the OSM database. It keeps the OSM attribution and adds the Catastro attribution.

Sources checked on 2026-10-18:

- https://www.catastro.hacienda.gob.es/webinspire/index.html
- https://www.catastro.hacienda.gob.es/webinspire/documentos/Licencia.pdf
- https://datos.gob.es/es/catalogo/e0dat0002-spanish-general-directorate-for-cadastre-building-inspire-download-service-wfs1

The fixtures `tests/fixtures/cadastre/synthetic_bu.gml` and `synthetic_bu_buildings.gml` are synthetic (CC0-1.0) and contain no Catastro data.

## Source plugins

Datasets that only cover some countries are source plugins ([ADR-018](../decisions/ADR-018-source-plugins.md)). Each declares its licence (SPDX id, URL, attribution, obligations). `config/sources.json` `licence_allowlist` lists the licences a plugin may be fetched under: `CC-BY-4.0`, `etalab-2.0` (Licence Ouverte 2.0) and `ODbL-1.0`, plus the Overture place licences `CDLA-Permissive-2.0`, `Apache-2.0` and `CC0-1.0`, checked per place by `overture match`, and `DL-DE-ZERO-2.0` (Datenlizenz Deutschland – Zero 2.0, for `de.nrw_ndom`). `nl.ahn` uses `CC0-1.0`. A plugin whose licence is not on the list stops `run` before any request. `onward-generator source list` prints each plugin's licence and whether the list accepts it. When several plugins give a building a height, the merged overrides keep the winning source's licence and attribution, and name both when a second source lends the storey count.

Plan D4 (9 November 2026) adds opt-in plugins outside Spain and France. An opt-in plugin runs only on a map that switches it on (`run --source <id>`), never by coverage alone ([ADR-018](../decisions/ADR-018-source-plugins.md), amendment of 9 November 2026). Each licence below was read on the publisher's own page or service before any code was written; a dataset whose licence could not be confirmed there is recorded as blocked and has no plugin.

### AHN (`nl.ahn`)

| Field | Value |
| --- | --- |
| Provider | Rijkswaterstaat (AHN, with the provinces and the water boards), published by PDOK |
| Dataset | Actueel Hoogtebestand Nederland, DSM and DTM rasters 0.5 m, RD New (EPSG:28992), heights in metres above NAP. The service abstract names AHN4, flown 2020–2022; ahn.nl announces AHN5 and the first part of AHN6, which this service did not name on the check date |
| Service | WCS 2.0.1 `https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wcs/v1_0`, coverages `dsm_05m` and `dtm_05m`, `format=image/tiff` (float32, deflate with floating-point predictor, no data `3.4028234663852886e+38`). Grid origin (cell centre) 10000.25, 618749.75, so cell edges lie on multiples of 0.5 m |
| Licence | CC0 1.0, SPDX `CC0-1.0`. The capabilities state `AccessConstraints: otherRestrictions; Geen beperkingen; http://creativecommons.org/publicdomain/zero/1.0/deed.nl` and `Fees: NONE`; the PDOK AHN page calls the data open data, free of charge and without restrictions |
| Attribution | `Actueel Hoogtebestand Nederland (AHN), Rijkswaterstaat / PDOK` (not required by CC0; kept as good practice) |

Obligations: none under CC0. The overrides and the report still name the source, the series and that the heights are derived (DSM minus a filled DTM, [04-heights.md](../04-heights.md#lidar-heights)). The tiles stay in `maps/<id>/data/raw/nl.ahn/`, outside git. Coverage is `config/ahn_coverage.json`, a coarse hand-drawn ring around the Netherlands. Checked 2026-11-09: the WCS capabilities and `DescribeCoverage` of both coverages, two real `GetCoverage` replies over Amsterdam (Dam square: DSM median 18.2 m, DTM 86 % no data under the palace, ground about 2.0 m NAP; Pillow 11.3 decodes them), the error reply of an unknown coverage (`ows:ExceptionReport`), https://www.pdok.nl/introductie/-/article/actueel-hoogtebestand-nederland-ahn and https://www.ahn.nl/.

### nDOM50 NRW (`de.nrw_ndom`)

| Field | Value |
| --- | --- |
| Provider | Bezirksregierung Köln, Geobasis NRW |
| Dataset | Normalisiertes Digitales Oberflächenmodell nDOM50, 0.5 m, ETRS89 / UTM 32N (EPSG:25832): an image-based, leaf-on surface model (bDOM50, summer flights) minus a 0.5 m LiDAR terrain model, so height above ground of buildings and trees. Continuously updated; the service gives no flight date per tile |
| Service | WCS 2.0.1 `https://www.wcs.nrw.de/geobasis/wcs_nw_ndom`, coverage `nw_ndom`, `FORMAT=image/tiff` (float32, uncompressed on the check date). Grid origin (cell centre) 278000.25, 5827999.75, so cell edges lie on multiples of 0.5 m. The capabilities limit a request to one original tile (4000 × 4000 cells) |
| Licence | Datenlizenz Deutschland – Zero – Version 2.0, SPDX `DL-DE-ZERO-2.0`. The capabilities `Fees` state: "Es gelten die … Lizenzbedingungen „Datenlizenz Deutschland – Zero“ (https://www.govdata.de/dl-de/zero-2-0). Jede Nutzung ist ohne Einschränkungen oder Bedingungen zulässig." The same text is on the DOM and DGM services |
| Attribution | `Geobasis NRW, nDOM50 (dl-de/zero-2-0)` (not required; kept as good practice) |

Obligations: none; the licence allows any use without conditions, and Geobasis NRW excludes liability for currency, accuracy and availability. The tiles stay in `maps/<id>/data/raw/de.nrw_ndom/`, outside git. Coverage is `config/nrw_coverage.json`, a coarse hand-drawn ring around North Rhine-Westphalia. Checked 2026-11-09: the WCS capabilities of `wcs_nw_ndom`, `wcs_nw_dom` and `wcs_nw_dgm`, `DescribeCoverage` of `nw_ndom`, a real `GetCoverage` over Cologne Cathedral (200 × 200 float32 cells, −1.0 to 108.1 m), and https://www.govdata.de/dl-de/zero-2-0.

### Madrid 3D building model (`es.madrid_lod2`)

Plan D6 (9 November 2026), opt-in like the plugins of plan D4: it runs only on a map that switches it on (`run --source es.madrid_lod2`). The licence below was read on the publisher's catalogue before any code was written.

| Field | Value |
| --- | --- |
| Provider | Ayuntamiento de Madrid (Área de Urbanismo, cartografía base municipal), published on datos.madrid.es and the Geoportal |
| Dataset | Modelo tridimensional de edificaciones de la cartografía base municipal: LoD2 buildings extruded from the roof lines of the 1:1000 municipal base map. CKAN package `300722-0-ancho-viario-mapas`, `metadata_modified` 2026-07-23. The per-block OBJ files are FME output in ETRS89 / UTM 30N (EPSG:25830) with absolute orthometric heights, triangles only, one `usemtl` group per building or part |
| Service | ArcGIS FeatureServer `https://services-eu1.arcgis.com/UrtTdLP2PYSxwtI6/arcgis/rest/services/MANZANAS_PARA_DESCARGA/FeatureServer/0` (fields `COD_MANZAN` and `URL_OBJ`, one row per city block), whose `URL_OBJ` points at one ZIP per block on `https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/CARTOGRAFIA/CARTOGRAFIA_ACTUALIZADA/3D_EDIFICACIONES_CONSTRUCCIONES/MANZANAS/OBJ_ACTUALIZADO/<COD_MANZAN>.zip` (an `.obj` and a `materials.mtl`). The whole-city I3S/SLPK (2.2 GB) and the 2D `01_EDIFICIO_P` layer are not used |
| Licence | Creative Commons Attribution 4.0 International, SPDX `CC-BY-4.0`. CKAN `package_show` returns `license_id: cc-by`, `license_title: Creative Commons Attribution 4.0 International (CC BY 4.0)`. `CC-BY-4.0` is already on `config/sources.json` `licence_allowlist`, so the allowlist does not change |
| Attribution | `Origen de los datos: Ayuntamiento de Madrid. Modelo tridimensional de edificaciones de la cartografía base municipal (actualizado 2026-07-23)` |

Obligations, from CC BY 4.0 and the attribution the Ayuntamiento asks for: credit "Origen de los datos: Ayuntamiento de Madrid" with the date of the data, link the licence, and indicate the changes. The landmark volumes are a derived work: the LoD2 geometry is clipped to the OpenStreetMap footprint, moved to the map's local grid and ground and simplified to a triangle cap, and the provenance of each landmark says so (`docs/07-detail.md`). Do not suggest that the Ayuntamiento endorses the map. The line reaches the package attribution through the detail attribution, next to the OSM line. The ZIPs stay in `maps/<id>/data/raw/madrid_lod2/`, outside git. Coverage is `config/madrid_coverage.json`, a coarse hand-drawn ring around the municipality of Madrid (CC0). Checked 2026-11-09: CKAN `package_show` of `300722-0-ancho-viario-mapas`, the FeatureServer layer description and an envelope query near Puerta del Sol, and one real block ZIP (`M-000214.zip`, 70 KB, 7112 triangles in 16 groups).

### Blocked and deferred in plan D4

| Dataset | Planned id | State | Why |
| --- | --- | --- | --- |
| Environment Agency LIDAR Composite DSM and DTM 1 m (England) | `gb.ea_lidar` | Blocked by licence | The data.gov.uk records of both datasets (`lidar-composite-dsm-2022-1m`, `lidar-composite-digital-terrain-model-dtm-1m`) and their ISO metadata (`useLimitation`) say only "Open Government Licence", with no version, while the same record's attribution reads "© Environment Agency copyright and/or database right 2022. All rights reserved."; the WCS capabilities say `AccessConstraints: NONE`. The only explicit "Open Government Licence v3.0" on the page is the data.gov.uk site footer, which covers the site, not the dataset. Plan D4 builds the plugin only when the publisher states OGL v3 for the data. Endpoints found and working: `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-last-return-dsm-1m/wcs` and `…/lidar-composite-digital-terrain-model-dtm-1m/wcs` (EPSG:27700, `image/tiff`). Checked 2026-11-09 |
| USGS 3D Elevation Program (US) | `us.3dep` | Blocked by dependency | USGS states 3DEP products are free of charge and without use restrictions (US public domain). Outside Alaska (IfSAR DSM) the raster products are bare-earth DEMs; a surface model needs the LiDAR point clouds (LAZ), and reading LAZ needs a new dependency, which plan D4 does not allow. Checked 2026-11-09: https://www.usgs.gov/3d-elevation-program/about-3dep-products-services |
| Basisregistratie Adressen en Gebouwen (Netherlands) | `nl.bag` | Deferred | Kadaster publishes the BAG under the Creative Commons Public Domain Mark 1.0 (https://www.kadaster.nl/zakelijk/registraties/basisregistraties/bag, checked 2026-11-09). A BAG plugin would add a construction year or a use to existing OSM buildings only, never a footprint, and needs the ADR-022 and ADR-024 consumers generalised first; it is not part of this increment |

## Overture Maps

Places and buildings used by `overture fetch` and `overture match` (ADR-021). A source plugin (`overture`, ADR-018) that covers every city; `run --no-source overture` turns it off.

| Field | Value |
| --- | --- |
| Provider | Overture Maps Foundation, release GeoParquet on `s3://overturemaps-us-west-2/release/<release>/`; release from `https://stac.overturemaps.org/catalog.json` |
| Places licence | Per record, in `sources[].license`: CDLA Permissive 2.0 (Meta, Microsoft, BrightQuery, PinMeTo, DAC, Uberall, …), Apache 2.0 (Foursquare), CC0 1.0 (AllThePlaces) |
| Buildings licence | ODbL 1.0 (OpenStreetMap; Microsoft Global ML Building Footprints), CC BY 4.0 (Esri Community Maps, Google Open Buildings, IGN BTN 2024) |
| Attribution | The wording of https://docs.overturemaps.org/attribution/ per dataset: `Data from <provider>` for places; `© OpenStreetMap contributors, Overture Maps Foundation`; `Global ML Building Footprints, licensed by Microsoft`; `Work derived from BTN 2024 ign.es`; Foursquare adds `Copyright 2024 Foursquare Labs, Inc. All rights reserved.` and the release NOTICE |
| What is stored | `data/raw/overture_places.parquet` and `overture_buildings.parquet`: the release rows whose bbox meets the map, unchanged, with a `.source.json` (release, query, SHA-256, records per dataset and licence with `license_origin`, attribution lines). Outside git, like the OSM extract |

Obligations that apply:

- **Per-record licence.** `overture_places.json` keeps the dataset and licence of every place. A place whose licence is not on the `config/sources.json` allowlist, or that has none, is dropped (`licence_not_allowed`). A shop made from a place records them in `poi_source`, and the attribution of its dataset goes into `scene_detail.json` and the export package. A place whose dataset gives no shop adds nothing.
- **Foursquare (Apache 2.0).** Keep the copyright line and the NOTICE text of the Overture release with the attribution. The data was transformed to the Overture schema by Overture; this project transforms it again (category to an OSM-style tag, name, position in the local grid).
- **Buildings.** A height used from Overture carries the dataset in the override `source` and its licence; `heights.json` lists the sources a building used, and `generate` adds them to the scene attribution. ODbL share-alike applies to a distributed derived database as for OSM. CC BY 4.0 sources need their attribution line.
- **Records without a licence.** IGN, Google Open Buildings and Esri records carry no `license` in `sources[]`. The licence then comes from the Overture attribution page (checked 2026-10-30) and is marked `license_origin: overture_attribution_page`. Any other dataset without one is recorded as `unknown`, and neither its places nor its heights are used: `unknown` is not on the allowlist.
- **Building colours (opt-in, plan C2).** With `map.json` `"overture_tints": true`, a `facade_color` or `roof_color` from an observed dataset other than OpenStreetMap (Esri, IGN) may become a wall or roof tint. Each tint carries its GERS id, dataset and licence, and the colour's own property source decides the dataset. A licence off the allowlist (so `unknown`) and an ML or unlisted dataset are refused. The detail manifest, and so the export package, gains the attribution line of each dataset whose colour a building took (for Esri, `Esri Community Maps contributors; Overture Maps Foundation`, CC BY 4.0).
- **OpenStreetMap through Overture.** Overture building values whose dataset is OpenStreetMap are not lent (`skip_datasets` in `config/overture.default.json`): the OSM extract already has them under ODbL.
- **Origin.** Places are `observed` by a third party. Heights are `observed` (OSM, Esri, IGN), `ai_generated` (Microsoft and Google ML footprints) or `estimated` (any other dataset), recorded as `origin` on every override. Neither is ever `measured`.

Checked on 2026-10-30: https://docs.overturemaps.org/attribution/, https://docs.overturemaps.org/guides/places/, https://docs.overturemaps.org/schema/reference/buildings/building/.

Test parquets are written at test time by `tests/overture_fixture.py`. They are synthetic (CC0-1.0) and contain no Overture data.

### IGN MDSn (`es.pnoa_ndsm`)

| Field | Value |
| --- | --- |
| Provider | Instituto Geográfico Nacional (IGN) / CNIG |
| Dataset | Modelo Digital de Superficies normalizado, clase edificación, primera cobertura, 2.5 m (MDSnE2,5), from PNOA-LiDAR first-coverage flights (series 2008–2015) |
| Service | WCS 2.0 `https://wcs-mds.idee.es/mds`, coverage `mdsn_e025`, format `application/asc`, EPSG:25830. The capabilities state `AccessConstraints: CC BY 4.0 scne.es` and `Fees: No se aplican condiciones` |
| Licence | CC BY 4.0 (Orden FOM/2807/2015; IGN licence conditions `https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf`) |
| Attribution | `Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es` |

Obligations, from the IGN conditions: cite `<product> <date> CC-BY 4.0 <producer>`, visible next to the data; a derived product (heights per footprint are one) prefixes `Obra derivada de`. The date is the series period; the CNIG lists sheet dates of 2009, 2010 and 2012. Ceuta, Melilla and Alborán are not in the product. The tiles stay in `maps/<id>/data/raw/es.pnoa_ndsm/`, outside git. Checked 2026-10-30: capabilities, a real `GetCoverage` over Madrid, the CNIG product page and the datos.gob.es record `e00125901-spaignmds-normalizado`.

### IGN LiDAR HD MNH (`fr.lidar_hd`)

| Field | Value |
| --- | --- |
| Provider | Institut national de l'information géographique et forestière (IGN) |
| Dataset | LiDAR HD, Modèle Numérique de Hauteur (MNH, surface minus terrain), 0.5 m, Lambert-93 (EPSG:2154), IGN69 |
| Service | Géoplateforme WMS-R `https://data.geopf.fr/wms-r/wms`, layer `IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93`, `FORMAT=image/x-bil;bits=32` (raw float32, no data `-9999` in restricted zones); acquisition dates from the WFS `https://data.geopf.fr/wfs/ows` layer `IGNF_LIDAR-HD_METADONNEE:metadata`. No key is needed; the service announces 1 request per second, which `fetch` keeps |
| Licence | Licence Ouverte / Open Licence 2.0 (Etalab), SPDX `etalab-2.0` (data.gouv.fr dataset "MNH LiDAR HD") |
| Attribution | `IGN, LiDAR HD MNH` and the acquisition dates |

Obligations, from the Licence Ouverte 2.0: mention the source (the producer and the date of the data) and do not suggest the IGN endorses the reuse. The heights per footprint are derived from the MNH; the overrides and the report carry the source, the licence and the tile dates. The tiles stay in `maps/<id>/data/raw/fr.lidar_hd/`, outside git. Coverage is `config/lidar_hd_coverage.json`, a coarse polygon around metropolitan France and Corsica; the national acquisition was due to finish in 2026. Checked 2026-10-30: WMS-R capabilities (formats, layer extent), a real `GetMap` over Lyon and over a restricted zone (all `-9999`), the metadata WFS, and the data.gouv.fr dataset page.

## Generated geometry

Phase 04 does not use external models. Windows, balconies, roofs, street furniture, and interiors are procedural geometry from this code. `scene_detail.json` and `scene_visual.glb` inherit the base attributions (OpenStreetMap ODbL-1.0, and Catastro CC BY 4.0 when it was used) and state that the facades are generic templates, not a faithful reconstruction. They must not be presented as the real buildings.

`dress style` adds no third-party asset. Its atlas is generated in code (noise on the existing colours, 256 px tiles). It is not a photograph of a city and it is not an extract from Onward. Licence of that atlas: `project-generated`.

`materials apply` bakes the trim-sheet facade atlas ([ADR-020](../decisions/ADR-020-trim-sheet-facades.md)) in code from three photographs already in the imported library, all CC0 1.0: `ph_plastered_wall` (Poly Haven, Amal Kumar) for the plaster grain, `acg_rock030` (ambientCG) for the stone grain of the zocalo, cornice, ashlar and window surrounds, and `acg_bricks105` (ambientCG) for brick. Only their luminance (brick: its colour) is resampled; colours, string courses, cornices, shutters, doors, shop glass, boards and grime are drawn by the code. `apply_trim` re-checks each source's library hash and refuses one whose licence is not CC0. Licence of the atlas: `project-generated`, recorded with the sources in `materials_report.json` (`facades.atlas`). The OSM2World default-style textures were considered and are not used, so their licence was not needed; a style that adds them must record and verify it here first.

With the opt-in regional atlases (`map.json` `"regional_facades": true`, plan D1, `config/facade_styles.regional.json`) the same code bakes three more atlases, each from three photographs already in the imported library, all CC0 1.0, with no new file under `assets/` and nothing downloaded: `central_european` from `ph_painted_plaster_wall` (Poly Haven, Amal Kumar), `acg_rock022` and `acg_bricks023` (ambientCG); `anglo` from `ph_plastered_wall` (Poly Haven, Amal Kumar), `acg_rock051` and `acg_bricks059` (ambientCG); `latin_american` from `acg_paintedplaster006`, `acg_rock034` and `acg_bricks097` (ambientCG). The casement, sash and grille windows are drawn by the code. The same CC0 check and library hash check apply, and `materials_report.json` `facades.atlas.sources` lists each source with its licence, credit and sha256. Licence of those atlases: `project-generated`.

`signs place` paints the OSM `name` of the way on a generated plate. The name is ODbL data. The map keeps the OpenStreetMap attribution. The name is not translated and not invented. A way with no `name` gets no plate.

Shop signs and window vinyls use the OSM `name` of the shop POI the same way (ODbL, `name_origin: observed`). A POI with no `name` gets a generic Spanish word for its `shop`, `amenity`, `office` or `craft` value from `config/detail.default.json` (`name_origin: estimated`), for example `Farmacia` or `Panadería`. That word is a project assumption, not a brand or a real sign. The colours and layout are generated. The text uses the same vendored DejaVu font; no other font is added.

## Panoramax

Street photographs used by `panoramax apply`. The step is under development. It is off unless `map.json` has `"panoramax": true`.

| | |
| --- | --- |
| Provider | Panoramax (GeoVisio). Search is the metacatalogue `https://api.panoramax.xyz/api/search` |
| Hosts | Search pages, `next` links included, only from `api.panoramax.xyz`. A JPEG comes from the host its search href names: the instance or its object store (for example `panoramax.ign.fr`, an OVH bucket, `panoramax.mapcomplete.org`), which the metacatalogue instance list does not name. Only `https` on a DNS name with a dot is fetched; `file://`, `http://`, IP literals and `localhost` are refused. A search page is capped at 32 MiB and a JPEG at 64 MiB; HTTP 429 and transient failures are retried twice |
| Licence | CC BY-SA 4.0. `https://spdx.org/licenses/CC-BY-SA-4.0.html` |
| What is stored | A picture whose `field_of_view` is 360, its JPEG, and a sidecar: provider, licence, licence URL, attribution, URL, author, SHA-256, `retrieved_at`. A flat picture is listed as `skipped: flat` and the JPEG is not downloaded |
| Where | JPEGs and sidecars: `cache/panoramax/<picture id>`, gitignored, shared by every map. Each map keeps its search index in `maps/<id>/data/raw/panoramax/` |
| On the building | One cropped facade per exposed face. The material stays `CC-BY-SA-4.0` with origin `observed`. It is not relicensed to CC0 and its pixels are not mixed into the catalogue atlas. With `map.json` `"panoramax_atlas": true` the crops of one 100 m combine cell share one image and material (`pano-cell-<cx>_<cy>`); that image holds only Panoramax crops, all CC BY-SA 4.0, and is never the CC0 trim atlas (`facade_trim_<style>`) or any other CC0 image |
| Photo colour | With `"panoramax_atlas": true`, the median colour of a building's crops chooses one of the existing strips of the CC0 trim atlas for its wall. No photo pixel is copied into that atlas, whose bytes do not change. The colour is recorded as an observed attribute, source `panoramax`, with the picture ids and authors (`tints` in `panoramax_report.json`, `panoramax_tint` in the GLB extras) |

The Spanish instance `panoramax.openstreetmap.es` publishes under CC BY-SA 4.0. The extra grant that OSM-FR gives for non-photographic derivatives does not apply to pictures from the Spanish instance or from MapComplete. A facade crop keeps the picture's author and the CC BY-SA 4.0 attribution. The GLB extras and `panoramax_report.json` record the picture id, the author, the licence, and `observed`.

## Textures

A new map's plan is `maps/<id>/config/texture_plan.json`, written by `map create` from [config/dress/imported_library.json](../../config/dress/imported_library.json). There is no shared Murcia or generic template.

| Source | Licence | What is used |
| --- | --- | --- |
| ambientCG, [docs.ambientcg.com/license](https://docs.ambientcg.com/license/) | CC0 1.0 | The `Color` JPG at 1k only. Normal, roughness, displacement, blend, and USD are not copied |
| Kenney packs (City Kit Suburban, Modular Buildings, Retro Urban Kit, Retro Textures Fantasy), each pack's `License.txt` | CC0 1.0 | GLBs in `assets/kenney`. Texture-pack PNGs in `assets/textures/`. FBX and OBJ are not copied |
| Poly Haven `painted_plaster_wall` and `plastered_wall` by Amal Kumar, already in `assets/textures/` with their `.source.json` | CC0 1.0 | `Diffuse` map, `1k`, `jpg`. Listed in the imported library as `ph_painted_plaster_wall` and `ph_plastered_wall` (plaster family) since 2026-10-30 |
| Poly Haven `wood_planks` by Amal Kumar, already in `assets/textures/wood_planks/` with its `.source.json` | CC0 1.0 | `Diffuse` map, `1k`, `jpg`. Listed in the imported library as `ph_wood_planks` since 2026-10-30, with no texture family, so no plan slot draws it. It floors the bars and generic shops; `ph_plastered_wall` and ambientCG `Tiles139` cover the other shop walls and floors (`docs/07-detail.md`, *Shop interiors*) |
| Kenney Mini Shop (the `mini-market` link now redirects there), Furniture Kit and Food Kit, each pack's `License.txt` | CC0 1.0 | GLBs in `assets/kenney_mini_shop`, `assets/kenney_furniture` and `assets/kenney_food`, scaled by `tools/import_shop_assets.py`. FBX and OBJ are not copied |
| Poly Haven `Shelf_01`, `wooden_display_shelves_01`, `steel_frame_shelves_01`, `steel_frame_shelves_02`, `CashRegister_01`, `wooden_crate_01`, `standing_chalkboard_01` | CC0 1.0 | Albedo-only GLBs in `assets/polyhaven_shop`. Originals stay in the gitignored `cache/shop_assets` |
| Quaternius Ultimate Home Interior, Ultimate Furniture and Furniture ([quaternius.com/packs](https://quaternius.com/packs/ultimatehomeinterior.html)), the pack page and the pack's `License.txt` (Furniture ships none; its page names CC0) | CC0 1.0 | Albedo-only GLBs in `assets/quaternius_home_interior`, `assets/quaternius_ultimate_furniture` and `assets/quaternius_furniture`, converted from the OBJ + MTL by `tools/import_quaternius.py` (flat colours, scaled by `street_scale`). Doors, windows, columns, curtains, mirrors and ceiling lights are not copied. Originals stay in the gitignored `cache/quaternius` |
| Quaternius Downtown City MegaKit [Standard] and Stylized Nature MegaKit [Standard], the free zips from `quaternius.itch.io`, each with `License_Standard.txt` | CC0 1.0 | `assets/quaternius_city_props`: the five `Prop_*` models only; the building and road kit is not copied (a kit building never replaces an OSM footprint). `assets/quaternius_nature`: the 35 opaque models (dead trees, rocks, pebbles, stepping stones, grass clumps, mushrooms); the trees and plants with alpha-cutout leaves are not copied. Base colour image only, at most 512 px; normal and ORM maps are not copied |
| Poly Haven, only when an older plan still names those ids and `--download` is passed | CC0 1.0 | `Diffuse` map, `1k`, `jpg`, from `api.polyhaven.com` / `dl.polyhaven.org`. HTML is not scraped. An explicit licence other than CC0 is refused. JPEG cache: `assets/textures/` |

Since 7 November 2026 the interior rows of the Kenney Mini Shop, Furniture Kit and Food Kit catalogues and the Poly Haven shop models can be placed in shop rooms by the furnishing of `detail` ([ADR-031](../decisions/ADR-031-shop-furnishing.md)); since 10 November 2026 the Quaternius interior catalogues too, and furnishing is on by default. Their licences do not change; each placed piece records its catalogue and `licence: CC0-1.0`, which the loader reads from the manifest `source` and refuses otherwise.

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

Since 10 November 2026 each user can convert **their own** downloaded Asset Store packs into local catalogues (`asset-store import`, [ADR-035](../decisions/ADR-035-local-asset-store-catalogues.md)). The Standard Unity Asset Store EULA forbids redistributing the files. The converted GLBs therefore stay in `assets/asset_store_*/` on that machine. Git ignores those folders, and a test checks that no tracked file names the EULA. A built map may embed them, as the EULA allows for a Licensed Product. The export package and those GLBs must not be shared or published.

Approved to import, and still not instantiated: **327313**, **71042** (furniture only), and **251403**. A pack whose licence or shader does not fit `PP_Opaque_Simple_Lit` stays out. Store shaders do not replace that shader.

## Quest measurement and export

Phase 05 and the export adapter add no extracted Onward assets. Test packages contain the generated geometry, the SDK `PP_` materials, and the prefabs the validator requires. They inherit the OpenStreetMap attribution and, when Catastro was used, the Catastro attribution. They are local. They have not been published. `device_verified` stays false until the headset checklist is filled in.

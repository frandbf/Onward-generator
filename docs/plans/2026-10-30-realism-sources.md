<!-- Written in Spanish for the maintainers; claims marked verified/[medido]/[no verificado] as of 2026-10-30 -->

# Informe: cómo ganar realismo y simplicidad en Onward-generator con fuentes y técnicas que ya existen

*Investigación del 2026-10-30. No hay cambios de código ni commits. Hay tres tipos de afirmación:*
- **verificada**: leí la fuente citada (búsqueda o lectura de la página).
- **[medido]**: la saqué yo con Overpass.
- **[no verificado]**: no pude confirmarla.

*El usuario corrigió el alcance: el generador debe valer para cualquier ciudad. Por eso el orden es global primero; las fuentes nacionales y municipales van como plugins por país (sección 3).*

---

## 0. Resumen para decidir

1. **Lo más barato es usar mejor el OSM que ya descargamos.**
   - En el municipio de Madrid hay 19 678 `building:part` [medido].
   - `detail/scene.py:422-435` descarta toda parte sin `height`: emite `building_part_no_height` y la salta. Solo 342 edificios de Madrid llevan `height` [medido].
   - En cambio, 23 779 llevan `building:levels` y 3163 llevan `roof:levels` [medido].
   - Aceptar `building:levels` en las partes (con la altura de planta que ya usa `heights`) y ampliar las reglas de tejado sale casi gratis y vale en todo el mundo.
2. **Overture Maps es la fuente global que más aporta**, y su licencia encaja con lo que ya hacemos:
   - **Places** da POI con nombre y taxonomía, bajo licencias permisivas según el proveedor.
   - **Buildings** es ODbL, como OSM, y su esquema ya trae `height`, `num_floors`, `roof_shape`, `roof_color`, `facade_color` y `facade_material`.
   - Las dos sustituyen estimaciones de tiendas y alturas allí donde OSM es pobre.
3. **Las fuentes nacionales son muy buenas, pero dependen del país.** Algunos ejemplos:
   - Madrid publica un modelo LoD2 por edificio en OBJ/FBX bajo CC BY 4.0.
   - PNOA, IGN LiDAR HD, el LiDAR del Environment Agency, AHN y 3DEP dan alturas medidas.
   - Deben entrar como plugins detrás de un contrato que ya existe: los ficheros `heights_overrides.<fuente>.json` que hoy usa Catastro.
4. **En el render, lo que más rinde en Quest es reducir materiales y draws**:
   - un atlas de *trim sheets* por estilo regional;
   - la suciedad y la oclusión horneadas en el albedo, que es lo único que admite `PP_Opaque_Simple_Lit`.
5. **No adoptar** (detalle en la sección 5):
   - Google/Apple 3D Tiles y Street View;
   - Mapillary para texturas;
   - CityEngine y Blackshark;
   - el código GPL de roofer y blosm (las ideas sí se pueden copiar);
   - la ortofoto en los tejados por ahora, porque el jugador está a pie de calle.

---

## 1. Fuentes de datos verificadas

### 1.1 Globales (núcleo genérico)

| Fuente | Licencia exacta | Encaje con ODbL y redistribución en un mapa | Acceso | Resolución, contenido, fecha | Atribución |
|---|---|---|---|---|---|
| **Overture Places** | Por proveedor:<br>- CDLA Permissive 2.0 (Meta, Microsoft, PinMeTo, BrightQuery y otros)<br>- Apache 2.0 (Foursquare)<br>- CC0 1.0 (AllThePlaces)<br>Sin datos de OSM. [attribution](https://docs.overturemaps.org/attribution/), [guía Places](https://docs.overturemaps.org/guides/places/) | Bueno. Son licencias permisivas sin *share-alike*, así que pueden convivir dentro de una base derivada ODbL si se conserva cada atribución. | GeoParquet en `s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*` y en Azure. Se consulta con DuckDB `httpfs`; para la última release se usa el catálogo STAC. [guía](https://docs.overturemaps.org/guides/places/) | Unos 88 M de lugares.<br>- `names.primary` y `taxonomy` (`hierarchy`, `primary`, `alternates`): unas 2300 categorías.<br>- `basic_category`: unas 280.<br>- `confidence` entre 0 y 1, más `brand` y `operating_status`.<br>- `categories` desapareció en septiembre de 2026.<br>- La taxonomía cambia cada trimestre y la release es mensual. [guía](https://docs.overturemaps.org/guides/places/) | "Data from [Provider]" por cada proveedor. Para Foursquare: "Copyright 2024 Foursquare Labs, Inc. All rights reserved." más su NOTICE. [attribution](https://docs.overturemaps.org/attribution/) |
| **Overture Buildings** | ODbL, con fuentes CC BY 4.0 adicionales (Esri Community Maps, Google Open Buildings). [attribution](https://docs.overturemaps.org/attribution/) | Mismo régimen que nuestro OSM. | `.../theme=buildings/type=building/*` y `type=building_part/*`. [guía](https://docs.overturemaps.org/guides/buildings/) | Esquema v2.0.0:<br>- `height`, `num_floors`, `min_height`;<br>- `roof_shape`, `roof_height`, `roof_direction`, `roof_orientation`, `roof_color`, `roof_material`;<br>- `facade_color`, `facade_material`;<br>- `has_parts`. [schema](https://docs.overturemaps.org/schema/reference/buildings/building/)<br>Fuentes, por prioridad: OSM, Esri, **Instituto Geográfico Nacional (España)**, Vancouver, Microsoft, Google y otras. `building_part` viene solo de OSM. [guía](https://docs.overturemaps.org/guides/buildings/) | "© OpenStreetMap contributors, Overture Maps Foundation" más una línea por fuente. |
| **Foursquare OS Places** | Apache 2.0 y "Copyright 2024 Foursquare Labs, Inc.". [docs](https://docs.foursquare.com/data-products/docs/access-fsq-os-places) | Bueno. Ya viene **dentro de Overture Places**. | Un catálogo Iceberg con token sustituye al bucket S3 público heredado. También hay Hugging Face y PMTiles. [docs](https://docs.foursquare.com/data-products/docs/access-fsq-os-places) | `fsq_category_ids` con jerarquía. La cadencia de actualización no está publicada. | Aviso Apache y la línea de copyright. |
| **Microsoft Global ML Building Footprints** | ODbL. [World Bank](https://worldbank.github.io/alternative-data-for-crisis/notebooks/mapping-exposure/mapping-exposure-infrastructure-mbf.html), [repo](https://github.com/microsoft/globalmlbuildingfootprints) | Igual que OSM. | GitHub y Overture. | 1400 M de huellas y "decenas de millones" de alturas estimadas desde imagen. [blog](https://tech.marksblogg.com/ms-buildings-2026.html) Que las alturas sean ODbL no está verificado en el README. | ODbL y Microsoft. |
| **Google Open Buildings 2.5D Temporal** | A elegir entre CC BY 4.0 y ODbL 1.0. [GEE](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1?hl=en) | Bueno, pero **no cubre Europa ni Norteamérica**: es el Sur Global. [sitio](https://sites.research.google/gr/open-buildings/temporal) | Rásteres en GEE. | Alturas a 4 m efectivos, de 2016 a 2023, a partir de Sentinel-2. | Google, según la licencia elegida. |
| **Meta/WRI Canopy Height** | CC BY 4.0. [AWS registry](https://registry.opendata.aws/dataforgood-fb-forests/) | Bueno. Es AI-generated: se marca así en la procedencia. | `s3://dataforgood-fb-data/forests/v1/alsgedi_global_v6_float/`, sin cuenta. | Un metro, global, derivado de Maxar y GEDI. Error absoluto medio de unos 2,8 m. [Land & Carbon Lab](https://landcarbonlab.org/data/global-tree-canopy-height), [Mongabay](https://news.mongabay.com/2024/04/ai-model-maps-global-tree-canopy-heights-in-hi-res-with-carbon-counting-in-mind/) | CC BY y Meta/WRI. Texto exacto [no verificado]. |
| **Copernicus DEM GLO-30** | Licencia propia COPDEM-30, gratuita. [PDF](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf) | Redistribuible con aviso y descargo de responsabilidad obligatorios. Algunos países están excluidos [Armenia y Azerbaiyán, no verificado en el texto inglés]. [GEE](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30_2024_1) | AWS y GEE. | 30 m, un DSM global. | "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved", más el descargo. [NISAR README](https://cumulus.asf.earthdatacloud.nasa.gov/PUBLIC/DATA/NISAR/NISAR_DEM_v1.2_EPSG4326_README.txt) |
| **Panoramax** (ya integrado) | Por instancia:<br>- OSM-FR: CC BY-SA 4.0 con permiso ODbL para datos no fotográficos.<br>- IGN: Etalab 2.0. [Wikipedia](https://en.wikipedia.org/wiki/Panoramax) | Ya está documentado en `docs/legal/data-sources.md`. | API. | Unos 74 M de imágenes (oct-2025). | La de la instancia. |
| **Mapillary** | CC BY-SA 4.0. [help](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data) | Arriesgado para texturas. El permiso de datos derivados cubre OSM y Wikimedia, no juegos. La licencia de lo derivado de varias fotos no está resuelta ni por el propio Mapillary. [foro](https://forum.mapillary.com/t/opening-up-for-3rd-party-cc-by-content/739) | API. | Global. | CC BY-SA. |

**Muestra de Overture Places en Madrid con DuckDB: no ejecutada.** DuckDB y pyarrow no están instalados y no instalé software sin aprobación. Que haya nombres y categorías de tienda por calle en Madrid es **[no verificado]**. Consulta propuesta (unos MB por HTTP):

```sql
INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
SELECT names.primary, taxonomy.primary, basic_category, confidence, brand.names.primary
FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*', hive_partitioning=1)
WHERE bbox.xmin BETWEEN -3.7065 AND -3.7005 AND bbox.ymin BETWEEN 40.4155 AND 40.4195;
```

### 1.2 Etiquetas OSM que no aprovechamos

Conteos Overpass del 2026-10-30, `way` con `building` dentro del municipio [medido]:

| Etiqueta | Madrid | Murcia | ¿Se usa en el pipeline? |
|---|---|---|---|
| `building` (total) | 124 811 | 33 551 | sí |
| `building:levels` | 23 779 (19 %) | 1228 (3,7 %) | sí, en `heights` |
| `height` | 342 | — | sí |
| `roof:shape` | 1625 | 5 | parcialmente: solo ≤800 m² y se convierte en `hip` (`docs/07-detail.md` §5) |
| `roof:levels` | 3163 | — | **no** |
| `roof:height` | 178 | — | **no** |
| `roof:colour` / `building:colour` | 351 / 449 | — / 0 | **no** |
| `building:material` | 407 | — | sí |
| `building:part` (ways) | **19 678** | 81 | solo con `height`; el resto se descarta (`detail/scene.py:432-435`) |
| `shop` (nwr) | 14 531 | 996 | sí, como evidencia de escaparate |
| `natural=tree` (nodos) | 70 888 | 6181 | sí |
| `highway=street_lamp` / `amenity=bench` / `waste_basket` | 17 663 / 9879 / 3691 | — | farolas sí; el resto, [no verificado] |

Conclusión: los colores son raros y aportan poco. Las **partes de edificio** y `roof:levels` sí son masivos en Madrid, porque vienen de importaciones de Catastro con catAtom2OSM ([hilo TomTom/Catastro, 2026](https://community.openstreetmap.org/t/tomtom-propuesta-para-actualizar-alturas-de-edificios-en-madrid/141151)). En Murcia OSM es pobre, y por eso Overture y los plugins pesan más allí. No pude medir cuántas partes de Madrid llevan `building:levels`: Overpass expiró. **[no verificado]**

### 1.3 Nacionales y municipales (candidatas a plugin)

**España**
- **CNIG/IGN.** Los productos de la Orden FOM/2807/2015 se usan bajo CC BY 4.0. La atribución sigue el formato "<producto> <año> CC-BY 4.0 ign.es", con "Obra derivada de" delante cuando se modifica. [Condiciones IGN](https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf), [datos.gob.es](https://datos.gob.es/es/aplicaciones/centro-de-descargas)
  - **MDSn (nDSM)** a 2,5 m, en ASCII y WCS, pero sacado de la **1.ª cobertura** LiDAR. [ficha](https://datos.gob.es/en/catalogo/e00125901-spaignmds-normalizado)
  - La 3.ª cobertura cubre 2022-2025 y se publica por comunidades. [JIIDE](https://www.dgterritorio.gov.pt/jiide2023/pdfs/resumos/04-ES_Resumen_JesusmariaGarrido.pdf), [BOE](https://boe.es/boe/dias/2024/12/13/pdfs/BOE-A-2024-26046.pdf)
  - No está verificado si existe un nDSM de 2.ª o 3.ª cobertura como producto descargable: habría que derivarlo de la nube de puntos.
- **PNOA MA (ortofoto).** WMS `https://www.ign.es/wms-inspire/pnoa-ma` y WMTS. [CODSI](https://idee.es/csw-codsi-idee/srv/api/records/spaignwms_ortoimagenes)
  - Resolución de 0,25 m o 0,5 m según la zona, y 20 cm en Canarias. [IDEE API](https://api-maps.idee.es/collections/OI.OrthoimageCoverage?f=html), [PNOA 2025](https://datos.gob.es/es/catalogo/e0dat0002-ortofotos-pnoa-anual-de-2025-maxima-actualidad)
  - **15 cm: [no verificado]**.
- **Catastro BU** (ya integrado). `currentUse` con extensión española: `1_residential`, `2_agriculture`, `3_industrial`, `4_1_office`, `4_2_retail`, `4_3_publicServices`. `BuildingPart` lleva `numberOfFloorsAboveGround` y `numberOfFloorsBelowGround`. [JIIDE 2017](https://www.idee.es/resources/presentaciones/JIIDE17/Apresentacoes_JIIDE2017_Conjunto_datos_edificios_BU_en_la_Direccion_General_del_Catastro_Pablo_Gomez_Dominguez.pdf)
  - Esa lista es de 2017; los literales actuales de la WFS están [no verificados].
  - **No encontré un BU 3D ni LoD2 de Catastro para toda España** [no verificado que no exista].
- **Madrid, modelo 3D municipal.** LoD2 "con alta precisión geométrica en cubiertas", extruido de la cartografía 1:1000. [datos.madrid.es](https://datos.madrid.es/dataset/300722-0-ancho-viario-mapas), [Geoportal](https://geoportal.madrid.es/IDEAM_WBGEOPORTAL/dataset.iam?id=ece2d15a-d16f-46e8-aaec-9576771b9997)
  - Formatos: OBJ y FBX **por edificio y por distrito**, Shapefile multipatch, SLPK/I3S, más una capa 2D `01_EDIFICIOS_P` con `ID_3D`. EPSG:25830.
  - Fecha de los datos: 2026-07-01. Publicado el 2024-11-22. Sin texturas mencionadas.
  - **Licencia: CC BY 4.0** en datos.madrid.es. El Geoportal remite a las condiciones generales, que permiten uso comercial y redistribución con "Origen de los datos: Ayuntamiento de Madrid" y la fecha de actualización. [condiciones](https://datos.madrid.es/pages/condiciones-generales-ayuntamiento-de-madrid)
  - Hay además una capa de alturas de edificios bajo CC BY 4.0. [ficha](https://datos.madrid.es/dataset/300750-0-servicio-mapas)
- **Madrid, inventarios urbanos.**
  - Arbolado por ejemplar, con X/Y en ETRS89 UTM30, código de especie y singularidad. [estructura, jul-2025](https://datos.madrid.es/FWProjects/egob/Catalogo/MedioAmbiente/ZonasVerdes/Ficheros/Estructura_DatosArbolado.pdf)
  - Las alturas son promedios, no por árbol, en el dataset de estado. [estructura](https://datos.madrid.es/FWProjects/egob/Catalogo/MedioAmbiente/ZonasVerdes/ficheros/Estructura_DS_Estado.pdf)
  - Unas 92 000 papeleras con modelo, mesas y juegos infantiles. [papeleras](https://datos.gob.es/es/catalogo/l01280796-mobiliario-urbano-papeleras), [mesas](https://datos.madrid.es/dataset/300217-0-mobiliario-mesas/resource/300217-9-mobiliario-mesas-csv?view=table)
  - **No encontré un dataset abierto de farolas ni de bancos** [no verificado].
  - La licencia de cada inventario no está confirmada en su ficha. Por las condiciones generales, previsiblemente es reutilización libre con atribución [no verificado por dataset].
- **Barcelona.** Open Data BCN publica datos de edificios bajo CC BY 4.0, pero no encontré un modelo 3D. El ICGC tiene LoD2, con licencia [no verificada]. [ICGC](https://www.icgc.cat/en/content/download/79516/689182/version/2/file/20171212_com_obtenir_model_3d_cataleg_i_bd.pdf)
- **Murcia.** No hay modelo 3D municipal abierto [no encontrado]. Queda el LiDAR nacional del PNOA. La noticia de 2009 sobre un LiDAR regional bajo CC BY-SA es antigua y está [no verificada hoy]. [OSM-talk](https://lists.openstreetmap.org/pipermail/talk/2009-April/035886.html)

**Otros países con LiDAR o LoD2 abiertos**

| País | Dataset | Licencia | Fuente |
|---|---|---|---|
| EE. UU. | USGS 3DEP (nubes, DEM) | Dominio público | [ScienceBase](https://sciencebase.gov/catalog/item/4f70ab64e4b058caae3f8def), [AWS](https://registry.opendata.aws/usgs-lidar/) |
| Inglaterra | EA LIDAR Composite DSM/DTM, 1 m (WMS/WCS) | OGL v3 (en un registro de 2022 figura "No Licence Provided") | [data.gov.uk](https://data.gov.uk/dataset/80c522cc-e0bf-4466-8409-57a04c456197/lidar-composite-dsm-1m), [environment.data.gov.uk](https://environment.data.gov.uk/dataset/9ba4d5ac-d596-445a-9056-dae3ddec0178) |
| Francia | IGN LiDAR HD (nube, MNS, MNH), unos 10 pt/m², excepto las ZICAD | Etalab 2.0 | [Landot](https://blog.landot-avocats.net/2023/11/24/une-cartographie-en-acces-libre-et-gratuit-3d-du-sol-et-du-sursol-de-la-france/), [MNH](https://www.data.gouv.fr/datasets/mnh-lidar-hd-ign-2025) |
| Países Bajos | AHN (LiDAR) y 3DBAG (LoD1.2/1.3/2.2 de los 10 M de edificios) | AHN: datos abiertos sin restricciones (CC0 [no verificado]). 3DBAG: CC BY 4.0, "© 3DBAG by tudelft3d and 3DGI" | [GEE AHN](https://developers.google.cn/earth-engine/datasets/catalog/AHN_AHN2_05M_RUW?hl=de), [3DBAG](https://docs.3dbag.nl/en/copyright/) |
| Alemania | LoD2 de NRW (unos 11,4 M de edificios) y de Berlín | Datenlizenz Deutschland – Zero 2.0 | [NRW CKAN](https://ckan-open-nrw.nrw.de/is/dataset/5d9a8abc-dfd0-4dda-b8fa-165cce4d8065/resource/d9eff3f2-ad06-429c-b17e-864ee39654f8), [Bayern](https://geodaten.bayern.de/odd/m/3/pdf/hinweise_daten_lod2_download.pdf) |
| Suiza | swissBUILDINGS3D 3.0 Beta | "Open use. Must provide the source" | [opendata.swiss](https://opendata.swiss/en/dataset/swissbuildings3d-3-0-beta) |
| Japón | PLATEAU (CityGML LOD1/2) | CC BY 4.0 / ODbL según el conjunto [verificar por ciudad] | [MLIT](https://www.mlit.go.jp/en/toshi/daisei/content/Leaflet_en.pdf) |
| España | PNOA LiDAR/MDSn, PNOA MA, Catastro, Madrid LoD2 | CC BY 4.0 / licencia INSPIRE de Catastro | arriba |

---

## 2. Qué hacen los demás

| Proyecto | Qué hace | Licencia | Qué podemos reutilizar |
|---|---|---|---|
| **OSM2World** | De OSM a glTF/GLB/OBJ/3D Tiles. Más de 250 claves OSM, PBR, varios LOD, mobiliario urbano (bancos). [osm2world.org](https://osm2world.org/) | Código **MIT** según el LICENSE de GitHub ([repo](https://github.com/tordanik/OSM2World)); materiales antiguos dicen LGPL, conflicto a revisar. Texturas del estilo por defecto: **CC0** ([default-style](https://github.com/tordanik/osm2world-default-style)) | Sus reglas de tejado y de partes (Simple 3D Buildings), y las **texturas CC0** como candidatas al catálogo. Con MIT se puede incluso portar código, con su aviso. |
| **Streets GL** | Visor web de OSM con Simple 3D Buildings, árboles, terreno y PBR. [repo](https://github.com/StrandedKitty/streets-gl) | MIT | Reglas de tejados y partes. Es runtime web, no una tubería de exportación. |
| **blosm** (Blender) | Tejados flat, gabled, hipped, mono-pitched, half-hipped, round, pyramidal, gambrel, dome, onion y saltbox. La versión premium añade texturas tileables, ventanas iluminadas y árboles. [wiki](https://github.com/vvoovv/blosm/wiki/Documentation) | **GPL**. Texturas CC0 según un foro [no verificado] | La **lista de formas como especificación**. No copiar código GPL. |
| **Arnis** | De OSM y elevación a Minecraft, con interiores opcionales. [repo](https://github.com/louis-e/arnis) | Apache 2.0 | Poco: es vóxel. |
| **Cesium OSM Buildings** | 3D Tiles globales derivados de OSM. [Cesium](https://cesium.com/platform/cesium-ion/content/cesium-osm-buildings/) | ODbL y términos de Cesium ion | Nada que no tengamos ya; ion añade términos. |
| **3DBAG + roofer** | LoD2.2 automático desde LiDAR más huellas. [3DBAG](https://3d.bk.tudelft.nl/projects/3dbag/), [roofer](https://github.com/3DBAG/roofer) | Datos de 3DBAG: CC BY 4.0. **roofer: GPLv3**, en desarrollo y con cambios incompatibles | Consumir las salidas donde existan (NL) y copiar la idea: planos de tejado por RANSAC dentro de la huella. No enlazar el binario GPL en el núcleo. |
| **PLATEAU** | LOD1/2 de ciudades japonesas en CityGML. | CC BY 4.0 / ODbL | Un plugin JP. |
| **CityEngine CGA** | Gramáticas de fachada (split, repeat, comp). | Propietario | Solo la **idea de gramática**: nuestro `detail/grammar.py` ya va en esa línea. |
| **Microsoft Flight Simulator / Blackshark** | Unos 1500 M de edificios generados por IA desde imagen satelital: huella, altura, tipo, tejado y fachada. Plantas desde nubes de puntos. Texturas procedurales por contexto y región. De cerca "no aguanta". [Blackshark](https://blackshark.ai/?p=1857), [TechCrunch](https://techcrunch.com/2021/11/10/blackshark-ai-digital-twin-of-earth-microsoft-funding/) | Propietario | La lección: **la plausibilidad sale de un estilo regional y paramétrico**, no de fotos por edificio. Es justo lo que permite un *trim sheet* por región. |
| **Trim sheets y kits modulares** | Un atlas de franjas (cornisas, zócalos, marcos) que texturiza muchas piezas con un solo material. Se cita expresamente por reducir draws en VR móvil. [Rookies](https://www.therookies.co/entries/7336), [80.lv](https://80.lv/articles/the-neighbourhood-planning-the-texturing-stage-for-a-modular-scene) | Técnica | Sustituye a los muchos materiales por superficie. Con albedo solo, la suciedad y el AO van pintados dentro del atlas. |
| **Impostores y HLOD** | Vistas precapturadas en un plano para LOD lejano. Problemas: intersecciones y especular. [Unity Pixyz](https://docs.unity3d.com/Packages/com.unity.pixyz.unity-sdk@1.0/manual/features/impostors.html), [foro](https://discussions.unity.com/t/imposter-rendering-webplayer-demo/587820) | Técnica | Solo para el horizonte (ya existe ADR-014). No hay benchmarks en Quest [no verificado]. |

Meta da para Quest 3 menos de 200 draws y menos de 1,5 M triángulos por frame, y para Quest 2 menos de 100 y menos de 750 k. [Meta](https://developers.meta.com/vr/resources/device-optimization-comparison/) Nuestro presupuesto de unos 250 k (ADR-005) es más estricto porque incluye a Onward y a los jugadores. Toda mejora debe bajar draws o mantenerlos.

---

## 3. Interfaz de plugins por país (para que el núcleo siga genérico)

Ya hay un punto de extensión: `heights --overrides`, con los pasos 1a (`height_m` medido) y 1c′ (`levels` autoritativos) de `docs/04-heights.md`. Catastro es de hecho el primer plugin. Propuesta mínima:

```python
class SourcePlugin(Protocol):
    id: str                     # "es.pnoa_ndsm", "es.madrid_lod2", "fr.lidar_hd", "nl.3dbag"
    licence: LicenceRecord      # SPDX o nombre, URL, texto de atribución, obligaciones
    produces: frozenset[Kind]   # {"height_m", "levels", "roof_mesh", "trees", "furniture", "poi"}
    def covers(self, centre: LatLon, aoi: Polygon) -> bool  # polígono estático versionado en el repo, sin red
    def fetch(self, aoi: Polygon, raw_dir: Path) -> SourceRecord  # cuerpo + .source.json (sha256, retrieved_at, query)
    def normalize(self, raw: SourceRecord, scene: GeoScene) -> Overrides  # heights_overrides.<id>.json u otro contrato
```

- **Prioridad declarativa** en la configuración: medido de LiDAR o LoD2 por encima de Catastro, Catastro por encima de Overture, Overture por encima de OSM `levels`, y este por encima de la estimación.
- **Procedencia:** `observed`, `estimated` o `ai_generated` por campo. Por ejemplo, el dosel de Meta es `ai_generated` y los niveles de Overture que vienen de ML también.
- **Licencia:** se exige una licencia de la lista blanca antes del `fetch`.
- **Sin red en `covers`**, para no romper el determinismo.
- **Huecos:**
  - Overture y la detección de cobertura son globales y van en el núcleo.
  - Los plugins viven en `onward_generator/sources/<cc>/` y nunca importan el exportador.

---

## 4. Las 8 oportunidades, por (realismo × genericidad) / (código + mantenimiento)

| # | Oportunidad | Qué sustituye en nuestro pipeline | Genérica | Riesgo de licencia | Esfuerzo | Primer incremento |
|---|---|---|---|---|---|---|
| 1 | **Exprimir OSM 3D:** `building:part` con `building:levels`/`roof:levels`; `roof:shape` más allá de ≤800 m² → `hip`; `roof:height`; colores cuando existan | Partes descartadas y tejados reducidos a `hip` | Global | Nulo (ODbL ya cumplido) | **S** | En `detail/scene.py`, derivar la altura de la parte con la altura de planta de `heights` cuando falte `height`. Test con fixture de una parte con solo `building:levels`. Medir en Madrid cuántos `building_part_no_height` desaparecen. **Hecho** en [ADR-023](../decisions/ADR-023-simple-3d-buildings.md): partes desde niveles, seis formas de tejado sobre cualquier área y colores como tintes de una paleta. |
| 2 | **Overture Places como evidencia de uso:** escaparates, rótulos con `names.primary`, `basic_category` → `retail`/`food` | Tiendas estimadas e inferencia por categoría donde OSM no tiene POI (Murcia: 996 `shop`) | Global | Bajo. Atribución por proveedor; Foursquare con su línea de copyright | **M** (dependencia nueva, DuckDB; justificar) | `onward-generator places fetch --map` escribe un GeoParquet recortado y su `.source.json`. `detail` lo lee como POI `observed_third_party` con `confidence ≥ 0,7`, sin sobrescribir OSM. |
| 3 | **Overture Buildings:** `height`, `num_floors`, `roof_shape`, `facade_color` como paso entre OSM explícito y la estimación | `building_type` y `neighbourhood` en `heights` | Global (la cobertura de alturas fuera de EE. UU. es [no verificada]) | Nulo (ODbL) | **S–M** (reutiliza el DuckDB de #2) | Un `heights_overrides.overture.json` por *matching* de huellas por IoU, con `height_source=overture` y la fuente original de `sources[]`. Informe de cobertura por mapa. |
| 4 | **Atlas de *trim sheets* por estilo regional** (mediterráneo, centroeuropeo, anglosajón, latinoamericano…), elegido por país o clima | Muchos materiales por superficie más teselas procedurales; reduce draws | Global | Bajo si es propio o CC0 (ambientCG, estilo por defecto de OSM2World) | **M–L** | Un estilo, "mediterráneo", de 2048² con zócalo, impostas, cornisa, recercados y persianas. Remapear `uv_frame` (ADR-012) a sus franjas y medir draws en Madrid r150. |
| 5 | **Suciedad y AO horneados en el albedo:** gradiente en la base de fachada, chorreones bajo cornisas, oscurecer esquinas | Albedo plano; `PP_Opaque_Simple_Lit` no admite más mapas | Global | Nulo (generado) | **S–M** | En `materials apply`, multiplicar en el atlas un gradiente vertical determinista por fila de franja. Antes, comprobar que el bake de Unity no duplica el AO [no verificado]. |
| 6 | **Plugin de alturas por LiDAR (nDSM):** mediana del nDSM dentro de la huella | `levels × 3 m` de Catastro y las estimaciones; mide en vez de inferir | Por país: ES, FR, EN, NL, US… | Bajo. CC BY 4.0, Etalab, OGL, CC0 o dominio público, cada uno con su texto | **M** (interfaz + primer plugin) | Interfaz de §3 más `es.pnoa_ndsm` por WCS del MDSn a 2,5 m (1.ª cobertura: marcar la fecha) y `fr.lidar_hd` (MNH). Salida en `heights_overrides.<id>.json` con `height_m`. |
| 7 | **Plugin LoD2:** Madrid OBJ por edificio, NRW/Berlín, swissBUILDINGS3D, 3DBAG, PLATEAU | Tejados inferidos (`hip`) por geometría de cubierta observada | Por país o ciudad | Bajo a medio. Madrid CC BY 4.0 con "Origen de los datos: Ayuntamiento de Madrid" | **L** (presupuesto de triángulos, simplificación, casar con la huella OSM) | Solo tejados: importar los OBJ de un distrito de Madrid, recortar a la huella OSM, simplificar a N triángulos por edificio y conservar los muros nuestros (ventanas, colliders). |
| 8 | **Árboles desde el dosel de Meta (1 m) y los inventarios municipales** | Árboles solo de `natural=tree` (Madrid OSM 70 888; el inventario municipal es mucho mayor [no verificado]) | Dosel global; inventario por ciudad | Bajo. CC BY 4.0, AI-generated | **M** | Detectar máximos locales del CHM en parques y aceras sin árbol OSM. Instancias del árbol existente con altura del CHM, `ai_generated`, y tope por presupuesto. |

**Fuera del top 8, pero razonables más adelante:**
- Copernicus GLO-30 para el horizonte o la pendiente: 30 m no sirven para la calle y hoy los mapas son planos.
- Impostores para el horizonte de ADR-014.
- Mobiliario municipal (papeleras de Madrid) como plugin.

---

## 5. Qué no adoptar y por qué

- **Google o Apple Photorealistic 3D Tiles, Street View:** sus licencias lo prohíben. Ya estaba en las restricciones.
- **Mapillary para texturas de fachada:** CC BY-SA 4.0, y la licencia de lo derivado de varias fotos no está resuelta. [foro](https://forum.mapillary.com/t/opening-up-for-3rd-party-cc-by-content/739) Panoramax ya cubre ese hueco con una procedencia más clara.
- **Ortofoto PNOA en los tejados (por ahora):** licencia compatible, pero en Onward el jugador está a pie de calle. Además añade texturas únicas por edificio, que rompen el atlas y suben memoria y draws. Poco realismo por coste.
- **CityEngine, Blackshark, Cesium ion:** propietarios o con términos de servicio añadidos. Ideas sí, dependencias no.
- **Código GPL de roofer y blosm en el núcleo:** contamina la licencia del generador. Se pueden consumir los datos de 3DBAG (CC BY) y reimplementar las reglas.
- **Reconstrucción LoD2 propia desde nubes LiDAR en bruto:** XL, mucho mantenimiento y mucha CPU. Antes conviene nDSM más reglas de tejado (#6) y LoD2 ya publicado (#7).
- **Google Open Buildings Temporal:** no cubre Europa.
- **Alturas de Microsoft directas:** ya llegan conflacionadas vía Overture. Mejor no mantener dos *matchings*.
- **Generar fachadas o texturas con IA local (difusión):** viola "albedo validado" y la regla de procedencia, y no es determinista. Una IA solo como clasificadora de estilo, opcional y validada, siguiendo ADR-006.

---

## 6. Qué no pude verificar
- La muestra real de Overture Places en Madrid: falta DuckDB.
- La cobertura de `height` de Overture Buildings en España.
- Cuántas `building:part` de Madrid tienen `building:levels`: Overpass expiró.
- Si existe un nDSM de 2.ª o 3.ª cobertura del PNOA como producto descargable, y la ortofoto a 15 cm.
- Un LoD2 nacional de Catastro.
- La licencia de cada inventario de Madrid (arbolado, mobiliario) en su ficha, y si hay farolas o bancos.
- La licencia actual del código de OSM2World: MIT en GitHub frente a LGPL en materiales antiguos.
- El texto exacto de atribución de Meta/WRI y de swisstopo, y la licencia de AHN como CC0.
- Benchmarks de impostores en Quest, y si el bake de Unity ya hornea AO.

**Siguiente paso recomendado:** el #1 (S, sin dependencias ni licencias nuevas). En paralelo, una propuesta corta, al estilo ADR, para añadir DuckDB como dependencia opcional antes de #2 y #3.

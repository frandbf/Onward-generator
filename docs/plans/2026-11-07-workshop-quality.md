<!-- Written in Spanish for the maintainers. State as of HEAD 5d92a18, 2026-11-07. No code changes. -->

# Plan: mapas con «calidad de workshop»

*7 de noviembre de 2026. Analiza una lista de requisitos externa («más realismo», «más escalabilidad», «calidad de vida») y la contrasta con el código, los ADR y los planes de [realismo y fuentes](2026-10-30-realism-sources.md) y de [escala y velocidad](2026-10-30-scale-and-speed.md), del 30 de octubre, y el de [fachadas, tipología y presupuesto](2026-10-31-facades-typology-budget.md), del 31. Cada afirmación sobre el estado actual cita su fuente. Los tiempos y triángulos vienen de esos documentos; aquí no se ha medido nada nuevo.*

---

## 0. Resumen para decidir

1. **La mitad de la lista ya existe, en parte o entera.** El perfil de bake por defecto ya es `draft` y el lightmapper ya es `ProgressiveGPU`. `run --no-unity` ya para en el paquete. Ya hay instalación sin Docker. Las luces por habitación ya las crea el adaptador, y la tipología y la época ya se registran (ADR-024) y ajustan la fachada (ADR-027). Overture ya trae las huellas de Microsoft y Google. Lo que falta casi siempre es **validación en el casco**, no código.
2. **Varias propuestas chocan con reglas que el proyecto ya decidió**:
   - Street View, las texturas de Mapillary y las fachadas generadas por IA están descartadas por licencia o determinismo (informe de realismo §5).
   - Inventar huellas de Microsoft o Google sin edificio OSM está prohibido (AGENTS.md).
   - Normales y roughness no son posibles con `PP_Opaque_Simple_Lit`, que es `_BaseColor × _BaseMap` y nada más (ADR-020). Probarlas significa pasar a `PP_Opaque_Lit`, con coste en Quest sin medir.
   - El SDK de Onward no consta que respete un `LODGroup` (ADR-025). Los Gaussian splats no tienen cabida en un shader opaco del SDK.
   - El occlusion culling se midió y no bajó los draws: 571 frente a 578 (ADR-005).
3. **La lista olvida lo que hace que un mapa se juegue una y otra vez: el diseño de juego.** `perf/layout.py` se define como «a measurement fixture, not a gameplay design». Coloca dos spawns, un Uplink y la tienda donde hay suelo libre. Un mapa de workshop vive de sus rutas, coberturas, líneas de visión, puntos de choque y equilibrio entre bandos. Este plan añade ese eje y lo pone **al mismo nivel que el realismo**.
4. **La deuda principal es de validación.** ADR-004, 013, 017, 020, 024, 025 y 027 siguen en `proposed` o con la revisión en casco abierta. Sin una sesión de casco por bloque, cada incremento nuevo se apoya en otros que nadie ha visto en el Quest.
5. **Las tres prioridades de la lista se mantienen, con matices**:
   - **Fotos de calle:** primero hay que resolver el conflicto con el atlas *trim sheet*. Hoy Panoramax exige `facades.mode: legacy` (ADR-020).
   - **Núcleo por presupuesto:** se puede hacer con un modelo de coste por edificio sacado de **una sola** ejecución de `detail`, no con una búsqueda en varias.
   - **Interiores:** amueblar es barato. Los catálogos ya están (`kenney_furniture`, `kenney_mini_shop`, `kenney_food`, `polyhaven_shop`, todos con `placement: interior`); solo falta el colocador.

   Añado una cuarta prioridad: **diseño de juego** (rutas, coberturas, spawns).

---

## 1. Qué es «calidad de workshop»: criterios medibles

Sin una definición medible, «calidad» no se puede cerrar. Propongo cuatro grupos de criterios. Cada incremento debe mover al menos uno sin romper los demás.

| Grupo | Criterio | Fuente de la medida | Umbral propuesto |
|---|---|---|---|
| Rendimiento | fps en la ruta y en Hunt con bots | OVR Metrics (docs/09-perf.md) | mediana ≥ 72, sin caídas fuera de la carga |
| Rendimiento | triángulos en Unity con prefabs del SDK | `scene_report.json` | ≤ 220 000 (deja margen a los jugadores, sobre 250 000 de ADR-005). Desde B1 se puede apuntar con `--core-target-triangles`, que cuenta los triángulos del núcleo en `detail`, no en Unity: el export los multiplica por 1,5 a 1,7 en el bench y añade el anillo ([09-perf.md](../09-perf.md#budget-core)) |
| Rendimiento | draws en las vistas fijas A y B | OVR Metrics | ≤ 150 |
| Rendimiento | memoria gráfica | OVR Metrics | ≤ 1 GB |
| Generación | Unity `final` en r150 a 10 t/m | `seconds.bake` | ≤ 5 min. `draft` ≤ 1,5 min (sin medir hoy) |
| Generación | Python en r150 sin red | `run_log` | ≤ 30 s |
| Realismo | revisión visual con una rúbrica de 1 a 5 (fachada, calle, tejado, interior, repetición) | hoja de contactos y capturas fijas desde los spawns | media ≥ 4 en las ciudades de referencia |
| Realismo | repetición visible | métrica de contraste grueso, ampliada a tejados, pavimento y puertas | ningún tile por encima del límite |
| Juego | rutas distintas entre spawns | grafo de la navmesh | ≥ 3, sin solapamiento > 50 % |
| Juego | línea de visión máxima sin cobertura en rutas | raycasts sobre colisión | ≤ 60 m (ajustable por modo) |
| Juego | tiempo al primer contacto por bando | longitud de ruta / velocidad | diferencia entre bandos ≤ 15 % |
| Juego | densidad de cobertura | colliders ≥ 0,5 m por cada 100 m de ruta | ≥ N (fijar tras la primera medida) |

Los umbrales de juego son una propuesta. Hay que calibrarlos con dos o tres partidas reales antes de convertirlos en un control que bloquee.

**Mapas de referencia (benchmark fijo).** Cada medida se toma sobre los mismos mapas, con semilla fija:

- `murcia` r150: el caso base;
- Madrid r300 con Catastro: el más grande que exporta;
- París r50 a 40 t/m: el peor bake;
- una ciudad sin atlas propio, como Cracovia: valida que vale fuera de España.

---

## 2. Diagnóstico requisito por requisito

Veredicto: **Hacer** (falta y encaja), **Ajustar** (falta, pero la propuesta choca con algo y se reformula), **Validar** (el código está; falta el casco), **Descartar** (lo prohíbe una regla o una medida).

### 2.1 Realismo

| Requisito | Estado hoy | Veredicto | Reformulación |
|---|---|---|---|
| Fachadas con fotos de Panoramax | En desarrollo y opcional (`--panoramax`). Proyecta un decal por tramo de muro, 4 cm por delante, con su PNG y su material (`panoramax/apply.py`). Exige `facades: legacy` | **Ajustar** | Que conviva con el atlas. Cada fachada fotografiada también muestrea su **color dominante** como tinte (gratis en draws). Los decals se agrupan en un atlas por celda; son CC BY-SA, así que no se mezclan con el atlas CC0 (data-sources.md). Hay que medir cobertura y draws |
| Mapillary, KartaView, Street View | Street View prohibido por licencia; Mapillary descartado para texturas (informe §5). KartaView no aparece en el repositorio | **Descartar** Street View. **Investigar** KartaView (licencia CC BY-SA, por confirmar) solo como plugin de imágenes, detrás de la misma lista de licencias de ADR-018 |
| Fotogrametría ligera o splats para edificios emblemáticos | No existe | **Ajustar** | Los splats no se pueden pintar con un shader opaco del SDK: se descartan. Sí se puede **importar modelos LoD2/LoD3 con licencia abierta** como volumen de landmark (ADR-008). Ejemplo: el LoD2 de Madrid, CC BY 4.0 (informe §3) |
| Tipologías regionales | Cinco estilos (`config/facade_styles.json`); **solo `mediterranean` tiene atlas** y los demás caen a la ruta por material | **Hacer** (D1: código hecho, opcional, falta el casco) | Atlas para `central_european`, `anglo` y `latin_american`. Franjas por época (pendiente en ADR-027). Con D1, `"regional_facades": true` les da atlas, ventanas propias y bandas por época (`config/facade_styles.regional.json`) |
| Tejados con LiDAR | LiDAR solo da la mediana de altura. Las formas de tejado salen de `roof:shape` de OSM o de una estimación por plantilla (ADR-023) | **Hacer**, acotado | Clasificar **plano o inclinado** con la dispersión p25–p75 del nDSM, que ya se calcula. Orientar la cumbrera por el eje largo. Atributo `estimated` con su origen. LoD2 completo desde LiDAR crudo sigue descartado (informe §5). **Hecho (C3, desactivado por defecto)**: `map.json` `"lidar_roofs": true` |
| Normales y roughness | Imposible con `PP_Opaque_Simple_Lit` (ADR-020). Existe `PP_Opaque_Lit` en el SDK, sin usar (docs/11) | **Ajustar** | Un *spike* aislado: un material de fachada con normal en `PP_Opaque_Lit`, medido en el casco. Si el coste es aceptable, nuevo ADR que enmiende ADR-009/020. Mientras tanto, AO y suciedad horneadas en el albedo |
| Repetición de texturas | Límite de contraste grueso para asfalto, hormigón y muros (`materials/library_plan.py`). Tejados, pavimento y puertas no se miden | **Hacer** | Ampliar la métrica. Rotación y desplazamiento de UV por edificio, que ya se aplican en fachadas, también para el pavimento. Variantes de tile por celda |
| Luces interiores | El adaptador crea una luz puntual horneada por habitación y un `LightProbeGroup` (hasta 160 por mapa). No verificado en casco (ADR-013) | **Validar** | Sesión de casco. Si funciona, cerrar el `todo.md` |
| Tiendas amuebladas | Catálogos en `assets/`, todos `placement: interior`; «Nothing places these yet» (`overgrow/manifest.py`) | **Hacer** | Colocador de interiores. Ver §3, B3 |
| Más LiDAR (EE. UU., Reino Unido, Alemania, Países Bajos, Australia) | Plugins: `es.pnoa_ndsm`, `fr.lidar_hd`, `es.catastro`, `overture` (ADR-018) | **Hacer** | Un plugin por país, con la licencia comprobada antes: AHN (NL), Environment Agency (UK), 3DEP (US), NRW/Bayern (DE). Ninguna verificada hoy |
| Huellas de Microsoft, Google y Overture | Llegan conflacionadas por Overture (ADR-021); solo prestan atributos a edificios OSM por IoU ≥ 0,5 | **Descartar** añadirlas como geometría (AGENTS.md: no inventar edificios). **Hecho (C2, desactivado por defecto)**: `facade_color` y `roof_color` de Overture se prestan como tintes `observed` con `map.json` `"overture_tints": true` (enmienda de ADR-021) |
| Catastros de otros países | Solo España | **Hacer** caso a caso, como plugin | Por ejemplo, BAG (NL), cadastre FR (PCI). Licencias por verificar |
| IA local más agresiva | La intención solo admite cuatro parámetros (ADR-006). El overgrow con IA admite 24 gestos (ADR-010). El brief se reduce a `street_blocking` | **Ajustar** | Ampliar el esquema por **elección entre ids enumerados** (tipología, paleta, perfil de props, nivel de abandono), nunca valores libres. Validación por lista blanca y repetición byte a byte, como hoy. Ver §3, D2 |

### 2.2 Escalabilidad

| Requisito | Estado hoy | Veredicto | Reformulación |
|---|---|---|---|
| Núcleo por presupuesto de triángulos | Radio fijo, `min(150, radius−50)`. El modo por presupuesto quedó como «next step» (ADR-017) | **Hacer** | Un modelo de coste: `detail` ya escribe `visual.triangles` por objeto (comprobado en las partes de edificio, `detail/scene.py:791`; en los edificios enteros hay que comprobarlo). Una ejecución con núcleo máximo, y después un crecimiento greedy desde los spawns hasta el objetivo. El núcleo puede ser irregular, no un cuadrado |
| LOD reales | Anillo de cajas de unos 44 triángulos, frente a unos 1180 de un edificio detallado. Horizonte con fog y backdrop (ADR-014). `LODGroup` no fiable | **Ajustar** | No hay LOD dinámico. Lo que sí cabe es un **HLOD estático de tres anillos**: núcleo detallado; anillo intermedio con cajas, celda de atlas y forma de tejado (unos 50–150 triángulos, idea del plan del 31 de octubre); anillo `scenic` actual |
| Bake inteligente | `draft` por defecto (32/128/128). `ProgressiveGPU`. Escala de texels 0,25 para `scenic`, que **sí** se hornea (ADR-017) | **Hacer** | Sacar el `scenic` del GI (`ContributesGi`) e iluminarlo con probes o ambiente. **Código hecho (C4, desactivado por defecto)**: `build.scenic_out_of_lightmap: true`; falta medir el horneado y el casco. Medir de una vez la ganancia de `draft`, nunca medida (docs/12). Añadir un tope `lightmap_pages_max` (U2, sin hacer). Bakery y otros lightmappers externos: descartados por ahora, por ser otra dependencia de pago en un proyecto Unity que no es nuestro |
| Instancing y combinación | Combinación por celda de 100 m y material. `enableInstancing = false`. Occlusion off (sin ganancia medida) | **Ajustar** | La combinación ya hace el trabajo del instancing en draws. Lo que queda es **memoria y tamaño**: medir si el instancing de props repetidos baja el paquete sin subir los draws. Occlusion: descartado mientras no haya una medida nueva |
| Windows | No es host (ADR-011) | **Ajustar**, baja prioridad | Python ya corre sin Docker. Windows como host de Unity es otro perfil y otros scripts, y la parte del SDK que hoy se parchea es justo la de fuera de Windows. Abrirlo solo si hay usuarios que lo piden |
| Sin Docker | Soportado con venv (README) | **Hecho** | Documentarlo mejor |
| «Solo GLB e instrucciones» | `run --no-unity` para en el paquete e imprime el comando de Unity | **Hecho** | Un `README` dentro del paquete con los pasos del SDK. Un GLB sigue sin ser un mapa jugable (AGENTS.md) |

---

## 3. Plan de acción por bloques

Cada línea es un PR propio con su prueba. Lleva ADR cuando cambia una decisión. Los bloques A y B van primero; dentro de cada bloque, los incrementos sin dependencias pueden ir en paralelo.

### Bloque A: medir y cerrar la deuda (prerrequisito)

| # | Incremento | Dónde | Medida |
|---|---|---|---|
| A1 | **`task bench`**: genera los mapas de referencia (§1) con semilla fija y escribe un `bench_report.json` con triángulos, draws previstos, páginas, segundos por fase y la hoja de contactos. Compara con la ejecución anterior | `tools/bench/` (no es una herramienta de control de calidad: no toca `tools/quality/`) | Que sea reproducible: dos ejecuciones dan el mismo informe salvo los tiempos |
| A2 | **Sesión de casco agrupada** con lo pendiente: ASTC y costuras del atlas, huecos planos, luces interiores, núcleo y anillo, el `draft` frente al `final`. Pasa ADR a `accepted` o los corrige | `needs-user`, docs/09 | Checklist de docs/09, con cifras |
| A3 | **Medir `draft` frente a `final`** en los cuatro mapas | adaptador y `report_timings.py` | `seconds.bake` de los dos perfiles |

### Bloque B: las tres prioridades

| # | Incremento | Dónde | ADR | Medida |
|---|---|---|---|---|
| B1 | **Núcleo por presupuesto**: `core.mode: budget`, con `target_triangles`. Coste por edificio desde una ejecución de `detail`; el núcleo crece desde los spawns y las rutas hasta el objetivo. El radio fijo se queda como modo por defecto hasta validar | `tiers.py`, `detail`, `maps/workspace.py` | Enmienda a ADR-017 | Triángulos del núcleo dentro de ±5 % del objetivo en r150, r300 y r500; ningún edificio partido |
| B2 | **Panoramax con atlas**: quitar la exigencia de `legacy`; tinte de color por fachada fotografiada; decals agrupados por celda; `panoramax_report` con cobertura (% de frente con foto) | `panoramax/`, `materials` | Enmienda a ADR-020 | Cobertura en dos ciudades con fotos; draws añadidos ≤ 10 por celda |
| B3 | **Colocador de interiores para tiendas**: elige mobiliario por uso de la tienda (Catastro, Overture Places, OSM `shop=*`), con reglas: mostrador junto al escaparate, estanterías contra la pared, paso libre de 1,2 m, sin tapar la puerta. Respeta `shops.max_triangles`. Collider solo en lo que sirve de cobertura | `detail/shops.py` o un módulo `furnish` nuevo | Nuevo ADR (amueblado) | Tiendas amuebladas / total; navmesh sin cortes; triángulos ≤ presupuesto |
| B4 | **Diseño de juego, primera versión**: rutas entre spawns sobre la navmesh, líneas de visión, cobertura por tramo y equilibrio por bando, **solo como informe** (`gameplay_report.json`). Ninguna geometría cambia todavía | `perf/layout.py` (o `gameplay/`) | Nuevo ADR (métricas de juego) | Las métricas de §1 en los cuatro mapas |

**Estado de B1 (7 de noviembre de 2026):** hecho en la rama `workshop/b1`, desactivado por defecto. El núcleo sigue siendo un cuadrado alrededor del centro: los spawns y las rutas no existen todavía cuando corre `detail`. El coste sale de una sola ejecución de `detail` con el núcleo máximo, y el cuadrado se resuelve sin búsqueda. Dentro de ±5 % en r150, r300 y r500 (rejillas sintéticas en `tests/integration/test_core_budget.py` y los mapas del bench en [09-perf.md](../09-perf.md#budget-core)). La enmienda a ADR-017 queda `proposed` hasta la sesión de casco (A2).

**Estado de B4 (7 de noviembre de 2026):** hecho en la rama `workshop/b4`, solo como informe y desactivado por defecto (`perf layout --playable --gameplay-report`, `"gameplay_report": true` en `map.json`, `bench.py run --gameplay-report`). `gameplay_report.json` va junto a `layout.json`; `layout.json` y el paquete no cambian, byte a byte, con la opción encendida o apagada. Las métricas de §1 en los cuatro mapas están en [09-perf.md](../09-perf.md#gameplay-metrics): rutas distintas 2, 3, 4 y 6; línea de visión abierta más larga 134 a 291 m (falla los 60 m propuestos en los cuatro); diferencia de contacto 5,2 %, 29,5 %, 0,7 % y 20,5 %; la cobertura N se informa sin umbral. Son estimaciones sobre la navmesh 2D. Una ruta comparte un tramo con otra cuando pasa a menos de 8 m: con solo las aristas compartidas, Murcia daba 6 rutas que eran carriles paralelos de la misma calle. ADR-032 queda `proposed` hasta la partida de prueba de 4.2. C6 y C7 no han empezado.

### Bloque C: realismo de bajo coste y escala

| # | Incremento | Depende de |
|---|---|---|
| C1 | Repetición: métrica para tejados, pavimento y puertas; variantes por celda | A1 |
| C2 | Tinte de `facade_color` y `roof_color` de Overture como atributo `observed`, de origen `overture` | — |
| C3 | Tejado plano o inclinado desde el nDSM (`estimated`, con origen) | — |
| C4 | `scenic` fuera del GI, con probes o ambiente (`ContributesGi`) | A3 |
| C5 | `lightmap_pages_max` (U2) | A3 |
| C6 | Spawns y objetivos **colocados por las métricas de B4**: varias posiciones candidatas, se elige la más equilibrada. Más rutas por callejones y patios (`todo.md`) | B4 |
| C7 | Cobertura en tramos con línea de visión larga: props sólidos del catálogo en los puntos que B4 marca, sin tapar el paso de 1,2 m | B4 |

**Estado de C2 (8 de noviembre de 2026):** hecho en la rama `workshop/c2`, desactivado por defecto (`map.json` `"overture_tints": true`, `overture match --tints`, `detail --overture-tints`, `bench.py run --overture-tints`). Un color de Overture solo se presta desde un dataset `observed` que no sea OpenStreetMap, con licencia de la lista, y por debajo de toda etiqueta OSM de muro o tejado. Va con la evidencia en `wall_evidence` y `roof_evidence` (proveedor `overture`, GERS id, dataset, licencia). El emparejamiento va aparte del de alturas, así que `heights_overrides.overture.json` no cambia. En los cuatro mapas del bench no se presta nada: todos sus colores de Overture vienen de OpenStreetMap, con delta 0 en triángulos, draws y páginas ([09-perf.md](../09-perf.md#overture-colours-as-tints)). El efecto se prueba solo con el parquet sintético. La enmienda de ADR-021 queda `proposed` hasta encontrar una ciudad con colores que no sean de OSM y pasar la sesión de casco.

**Estado de C3 (8 de noviembre de 2026):** hecho en la rama `workshop/c3`, desactivado por defecto (`map.json` `"lidar_roofs": true`, `detail --lidar-roofs <report>`, `bench.py run --lidar-roofs`). `detail` lee el `<id>_report.json` que ya escribe el plugin LiDAR (sin cambiar `heights_overrides` ni su esquema) y clasifica el tejado de cada edificio sin etiqueta `roof:*` como plano o inclinado por la dispersión p25–p75 del nDSM, normalizada por la media luz, con al menos 8 celdas válidas, huella al menos al 85 % de su rectángulo mínimo y la regla de terraza intacta. Un inclinado es un `gabled` con cumbrera por el lado largo y subida igual a la dispersión (máx. 4 m) sobre la altura registrada; un plano conserva el parapeto y no recibe la cadera estimada. Se registra como `estimated` con su origen (`roof.lidar`: plugin, proveedor, licencia, p25, p75, celdas, regla) y `counts.lidar_roofs`. Con la opción apagada las salidas de `detail` son idénticas byte a byte y el bench da delta 0. Con ella, Murcia clasifica el 2,6 % de los edificios del núcleo (2 planos, 4 inclinados, −161 triángulos, draws iguales), Madrid el 3,3 % (9 y 12, −984 triángulos, +1 draw) y París y Cracovia no cambian ([09-perf.md](../09-perf.md#roof-form-from-the-ndsm-spread)). La enmienda de ADR-023 queda `proposed` hasta la sesión de casco: cumbreras y piñones sin huecos ni costuras, edificios no más altos en un tejado entero, terrazas de Catastro planas, ningún acceso a tejado inclinado ni ruta perdida, ≥ 72 fps y ≤ 150 draws en las vistas A y B con la opción encendida y apagada, y una prueba de juego opcional.

**Estado de C4 (8 de noviembre de 2026):** código hecho en la rama `workshop/c4`, desactivado por defecto; falta medir el horneado y la sesión de casco. La tarjeta de exportación acepta `build.scenic_out_of_lightmap` (booleano opcional; sin él el paquete es idéntico, `package_id` incluido). Con la opción, los muros `scenic_*` del anillo no ocupan lightmap (`scaleInLightmap = 0`, `receiveGI = LightProbes`, luz ambiente al combinar) pero siguen en `ContributeGI` y proyectan las sombras horneadas. Los tejados del anillo comparten `roof_flat_grey` con el núcleo y siguen horneados. El espejo de Python (`export/lightmap.py`) quita la clase `scenic` de `lightmap_scales` y de `lightmap_estimate`; `bench.py run --scenic-out-of-lightmap` la activa en la copia del run. Apagada, el bench da delta 0. Encendida, la estimación baja Madrid 27 % (11 → 8 páginas), Murcia 14 % (4 → 3), Cracovia 11 % y París nada; triángulos y draws iguales ([09-perf.md](../09-perf.md#scenic-walls-out-of-the-lightmap)). A3 no está disponible: compilar en Unity 2021.3.23f1, medir `draft` y `final` con la opción encendida y apagada (`report_timings.py`) y revisar el brillo del anillo y la costura con el núcleo en el casco son pasos del usuario. La enmienda de ADR-017 queda `proposed` hasta entonces.

**Estado de C5 (8 de noviembre de 2026):** código hecho en la rama `workshop/c5`, desactivado por defecto; falta medir el horneado y la sesión de casco. La tarjeta de exportación acepta `build.lightmap_pages_max` (entero de 1 a 64; sin él el paquete es idéntico, `package_id` incluido). Con la clave, `export package` baja `lightmap_texels_per_m` en pasos de 0,25 (bisección) hasta la mayor densidad cuya estimación cabe en el presupuesto, con las escalas por clase y la opción C4; nunca por debajo de 4 t/m (el doble de los 2 t/m en que ADR-005 vio solapes de UV), y si no cabe ahí rechaza el paquete con un `ExportError`. La densidad aplicada va a `adapter_options.json`, que el adaptador ya usa para el horneado y el margen del desplegado, sin cambio en C#; `lightmap_estimate` añade `texels_per_m_requested` y `lightmap_pages_max`. `bench.py run --lightmap-pages-max N` la activa en la copia del run. Apagada, el bench da delta 0. Con N = 8 solo cambia Madrid r300 (10 → 8,75 t/m, 11 → 8 páginas); con C4 también encendida, Madrid se queda en 10 t/m y 8 páginas; triángulos y draws iguales ([09-perf.md](../09-perf.md#lightmap-page-budget)). A3 no está disponible: comprobar en Unity 2021.3.23f1 que el horneado no pasa de N páginas (la estimación es un mínimo), los segundos de `draft` y `final` y las sombras más blandas en el casco son pasos del usuario. La enmienda de ADR-005 queda `proposed` hasta entonces.

**Estado de C6 (8 de noviembre de 2026):** código hecho en la rama `workshop/c6`, desactivado por defecto; faltan la partida de prueba de 4.2 y la sesión de casco. `perf layout --playable --balanced-spawns` (`"balanced_spawns": true` en `map.json`, `bench.py run --balanced-spawns`) prueba hasta 8 colocaciones: 4 posiciones de los defensores (las más lejanas que caben, a 30 m o más de los atacantes y a 10 m entre sí) y, para cada una, el Uplink en el punto medio o en el nodo del camino más corto donde los dos equipos llegan casi a la vez. Las puntúa con las métricas de B4 (las mismas funciones que el informe) y se queda con la de menor diferencia de contacto, luego más rutas distintas, luego menos solape, luego el índice; solo sustituye a la colocación actual si es estrictamente mejor, y si no, o si no se puede construir, la conserva y lo dice. `layout.json` no cambia de claves ni de versión. Apagada, el bench da delta 0 frente a `workshop/c5`. Encendida, dos runs son idénticos y la diferencia de contacto baja en los cuatro mapas: Murcia 5,23 → 2,29 %, Madrid 29,48 → 0,57 %, París 0,67 → 0,13 %, Cracovia 20,48 → 0,49 %, todos por debajo del 15 %; las rutas distintas no bajan (2, 3, 4 y 6) y Murcia sigue sin llegar a 3; la etapa de layout sube como mucho a 5,1 s en Madrid ([09-perf.md](../09-perf.md#balanced-spawns)). "Más rutas por callejones y patios" queda en parte: solo se prefieren colocaciones con más rutas por el suelo transitable que ya existe; no se añade geometría ni se tocan las patrullas de Hunt. La enmienda C6 de ADR-032 queda `proposed` hasta la partida de prueba. C7 está hecho en `workshop/c7` (abajo).

**Estado de C7 (8 de noviembre de 2026):** código hecho en la rama `workshop/c7`, desactivado por defecto; faltan la partida de prueba de 4.2 y la sesión de casco. Con `"sightline_cover": true` en `map.json` (`bench.py run --sightline-cover`), `run` añade la etapa `cover` (`perf cover`) entre `layout` y `export`: copia `scene_playable` en `scene_covered` y coloca hasta 24 props sólidos del catálogo ya instalado (barreras, barriles, restos; ningún asset nuevo) en las muestras de ruta que el informe de B4 ve abiertas más de 60 m, de mayor a menor. Cada prop deja 1,2 m de paso (la prueba de erosión de ADR-031, en local y en todo el suelo jugable), queda a 5 m de spawns, objetivos y salidas de IA, a 3 m de munición, escaleras, ascensores y puertas, fuera de la calzada, de los edificios y de las rutas de Hunt; su colisión va solo a `scene_collision.glb` y cada uno lleva un registro `generated` con `op: sightline_cover` y el motivo. `export` lee entonces `scene_covered`. Apagada, el bench da delta 0 frente a `workshop/c6` y los archivos son idénticos byte a byte. Encendida, dos runs son idénticos; la línea de visión abierta más larga baja en los cuatro mapas (Murcia 133,86 → 129,42 m, Madrid 291,21 → 254,3 m, París 195,2 → 192,34 m, Cracovia 208,99 → 205,01 m), los tramos sin cobertura bajan de 17 a 4, 53 a 35, 19 a 5 y 66 a 31; rutas, solape y diferencia de contacto no cambian (la navmesh 2D ignora el mobiliario), con +264 a +934 triángulos, +2 a +5 draws y las mismas páginas de lightmap ([09-perf.md](../09-perf.md#cover-on-long-sightlines)). La cobertura mínima por 100 m sigue en 0,0: quedan tramos sin cobertura junto a los spawns. Parte de la mejora es por definición (una muestra con cobertura a 1,5 m deja de contar aunque el prop no tape la vista). La enmienda C7 de ADR-032 queda `proposed` hasta la partida de prueba y el casco.

### Bloque D: ampliaciones mayores

| # | Incremento | Nota |
|---|---|---|
| D1 | Atlas `central_european`, `anglo` y `latin_american`; franjas por época | Cada atlas, su licencia CC0 y su revisión en casco |
| D2 | IA: esquema `street_profile` con ids enumerados (tipología dominante, paleta, perfil de props, abandono, cobertura). Validación por lista blanca | Enmienda a ADR-006/010 |
| D3 | Anillo intermedio HLOD (cajas con celda de atlas y tejado) | Después de B1: el núcleo dinámico decide qué cae en él |
| D4 | Plugins LiDAR y catastro: AHN, EA, 3DEP, NRW, BAG | Uno por PR, la licencia antes del código |
| D5 | *Spike* `PP_Opaque_Lit` con normales | Medida en casco antes de cualquier ADR |
| D6 | Landmarks desde LoD2 abierto (Madrid) | ADR-008 |
| D7 | Amueblado de plantas de vivienda y luces por planta | Después de B3 y A2 |

**Estado de D1 (8 de noviembre de 2026):** código hecho en la rama `workshop/d1`, desactivado por defecto; falta la sesión de casco. `config/facade_styles.json` no cambia (ni su hash). Las recetas de `central_european`, `anglo` y `latin_american` van en `config/facade_styles.regional.json`, que solo se lee con `"regional_facades": true` en `map.json` (`materials apply --regional-facades`, `bench.py run --regional-facades`). Misma geometría que el atlas mediterráneo (2048 × 4096, seis paredes en tres tonos, sin memoria extra por estilo), y las mismas familias de pared (plaster, ashlar, brick) para el tinte de Panoramax. Ventanas pintadas por código, sin aleatoriedad: batientes sin persiana (centroeuropeo), guillotina (anglo) y rejas de hierro sobre hojas de madera (latinoamericano); la ventana de persiana mediterránea no cambia (bytes fijados por test, `TRIM_BAKE_VERSION` sigue en 3). Bandas por época como *looks*, no franjas: un edificio con época `observed` toma los tonos, familias de ventana y reparto de zócalo/cornisa/imposta de su periodo; sin época observada conserva el look de siempre (ADR-024). El informe registra `era_bands` (época observada, banda estimada). Fuentes, todas CC0 de la biblioteca y sin descargas: centroeuropeo `ph_painted_plaster_wall`, `acg_rock022`, `acg_bricks023`; anglo `ph_plastered_wall`, `acg_rock051`, `acg_bricks059`; latinoamericano `acg_paintedplaster006`, `acg_rock034`, `acg_bricks097`. `acg_paintedplaster010` y `014`, propuestos, se descartaron en la hoja de contacto porque son revoque desconchado sobre ladrillo. Hueco: no hay en la biblioteca ladrillo en aparejo flamenco ni adobe o encalado. Apagada, el bench da delta 0 frente a `20261108T232105Z` y paquetes idénticos byte a byte (salvo `onward_export.json`, como entre dos runs cualesquiera). Encendida, dos runs son idénticos, Murcia, Madrid y París no cambian, y Cracovia pasa de 272 a 214 draws previstos (−21 %), de 118 a 103 materiales, de 72 884 a 77 688 triángulos (+6,6 %) y de 14,5 a 17,5 MiB de textura en ASTC 6×6 ([09-perf.md](../09-perf.md#regional-atlases)). Solo 1 de 155 edificios de Cracovia tiene época observada. `anglo` y `latin_american` no tienen mapa de bench: los cubren tests unitarios y de integración. Las enmiendas de ADR-020 y ADR-027 quedan `proposed` hasta el casco (lista en 09-perf.md).

### Fuera del plan (no objetivos)

- Street View, Apple o Google 3D Tiles, texturas de Mapillary, fachadas o texturas generadas por IA.
- Gaussian splats.
- Huellas sin edificio OSM.
- `LODGroup`, occlusion culling sin medida nueva y lightmappers de pago.
- Ejecutar código devuelto por el modelo, o dejar que la IA escriba geometría o valores libres.

---

## 4. Workflow

### 4.1 Ciclo de cada incremento

Sigue el flujo obligatorio de AGENTS.md y le añade la medida antes y después:

1. **Ficha** (en el PR): objetivo, criterio de §1 que mueve, ADR que toca, mapas del benchmark afectados.
2. **Línea base**: `task bench` sobre `main`. Se guarda `bench_report.json`.
3. **Plan corto** en el PR, antes del código, si el cambio es significativo.
4. **Incremento mínimo** con tests: unitarios para el dominio, de integración si cambia el pipeline. Fixtures pequeñas con `MURCIA_CENTER`.
5. **Controles**: `task lint`, `task typecheck`, `task test`; después del commit, `task mutate BASE=origin/main` y `task guard BASE=origin/main`. Un cambio de control va en su propio PR.
6. **Comparación**: `task bench` sobre la rama. El PR lleva la tabla de diferencias (triángulos, draws, páginas, segundos, métricas de juego) y la hoja de contactos.
7. **Documentación**: el documento numerado y el ADR, si cambia un contrato.
8. **Casco**: el incremento queda en `needs-user`, con su punto en el checklist de la próxima sesión. El ADR sigue en `proposed` hasta entonces.

### 4.2 Cadencia

- **Una sesión de casco por bloque** (A2 al empezar y una al cerrar B, C y D). Es lo que convierte `proposed` en `accepted`.
- **Partida de prueba con jugadores** al cerrar B4 y C6, para calibrar los umbrales de juego de §1.
- Nada se activa por defecto (`core.mode: budget`, Panoramax con atlas, amueblado) hasta pasar el casco. Antes va detrás de una opción.

### 4.3 Orden recomendado

```
A1 ─┬─ A3 ─┬─ C4, C5
    │      └─ (B1 usa sus cifras)
    ├─ B1 ─── D3
    ├─ B2
    ├─ B3 ─── D7
    └─ B4 ─┬─ C6
           └─ C7
A2 (casco) en paralelo, al principio
C1, C2, C3 sueltos, cuando haya hueco
D1, D2, D4, D5, D6 después del casco de B
```

---

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| Luces y probes interiores que el runtime de Onward no admite | A2 antes de B3 y D7 |
| El núcleo irregular deja edificios cortados en el borde | B1 trabaja por edificio entero, nunca por triángulo |
| La cobertura de Panoramax es baja fuera de Francia | B2 informa la cobertura; el tinte de color solo actúa donde hay foto |
| Licencias CC BY-SA de las fotos en un paquete que se publica | Atribución por foto en los extras del GLB y en el informe (ya existe); atlas aparte |
| Métricas de juego que no predicen la diversión | Son un informe hasta calibrarlas con partidas (4.2) |
| Las cifras de bake dependen del host | `task bench` registra el host y la GPU |

---

## 6. Primer paso recomendado

**A1 (`task bench`)**, porque todo lo demás se valida con él, y **A2** en paralelo. Después, B1 y B3, que son los que más se notan con menos riesgo. B4 da la base para el diseño de juego.

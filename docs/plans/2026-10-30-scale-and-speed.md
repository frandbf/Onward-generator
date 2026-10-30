<!-- Written in Spanish for the maintainers. Numbers are as of fork/main 8609691; later PRs change them. -->

# Plan: Onward-generator más rápido y escalable

*Síntesis de cinco estudios independientes (perfil del pipeline, escalado por tamaño, arquitectura, Unity y ciclo de desarrollo), corregida con una revisión escéptica. Base: `fork/main` 8609691, 2026-10-30.*

*Marcas de origen de cada cifra:*
- ***[M]***: se midió en los estudios.
- ***[M-rev]***: se midió en la revisión, con micro-benchmarks en la imagen `onward-generator:dev` limitada a 4 CPU.
- ***[log]***: sale de los logs de ejecución de los estudios.
- ***[E]***: es una estimación sacada del código, de la documentación o de extrapolaciones.

*Ninguna cifra viene de fuera de los estudios o de la revisión.*

---

## 1. Resumen ejecutivo

- **Tiempo de un mapa r=150:** unos 40 s de Python sin red [M] y 83 s con red [M]. La red se lleva unos 50 s: 42 s de Overpass y 8 s de Catastro [M]. Python no es el cuello de botella del tamaño del mapa, pero sí de la velocidad de iteración.
- **Codificar PNG a nivel 9 es el 32 % del tiempo de una ejecución perfilada** (18,1 s de 57 s [M]). Esa base perfilada no es la misma que la de los 40 s sin perfilar.
  - `materials` tarda entre 13 y 15 s fijos por mapa, y el 86 % es codificar PNG [M]. Es un coste fijo porque en cada ejecución vuelve a hornear y a codificar los mismos JPEG de `assets/textures` [E].
  - Una caché por contenido (P7) quita unos 12 s y deja la salida idéntica byte a byte [E].
  - Con 4 hilos a nivel 9, la entrada real baja de 10,1 a 4,6 s, también con bytes idénticos [M-rev].
  - Pasar del nivel 9 al 3 acelera unas 2,9 veces, no 7,6 [M-rev]. Ese cambio rehace todos los hashes, así que va en un segundo paso.
- **Las previsualizaciones de diagnóstico cuestan unos 14 s en la ejecución perfilada** [M]. Con `--no-preview`, `detail` baja 3,1 s y `generate` baja 0,9 s [M].
  - P1 y P2 juntos llevan r150 de unos 40 s a unos 24 s [E].
  - Para llegar a unos 15 s hacen falta también P8 (regenerar la fase 03 una sola vez) y P4 [E].
- **Hoy el límite de tamaño es la robustez de la geometría con Catastro.** Madrid r150 con Catastro llega a export en 39,2 s de Python [log]. r200 y r300 fallan en `generate`, con 4 y 5 críticos [log].
- **El presupuesto de triángulos de Quest ya está lleno con el tamaño actual.** ADR-005 registra 247 627 triángulos en Unity, prefabs del SDK incluidos, en el límite de unos 250 000 de la guía (que incluye a los jugadores). Las tiendas interiores se añadieron después.
- **El barrido de escala a partir de r200 es solo OSM**, es decir, la modalidad barata. Con Catastro no se han medido costes por encima de r150.
- **El Overpass actual trae entre un 91 % y un 94 % de datos fuera del área**, por las relaciones de ruta [M]. Filtrando a `type=multipolygon` el extracto es 6 veces menor y `geo_scene` sale idéntico [M]. `geo_report` sí cambia.
- **Unity ya registra `seconds.scene` y `seconds.bake`** en `scene_report`. Lo primero es reunir esos valores; no hay tiempos de las demás fases.
  - El presupuesto de páginas de lightmap (U2) solo importa en mapas creados con densidad alta (40 t/m) o a partir de r300. Con la densidad por defecto (10 t/m), r150 necesita 2–3 páginas [estimador].
- **En el ciclo de desarrollo lo que pesa es el proceso.** En 15 de 28 PR la primera CI falló en `mutate` [M]. Cada PR necesita de media 2,4 ejecuciones de CI [M]. Se consumen unos 600 minutos de runner al día [M].
  - El repositorio es privado, así que eso son unos 18 000 min al mes, unas 9 veces los 2000 supuestos.
  - O ya se están facturando excesos o la suposición es errónea; la facturación no se pudo consultar.

---

## 2. Línea base medida

| Etapa / recurso | Valor | Tipo |
|---|---|---|
| `run --no-unity` Madrid r150 con Catastro, total (incluye arranque de Docker) | 83,2 s; 39,2 s de Python; las 14 etapas llegan a export | M / log |
| `run --no-unity --no-catastro` Murcia r150, total | 40,2 s | M |
| Preparación del mapa con descarga de Overpass (r150) | 42,1 s. El servidor tarda 35–36 s en responder (TTFB), aunque la consulta pese 6 veces menos | M |
| Descarga de Catastro WFS r100 / r150 / r200 / r300 | 7,3 / 8,1 / 19,8 / 21,6 s | M |
| Etapas Python Madrid r150, en reejecución | generate 1,2 · detail 5,4 · dress 2,5 · materials 13,7 · signs 0,4 · overgrow 0,9 · layout 1,4 · export 1,2 s | M |
| Etapas Python Murcia r150 | generate 1,7 · detail 8,1 · dress 3,6 · materials 14,6 · signs 3,3 · overgrow 1,4 · layout 4,1 · export 2,4 s | M |
| Etapas `geo`, `heights`, `cadastre`, `fit`, `bounds` | entre 0,0 y 1,8 s | log |
| Python total sin Catastro, r100 / r200 / r300 / r500 | 24,1 / 37,4 / 49,5 / 121 s (r300 llega a export) | M / log |
| `materials` (coste fijo), r100 → r500 | 14,9 / 16,0 / 15,2 / 19,8 s | M |
| `layout`, r100 → r500 (solo OSM a partir de r200) | 0,8 / 2,4 / 4,6 / 20,4 s; a r500, 8,1 s son regeneración de la fase 03 | M / log |
| `signs`, r100 → r500 (solo OSM a partir de r200) | 0,4 / 1,1 / 2,9 / 15,0 s; incluye codificar hasta 2 páginas de atlas de rótulos de 2048² a nivel 9 | M / E |
| `detail`, r100 → r500 (solo OSM a partir de r200) | 2,7 / 8,1 / 14,3 / 34,5 s | M |
| Edificios tras `heights`, sin / con Catastro | r100: 10 / 36 · r200: 53 / 206 · r300: — / 554 · r500 (solo OSM): 414 | log |
| Codificación PNG (ejecución perfilada de 57 s) | 18,1 s (32 %); en `materials`, 12,6–12,9 s | M |
| PNG en la entrada real de `materials` (14 teselas de foto, 1024² RGBA) | nivel 9 en serie: 10,1 s y 29,0 MB · nivel 3 en serie: 3,45 s y 30,2 MB · nivel 9 con 4 hilos: 4,56 s, bytes idénticos · nivel 3 con 4 hilos: 1,0 s, bytes idénticos | M-rev |
| PNG en teselas procedurales de 1024² | nivel 9: 3,82 s · nivel 3: 1,60 s (2,4 veces) | M-rev |
| PNG en RGB frente a RGBA (nivel 3; el alfa vale siempre 255) | 2,19 s frente a 3,45 s; 12 % más pequeño | M-rev |
| `render_rgb` (previsualizaciones, perfilado) | 13,0 s acumulados: detail 8,6, miniatura de export 3,3, generate 1,1 | M |
| Fase 03 regenerada | 4 veces por ejecución (generate, detail, dress y layout); 0,65–1,1 s cada vez a r150 y 8,1 s a r500 | M / log |
| Arranque | contenedor 0,5–0,78 s; imports unos 0,8 s; GC 0,06 s | M |
| Leer y reescribir el GLB de 49 MB / sha256 | 0,25 s / 0,023 s | M |
| Memoria pico a r500 | unos 1,2 GiB, un solo núcleo | M |
| Disco por mapa r100 / r150 / r200 / r300 / r500 (una sola instantánea) | 178 / 284 / 276 / 409 / 737 MB | M |
| Triángulos visuales del GLB r100 / r200 / r300 / r500 (solo OSM a partir de r200; sin prefabs del SDK ni jugadores) | 76k / 227k / 441k / 1,08M | M |
| Triángulos en Unity con prefabs del SDK (ADR-005, anterior a las tiendas interiores) | 247 627 | ADR-005 |
| Draws de todo el mapa con celda de 100 m (solo OSM a partir de r200) | 154 / 366 / 684 / 1548 | M (predicción de `perf measure`) |
| Páginas de lightmap a 10 t/m, la densidad por defecto (r100 → r500) | 1 / 5 / 10 / 25; r150, 2–3 | M (estimador del exportador) |
| Páginas de lightmap de Madrid r150 a 40 t/m, el máximo | 21 | M |
| Unity: tiempos registrados | `seconds.scene` y `seconds.bake` existen en `scene_report.json`, pero no se han reunido | código |
| Unity: bake Madrid r150 a 40 t/m (21 páginas) | 5–15 min | E (no contrastado con `scene_report.json`) |
| Unity: arranque del editor por lanzamiento | 20–60 s, 2 lanzamientos por mapa | E |
| Unity: paso del launcher | 17,8 s; content3 de 79 MB | dato recibido, no medido aquí |
| CI `mutate` | p50 3,4 min · p90 10,6 min · máx 17,5 min | M |
| CI `linux` | p50 1,6 min · p90 1,9 min | M |
| Ejecuciones de CI por PR / PR que fallan la primera vez | media 2,4 / 15 de 28 | M |
| Ejecuciones en el fork desde 2026-10-29 | 118 `pull_request` y 58 `push` | M-rev |
| Minutos de runner al día | unos 600 (mutate 439, de ellos 104 en push a main); unos 18 000 al mes, repositorio privado | M |
| `task test` local | 41,8 s, 1605 tests | M |
| `task ci` local | unos 52 s | M |

---

## 3. Límites de escala

| Recurso | Dónde se rompe | Tipo | Nota |
|---|---|---|---|
| Puerta crítica de `generate` (todo o nada) | Con Catastro: r150 pasa; **r200 y r300 fallan** con 4 y 5 críticos | log | Es el límite que manda hoy. Sin Catastro, r300 pasa con 1 aviso (`collider_not_closed`) y llega a export [log] |
| Triángulos en Quest (unos 250k, jugadores incluidos) | **ya está lleno con el tamaño actual** (247 627 en Unity según ADR-005, antes de las tiendas interiores) | ADR-005 | Los 227k a r200 son triángulos visuales del GLB, solo OSM, sin prefabs ni jugadores. El límite llega antes de r200 |
| Draws por vista (unos 150) | cerca de r≈200–300 | E | Por mapa se miden 366 a r200, solo OSM |
| Separación de nodos de navegación | por encima de r≈150 supera 15 m; 34,7 m a r300 y 55,8 m a r500 | E (fórmula sobre áreas medidas) | Solo la profundidad de 400 es límite del SDK y del casco (docs/11). `NavNodeBudget = 320` es una heurística del proyecto (ADR-015) y se puede ajustar |
| Lightmap | A 10 t/m (por defecto): r150 2–3 páginas, r200 5, r300 10, r500 25; a r500 aparece el primer renderer recortado | M (estimador) | Con 40 t/m (máximo), r150 ocupa 21 páginas y r300 ocuparía unas 84 [E] |
| Catastro con una sola petición | **r ≤ 450 m**; por encima se desactiva sin avisar (el lado es 2·(r+30+20) ≤ 1000 m) | E (`cadastre/acquire.py:136-144`) | Todas las cifras de r500 son solo OSM |
| Respuesta de Overpass de 32 MiB | unos r≈880 m con la basura de relaciones de ruta; unos 1100 m sin ella | E (ajuste a r300 y r500) | El `[timeout:90]` del servidor puede llegar antes; no se ha medido |
| Fiabilidad de Overpass | ya hoy: 2 de 4 intentos fallaron (504 y 502) | M | El endpoint está fijado a overpass-api.de: no hay mirror, ni failover, ni reutilización del extracto por bbox |
| Cuadrado de inclusión de OSM | `MAX_SIDE_M = 4100`, es decir, r ≤ unos 2000 m | E (código) | Tope duro |
| `layout` y `signs` (crecen más que el área) | 20,4 s y 15 s a r500; minutos a r1500 | M / E | En `layout`, al menos el 40 % es regeneración de la fase 03. En `signs` entra codificación PNG |
| Presupuesto de `detail` (600k visuales, 120k de colisión) | solo se supera a r500, y únicamente como aviso | M | |
| Disco | 737 MB por mapa a r500 en una instantánea; crece con cada `run` | M / E | Los paquetes de export nunca se sobrescriben (`next_package` llega a `pkg_99`) |
| Memoria y tiempo de Python | no es un límite: 1,2 GiB, 121 s y un solo núcleo a r500 | M | El paralelismo entre mapas no se ha evaluado |

**Contradicción resuelta.** El estudio de escalado dice que Python no es el cuello de botella. Los estudios de perfil y de arquitectura dedican casi todo a Python. No se excluyen:
- Para el **tamaño máximo** mandan la robustez de la geometría con Catastro (r200 ya falla) y el presupuesto de triángulos de Quest, que ya está lleno.
- Para la **velocidad de iteración** en mapas pequeños y medianos manda el coste fijo de Python: re-hornear y codificar PNG, las previsualizaciones y las 4 regeneraciones de la fase 03.

---

## 4. Plan priorizado

### 4.1 Iniciativas, ordenadas por ganancia frente a esfuerzo

| id | Título | Área | Ganancia esperada | Esfuerzo | Riesgo | Depende de |
|---|---|---|---|---|---|---|
| P7 | Caché de teselas por contenido (`~/.cache/onward-generator/tiles`), con clave sha del asset más parámetros de horneado | Python | Quita unos 12 s de `materials`, con bytes idénticos [E] | S–M | Bajo, si la clave incluye `BAKE_VERSION`, la versión de Pillow y el nivel | — |
| P1a | Codificación PNG en `ThreadPoolExecutor` a nivel 9 (4–6 hilos, orden de salida ordenado) | Python | Entrada real: de 10,1 a 4,6 s con bytes idénticos [M-rev]. Con P7 y la caché caliente, `materials` baja de unos 13 s a 1 s o menos [M-rev/E] | S | Muy bajo: los hashes no cambian | — |
| P2 | `run` pasa `--no-preview` (el flag ya existe en `generate` y `detail`, pero `next_step` no lo pasa) y se porta el splat vectorizado | Python | detail −3,1 s, generate −0,9 s [M]; miniatura de 1,21 a 0,68 s, idéntica byte a byte [M]; en total, unos −4 s | S | Cambio de comportamiento documentado (docs/01, 06, 07). La miniatura se queda | — |
| P8 | Fase 03 en caché en memoria dentro de un mismo `run` (lru por sha de entradas): una regeneración en vez de 4 | Python | unos −3 s a r150 y unos −24 s a r500 (3 × 8,1 s de 121 s) [log/E] | M | Medio: hay que conservar la prueba de `load_base`. No sobrevive entre invocaciones separadas de la CLI | — |
| P11 | Cuarentena por objeto y losas de cruce por celda. **Empieza por diagnosticar** los 4–5 críticos de r200 y r300 con Catastro | Python | Quita el límite que manda hoy: r200–450 con Catastro llegarían a export [E] | M | Puede dejar huecos: informar del área en cuarentena y fijar un umbral. Cambia docs/06 | — |
| U1 | Reunir los `seconds.scene` y `seconds.bake` que ya existen, añadir tiempos por fase y comprobar si el bake se hizo en GPU o cayó a CPU (`ProgressiveGPU` necesita OpenCL) | Unity | 0 s por sí solo, pero ordena todo lo demás de Unity | S | Muy bajo | — |
| P1b | PNG a nivel 3 y en RGB (el alfa vale siempre 255), en su propio PR que actualiza los hashes | Python | Unos −3,5 s más sobre P1a [M-rev]. Nivel 3 frente a 9: 2,9 veces más rápido y +4 % de tamaño [M-rev]. RGB frente a RGBA: −37 % de tiempo y −12 % de tamaño [M-rev] | S | Bajo: cambian los hashes de PNG una vez. Unity recomprime a ASTC | P1a |
| P3 | Spawn: `shapely.prepare(area)` antes de `_fits` (`layout.py:120`) o `contains_xy`, con corte en el primer hueco válido | Python | Menor de lo previsto: `layout` no puede bajar del tiempo de regeneración (1,2–2,0 s a r150 [log]) sin P8; unos −2 s a r150 [E]. Volver a medir | S | Bajo, si se conserva el desempate (dist, y, x) | P8 |
| P4 | `STRtree` de muros en `signs/place.py` (el bucle de `_sign_for` es O(cruces × muros), `place.py:358`) | Python | Explica los 15 s a r500 [M]. La cifra de r150 se solapa con P1 por la codificación del atlas y no se suma | S | Bajo, con un test de igualdad | Confirmar con un perfil después de P1 |
| P5 | Overpass: relaciones solo `type=multipolygon` (`assemble` descarta los demás tipos, `osm_import.py:388-393`) | Python | Extracto 6 veces menor y `geo_scene` idéntico [M]; límite de 32 MiB de unos 880 a más de 1100 m [E]. El valor está en el tamaño y la robustez, no en la latencia | S | Medio-bajo: cambian `osm_relations` y los avisos `unsupported_relation` de `geo_report`, y la procedencia. Documentarlo | — |
| D4a | `poll_interval=0.05` en el único servidor falso (`fake_ollama`, `tests/unit/test_ai.py:430`, 15 tests) | Dev-loop | −4,4 s por suite (11 %) [M base] | S | Bajo (solo tests) | — |
| D3 **o** D2 (no los dos) | D3: quitar `mutate` del push a main y pasarlo a una ejecución nocturna. D2: no repetir la CI tras el rebase de `land.sh` (caché `mutate-ok-<patch-id>`, `gh run watch`) | Dev-loop | D3: −104 min de runner al día (17 %) [M]. D2: 3,4 min (p50) a 10 min (p90) menos por aterrizaje [E] | S | Hoy el `mutate` del push (`BASE=github.event.before`, `ci.yml:53`) es la única comprobación del commit rebasado. Con ambos, solo lo cubre la ejecución nocturna. Cambia una puerta: PR propio | — |
| U7 | Corregir el límite de 8 lightmaps en `EnsureLightmapsAssigned` | Unity | Robustez: solo afecta a la ruta de reserva, cuando `LightmapSettings.lightmaps` sale vacío tras `OpenScene` en `PrepareHuntPatrol` (`OnwardMapAdapter.cs:848-871`). Esa ruta también ignora `lightmapDir` en modo direccional | S | Bajo | — |
| P6 | Hoja de contactos `atlas.png` a nivel 1–3 o solo bajo petición | Python | unos 2 s por ejecución y unos 5 MB por mapa [E] | S | Bajo | — |
| D1 | `task mutate BASE=fork/main JOBS=6` antes de `gh pr create` | Dev-loop | −0,7 ciclos de CI por PR; 5–8 min por PR y unos 120 min de runner al día [E] | S | Medio: pasa el coste a la máquina compartida de 20 núcleos, que varios agentes con `JOBS=6` saturan | — |
| U2 | Presupuesto de páginas de lightmap (`lightmap_pages_max`) aplicado en Python | Unity | unas 3 veces menos texels y un bake entre 2,5 y 3 veces más corto; lightmaps de content3 de unos 28 a unos 9 MB [E]. **Solo para mapas a 40 t/m o desde r300** | S | Medio en calidad: sombras más blandas. Hay que verlo en el casco | U1 recomendable |
| U3 | Perfil de bake `draft`/`final` (muestras, rebotes, OIDN por canal) | Unity | 40–55 % menos de bake en iteración [E] | S | Bajo-medio: se sale del rango de la guía de Onward | U1 |
| U4 | Escena y launcher en un solo lanzamiento de Unity; `cmp -s` antes de copiar | Unity | 20–60 s por mapa [E] | S | Bajo | — |
| U5 | `task unity:prune KEEP=3` (opcional, pide confirmación) | Unity | Mantiene constante el arranque del editor y el refresco [E] | S | Bajo. Es destructivo en un proyecto externo: confirmar | — |
| D4b | `pytest-xdist` solo en `task test` | Dev-loop | unos 25 s en local y 15–20 s en CI [E] | S | Bajo. Cambia una puerta: PR propio | — |
| D10 | Recortar la preparación de CI (imagen en ghcr, fetch superficial) | Dev-loop | 10–20 s por job [E] | S | Bajo. Cambia una puerta | — |
| P9 | Catastro en paralelo con Overpass | Python | 8–20 s a r150–r300 por mapa español nuevo [log]; 0 s en reejecuciones | M | Medio: el bbox no puede ser idéntico (hoy sale de los edificios OSM de `geo` más 20 m, `acquire.py:108-131`). Habría que usar el cuadrado de `map.json`, lo que cambia la petición y quizá los edificios, y elimina la guarda "scene has no buildings" | — |
| U6 | Reimportaciones ASTC en lote y albedo ASTC 6x6 | Unity | 20–60 s [E]; content3 de 79 a unos 35 MB [E] | M | Bajo en el lote; medio en el albedo (casco) | U1 |
| D7 | Caché de `mutmut-stats.json` y tiempos por fase en `run.py` | Dev-loop | 60–90 s por ejecución de `mutate` [E] | M | Medio: un mapa de tests viejo. Tratar "no tests" como fallo | — |
| D5 | Runner autoalojado para `mutate` (6 CPU) | Dev-loop | PR #54 de 13 a unos 6,5 min; `mutate` mediano de unos 4,7 a unos 3 min; saca unos 440 min al día de la cuota [E] | M | Medio: compite por CPU con los agentes | — |
| D6 | Repartir la fase de ejecución de `mutate` en una matriz de N jobs | Dev-loop | PR grandes −5 a 6 min; mediana −1 a 2 min [E] | M | Medio: llevar artefactos entre jobs. Cambia una puerta | Mejor después de D5 |
| P10 | Frescura por hash de contenido (`.stage.json`) con corte temprano | Python | Reejecución sin cambios en unos 0,1 s [E]; detecta cambios de config o código | M | Medio: hash incompleto. Cambia el contrato de docs/01 y necesita ADR | — |
| U10 | Unity sin cabeza en contenedor (GameCI) | Unity | Más mapas en paralelo, no más velocidad por mapa; CPU entre 2 y 5 veces más lenta que la GPU del Mac [E] | M | Medio: licencia, SDK privado, instalación con aprobación | U1 |
| P12 | Catastro WFS en teselas (≤1 km²) | Python | Edificios de Catastro hasta r1500 (unas 4 veces más edificios en Madrid centro) [E] | M | Medio: deduplicar por `localId` | P11 |
| P15 | Texturas intermedias en un almacén por contenido o con enlaces duros | Python | Disco de 284 a unos 130 MB por mapa a r150 [E]; menos de 1 s | M | Toca el contrato de los GLB intermedios. Export sigue embebido | — |
| P18 | Navmesh limitado al núcleo jugable | Unity | Separación ≤15 m con núcleo r≈150 [E] | M | Medio | P16 |
| D9 | Tren de merges (`land_train`) | Dev-loop | unos 3 min por PR con k=3 [E] | M | Medio. **Cambia la política de merge: requiere acuerdo explícito del usuario** | D2 |
| P13 | `detail` por edificio en `ProcessPoolExecutor` y presupuestos en secuencia | Python | r150 de 5,2 a unos 1,5 s; r400 de unos 35 a unos 8 s [E] | L | Medio: forkserver, pickling, bytes idénticos | P1–P4 |
| P16 | Núcleo jugable con capa escénica de baja carga (HLOD por niveles) | Python + Unity | r≥300 dentro de unos 250k triángulos y unos 150 draws por vista; la capa exterior cuesta 10–20k triángulos [E, ADR-014] | L | Medio: medir en el casco antes de fijar presupuestos (ADR-005). El presupuesto ya está lleno hoy | P11 |
| U8 | Reutilizar el bake cuando solo cambia el gameplay | Unity | Más del 80 % del tiempo de Unity en esas iteraciones (de 10+ a 1–2 min) [E] | L | Medio-alto: copiar el LightingData | U1 |
| D8 | Regla: el comportamiento nuevo va en helpers, no dentro de las funciones grandes | Dev-loop | Mutantes entre 3 y 5 veces más baratos; −3 a 6 min en PR de `detail` [E] | L (gradual) | Medio. Solo cuando ya se toca esa función | — |
| P14 | Vectorizar fachadas (`facades.py`) | Python | 1–1,5 s a r150 [E] | L | Medio: redondeo de flotantes | P2 |
| U9 | UV2 en Python (planar o xatlas), en caché | Unity | 30–120 s por mapa [E], sin confirmar | L | Medio: dependencia nueva y contrato `TEXCOORD_1` | U1 muestra que `uv2` > 10 % |
| P17 | Límites (props, sondas) por área del núcleo | Python | Densidad visual: ahora cae unas 27 veces de r100 a r500 [M] | S | Choca con los presupuestos si va antes de P16 | P16 |

### 4.2 Contradicciones y duplicados resueltos

- **Nivel PNG y orden de P1 y P7.** El cálculo de 7,6 veces no se reproduce con la entrada real de `materials`: allí el nivel 3 es 2,9 veces más rápido, con +4 % de tamaño [M-rev], y en teselas procedurales 2,4 veces [M-rev]. El orden queda así:
  1. P7 y P1a primero, porque no cambian los bytes.
  2. P1b después, porque cambia los hashes. P1b pasa al nivel 3 y a RGB.

  Unity recomprime a ASTC, así que el tamaño no llega al Quest.
- **La caché de teselas ya no depende de P1.** `materials` es un coste fijo porque re-hornea los mismos JPEG en cada ejecución (`materials/apply.py:138-140`, `MAX_PHOTO_PX=1024`). Su esfuerzo es S–M porque la clave necesita versionado.
- **P3 y P8 contaban lo mismo.** La etapa `layout` (`perf layout --playable`) llama a `load_base`, que regenera la fase 03 completa (`perf/__main__.py:90`). La regeneración se asigna a P8. P3 solo cuenta la búsqueda de spawn y `scatter_points`.
- **P4 y P1 se solapaban.** `signs` también codifica páginas de atlas de 2048² a nivel 9 (`signs/shop.py:32-33,244`). La cifra de P4 a r150 no se suma a la de P1.
- **Ganancia de Overpass.** El estudio de escalado sugiere menos espera. El de perfil mide que la respuesta sigue tardando 36 s con 6 veces menos datos. **Conclusión:** el valor está en el tamaño, la robustez y el límite de 32 MiB, no en la latencia.
- **Paralelismo entre etapas.** El estudio de arquitectura dice que no compensa (1–2 s). **Solo se acepta P9**, que solapa red con red y ahorra entre 8 y 20 s. Su coste es cambiar el bbox de Catastro al cuadrado de `map.json`. No se hace un DAG general de etapas.
- **Densidad de lightmap.** La densidad por defecto es 10 t/m (`_TEXELS_DEFAULT`) y el máximo es 40 (`_TEXELS_MAX`). El mapa de 21 páginas se creó con el máximo. U2 solo importa con densidad alta o a partir de r300, y sale del top 10.
- **Total esperado.** La cifra de 40 → 15 s contaba dos veces lo mismo, y su base mezclaba una ejecución perfilada (57 s) con otra sin perfilar (40 s). La estimación corregida es:
  - P1 y P2 llevan r150 a unos 24 s.
  - Con P3, unos 22 s.
  - Para llegar a 15 s hacen falta además P8 y P4 [E].

  La cifra del perfil (27 → 11 s, Madrid, reejecución) tiene otra base.
- **`detail` a escala.** El estudio de arquitectura estima unos 35 s a r400 y el de escalado mide 34,5 s a r500. El orden de magnitud coincide, pero las dos cifras son solo OSM.
- **Límite de Quest.** El "r≈200" comparaba triángulos visuales del GLB con un presupuesto que incluye prefabs del SDK y jugadores. Según ADR-005, el presupuesto ya está lleno con el tamaño actual.

### 4.3 Olas

**Ola 1, esta semana (orden del top 10 corregido):**
1. P7 y P1a (bytes idénticos).
2. P2.
3. P8.
4. P11 como diagnóstico de los críticos de r200 y r300 con Catastro.
5. U1.
6. P1b, en un PR que actualiza los hashes.
7. P3, después de P8 y volviendo a medir.
8. P4, después de confirmar con un perfil.
9. P5.
10. D4a, y D3 **o** D2, cada uno en su propio PR de puertas (`task guard`).

Además, U7 como corrección pequeña de robustez y P6.

**Ola 2, la siguiente:**
- Python: P11 completo (desbloquea r200–450 con Catastro) y P9.
- Unity: U2 (solo mapas de densidad alta o desde r300), U3, U4, U5 (con confirmación) y U6, priorizados según lo que muestre U1.
- Ciclo de desarrollo:
  - D7.
  - D5 o D6; D6 solo si D5 no basta.
  - D4b y D10.
  - D1, solo si no satura la máquina compartida.
- Volver a medir a r300, r500 y r1000, **con Catastro** donde sea posible, antes de pasar a la ola 3.

**Ola 3, arquitectura:**
- P10 (ADR), P16 con P18 y P17, P12, P13, P15 y P14.
- U8, U9 si U1 lo justifica, y U10 con aprobación de instalación.
- D8 de forma gradual; D9 solo con acuerdo del usuario.

---

## 5. Qué NO hacer y por qué

- **No partir las etapas en procesos o contenedores, ni montar un DAG entre etapas.** Arranque e imports cuestan unos 1,3 s por ejecución [M], y la cadena es estrictamente secuencial. La ganancia máxima es de 1–2 s [E]. El paralelismo va dentro de las etapas (P1a, P13) y en las cachés (P7, P8). `run` ejecuta todas las etapas en el mismo proceso, y P8 se apoya en eso.
- **No sustituir el GLB intermedio por otro formato.** Leer y reescribir cuesta 0,25 s [M]. El problema es el disco, y P15 lo resuelve.
- **No repartir el bake de un mapa entre procesos.** Requiere API no pública o scripts en tiempo de ejecución que Onward probablemente no admite (AGENTS.md: no inventar compatibilidad con el SDK). Se paraleliza entre mapas.
- **No subir la profundidad de navegación de 400 ni los 6 intentos.** Son límites del SDK y del casco (docs/11). `NavNodeBudget = 320` es una heurística del proyecto y sí se puede ajustar.
- **No escalar los límites de props y sondas con el área antes de HLOD (P16).** Choca con los presupuestos de triángulos y draws, y el de triángulos ya está lleno.
- **No quitar la miniatura de export.** Es obligatoria en el paquete. Solo se sacan de `run` las previsualizaciones de diagnóstico.
- **No inventar edificios en la capa escénica.** Deben ser huellas OSM reales.
- **No quitar `mutate_only_covered_lines` para ahorrar 50–80 s.** Cambia lo estricta que es la puerta. Requiere su propio PR y una discusión.
- **No fijar presupuestos de Quest nuevos sin medirlos en el casco** (regla de ADR-005).
- **No meter el SDK privado en una imagen ni en artefactos de CI** (U10).
- **No mezclar cambios de `src/` y de puertas en el mismo PR** (`task guard`). D2, D3, D4b, D5, D6, D7 y D10 tocan puertas.
- **No aplicar D2 y D3 a la vez.** Juntos dejan el commit rebasado sin mutation testing hasta la ejecución nocturna.
- **No cambiar el nivel PNG en el mismo PR que los hilos.** P1a conserva los bytes y P1b los cambia a propósito.
- **No hacer push directo a main con el tren de merges (D9) sin el acuerdo explícito del usuario.** Cambia la política de merge rebase tras CI verde.

---

## 6. Cómo medir el éxito

| Métrica | Base | Objetivo (de los estudios y la revisión) | Cómo se mide |
|---|---|---|---|
| Python r150 sin red | 40,2 s [M] | unos 24 s con P1 y P2; unos 15 s con P8 y P4 además [E] | `onward-generator run --no-unity --no-catastro`, con el tiempo por etapa del resumen de `run` |
| `materials` | 13,7–14,6 s [M] | 1 s o menos con P7 y P1a y la caché caliente [M-rev/E] | ídem, más `python -m cProfile` sobre la etapa (`ImagingEncoder.encode`) |
| `detail` y `generate` en `run` | 5,4 / 1,2 s [M] | −3,1 / −0,9 s [M con `--no-preview`] | ídem |
| Regeneraciones de la fase 03 por `run` | 4 [E, confirmado] | 1 | contador o perfil de `generate_scene` |
| `layout` y `signs` a r500 | 20,4 / 15,0 s [M] | crecimiento lineal o menor; `layout` sin regeneración | barrido r100 / r200 / r300 / r500 con `run --no-unity` |
| Extracto de Overpass r150 | 3,63 MB [M] | unos 0,60 MB y el mismo `geo_scene` [M]; cambio de `geo_report` documentado | tamaño de `.osm` y `geo_report` (`kept` / `dropped_outside` / `osm_relations`) |
| Mapas que pasan la puerta de `generate` con Catastro | hasta r150 [log] | r200–450 | barrido por radios; contar críticos y cuarentenas en el informe |
| Triángulos y draws | 247 627 en Unity (ADR-005); 441k / 684 a r300 solo OSM [M] | unos 250k / unos 150 por vista | `perf measure` y, después, el casco |
| Páginas de lightmap | 21 (r150, 40 t/m); 2–3 (r150, 10 t/m) | ≤ `lightmap_pages_max` (p. ej. 6–8) | `lightmap_estimate.pages_at_least` y `scene_report` |
| Tiempo de Unity por fase | solo `seconds.scene` y `seconds.bake`, sin reunir | todas las fases visibles y el backend del bake identificado | `scene_report.json` (U1); log de `run_export.sh` |
| Disco por mapa | 284 MB a r150 [M], una sola instantánea | unos 130 MB [E] | `du -sh maps/<id>/data`, tras varias iteraciones |
| `mutate` en CI | p50 3,4 / p90 10,6 min [M] | PR #54: unos 6,5–8 min [E] | `gh run list` y duración de los jobs |
| Ejecuciones de CI por PR / PR que fallan la primera vez | 2,4 / 15 de 28 [M] | cerca de 1 / cerca de 0 | `gh run list --json`, agrupado por rama |
| Minutos de runner al día | unos 600 [M], unos 18 000 al mes | −104 (D3) − unos 120 (D1) [E]; contrastar con la facturación | suma de duraciones de job |
| Hueco entre merges | p50 8 min [M] | 2,4–4 min (15–25 por hora) [E] | marcas de tiempo de merge de los PR |
| `task test` | 41,8 s [M] | unos 37 s (D4a); 13–15 s con xdist [E] | `task test` |

**Comandos de referencia:**
- `onward-generator run --no-unity`
- `python -m cProfile -o out.prof -m onward_generator <etapa> ...`
- `task ci`
- `task mutate BASE=fork/main JOBS=6`
- `task test`
- `gh run list`

Los scripts de los micro-benchmarks de la revisión están en `/tmp/claude-1000/-home-aledbf-Trabajo-github-Onward-generator/a5a9246a-0e8a-4bbf-ae2e-80d780a9e4e1/scratchpad/bench/png.py` y `/tmp/claude-1000/-home-aledbf-Trabajo-github-Onward-generator/a5a9246a-0e8a-4bbf-ae2e-80d780a9e4e1/scratchpad/bench/photo.py`.

**Regla de verificación:** cada PR de rendimiento incluye un test de que la salida en paralelo o la nueva es **idéntica byte a byte** a la serie o a la anterior. P7, P1a y P8 deben cumplirlo. Si los bytes cambian a propósito (P1b, P5), el PR lo dice y actualiza los hashes y la documentación.

---

## 7. Pendiente de medir

- **Críticos de `walkable_crossing`:** ningún log respalda que r300 sin Catastro tenga un crítico en la losa global `walkable_crossing`. En `r300nc.log`, `generate` pasa con un aviso. Hay que reproducirlo antes de usarlo como objetivo de P11.
- **Costes con Catastro a partir de r200:** triángulos, draws, `detail`, `signs` y `layout`. No se pueden medir mientras `generate` falle. Es el barrido que toca después de P11.
- **`panoramax`:** nunca se ha medido. Depende de la red (hasta 20 × 100 páginas de metadatos y 4 hilos de descarga).
- **Planificador de IA (Ollama):** es una modalidad opcional y no tiene medición.
- **Tiempos reales de Unity:** no se han leído los `seconds.bake` de los `scene_report.json` existentes. La cifra de 5–15 min no está contrastada, y tampoco se sabe si el bake usó GPU (OpenCL) o cayó a CPU en Linux.
- **Rendimiento en el casco:** tiempo de frame, GPU y memoria de texturas frente al 1 GB de la guía, con la geometría actual. Las cifras de ADR-005 son anteriores a las tiendas interiores.
- **Subida al Quest:** no se ha medido cuánto tarda `step_push` (copia de seguridad del content3 anterior de 79 MB, subida y md5). `device_backup/` crece con cada subida.
- **Disco por iteración:** cuánto crece realmente con cada `run` (paquetes hasta `pkg_99`) y en el proyecto de Unity.
- **Overpass:**
  - Si el `[timeout:90]` del servidor llega antes que el límite de 32 MiB en radios grandes.
  - Su fiabilidad (2 de 4 fallos): no hay iniciativa que la cubra. Usar un mirror requiere una decisión de política.
- **Paralelismo entre mapas:** no se ha evaluado en la máquina de 20 núcleos (Python usa un núcleo y 1,2 GiB a r500).
- **Ganancias reales de P3 y P4:**
  - P3 hay que medirlo después de P8. `scatter_points` no se ha perfilado.
  - P4 necesita confirmar con un perfil, después de P1, que el bucle de muros domina.
- **Facturación de minutos de runner:** no se pudo consultar, así que no se sabe si ya hay excesos.
# Plan: fachadas por tipología, detalle sin draws y presupuesto de triángulos

31 de octubre de 2026. Continúa el [informe de realismo](2026-10-30-realism-sources.md), cuyas oportunidades #4 (atlas *trim sheet* por estilo) y #5 (suciedad horneada) ya están en [ADR-020](../decisions/ADR-020-trim-sheet-facades.md). Este plan cubre lo que falta: **qué tipo de edificio es cada uno**, **cómo dar más detalle sin más materiales** y **cómo escalar sin pasar el presupuesto de triángulos** (unos 250 000 con jugadores, [ADR-005](../decisions/ADR-005-quest-performance.md)).

## 0. Resumen

1. **Lo que ya hacemos como la industria:** *trim sheet* con suciedad y AO horneados (ADR-020), gramática de fachada por plantilla (`detail/grammar.py`, como CityEngine CGA), estilo regional paramétrico en vez de fotos por edificio (la lección de Flight Simulator / Blackshark), HLOD en tres niveles (núcleo, anillo `scenic`, horizonte: ADR-017 y ADR-014) y combinación por celda y material (ADR-005).
2. **La carencia mayor es la tipología.** Hoy el tipo es una de cinco plantillas más un *uso* que solo elige paleta. Un bloque del XIX, uno de ladrillo de los sesenta y una casa adosada salen con la misma gramática y solo cambia el color.
3. **El albedo no está comprimido.** El adaptador carga cada imagen como `RGBA32` (`OnwardMapAdapter.cs`, `LoadPng`); solo los lightmaps pasan a ASTC. El atlas de 2048 × 4096 ocupa 42,7 MiB; en ASTC 6 × 6, unos 4,7. Sin arreglar esto no caben varios atlas por mapa.
4. **El triángulo caro es el relieve de los huecos.** ADR-004 midió que las jambas (`frame_stone`) eran el 45 % de los triángulos visuales. El jugador va a pie de calle: el relieve de un cuarto piso apenas se ve.

## 1. Qué hacen los demás

| Eje | Técnica | Quién | ¿Aplica aquí? |
|---|---|---|---|
| Tipo | Tipologías por **época** y **morfología** (TABULA/EPISCOPE, EUBUCCO, catastros) | Estudios energéticos y de *building stock* europeos | Sí. Catastro INSPIRE trae `dateOfConstruction` (comprobado en Murcia: `beginning` 1976); OSM tiene `start_date`. La morfología sale de la huella, en cualquier ciudad |
| Tipo | La tipología elige la **regla** de la gramática y la regla reparte (split, repeat, comp) | CityEngine CGA, Müller et al. 2006 | Sí: `detail/grammar.py` ya va en esa línea; falta el selector |
| Tipo | Clasificador por IA desde imagen | Blackshark (Flight Simulator) | No: propietario, no determinista. Una IA local solo como clasificador opcional y validado (ADR-006) |
| Detalle | **Atlas por crujía y planta**: cada celda es una ventana con su trozo de muro, la jamba y la sombra pintadas | Autogen de Flight Simulator, OSM2World | Sí: es lo que permite quitar el relieve sin perder la ventana |
| Detalle | *Interior mapping* con parallax | Spider-Man, Cities: Skylines 2 | No con `PP_Opaque_Simple_Lit`. Sí una celda con un interior oscuro pintado (cortina, lámpara) a coste cero |
| Detalle | Decals de suciedad | Casi todos los AAA | No: más draws y transparencia; la suciedad ya va en el atlas |
| Memoria | ASTC en todas las texturas | Guía de Onward (apéndice C), Meta | Sí, y es un cambio pequeño |
| Triángulos | **Detalle por exposición**: relieve real solo donde el jugador está cerca | Juegos de mundo abierto, kits modulares | Sí: plantas bajas con relieve, superiores planas |
| Triángulos | Presupuesto por edificio según su visibilidad (reparto tipo mochila) | Streaming de mundos abiertos | Sí, con el mismo patrón que los presupuestos de interiores (`max_triangles`) |
| Triángulos | `LODGroup` de Unity | Unity | No consta que el SDK de Onward lo admita en un mapa; no contar con ello |
| Triángulos | Impostores para lo lejano | Unity Pixyz, HLOD | Ya en el horizonte (ADR-014) |

## 2. Incrementos

Cada uno es un PR propio, con su ADR cuando cambia una decisión. Los tres primeros son independientes y van en paralelo; el cuarto depende de ellos.

| # | Incremento | Dónde | ADR | Medida que lo valida |
|---|---|---|---|---|
| 1 | **ASTC 6 × 6 para el albedo**, con opción `albedo_compression` (`astc_6x6` por defecto, `none` para volver atrás) y `maxTextureSize` ≥ 4096 para no reducir el atlas. Estimación de memoria ASTC junto a la `RGBA32` en los informes | Adaptador Unity y `export` | Enmienda a ADR-020 | Memoria de texturas en OVR Metrics; costuras del atlas a distancia en el casco |
| 2 | **Registrar tipología y época**, sin tocar geometría: época observada (OSM `start_date`, plugin Catastro `dateOfConstruction`), morfología de la huella (área, compacidad, alargamiento, proporción de medianera, alturas) y una clase por reglas: `landmark`, `civic`, `industrial_shed`, `historic_core`, `closed_block`, `open_block`, `tower`, `row_house`, `detached_house`, `other`. La tipología es `estimated`; la época conserva su propio origen. Las partes de una parcela comparten tipología | `cadastre split`, `detail` | ADR-024 | Distribución en Murcia y en una ciudad fuera de España; GLB idéntico antes y después |
| 3 | **Huecos planos desde `relief_storeys`** (2 por defecto): sin rebaje, jambas ni alféizar; el hueco es un polígono coplanario de 2 triángulos que mapea a su celda del atlas. Puertas, escaparates y ventanas con interior siguen con relieve. El colisionador no cambia | `detail` (geometría de huecos) | ADR-025 | Triángulos totales y de `frame_stone` con `perf measure` en Murcia y Madrid; los draws no suben |
| 4 | **La tipología elige gramática y franjas**: proporción de hueco, ritmo de crujía, balcones, bajo, tejado, paleta y celdas del atlas por tipología y época (por ejemplo `mediterranean` con franjas de casco histórico, desarrollismo y moderno). Y un **presupuesto por visibilidad**: cada fachada puntúa por distancia a las rutas y spawns de `layout` y por frente a calle; el total de triángulos del núcleo se reparte con ese orden y queda acotado con cualquier radio | `detail`, `materials` | ADR-027 | Triángulos del núcleo fijos al crecer el radio; revisión visual en el casco por tipología |

Después de 4, si hace falta: un **nivel intermedio** entre núcleo y anillo (cubos con la celda del atlas, unos 50 a 150 triángulos, frente a 44 de una caja `scenic` y unos 1 180 de un edificio detallado), y **celdas de ventana con interior pintado**.

## 3. Reglas que se mantienen

- Genérico mundial: la morfología y OSM son el núcleo; Catastro es un plugin con prioridad declarada en `config/sources.json`.
- Procedencia por campo: la época es `observed` cuando viene de un dato; la tipología, el reparto del presupuesto y el aspecto son `estimated` o `generated`. Las fachadas siguen siendo `generic_template` con `representative_of_real_facade: false`.
- Determinismo: toda elección por `sha256`, nunca un generador con estado.
- Sin IA generativa de texturas (sección 5 del informe de realismo).
- Nada se valida en el Editor: memoria, costuras, lectura de las plantas planas y tiempo de frame se miden en el casco (`docs/09-perf.md`).

## 4. Pendiente en el casco

- ASTC: que el atlas no se reduzca, las costuras de las franjas a distancia, la imposta y las celdas de ventana de cerca, la memoria.
- Huecos planos: si la fachada superior se lee plana a la altura de los ojos y si hay parpadeo en el borde entre hueco y muro.

Cada punto se pide al usuario como un issue `needs-user` cuando su PR esté fusionado.

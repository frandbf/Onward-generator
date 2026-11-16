# Plan: ambientes de calle (no solo ciudades abandonadas o en guerra)

Fecha: 16 de noviembre de 2026. Lo pidió el mantenedor tras generar Calle Sierpes (Sevilla), una calle comercial viva. `overgrow` vestía esa calle como una ciudad abandonada: enredaderas, restos de coches, barreras de hormigón, contenedores y coches destrozados. El modelo tampoco tenía cómo pedir otra cosa.

## Diagnóstico

- **Años y vida, mezclados.** `overgrow` mezcla cuánto tiempo lleva abandonada la calle (enredaderas, árboles silvestres, restos) con qué vida tiene (qué objetos y en qué estado). El perfil de años más bajo es de 10, y no hay una calle en uso.
- **Restos procedurales.** Salen siempre que el perfil tenga restos.
- **Estado de los objetos.** El `props_profile` del perfil de calle solo ordena los objetos por estado; no aparta los destrozados.
- **Biblioteca de exterior sesgada.** De unos 400 objetos de calle, 148 son barreras y 95 basura, y solo 9 son mobiliario. Las mesas y sillas están marcadas como mobiliario de interior.
- **Calcomanías de pared.** Hollín, impactos y musgo salen en cualquier mapa.

## La idea

`config/street_moods.json` (ADR-044) define ambientes como datos. Cada ambiente:
- escala las enredaderas, los árboles que `overgrow` planta donde OSM no tiene ninguno y los restos de coches;
- deja solo los objetos de ciertos estados;
- escala los topes por categoría de objeto;
- quita tipos de calcomanías de pared.

`map.json` `"street_mood"` elige uno. Sin la clave se usa `abandoned`, que es la identidad: un mapa sin la clave sale igual que antes. Los árboles que mapea OSM no se quitan nunca.

| Ambiente | Crecimiento y restos | Objetos | Topes | Paredes |
|---|---|---|---|---|
| `lived` (calle en uso) | ninguno | solo `intact` | mobiliario ×2, vegetación ×1,5; barreras ×0,15, basura y vehículos ×0,25, estructuras ×0,5 | sin hollín, impactos, musgo, óxido, pintura levantada, grietas ni tiza |
| `emptied` (vaciada hace días) | sin crecimiento, restos ×0,3 | `intact`, `worn` | barreras y basura ×0,5 | sin hollín, impactos ni musgo |
| `abandoned` (por defecto) | por años, como siempre | todos | como siempre | todas |

El daño de guerra (`map.json` `damage`) sigue aparte y añade sus propios tipos de calcomanía.

## Incrementos

1. **`street_mood` con `lived`, `emptied` y `abandoned`** en `overgrow` y `signs`, `run` pasando `--street-mood`, y la validación de la clave. Hecho (PR #122).
2. **Terrazas.** Una ubicación `terrace` delante de los comercios que registra `detail`, solo en calle peatonal o plaza y dejando 1,2 m de paso. Revisar qué mobiliario sirve para exterior y conseguir modelos CC0 de mesa, silla, sombrilla, macetero y caballete.
   Hecho el 16 de noviembre de 2026 (enmienda de ADR-044). El ambiente dice si pone terrazas (`"terraces"`, solo `lived`). Las reglas están en `config/terraces.json`: delante de los bares (`bar` cubre cafés, bares, pubs y restaurantes), solo en calle peatonal, calle residencial compartida o plaza, con 1,2 m de paso hasta el edificio de enfrente. Cada bar lleva hasta dos mesas con una silla a cada lado, a izquierda y derecha de la puerta, más pizarra y macetero, siguiendo la fachada en línea hasta 6 m de la puerta. No hizo falta descargar nada: los catálogos CC0 ya tenían mesa y silla Quaternius, pizarra Poly Haven y macetero Kenney. Las sombrillas quedan fuera; irían con los toldos del incremento 3, que también necesitan geometría de tela. En Sierpes salen 7 mesas delante de 5 de sus 9 bares.
3. **Toldos procedurales** sobre los escaparates, a partir de los datos de cada tienda: plano inclinado y faldón de tela, sin colisión.
4. **El modelo ofrece el ambiente.** Nueva versión del esquema del perfil de calle con un campo de ambiente, y migración de las respuestas guardadas para que no se invaliden en silencio.
5. **Coberturas por ambiente.** `perf cover` elige coberturas del ambiente (mesas, maceteros, quioscos) en vez de barreras en una calle en uso.

## Fuera de este plan

El problema de pasillo de Sierpes (una sola ruta entre los spawns) depende del área, no de `overgrow`: un mapa nuevo con radio de 150 m y núcleo de 125 m mete las calles paralelas.

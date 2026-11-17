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
   Hecho el 16 de noviembre de 2026 (segunda enmienda de ADR-044). El ambiente dice si cuelga toldos (`"awnings"`, solo `lived`). Las reglas están en `config/awnings.json`. El toldo se cuelga bajo el rótulo de fascia que `detail` pone sobre el escaparate y tiene su misma anchura. Sale 0,9 m de la pared y su borde más bajo queda a 2,2 m del suelo; si el rótulo está demasiado bajo, se omite. Lo llevan 7 de cada 10 tiendas con fascia, elegidas por hash. Tampoco se pone si taparía una placa de calle, una calcomanía u otro rótulo. Las terrazas suman una sombrilla por mesa (`parasol` en `config/terraces.json`). Toldos y sombrillas son geometría generada por código con una única textura de rayas también generada (rojo, verde o azul sobre crema), sin descargas y sin colisión. En Unity se iluminan con sondas en lugar de lightmap. En Sierpes salen 34 toldos de 61 fascias y 7 sombrillas.
4. **El modelo ofrece el ambiente.** Nueva versión del esquema del perfil de calle con un campo de ambiente, y migración de las respuestas guardadas para que no se invaliden en silencio.
   Hecho el 17 de noviembre de 2026 (tercera enmienda de ADR-044). El perfil pasa a la versión 0.2.0 con un sexto campo, `street_mood` (`map_default`, `lived`, `emptied`, `abandoned`). El id aplicado se convierte en `overgrow place --street-mood`, y `signs place` lo lee del registro cuando `map.json` no tiene `street_mood`. La clave de `map.json` siempre gana: el id queda omitido con motivo `user_setting`. Una respuesta 0.1.0 guardada no se vuelve a preguntar: si sus hashes coinciden con el esquema y el prompt 0.1.0 (guardados en el código) y con las mismas entradas, se reutiliza con `street_mood` `map_default` y el registro lo anota (`AI_PROFILE_MIGRATED`). Comprobado con la respuesta guardada de Calle Sierpes: se reutiliza sin llamar al modelo.
5. **Coberturas por ambiente.** `perf cover` elige coberturas del ambiente (mesas, maceteros, quioscos) en vez de barreras en una calle en uso.
   Hecho el 17 de noviembre de 2026 (cuarta enmienda de ADR-044). Cada ambiente dice qué roles y estados sirven de cobertura (`"cover"`, esquema 0.4.0). En `lived`, armarios eléctricos, contenedores, jardineras y papeleras sólidas, todos intactos y sin barreras, aunque no quepa ninguno (lo eligió el mantenedor). En `emptied`, eso más barreras, bidones y vallas, intactos o usados. `abandoned` se queda como estaba. `run` pasa a `perf cover --street-mood` el ambiente de `map.json` o, si no lo hay, el del perfil de calle. Las mesas de terraza no cuentan: con unos 0,75 m solo cubren agachado. Con los catálogos CC0 actuales solo una jardinera de Kenney cumple la regla en `lived`: los objetos de Asset Store no tienen licencia registrada y los contenedores de Kenney están marcados como usados. Hacen falta más modelos cívicos CC0 para dar variedad. Ese mismo día entraron cinco modelos CC0 de Poly Haven en `assets/normalized`: dos armarios eléctricos y tres jardineras de madera. Los armarios y la jardinera con patas cumplen la regla, así que `lived` tiene cuatro modelos de cobertura. Las dos jardineras bajas solo sirven como objetos de calle.

## Fuera de este plan

El problema de pasillo de Sierpes (una sola ruta entre los spawns) depende del área, no de `overgrow`: un mapa nuevo con radio de 150 m y núcleo de 125 m mete las calles paralelas.

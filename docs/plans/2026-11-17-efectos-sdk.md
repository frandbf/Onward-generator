# Plan: efectos del SDK (sonido bélico, fuego, polvo y filtro de color)

Fecha: 17 de noviembre de 2026. Pedido por el mantenedor tras probar Mogadiscio pkg_20 en el casco, después de revisar qué ofrece el SDK v1.15 además de lo que ya usamos (humo de ADR-043, tormenta de ADR-028, sobrevuelos, ambiente `Suburban Day/Night`, pájaros, viento en tejados, reverberación del monumento y niebla por preset).

**Estado (17 de noviembre de 2026).** D (paisaje sonoro, ADR-046 propuesto) fusionado (#132). E (fuego, enmienda de ADR-043) en su PR. Difiere del plan en dos puntos: se resuelve en `export package --soundscape` y no en `perf layout`, para no tocar el contrato del layout; y hay un modo `regional` (pájaros, viento y generador sin combate), que es lo que da `auto` sin daño `heavy`. G0 está construido y espera al casco.

Todo es genérico: cada efecto se decide por el estilo de fachada, el nivel de daño (`signs/damage.py`), el preset de luz o una clave de `map.json`, nunca por el nombre de un lugar. Un mapa sin las claves nuevas y sin estilo, daño o preset que las active sale igual byte a byte (Murcia, Madrid y París no cambian).

## 0. Lo que ofrece el SDK y lo que no

- **Sonido:** `OnwardAudioSource` con un clip propio del juego, de tipo `GlobalAmbience` (uno por mapa) o `LocalAmbience` (radio fijo por clip, sin campo para cambiarlo). Clips útiles que no usamos: `Distant Combat Sparse`, `Distant Combat Dense`, `Fire`, `Flies`, `Engine Generator`, `Wind Desert`, `Birds African Bush 1/2`, `Desert Day`, `Desert Night`. Son del juego: no hay licencia que comprobar ni memoria que añadir al paquete.
- **Partículas:** `PP_Particle_Lit`, `PP_Particle_Simple_Lit` (el del humo), `PP_Particle_UnLit_Dither` (sin mezcla de transparencias), `PP_Particle_Unlit`, sus variantes `_Additive` y `PP_Particle_Simple_Lit_Threshold`.
- **Filtro de color:** `SharedLevelSettings.SceneLUTTexture` y `SceneLUTStrength`, más los LUT de visión nocturna, que no se tocan. Ningún script del SDK lee el campo: lo aplica el cliente del juego, y no sabemos si lo respeta en un mapa personalizado.
- **No hay:** lluvia, nieve, clima ni viento sobre la vegetación. `OnwardCustomAudioSource` (clip propio) queda fuera, como dice `docs/11-onward-sdk.md`.

## 1. Opciones del pipeline, no ajustes de un mapa

Cada efecto es una opción del pipeline que cualquier mapa puede activar o desactivar. Ningún código, configuración o prueba nombra un lugar. Mogadiscio solo sirve de mapa de prueba para G0 y para las comprobaciones en el casco.

| Efecto | Clave de `map.json` | Opción de la etapa | Se rehace desde | Sin la clave |
| --- | --- | --- | --- | --- |
| Paisaje sonoro | `"soundscape"`: `auto`, `default`, `regional`, `war` o `battle` | `export package --soundscape` | `export` | `auto`: lo decide el daño resuelto |
| Fuego al pie del humo | `"fire"`: `true` o `false` | `export package --fire` | `export` | `false`: sin fuego |
| Polvo en el aire | `"dust"`: `auto`, `off` o `light` | `export package --dust` | `export` | `auto`: con el preset `dusty` o los estilos `arid` y `east_african` |
| Filtro de color | `"grade"`: `auto`, `off` u `on` | `export package --grade` | `export` | `auto`: el bloque `grade` del preset, si lo tiene |

Para cada opción:

- **El valor de `map.json` siempre gana** sobre la decisión automática, como `overhead_lines` y `street_mood`. Un valor explícito activa o apaga el efecto en cualquier mapa, también en uno cuyo estilo o preset no lo activaría.
- **`task map` la lleva sola:** `next_step` lee la clave y la pasa a su etapa, igual que `smoke` o `overhead_lines`. Así quien no usa la terminal la elige desde la skill.
- **Valores fuera de rango:** `workspace.py` los rechaza con `MapError`, y la etapa con un error de uso.
- **Documentación:** una fila en `docs/02-configuration.md`, en `docs/commands.md` y en la tabla de opciones de la skill (`reference/options.md`). Esa fila explica cuándo pedirla en palabras del usuario («una ciudad en guerra», «aire polvoriento»).
- **Informe:** `onward_export.json` registra el valor aplicado y su origen (`requested`, `auto` y la razón, u `off`). Si se pide algo que no se puede cumplir, como fuego sin humo, sale un aviso.
- **Configuración genérica:** las tablas (`config/soundscape.json`, los bloques del preset) van por estilo, nivel de daño o preset, nunca por mapa. Un estilo nuevo solo necesita una fila.
- **Mapas sin la clave:** pruebas de integración con el mapa de pruebas de Murcia comprueban que sale igual byte a byte, y que con la clave puesta el efecto aparece en cualquier estilo.

## 2. Orden y reglas comunes

Orden: **G0** (prueba del LUT, barata y puede descartar el punto 4), **D** (sonido), **E** (fuego), **F** (polvo), **G** (LUT, si G0 sale bien).

- Un PR por incremento, con sus pruebas unitarias y de integración, `task mutate BASE=origin/main` en cero y la documentación al día: `docs/02-configuration.md`, `docs/11-onward-sdk.md`, `docs/12-export.md`, la skill y `options.md`.
- Cada clave nueva se valida en `maps/workspace.py` con `MapError` y casos inválidos en las pruebas.
- Los efectos de partículas se miden en el casco, con la misma ruta con y sin el efecto. El listón es de 72 fps de mediana, como en Murcia.
- Las partículas no tapan a nadie a la altura de los ojos, porque los bots ven a través de ellas. Solo se permiten efectos pequeños (fuego) o casi invisibles (motas).

## 3. Incremento D: paisaje sonoro por ambiente (punto 1)

**Qué.** El sonido acompaña al mapa: combate lejano en una ciudad en guerra, fuego junto al humo, moscas junto a basura y restos, un generador en una ciudad cableada, y pájaros y viento del clima.

- **Configuración** `config/soundscape.json` con su esquema:
  - `combat` por nivel de daño: `heavy` da `Distant Combat Sparse`, y `none` o `light` no tienen combate.
  - `regional` por estilo de fachada: el clip de pájaros (por ejemplo `Birds African Bush 1` para `east_african`) y el de viento (`Wind Desert` para `arid` y `east_african`). Un estilo sin fila conserva `Birds Park` y `Wind Rooftop`.
  - Límites de fuentes locales: moscas 4 como máximo, generador 1, y 35 m entre fuentes del mismo clip.
- **Clave** `"soundscape"` en `map.json`: `auto` (por defecto), `default`, `war` o `battle`, y `export package --soundscape`. Cualquier mapa puede pedir `war` o `battle`.
  - `auto` lee el daño resuelto: `heavy` da `war` y el resto `default`.
  - `war` cambia el ambiente global a `Distant Combat Sparse`.
  - `battle` usa `Dense`, que nunca se elige solo.
  - `default` deja el sonido de hoy.
- **Local:**
  - `Fire` en cada columna de humo, a 2 m. El adaptador ya tiene las columnas, así que solo se añade una bandera `fire_audio` al bloque `smoke`.
  - `Flies` en los restos y contenedores del núcleo, por orden de id y lejos de los spawns.
  - `Engine Generator` junto al pie de un poste eléctrico (ADR-045), solo cuando el mapa tiene líneas.
- **Contrato:** el layout cambia. `audio_global` admite los clips de combate, y `audio_birds`, `audio_wind_clip`, `audio_flies_m` y `audio_generator_m` son campos nuevos.
  - Sube `GAMEPLAY_VERSION`.
  - El validador de `export/package.py`, que hoy exige `Suburban Day/Night`, pasa a aceptar la tabla de la configuración.
  - Un layout de la versión anterior sigue siendo válido.
- **Adaptador:** `PlaceLocalAudio` con el clip que dé el layout en vez de uno fijo.
- **Coste:** cero triángulos, cero texturas, unas 6 a 10 fuentes de audio más.
- **Pruebas:** tabla y esquema; resolución de `auto` con cada nivel de daño; separación y tope de fuentes; Murcia igual byte a byte; validador con layouts viejo y nuevo.
- **Casco:** que se oiga el combate sin tapar los pasos ni los disparos, y que el fuego y las moscas solo se oigan cerca.
- **ADR:** ADR-046, paisaje sonoro por ambiente.

## 4. Incremento E: fuego y brasas al pie del humo (punto 2)

**Qué.** Llamas pequeñas y brasas que suben en el resto bajo cada columna de humo, para que el humo tenga un origen.

- **Clave** `"fire"` en `map.json` (booleano; ausente es `false`) y `export package --fire`. Con `true` actúa donde haya columnas de humo, en cualquier mapa. Un mapa que ya tiene humo no cambia hasta que se active. Sin humo, `fire: true` es un aviso y no hace nada.
- **Textura** `fire_flame.png`, de 64 px y dibujada por código, CC0 por construcción como `smoke_puff.png`, con una llama suave de núcleo blanco y borde naranja.
- **Bloque** `fire` en `adapter_options.json`:
  - Llamas: unas 12 partículas por columna, que viven 0,6 s, miden de 0,4 a 1,2 m y suben a 1,5 m/s.
  - Brasas: 6 partículas por columna, de 4 cm, que viven 2 s, suben con algo de dispersión y se apagan.
- **Adaptador:** un segundo y un tercer `ParticleSystem`, hijos de cada columna, con `PP_Particle_Unlit_Additive`.
  - No se iluminan: el fuego da su propia luz y no necesita sondas.
  - Van sin sombras y sin luz en tiempo real, porque una luz dinámica es cara en el Quest y un parpadeo no se puede hornear.
  - Se rechaza un paquete con bloque `fire` y sin textura, o con textura y sin bloque.
- **Coste:**
  - Unas 18 partículas por columna, así que 54 billboards con 3 columnas, y ningún triángulo en el presupuesto.
  - La mezcla aditiva sigue siendo transparente, pero pinta poca pantalla.
- **Pruebas:** dibujo determinista, bloque `fire` en el paquete, rechazos del adaptador, y `fire` ausente igual byte a byte.
- **Casco:** fps mirando una columna a 5 m, comparado con el mismo mapa sin fuego, y que la llama no parpadee en estéreo.
- **ADR:** enmienda de ADR-043.

## 5. Incremento F: polvo y arena en el aire (punto 3)

**Qué.** Motas finas que flotan y derivan con el viento en mapas áridos o polvorientos. Dan sensación de aire cargado sin tapar la vista.

- **Clave** `"dust"`: `auto` (por defecto), `off` o `light`, y `export package --dust`. `light` lo pone en cualquier mapa y `off` lo quita. `auto` lo activa con el preset `dusty` o con los estilos `arid` y `east_african`.
- **Forma:** de 3 a 6 volúmenes de emisión sobre las calles principales del núcleo, no uno solo del tamaño del mapa.
  - Unity recorta cada sistema por su caja, así que los lejanos no se dibujan.
  - Cada volumen mide unos 30 × 30 × 6 m, va de 0,5 a 6 m de altura y lleva como mucho 60 partículas.
- **Partícula:** de 3 a 10 cm, con `PP_Particle_UnLit_Dither`. No mezcla transparencias, así que no hay overdraw por mezcla. Color arena tomado de la niebla del preset, deriva de 0,3 a 0,8 m/s en una dirección fija por semilla y vida de 8 s.
  - Se descartan las ráfagas de arena a ras de suelo: taparían a un jugador ante otro jugador y no ante los bots.
- **Textura:** `dust_mote.png`, de 32 px y dibujada por código.
- **Coste:** unas 300 partículas como máximo y ninguna en el presupuesto de triángulos. El tramado puede brillar en estéreo, así que hay que comprobarlo.
- **Pruebas:** colocación de los volúmenes (dentro del núcleo, sobre calles, deterministas), resolución de `auto`, y Murcia igual byte a byte.
- **Casco:** que se vea sin molestar, que no brille el tramado y que los fps no bajen.
- **ADR:** ADR-047, polvo en el aire.

## 6. Incremento G: filtro de color por preset (punto 4)

### G0. Prueba previa (sin PR)

Un build local de Mogadiscio con un LUT exagerado (sepia fuerte) en `SceneLUTTexture` y la intensidad a 1, subido al casco.

- Si el color cambia, se sigue con G.
- Si no cambia, el punto 4 se descarta y se anota en `docs/11-onward-sdk.md`.

Que un mapa publicado lo conserve solo se podrá ver tras publicar en el Workshop.

### G. Implementación

- **Configuración:** cada preset de `config/atmosphere.default.json` gana un bloque opcional `grade` con saturación, calidez, contraste e intensidad.
  - `dusty`: cálido y algo desaturado.
  - `overcast`: frío.
  - `golden_hour`: sin cambios.
  - Un preset sin bloque no escribe LUT.
- **Clave** `"grade"`: `auto` (por defecto), `off` u `on`, y `export package --grade`. `off` lo quita aunque el preset lo tenga. `on` con un preset sin bloque usa una gradación neutra y da un aviso.
- **Textura:** `scene_lut.png`, de 1024 × 32 como el `NeutralLUT_32` del SDK. Se genera por código desde la identidad con la gradación del preset, así que es determinista y CC0.
  - El adaptador la importa sin compresión, sin mipmaps, con `Clamp` y en sRGB según el LUT del SDK.
- **Adaptador:** asigna `SceneLUTTexture` y `SceneLUTStrength`. Los LUT de visión nocturna no se tocan.
- **Coste:** una textura de 128 KB y ningún triángulo. El juego aplica el filtro en su propio postproceso.
- **Pruebas:** identidad con intensidad 0 (el LUT neutro del SDK píxel a píxel), cada parámetro moviendo el color en su dirección, y `golden_hour` igual byte a byte.
- **Casco:** el color con y sin visión nocturna, y que no choque con el perfil de postproceso `PPProfileId`.
- **ADR:** enmienda de ADR-037.

## 7. Fuera de este plan

- Cielo propio (`PP_Cubemap_Skybox`, punto C10 del plan de atmósfera): el SDK recomienda el cielo integrado en Quest.
- Charcos y agua (`PP_Transparent_Lit`): mucho coste para lo que aportan.
- Elegir con la IA el paisaje sonoro o el polvo desde el `brief`: más adelante, si las reglas por daño, estilo y preset se quedan cortas.

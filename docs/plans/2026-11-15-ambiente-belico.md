# Plan: ambiente bélico genérico (daño, polvo y humo)

Fecha: 15 de noviembre de 2026. Pedido por el mantenedor al preparar Fallujah (noviembre de 2004), pero válido para cualquier mapa cuyo `brief` describa una ciudad en guerra o un clima árido: Mogadiscio 1993 ganaría lo mismo. Nada de este plan depende de un lugar.

Retoma dos puntos de la [parte 2 del plan de atmósfera](2026-11-10-atmosphere-advanced.md): C4 (decals de suelo) y C10 (humo). C10 descartaba los billboards estáticos y dejaba abierto si el SDK permitía algo mejor. El SDK v1.15 trae shaders de partículas propios (`PP_Particle_Simple_Lit`, `PP_Particle_UnLit_Dither`, `PP_Particle_Unlit`, sus variantes aditivas y `PP_Particle_Simple_Lit_Threshold`), así que el humo animado cabe dentro de la regla «solo shaders PP_*».

## 0. Lo que ya existe

- **Decals de pared desde código** (ADR-038): trece tipos dibujados por el generador, CC0 por construcción, entre ellos agujeros de bala con revoque saltado, grietas de revoque, hollín y pintura descascarada. Van de 30 a 50 por mapa en un atlas transparente, y la IA elige de 12 a 24.
- **Presets de luz** (ADR-037): `golden_hour` y `overcast` en `config/atmosphere.default.json`, con sol, ambiente, AO, color de niebla, suciedad y paleta de muros.
- **Perfil de calle** (`ai/street_profile.py`): la IA lee el `brief` y elige tipología, paleta, perfil de objetos (`wrecked`, `worn`…) y años de abandono.

Falta que el daño de guerra tenga más peso y más variedad cuando el ambiente lo pide, que haya daño en el suelo, una luz de polvo y humo.

## 1. Incremento A: nivel de daño y decals de guerra

- **Clave nueva** `"damage"` en `map.json`: `auto` (por defecto), `none`, `light` o `heavy`. Con `auto` la decide el perfil de calle a partir del `brief` (campo nuevo `damage` en el esquema, con enumeración) y, sin IA, las reglas: `props_profile = wrecked` da `heavy` y el resto `none`. Un mapa sin la clave sale igual que hoy, byte a byte.
- **Pared** (`signs/decals.py`): tres tipos dibujados nuevos, sin letras ni números: impacto de proyectil grande (cráter de revoque con ladrillo o bloque a la vista), quemadura de fuego y rociado de metralla. Con `heavy` la cantidad sube a 60–90 decals (de 30 a 50 hoy), al menos la mitad de daño de guerra. Con `light`, los de hoy, con más peso para los de daño.
- **Suelo** (C4): un atlas de decals de suelo con grietas, baches parcheados, arena acumulada junto al bordillo, quemaduras en la calzada y escombro suelto aplanado. Se colocan en la calzada y la acera del núcleo, lejos de los spawns y de los pies de escaleras y ascensores, 1,5 cm sobre el suelo para evitar el z-fighting. Usan el material de decals con `PP_Sprite_Lit` y sin sombra que ya existe.
- **Coste:** unos 2 triángulos por decal y un material transparente más por atlas. El overdraw se mide en el casco.
- **Pruebas:** dibujo determinista por semilla, colocación (sin vidrio ni puertas, sin solaparse, nada en los spawns), `damage` validado con casos inválidos, y Murcia sin la clave igual byte a byte.
- **ADR:** enmienda de ADR-038 (pared) y ADR nuevo para los decals de suelo.

## 2. Incremento B: preset de luz `dusty`

- **Preset nuevo** en `config/atmosphere.default.json`: sol cálido y algo apagado a unos 40°, ambiente `trilight` arena, niebla color arena (`#C9B48E` de partida), más suciedad (`grime` 1,3), muros más desaturados y oscurecimiento indirecto moderado. Se usa con `--atmosphere dusty`.
- **Niebla más cercana:** ADR-037 deja el inicio y el fin de la niebla en manos del mapa (ADR-014), porque una distancia fija por preset nublaría un mapa grande y no se vería en uno pequeño. El preset gana un factor opcional `fog_scale` (0,4–1,0) sobre esas distancias, para que el polvo se note sin cerrar el núcleo. Sin el factor, todo queda igual.
- **Valores de partida desde referencias, ajustados en el casco**, como se hizo con `golden_hour`.
- **Pruebas:** esquema (rango de `fog_scale`, colores), el bloque `lighting` del paquete y `golden_hour` / `overcast` sin cambios byte a byte.
- **ADR:** enmienda de ADR-037.

## 3. Incremento C: humo

- **Qué:** columnas de humo animadas con un `ParticleSystem` de Unity y un shader del SDK. Es humo que sube por encima de los techos y se ve desde lejos, no una cortina a ras de suelo.
- **Por qué no a ras de suelo:** las partículas no detienen los rayos de vista de los bots, así que un humo espeso en la calle dejaría ciegos a los jugadores y no a los bots. Columnas finas que suben quedan fuera de esa asimetría.
- **Dónde:** de 2 a 5 emisores por mapa (`"smoke"` en `map.json`: `auto`, un número de 0 a 5; con `auto`, 3 cuando `damage` es `heavy` y 0 en otro caso). Van sobre restos de coches, escombros o techos del núcleo y del anillo escénico, a 15 m o más de los spawns y entre sí, y nunca en el pie de una escalera o un ascensor.
- **Generador:** un nodo `fx/smoke/<n>` por emisor, sin malla y con `extras` (`type: smoke`, posición, altura de la columna, ritmo, vida, tamaño, color y semilla), y una textura de bocanada de 128 px dibujada por el generador (CC0). El manifiesto lo registra como `generated`.
- **Adaptador:** crea el `ParticleSystem` con `PP_Particle_Simple_Lit` (lo ilumina el sol del preset) y deja `PP_Particle_UnLit_Dither` como alternativa si el casco muestra demasiado coste de transparencia. Un máximo de 30 partículas por columna, sin colisión y sin sombras.
- **Riesgos:** la transparencia es cara en el renderizador por teselas del Quest (guía del SDK). Si la mediana baja de 72 fps se reduce a 2 columnas o se usa el shader con tramado. Tampoco se sabe si el cliente de Onward reproduce las partículas de un mapa publicado: lo dice la primera prueba en el casco, y si no las reproduce, el incremento se cierra documentando por qué.
- **Pruebas:** colocación (distancias, nunca en spawns ni en pies de escalera), `smoke` validado, textura determinista y paquete sin humo igual byte a byte. Lado C#: el nodo se convierte en un emisor con el shader permitido (`tools/unity`, como los demás pasos del adaptador).
- **ADR:** nuevo, propuesto hasta la sesión de casco.

## 4. Orden y reglas

1. A, luego B y luego C. Cada uno es un PR pequeño, con su ADR, tests, mutación limpia contra su rama padre y la documentación al día (`docs/01`, `docs/02`, `docs/12` y la skill del creador de mapas).
2. Ninguno cambia un mapa que no lo pide: sin `damage`, `dusty` ni `smoke`, el paquete es el de antes.
3. Triángulos dentro del límite del mapa (250 000 para el Workshop) y, si cambia la jugabilidad, las métricas de ADR-032 sin empeorar.
4. Fallujah es el primer mapa de prueba en el casco, y Mogadiscio 1993 el segundo.

## 5. Fuera de este plan (para más adelante)

Postes y cableado (C5), objetos de techo (C8), casas sin terminar y escombros o muros derrumbados de biblioteca, con su filtro de licencia y calidad. Quedan anotados en la conversación del 15 de noviembre como los puntos 3 a 6.

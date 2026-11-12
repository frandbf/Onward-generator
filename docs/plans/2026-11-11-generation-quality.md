<!-- Written in Spanish for the maintainers. State as of HEAD a7ae06e, 2026-11-11. No code changes. -->

# Plan: calidad de generación, con Rue Mouffetard como ejemplo

*11 de noviembre de 2026. Tras cerrar la parte 1 de atmósfera (ADR-037) generamos Rue Mouffetard (París) con todas las funciones activadas, para revisar la calidad con un mapa nuevo y no con Madrid, que es el mapa que más hemos pulido. Este plan recoge lo que ese mapa destapó y lo ordena por impacto en la partida. Va antes de la [parte 2 de atmósfera](2026-11-10-atmosphere-advanced.md): varios puntos de aquí (presupuesto, rutas) también condicionan aquella.*

---

## 0. El mapa de referencia

- `run --name "Rue Mouffetard" --lat 48.84045 --lon 2.34981 --radius-m 150 --core-radius-m 95`, `golden_hour`.
- Activados a mano en `map.json`: `street_profile`, `repetition`, `overture_tints`, `lidar_roofs`, `regional_facades`, `gameplay_report`, `balanced_spawns`, `sightline_cover`, `ambient_ai`, `panoramax`.
- `pkg_04`: 234 953 triángulos en Unity, 4 lightmaps, llamadas de dibujo por vista con mediana 142 y p90 293. En el Quest el 11 de noviembre: «se ve bien».
- Con el radio por defecto de 400 m el mismo mapa salía con 421 689 triángulos visuales, y con el núcleo de 100 m con 252 179 en Unity: los dos por encima del límite.

---

## 1. Ya resuelto en esta revisión

### Q0. Escaleras de mano y ascensores (PR #27, enmienda de ADR-029)

Ningún mapa reciente tenía accesos a las azoteas. Había un error de redondeo en `covers`, la calzada quedaba fuera contra lo que dice el ADR, y las reglas no dejaban ningún edificio candidato en un centro denso. Rue Mouffetard pasó de 0/0 a 2/3 (escaleras de mano/ascensores) y Madrid Sol arena de 0/0 a 0/1. `run` ahora avisa cuando un mapa queda sin accesos.

**Visto en el casco (11 de noviembre):** se subió por una escalera de mano y se viajó en un ascensor, y los dos funcionan bien, incluidos los ascensores entre alturas distintas. Queda anotado en ADR-029.

---

## 2. Puntos, ordenados por impacto

| # | Punto | Qué se vio | Impacto | Esfuerzo |
|---|---|---|---|---|
| Q1 | Rutas del Hunt y forma del mapa | el informe da 1 ruta distinta; Unity descarta 2 de 32 rutas de bots | alto (partida) | medio |
| Q2 | Presupuesto de triángulos y radio por defecto | 400 m da 421 689; nadie avisa por encima de 250 000 | alto (bloquea publicar) | bajo |
| Q3 | Estilo de fachada de París | Francia cae en `generic`: textura moteada, ladrillo rojo, sin piedra, mansardas ni balcones de forja | alto (identidad) | alto |
| Q4 | Calles estrechas muy oscuras | con el sol a 30° la sombra se come la calle | medio-alto (leer a un soldado) | medio |
| Q5 | Mobiliario urbano y árboles | 281 `urban_element_not_placed` en detail; `overgrow` pone 0 árboles | medio | medio |
| Q6 | Detalles que delatan lo generado | marcos blancos que parecen flotar, rejilla marcada en la losa de la plaza, bloques de hormigón fuera de lugar | medio | bajo-medio |
| Q7 | Solape de superficies en mapas grandes | 0,83 m² con 400 m (crítico, umbral 0,5) | bajo hoy, bloquea 400 m | medio |
| Q8 | `map create` y los interruptores | hay que editar `map.json` a mano y rehacer el plan de texturas | bajo (comodidad) | bajo |
| Q9 | IA desde Docker en macOS | Ollama no responde desde el contenedor; las etapas de IA caen a reglas | bajo (hay alternativa) | decisión |

### Q1. Rutas del Hunt y forma del mapa

- El informe de juego (ADR-032) da `routes 1`: el mapa se juega como un solo pasillo. En el horneado, Unity descartó `Route_27` y otra ruta («no navmesh path from the previous point»).
- ~~Al diagnosticar los accesos medimos que un 43 % del suelo del núcleo no se alcanza a pie.~~ Corregido el 11 de noviembre: esa cifra restaba también la calzada. Lo que no se alcanza son sobre todo patios interiores de manzanas cerradas, y la red de calles está conectada.
- **Hacer:** averiguar qué parte cierra el paso (pasos más estrechos que un jugador, cruces que faltan, manzanas que tocan el muro) y si `routes 1` es real o un efecto de la rejilla de 2 m del informe. Que `run` avise cuando el suelo alcanzable caiga por debajo de un umbral.
- **Aceptación:** al menos 2 rutas distintas en Mouffetard, y ninguna ruta de bot descartada en Unity.
- **Hecho (Q1a, enmienda C6 de ADR-032 del 11 de noviembre):** `balanced_spawns` también mueve a los atacantes, y con diferencia de contacto ≤ 15 % gana el candidato con más rutas. Madrid Sol arena pasa de 3 a 5 rutas y Madrid Sol workshop de 2 a 6. `run` avisa cuando un mapa queda con 1 ruta. Mouffetard sigue con 1: es un pasillo real, porque la calle del mercado es la única conexión norte–sur que pisan los bots, y eso lo cambia la elección del centro y del núcleo, no la colocación. Las rutas de bots que Unity descarta quedan pendientes.
- **Hecho (Q1b, 11 de noviembre):** los puntos de patrulla se muestrean sobre las islas encogidas 0,5 m más (`route_islands`). Los dos puntos que Unity descartaba en Mouffetard estaban a 0,41 m del muro del núcleo y a 0,2 m fuera del suelo encogido. Con el margen, el `launcher` conserva las 32 rutas: no ancla ningún waypoint ni descarta ninguno.

### Q2. Presupuesto de triángulos y radio por defecto

- **Hacer:** que `run` estime los triángulos de Unity antes de exportar y avise, o pare, por encima de 250 000. Que `map create` proponga el radio según la densidad, o que baje el radio por defecto. 150 m con núcleo de 95 m cabe en Mouffetard.
- **Aceptación:** un mapa nuevo con los valores por defecto no supera 250 000 sin aviso.

### Q3. Estilo de fachada de París

- `config/facade_styles.json` tiene `mediterranean` (con atlas), `central_european`, `anglo` y `latin_american` (atlas regionales), y `generic` sin atlas. París resuelve a `generic`, así que E8 (tono del atlas por atmósfera) no se aplica.
- **Hacer:** un estilo haussmanniano: piedra caliza clara, planta baja almohadillada, balcones corridos de forja en la 2.ª y la 5.ª planta, mansarda de zinc con buhardillas y molduras sobre las ventanas. Necesita texturas con licencia verificada (CC0 de ambientCG o similar) y un ADR o enmienda del ADR-020/027.
- **Riesgo:** las mansardas son tejado inclinado. ADR-029 no pone accesos en tejados inclinados, así que habría menos azoteas jugables.
- **Hecho (Q3a, 12 de noviembre):** París resuelve a `haussmann`, y con `regional_facades` su atlas pinta sillería caliza clara (juntas dibujadas en código sobre el grano CC0 de `acg_concrete012`), plinto de piedra, cornisa, imposta en cada planta y ventanas de marco pintado o gris oscuro. La clave `wall_tints` fija el tinte de la caliza, sea cual sea la paleta. No hace falta ninguna textura nueva; enmienda de ADR-020. Pendiente (Q3b): balcones de forja y mansardas de zinc, que son geometría.

### Q4. Calles estrechas muy oscuras

- **Hacer:** medir la luminancia en la calle con las capturas fijas de E1 y probar más luz ambiental en `golden_hour`, sondas de luz más densas en las calles estrechas o un sol algo más alto cuando la calle es muy estrecha. Ningún cambio debe alterar el `neutral`.
- **Aceptación:** un soldado se distingue a 30 m en la calle en sombra (casco).
- **Hecho (11 de noviembre, salvo el casco):** `scene_report.json` mide ahora `probe_light`, la luz de las sondas donde está un jugador (PR #33). En Mouffetard el p10 era 0,0143. Una luz ambiental más clara lo subía a 0,016, y sondas exteriores más densas o con menos holgura lo dejaban entre 0,013 y 0,017: la sombra de una calle estrecha la iluminan sus paredes, no su franja de cielo. `golden_hour` hornea ahora la luz rebotada al doble (`indirect_intensity` 2, PR #34): p10 0,0227, mediana 0,0594 → 0,1083, suelo de la calle en sombra ×2 y el soleado +26 %. `neutral` y `overcast` no cambian. Falta la comprobación en el casco.

### Q5. Mobiliario urbano y árboles

- La mayoría de los 281 avisos son `not_on_free_walkable_ground`: 139 farolas, 70 bancos, 35 papeleras, 16 semáforos y 11 aparcabicis. Hay que separar los que caen fuera del núcleo (esperado) de los que se pierden en la calle jugable.
- `overgrow` coloca 0 árboles en este mapa.
- **Hacer:** contar dentro y fuera del núcleo; recolocar a la acera más cercana lo que caiga a menos de 1 m; ver por qué `overgrow` no encuentra sitio para árboles.

- **Hecho (11 de noviembre):** casi todo lo «no colocado» era correcto. 107 de las 116 farolas observadas sin sitio están sobre la línea de fachada: son farolas de pared, y ahora se dibujan así (ver Q6). Los semáforos de OSM están en el nodo del cruce, en mitad de la calzada. Los intentos generados (bancos y papeleras en calles peatonales estrechas) pasan a `info`. Los avisos de `detail` en Mouffetard bajan de 258 a 43.
- Los «0 árboles» de `overgrow` no son un fallo: sus candidatos son los árboles de OSM, y los 16 del núcleo ya los planta `detail`. Lo que sí se vio es que la zona de `overgrow` es un bloque fijo de 110 m, mientras el núcleo mide 190 m: la ambientación cubre un tercio del núcleo. Ampliarla choca con el presupuesto (Mouffetard ya está en 234 953 triángulos), así que va con P0.

### Q6. Detalles que delatan lo generado

- Marcos de ventana blancos que parecen flotar delante de la fachada, rejilla repetida en la losa de la plaza y bloques de hormigón de cobertura que no encajan en una calle comercial.
- **Hacer:** hundir o sombrear el marco, variar o girar la textura de la losa, y elegir la cobertura según el contexto (macetas, puestos de mercado o vallas en una calle peatonal).
- **Hecho (11 de noviembre):**
  - **Farolas:** las farolas observadas guardan 8 m entre sí, como las generadas (una plaza tenía 12 postes en 30 × 37 m, todos con el brazo al este), y las de fachada se cuelgan de la pared. La separación mínima pasa de 6,6 a 8,1 m.
  - **Marcos:** los derrames de las ventanas usan el material de la pared, no `frame_stone` casi blanco.
  - **Cobertura:** las barreras van antes que el cubo de hormigón que `overgrow` ya había puesto. No hay macetas ni puestos de mercado con licencia registrada; los de la Asset Store quedan fuera a propósito.
  - **Losa:** la rejilla de la plaza no se ve en las capturas fijas de `pkg_04`, así que queda pendiente de una captura que la muestre.

### Q7. Solape de superficies en mapas grandes

- Al ajustar cada superficie a la rejilla de 1 mm se meten vértices en lo ya ocupado. Probamos un arreglo que restaba lo ocupado tras el ajuste, y se retiró: en una escena sintética la unión de GEOS fallaba con las aristas casi coincidentes que dejaba, y el chequeo daba un solape falso de 933 m². El parche está guardado en `cache/surfaces-unsnapped-rest.patch`, fuera de git.
- **Hacer:** ajustar también los edificios de la partición a la misma rejilla y hacer las operaciones con `grid_size`, para que toda la partición comparta una sola rejilla. Las mallas de los edificios no cambian (diferencia ≤ 0,5 mm). Probar con 300 escenas aleatorias, además de Mouffetard a 400 m.
- **Nota:** cambia la base de todos los mapas, que habría que regenerar desde `generate`.

### Q8. `map create` y los interruptores

- **Hacer:** que `map create` acepte los mismos interruptores que `map.json` y cree el plan de texturas con ellos, para no tener que repetir `--textures`.

### Q9. IA desde Docker en macOS

- AGENTS.md limita el modelo a Ollama en loopback. Desde un contenedor, `127.0.0.1` es el propio contenedor, no el Mac.
- **Opciones (tu decisión):** ejecutar las etapas de IA en el `.venv` nativo (faltan dependencias como `laspy`, cuya instalación necesita tu aprobación), o aceptar `host.docker.internal` como loopback del anfitrión, lo que exige cambiar la regla.

---

## 3. Orden recomendado

1. **Q2**: barato, y evita generar mapas que no se pueden publicar.
2. **Q1**: lo que más pesa en la partida.
3. **Q5** y **Q6**: mejoras visibles de coste moderado.
4. **Q4**: necesita sesión de casco.
5. **Q3**: el más caro; merece su propio ADR y un plan de texturas.
6. **Q7**, **Q8** y **Q9** cuando toquen.

Después, la parte 2 de atmósfera empezando por P0. Q2 deja hecha parte de esa medición.

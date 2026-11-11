<!-- Written in Spanish for the maintainers. State as of HEAD 928f7bf, 2026-11-10. No code changes. -->

# Plan: atmósfera, parte 1 (lo fácil y seguro primero)

*10 de noviembre de 2026. Nace de una propuesta externa («presets de atmósfera») y de dos capturas de mapas oficiales de Onward: un suburbio con luz cálida y un centro urbano en conflicto al atardecer. La propuesta se contrastó con el código y el SDK v1.15. Este documento recoge **solo los incrementos baratos, reversibles y sin costo de triángulos**. Lo que cambia geometría, contenido o jugabilidad está en la [parte 2](2026-11-10-atmosphere-advanced.md), que se evalúa después de probar esta en el casco.*

---

## 0. Resumen para decidir

1. **La luz es lo más barato que mejora el aspecto**: está horneada, no cuesta nada por frame en el Quest y suma 0 triángulos. Madrid Sol Arena está en 244 836 de 250 000 triángulos, así que todo lo que añada geometría va a la parte 2.
2. **Las capturas no muestran un sol de 12 a 18°.** En las dos, las sombras de los vehículos son cortas: el sol está entre 30 y 50°. Un sol a 12° deja calles enteras en sombra y hace más difícil ver a un enemigo, que en un mapa de Onward pesa más que el ambiente. Los presets empiezan a **30°**.
3. **Casi todo se aplica en `export package` y en el adaptador.** Un mapa existente solo vuelve a correr desde `export`; `detail` y `materials` no cambian, salvo en el incremento E8.
4. **Sin la clave, el paquete queda byte a byte igual**, `package_id` incluido. Es el mismo criterio que `lightmap_pages_max` (ADR-005, enmienda C5).
5. **Hay una sesión de casco a mitad del plan (E4)** para revisar la matriz de perfiles de post-proceso del SDK. Ahí se decide si vale la pena seguir afinando valores o si el cielo integrado lo impide.

### Corrección a la propuesta original

- El ADR nuevo es el **ADR-037**: ADR-035 y ADR-036 ya existen.
- La mugre de fachada ya se hornea en el atlas (`materials/trim_atlas.py`: chorreaduras, bajo cornisa, zócalo oscurecido). `PP_Opaque_Simple_Lit` no lee vertex colours (ADR-020), así que esa vía queda descartada.
- Los vehículos y barreras de `overgrow/dressing.py` **sí llevan collider** cuando `is_solid` los considera cobertura (`dressing.py:438-441`). El docstring de la cabecera del módulo dice lo contrario y está desactualizado; E9 lo corrige y lo cubre con un test.
- El análisis anterior a este plan decía que esos vehículos no tenían collider; era un error.

---

## 1. Reglas que valen para todo el plan

1. **Todo horneado.** Sin luces realtime ni partículas (ADR-005).
2. **Retrocompatible.** Sin la clave `atmosphere`, o con `neutral`, la salida es idéntica a la actual. Cada incremento lo prueba.
3. **0 triángulos.** Ningún incremento de este plan añade geometría. `scene_report.json` `triangles` no cambia entre `neutral` y un preset.
4. **Solo valores numéricos en el adaptador.** El C# lee un bloque `lighting` de `adapter_options.json`; no conoce nombres de presets.
5. **Determinista.** La única elección aleatoria (sentido del azimut, E6) usa `stable_unit` con el id del mapa.
6. **Honestidad de datos.** El azimut y el preset se registran como `generated` en el informe del paquete.
7. **Lo desconocido del SDK se marca** en la sección «To check on the headset» del ADR-037: si el runtime respeta la niebla, el ambiente Trilight, `SkyColor` y los perfiles.
8. **Un PR por incremento**, con tests, `task lint`, `task typecheck`, `task test`, `task mutate` y `task guard`.

---

## 2. Clave y presets

```jsonc
// map.json
"atmosphere": "golden_hour"   // neutral | golden_hour | overcast
```

- Opción de CLI: `run --atmosphere <preset>` y `maps create --atmosphere`, igual que `--time-of-day` y `--storm`.
- **Solo de día.** Con `time_of_day: night`, un preset distinto de `neutral` se rechaza con un error claro, igual que `--storm` en un mapa de día (ADR-028).
- La clave cambia la **luz**, no el contenido: el dressing de conflicto, el humo y los autos van en claves aparte (parte 2).
- Los valores viven en `config/atmosphere.default.json`, validado con `config/atmosphere.schema.json`.
- Un preset `dusk` (referencia B, con `Twilight`) solo se añade si la matriz de E4 muestra que `Twilight` cambia el cielo.

### Valores de partida, a ajustar en el casco

Colores en sRGB hex. Elevación en grados sobre el horizonte.

| Parámetro | `neutral` (actual) | `golden_hour` | `overcast` |
|---|---|---|---|
| Elevación del sol | 50° | **30°** | 55° |
| Azimut del sol | −30° fijo | alineado a las calles (E6) | −30° fijo |
| Color del sol | blanco | `#FFE0B8` | `#DDE3EA` |
| Intensidad del sol | 1.2 | 1.4 | 0.7 |
| Modo de ambiente | por defecto (skybox) | Trilight | Flat |
| Ambiente cielo / ecuador / suelo | n/a | `#8A9BB3` / `#9A9080` / `#5A5044` | `#9AA3AD` (flat) |
| AO `aoMaxDistance` | por defecto | 1.5 m | 1.5 m |
| AO exponente indirecto / directo | por defecto | 1.0 / 0.2 | 1.0 / 0.0 |
| Niebla lineal | la de la configuración | 140–800 m, `#E8D2B4` | 100–600 m, `#C9CFD6` |
| Texels/m en el núcleo jugable | 10 | 14 (E10) | 10 |
| Intensidad de la mugre del atlas | 1.0 | 1.0 | 1.2 |
| Desaturación / tope de albedo de muros | 0 % / sin tope | 8 % / 0.85 | 5 % / 0.85 |
| `PPProfileId` / `TimeOfDay` | `UrbanDay1` / `Day` | lo que decida E4 | lo que decida E4 |

---

## 3. Incrementos, del más fácil al más delicado

Cada fila indica lo que toca, el riesgo y cómo se prueba. Los cuatro primeros no cambian ningún mapa.

| # | Incremento | Toca | Riesgo | Esfuerzo |
|---|---|---|---|---|
| E0 | Clave y schema sin efecto | config, CLI, `maps` | nulo | bajo |
| E1 | Capturas fijas tras el bake | adaptador (opt-in) | nulo para el mapa | bajo-medio |
| E2 | Paquetes de prueba de perfiles PP y `SkyColor` | export (opt-in experimental) | bajo | bajo |
| E3 | Sol configurable | adaptador, export | bajo | bajo |
| **E4** | **Sesión de casco: matriz de perfiles y sol** | — | — | — |
| E5 | Ambiente Trilight y AO | adaptador | bajo | bajo |
| E6 | Azimut alineado a las calles | export (Python puro) | bajo | medio |
| E7 | Niebla por preset | export | bajo | bajo |
| E8 | Mugre y paleta del atlas | materials | medio | medio |
| E9 | Colliders de cobertura: test y docstring | tests, docstring | nulo | bajo |
| E10 | Densidad de lightmap en el núcleo | export, adaptador | medio | medio |
| **E11** | **Sesión de casco final y decisión sobre la parte 2** | — | — | — |

### E0. Clave `atmosphere` y schema, sin efecto

- `config/atmosphere.default.json` y `.schema.json` con los tres presets de la tabla.
- `map.json` gana `atmosphere` (opcional). `run --atmosphere` lo guarda; en un mapa existente avisa de que hay que volver a correr desde `export`.
- Rechazo con `night`.
- Borrador del **ADR-037 "Atmosphere presets"**, en estado *proposed*.
- **Tests:** schema válido e inválido; preset desconocido; rechazo con `night`; `neutral` y sin clave dan el mismo `adapter_options.json` y el mismo `package_id` que hoy.
- **Aceptación:** ningún paquete cambia.

### E1. Capturas fijas tras el bake

Es la herramienta que hace baratos los demás incrementos: compara antes y después sin ir al casco.

- Opción del adaptador `capture_views` (apagada por defecto): 4 cámaras a 1.7 m de altura dentro del núcleo jugable, deterministas, que renderizan PNG de 1920×1080 a `<pkg>/build/views/` después del bake.
- Posiciones: los dos spawns mirando al centro y dos puntos de calle elegidos por el tramo más largo dentro del núcleo. Se registran en `scene_report.json`.
- No toca la escena guardada ni el paquete del SDK; solo escribe imágenes.
- **Tests:** Python que elige las posiciones (unitario, con una grilla sintética). El C# se valida con un bake de Madrid Sol Arena.
- **Aceptación:** las 4 imágenes existen y se repiten igual en dos bakes de la misma escena.

### E2. Paquetes de prueba de perfiles de post-proceso

El SDK define 12 `EProfileId` (`UrbanDay1/2`, `DesertDay1/2`, `ForestDay1/2` y sus versiones de noche) y `ETimeOfDay.Twilight`. Hoy solo se usan `UrbanDay1`/`Day` y `UrbanNight1`/`Night` (`ENVIRONMENT_PROFILES`, `export/package.py:131`). `SharedLevelSettings.SkyColor` existe y no se usa. Cómo se ve cada uno depende del cliente del juego, no del SDK.

- Clave experimental de la tarjeta de export, `build.experimental_environment: {time_of_day, profile, sky_color}`, que salta la lista `ENVIRONMENT_PROFILES` y marca el paquete como `experimental: true` en su manifiesto.
- Nada se habilita por defecto. Una combinación entra en `ENVIRONMENT_PROFILES` solo cuando se ha visto en el casco, con nota en el ADR-037.
- **Tests:** la clave ausente no cambia nada; un perfil inexistente se rechaza; el manifiesto lleva la marca.
- **Aceptación:** se pueden exportar `UrbanDay2`, `DesertDay1` y `Twilight` del mismo mapa.

### E3. Sol configurable

- El adaptador lee `lighting.sun_elevation_deg`, `sun_azimuth_deg`, `sun_color`, `sun_intensity` y `sun_shadow_angle_deg`. Sin el bloque, mantiene exactamente `Quaternion.Euler(50f, -30f, 0f)` e intensidad 1.2 (`OnwardMapAdapter.cs:876-878`). `ApplyNight` sigue ganando de noche.
- `export package` traduce el preset en ese bloque.
- **Antes de escribir código:** comprobar con ADR-002 cómo se orienta el norte del generador en Unity. La rotación de una luz direccional indica hacia dónde **apunta**, no de dónde viene el sol. Un test fija la conversión con los cuatro puntos cardinales.
- **Aceptación:** un bake `golden_hour` de Madrid Sol Arena muestra en las capturas de E1 sombras más largas y cálidas, con el mismo número de triángulos y draws.

### E4. Sesión de casco: matriz de perfiles y sol (pausa)

- El mismo mapa en `neutral` y `golden_hour`, con `UrbanDay1`, `UrbanDay2` y `Twilight`, y uno o dos valores de `SkyColor`. Son unos 6 paquetes.
- **Qué mirar:** si el cielo acompaña al bake dorado; si el perfil cambia el grading; si se ve a un enemigo en la sombra; fps.
- **Decisiones:** valores del sol; qué perfil usa cada preset; si se añade `dusk`. Si ningún perfil arregla el cielo azul, el preset se queda más cerca del neutral y la parte 2 del cielo (C8) pierde prioridad.
- **Pausa para el usuario.**
- **Resultado (11 de noviembre de 2026).** El usuario probó `pkg_15` (`Day`/`UrbanDay1`), `pkg_16` (`Twilight`) y `pkg_17` (`UrbanDay2` con `SkyColor #E8B890`) en el Quest 3: los tres se ven bien, y `Twilight`, `UrbanDay2` y `SkyColor` no cambian nada visible. `golden_hour` se queda en `Day`/`UrbanDay1`, no se añade `dusk`, el sol no cambia y el cielo propio de la parte 2 pierde prioridad. Detalle en ADR-037.

### E5. Ambiente Trilight y AO

- `RenderSettings.ambientMode` y los tres colores; `aoMaxDistance`, `aoExponentIndirect` y `aoExponentDirect` en la configuración del bake (cerca de `settings.ao = true`, `OnwardMapAdapter.cs:2804`).
- El ambiente también ilumina a los jugadores a través de las light probes. Un suelo de ambiente muy oscuro oscurece a los enemigos en sombra: por eso los colores de la tabla son más claros que los de la propuesta original.
- **Aceptación:** sombras con tinte frío en las capturas, esquinas oscurecidas por AO y un soldado legible en la sombra en el casco.

### E6. Azimut alineado a las calles

- Función pura en Python, llamada desde `export package`:
  1. tramos de calle (calzada y peatonal) dentro del núcleo jugable;
  2. histograma del rumbo módulo 180°, ponderado por largo, en bins de 5°;
  3. eje dominante θ, suavizado con los bins vecinos;
  4. `azimut = θ ± 35°`, con el signo elegido por `stable_unit(id del mapa)`.
- Así el sol nunca queda paralelo a la calle principal y cada calle tiene una vereda al sol y otra a la sombra.
- Se registra en el informe del paquete: `sun.axis_deg`, `sun.azimuth_deg`, `status: generated`.
- **Tests:** grilla ortogonal, grilla en diagonal, una sola calle, núcleo sin calles (vuelve a −30°), y determinismo.

### E7. Niebla por preset

- La niebla lineal ya existe (`ApplyFog`, ADR-014). El preset solo cambia inicio, fin y color.
- Sigue sin saberse si el runtime de Onward respeta `RenderSettings.fog`: queda en el ADR-037.
- **Tests:** valores del preset en `adapter_options.json`; sin preset no cambia nada.

### E8. Mugre y paleta del atlas

Es el primer incremento que cambia `materials`, y por eso va después de la sesión de casco.

- La intensidad de la mugre ya horneada pasa a ser un factor del preset que escala `streak_dark` y el oscurecimiento del zócalo.
- La desaturación y el tope de valor se aplican **al hornear** el atlas, solo a las franjas de muro. Un color observado (OSM `building:colour` o el tinte de Panoramax) se respeta: no se desatura.
- `materials_report.json` gana un bloque opcional `atmosphere` con la luminancia media de los muros antes y después.
- **Riesgo:** un atlas más oscuro baja la legibilidad en el Quest. Los valores son suaves a propósito.
- **Tests:** atlas idéntico con `neutral`; luminancia menor con `golden_hour`; colores observados intactos.
- Un mapa existente vuelve a correr desde `materials`.

### E9. Colliders de cobertura: test y docstring

- Test de que cada vehículo y barrera que coloca `overgrow/dressing.py` tiene un nodo `overgrow/collider/prop/...` cuando `is_solid` es verdadero.
- Corregir la cabecera de `dressing.py`, que dice que no se crean colliders.
- Es un requisito de la parte 2 (C3, C4): ahí los vehículos pasan a ser cobertura de diseño.

### E10. Densidad de lightmap en el núcleo jugable

- `lightmap_texels_per_m_core` (14 en `golden_hour`): un renderer combinado cuya celda (`combine_cell_m`) cae dentro del núcleo (ADR-017) usa esa densidad; el resto mantiene la global y los `lightmap_scales` que ya existen.
- Compone con el presupuesto de páginas de la enmienda C5 del ADR-005. Si `lightmap_pages_max` está activo, manda el presupuesto.
- **Riesgo:** memoria de lightmaps y tiempo de bake. El ADR-005 registra que la estimación de páginas puede quedarse muy corta (París: 3 estimadas, 24 reales).
- **Aceptación:** `lightmap_texels` del informe del bake y memoria gráfica ≤ 1 GB en el casco. Si no cabe, bajar a 12 o descartar.

### E11. Sesión de casco final

- Checklist por preset: sombras legibles, cielo coherente, niebla, fps ≥ 72 de mediana, draws ≤ 150 en las vistas A y B, memoria ≤ 1 GB.
- El ADR-037 pasa a *accepted* solo con esa sesión.
- **Decisión:** qué entra de la [parte 2](2026-11-10-atmosphere-advanced.md) y en qué orden.

---

## 4. Fuera de este plan

- Todo lo que añade triángulos o cambia la jugabilidad: cornisas más profundas, decals de suelo, autos estacionados, dressing de conflicto, objetos de techo, parapetos irregulares, postes con cables, normal map y humo. Está en la [parte 2](2026-11-10-atmosphere-advanced.md).
- Luces o sombras realtime, shaders propios y cambios de arquitectura del pipeline.

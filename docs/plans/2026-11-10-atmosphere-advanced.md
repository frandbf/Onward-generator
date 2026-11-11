<!-- Written in Spanish for the maintainers. State as of HEAD 928f7bf, 2026-11-10. No code changes. -->

# Plan: atmósfera, parte 2 (lo complejo, para una segunda evaluación)

*10 de noviembre de 2026. Recoge los puntos de la propuesta de «presets de atmósfera» que añaden geometría, contenido nuevo o cambian la jugabilidad. No se empieza ninguno hasta cerrar la [parte 1](2026-11-10-atmosphere.md) con su sesión de casco final (E11). Ahí se decide cuáles entran y en qué orden. Cada uno necesita su propio ADR o una enmienda.*

---

## 0. Por qué estos van aparte

1. **Cuestan triángulos.** Madrid Sol Arena mide 244 836 de 250 000 en Unity. Quedan unos 5 000. Cualquier elemento de este plan exige liberar presupuesto antes (P0).
2. **Pueden empeorar la partida.** Barreras que cortan calles, vehículos cruzados u objetos en los techos cambian rutas, líneas de visión y coberturas. Las métricas de ADR-032 deben no empeorar.
3. **Traen contenido nuevo**, con licencia que verificar (`docs/legal/`), o dependen de decisiones de producto tuyas.
4. **Algunos dependen de lo que el SDK permita**, como un cielo propio, y eso solo se sabe probando.

---

## 1. Prerrequisito común

### P0. Liberar presupuesto de triángulos

- Medir en `scene_report.json` qué categorías pesan más fuera del núcleo jugable (anillo, fachadas lejanas, mobiliario de interiores que no se ve desde la calle).
- Opciones a evaluar: menos detalle de fachada fuera del núcleo, impostores más agresivos en el anillo (ADR-014), o bajar `max_triangles` del amueblado de interiores (ADR-036) si las tiendas del borde no se visitan.
- **Meta:** dejar 15 000–25 000 triángulos libres en Madrid Sol Arena, siempre ≤ 250 000.
- **Riesgo:** perder detalle que el jugador sí ve. Se valida con las capturas fijas de la parte 1 (E1).

---

## 2. Elementos, ordenados de menor a mayor riesgo

| # | Elemento | Impacto visual | Triángulos | Riesgo de juego | Necesita |
|---|---|---|---|---|---|
| C1 | Cornisas más profundas | alto | bajo-medio | nulo | P0 |
| C2 | Parapetos irregulares | medio | nulo-bajo | nulo | — |
| C3 | Autos estacionados | alto | alto | medio | P0, tu decisión, E9 |
| C4 | Decals de suelo | medio | bajo | nulo | licencias, medir overdraw |
| C5 | Postes y cableado | alto en suburbios | medio | bajo | P0, tu decisión |
| C6 | `regional_facades` por defecto | medio | nulo | nulo | tu decisión, sesión ADR-020/027 |
| C7 | Normal map de fachada | medio | nulo | nulo | medir en Quest (ADR-033) |
| C8 | Objetos de techo | bajo-medio | medio | medio | P0, ADR-029 |
| C9 | Dressing de conflicto | alto | alto | **alto** | P0, E9, métricas ADR-032 |
| C10 | Cielo propio y columnas de humo | alto | bajo | nulo | que el SDK lo permita |

### C1. Cornisas más profundas

- Con sol bajo o medio, la sombra de la cornisa es lo que hace legible cada edificio (referencia B).
- El preset sube el vuelo de la cornisa a 0.35–0.5 m en edificios de techo plano, desde la tipología (ADR-024/027) y `config/detail.default.json`.
- **Por qué no es trivial:** cambia `detail`, interactúa con los balcones, las escaleras de mano y los ascensores (ADR-029), y con las fachadas trim sheet (ADR-020). Hay que confirmar que ninguna cornisa invade la llegada de una escalera.
- **Prueba:** capturas antes y después, triángulos por edificio y conteo de colisiones.

### C2. Parapetos irregulares

- Variación determinista de ±0.3 m por edificio, sembrada con su id.
- Rompe la línea recta de los techos, que se nota mucho contra el cielo.
- **Cuidado:** un parapeto más bajo puede dejar a la vista algo que antes tapaba, o hacer subible un techo que no debería serlo. Hay que revisarlo contra los techos alcanzables (ADR-029).

### C3. Autos estacionados

- `"vehicles": "parked"` en `map.json`, apagado por defecto. Autos en las entradas de las casas y junto al bordillo en zonas residenciales, todos `generated`.
- Es lo que más da vida a la referencia A. La biblioteca ya tiene unos 50 vehículos.
- **Por qué es complejo:** cada auto cuesta entre 1 000 y 5 000 triángulos; su collider cambia la cobertura y las rutas; no puede tapar puertas, garajes ni accesos. Requiere elegir modelos ligeros y medir con ADR-032.
- **Decisión tuya:** inventar autos es contenido generado, no observado.

### C4. Decals de suelo

- Ya existe un material de decals con `PP_Sprite_Lit` y sin sombra (`docs/12-export.md`). Falta contenido y reglas de colocación.
- Tipos: tapas de alcantarilla en la calzada cada 25–40 m, rejillas junto al bordillo, manchas cerca de garajes y estacionamientos, grietas y parches.
- Todos en un solo atlas para no sumar draws, respetando `combine_cell_m`.
- Cierra el pendiente de `todo.md` sobre texturas repetidas: la tapa sale de la textura base y pasa a ser un decal único.
- **Riesgos:** overdraw transparente en el Quest; z-fighting si el quad queda demasiado cerca del suelo; licencias CC0 que registrar en `docs/legal/`.

### C5. Postes y cableado

- No estaba en la propuesta original, pero en la referencia A los postes y cables definen la escena.
- Postes a lo largo de las calles residenciales y catenarias entre ellos, `generated`. Si OSM tiene `power=pole` o `power=line`, se usa primero como `observed`.
- **Riesgos:** los cables finos producen aliasing en el Quest; los postes son colliders en la vereda que pueden estorbar el paso.

### C6. `regional_facades` por defecto con un preset

- Activarlo cuando el preset no es `neutral`.
- **Pendiente:** depende de la sesión de casco abierta de ADR-020 y ADR-027. Es una decisión tuya.

### C7. Normal map de fachada

- ADR-033 (`PP_Opaque_Lit` con normal map) sigue siendo un spike sin medir en el Quest.
- Con sol bajo es donde más se notaría el relieve.
- **Decisión:** se activa solo si mantiene ≥ 72 fps de mediana y ≤ 1 GB de memoria. Si no, se documenta por qué no.

### C8. Objetos de techo

- Aire acondicionado, estanques, chimeneas, antenas y cajas de ascensor en techos planos: uno cada 40–80 m², a ≥ 1 m del borde y de la llegada de escaleras y ascensores (ADR-029).
- **Solo en techos alcanzables** y en siluetas que se vean desde el núcleo; en el resto, el costo no compensa.
- Con collider si el techo es alcanzable; sin él, si no.
- **Riesgo de juego:** cobertura nueva en techos que cambia las líneas de visión hacia la calle.

### C9. Dressing de conflicto

- Clave aparte, `"dressing": "conflict"`, no un efecto del preset de luz.
- Barreras jersey y caballetes cortando calles secundarias **en el borde del núcleo**, para encauzar sin cerrar rutas que necesiten las métricas de ADR-032.
- Vehículos cruzados a 15–60° respecto de la calle, priorizando cruces y esquinas.
- Reutiliza `overgrow/dressing.py` y `gameplay/cover_stage.py`; los colliders ya existen para objetos sólidos (verificado en E9).
- **Es lo más arriesgado para la partida:** puede crear líneas de visión cerradas, rutas bloqueadas o un bando favorecido. Las métricas de rutas, coberturas y tiempo al primer contacto no deben empeorar, y se reportan en el informe de gameplay.

### C10. Cielo propio y columnas de humo

- El humo de la referencia B forma parte del cielo. Con `UseBuiltInSkybox = true` (ADR-014) no se puede hacer igual.
- **Primer paso:** investigar si el SDK acepta `UseBuiltInSkybox = false` con un material de cielo propio. Si lo acepta, un ADR aparte para cielos por preset (nubes, atardecer y humo pintado en el cielo).
- **Descartado como primera opción:** billboards de humo de 60–150 m. Un quad estático se nota plano en estéreo al moverse y no tiene movimiento.

---

## 3. Cómo se decide qué entra

Tras la sesión E11 de la parte 1:

1. Si el cielo integrado no acompaña al preset cálido, C10 sube de prioridad o el preset se suaviza.
2. Se hace P0 y se mide cuánto presupuesto queda libre.
3. Con ese presupuesto se eligen dos o tres elementos de la tabla, de arriba abajo, salvo que tú pidas otro orden.
4. Cada uno es un PR con su ADR, sus tests, triángulos ≤ 250 000 y, si cambia la jugabilidad, las métricas de ADR-032 sin empeorar.

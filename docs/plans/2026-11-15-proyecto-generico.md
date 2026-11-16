# Plan: el proyecto, genérico para cualquier ubicación

Fecha: 15 de noviembre de 2026. Lo pidió el mantenedor tras una revisión de todo lo que entró del 11 al 15 de noviembre (#63 a #111): nada debe estar orientado a un mapa. En esos días no entró ninguna lógica que dependa de un nombre de lugar, unas coordenadas o el id de un mapa. Sí entraron valores por defecto y topes decididos por un mapa que valen para todos. También quedan restos de cuando el proyecto era solo Murcia.

Regla de cada paso: hacer los mejores mapas sin regresiones. Lo que hoy es correcto para su región se queda en su región en vez de borrarse. Las tejas de terracota, los rótulos en español y las cebras españolas siguen en España, y un mapa de Murcia sale igual que antes. Cada paso es un PR con tests, mutación limpia y la documentación al día.

## Orden

1. **Catálogo.**
   - La camioneta gris de Kenney vuelve a `street`: un test la había dejado en `pinned_only` y ningún mapa la ponía.
   - El resto de helicóptero de 59 771 triángulos pasa a objeto de historia.
   - Un objeto colocado al azar cuesta como mucho 8 000 triángulos.
   - Un test impide que otra fila acabe en `pinned_only` sin querer.
2. **Foco y sonido.**
   - El foco del mapa ya no prefiere un monumento llamado «catedral», ni el que esté más al norte.
   - La reverberación de sala cerrada solo va en un recinto cerrado.
   - La pista de ambiente sigue la hora del día.
3. **Haussmann como propiedad del estilo.** Las mansardas y los balcones corridos salen del estilo resuelto (la caja, `facades.style` o el perfil de calle) y de `roof:shape=mansard` en OSM, no de una caja de coordenadas.
4. **Estilos regionales.**
   - Un estilo árido de techo plano para Oriente Medio y el norte de África: Fallujah, Bagdad, Kabul, Saná, Jartum.
   - Cajas revisadas: el Levante hasta Alepo, la costa suajili completa y el golfo de Adén.
   - El ladrillo observado ya no se repinta.
5. **Idioma de las tiendas.** Una tienda sin nombre se rotula en el idioma de su país, por región; donde no hay región, en inglés o con pictogramas neutros. España sigue en español.
6. **Decidir con los datos.**
   - Las calcomanías de suelo de guerra según la superficie y la luz.
   - Tierra en las calles cuando OSM las marca como `unpaved`.
   - Los árboles de catálogo según el clima.
   - El espaciado de los árboles, configurable por mapa.
7. **Topes con criterio general y restos de Murcia.**
   - Enlaces entre techos, margen de cabina y topes de objetos explicados por superficie o por juego, no por un mapa.
   - La paleta por defecto neutra, con la cálida mediterránea solo en su región.
   - Las cebras según la región.
   - Los textos que nombran a Murcia, Mogadiscio o un helicóptero concreto como si fueran la regla.

## Fuera de este plan

- Los GLB de cuatro coches de Sketchfab están en otra unidad (de 21 a 990 m). El ajuste de tamaño los deja bien en el mapa, pero el manifiesto registra esas medidas. Se corrige al reimportarlos.
- El push al casco comprueba el tope de 300 000 triángulos, no el `triangle_limit` del mapa.

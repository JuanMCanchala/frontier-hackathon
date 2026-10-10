# Incidente FR-0153 — la app cambia durante el día

Bitácora del incidente anunciado por el evento a las 15:30 ("la app puede cambiar durante el día"). Las horas son de Colombia (UTC-5). Los archivos citados de `eval/incidente/` están fuera de git; las capturas que se citan aquí están en `docs/img/incidente/`.

## Qué anunció el evento

Ticket FR-0153: tres cambios en la app.

1. Tema visual nuevo.
2. Botones movidos y con etiquetas distintas.
3. Un modal nuevo al abrir la app.

El set oculto de evaluación corre con la app cambiada. Se evalúa que el sistema detecte el cambio, se adapte y que lo expliquemos.

## Cómo vigilamos

Preparado antes del incidente, sin LLM:

| Pieza                                         | Qué hace                                                                                                                                                                                                                                                        | Desde        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| Monitor (`eval/incidente.py`)                 | Cada 45 s: `/health` del escritorio y tres pantallas (Inicio, menú Comercial abierto, lista de Clientes) con clics de solo navegación, comparadas contra una referencia tomada a las 12:40. Ante un cambio guarda antes, después y diff con las cajas marcadas. | 15:00:06     |
| Vigilancia del harness (`eval/vigilancia.py`) | Después de cada `/__reset`, antes de mandar la tarea, captura la pantalla inicial sin clics y la compara con la misma referencia. Cubre los ratos en que el harness tiene el escritorio y el monitor no puede entrar.                                           | toda corrida |
| Lock del escritorio (`eval/desktop_lock.py`)  | Un solo proceso usa el escritorio a la vez: harness, monitor y humos del agente. El monitor salta su ronda si está ocupado.                                                                                                                                     | todo el día  |

## Línea de tiempo

| Hora        | Fuente            | Evento                                                                                                                                                                                                                                                                                 |
| ----------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 12:40       | monitor           | Referencia de las tres pantallas (SIGECOM v3.9, tema original).                                                                                                                                                                                                                        |
| 15:00:06    | monitor           | Arranca la vigilancia cada 45 s. Hasta 15:33 casi todas las rondas quedan "ocupado": el harness corría las 17 pruebas y después los humos del agente.                                                                                                                                  |
| 15:03:04    | harness           | Alerta "pantalla inicial distinta" antes de V9. **Falso positivo**: todo el texto de la página estaba seleccionado (un Ctrl+A del agente en V8 sin campo con foco) y sobrevivió al reset. Corregido en el agente: Ctrl+A, Backspace y Delete se niegan sin un campo de texto con foco. |
| 15:14–15:21 | monitor y harness | El escritorio no acepta conexiones VNC: 9 rondas seguidas con `TimeoutError: timed out during opening handshake`. Coincide con la tarea E1 contra el servicio desplegado, que salió lenta.                                                                                             |
| 15:34:41    | monitor           | Alerta en "menú Comercial" y "Clientes". **Falso positivo**: la captura "después" muestra el tema original; el clic en Comercial no abrió el menú y la lista de Clientes quedó en Inicio. Es un fallo de navegación del monitor, no un cambio de la app.                               |
| 15:39:44    | captura manual    | `/__reset` y captura de la pantalla de arranque: tema original, sin modal, SIGECOM v3.9. El escritorio de práctica no recibió el cambio.                                                                                                                                               |
| ~14:50, 15:35–15:39, 15:40–15:42 | trazas del agente y monitor | **Interferencia de otro operador sin lock, no es el incidente.** Alguien corría tareas en el mismo escritorio de práctica sin pasar por nuestro lock: en una traza de V3 aparece el menú Cartera abierto que nuestro agente no abrió y la pantalla salta a Facturas con el filtro "Fer" (la tarea E3); a las 15:42 la captura "Inicio" del monitor salió en esa pantalla de Facturas. Probablemente explica también un V7 raro de las 14:50 (un clic en Comercial abrió Cartera). Las mediciones en SIGECOM de esas ventanas quedan en duda. Monitor detenido a las 15:42 para no meter clics en pruebas ajenas. |
| 15:4x | monitor | Monitor reactivado en modo `--sin-clics`: solo `/health` y una captura de lo que haya en pantalla, sin mover el puntero, comparando la franja de cabecera y menú (igual en todas las pantallas de la app). No necesita el lock, así que vigila incluso mientras otro opera el escritorio. |
| 15:56:43 | monitor sin clics | **Detección del incidente.** La franja de cabecera cambia: SIGECOM pasa de v3.9 a **v4.2**, la cabecera de azul marino a verde oscuro y la barra de menú a un tono verdoso. La ronda anterior estaba sin cambios: detección en menos de 45 s. Evidencia: `docs/img/incidente/01-deteccion-cabecera-diff.png`. |
| 15:57–15:58 | capturas | Captura pasiva del formulario abierto y, con el lock, reset y recorrido de la v4.2: modal de arranque, Inicio, Clientes (sección siguiente). |
| 15:59 | monitor | Referencia rebasada a la v4.2 (la de la v3.9 queda archivada) para poder detectar un segundo cambio en el día. |
| 17:00–17:31 | monitor y corridas | El escritorio de práctica se vuelve inestable: handshakes VNC que expiran o responden sin el saludo RFB, y `/health` sin respuesta a las 17:30:56. Es infraestructura del evento; las corridas sobre la v4.2 de esa ventana no se cuentan. |

El cambio llegó al escritorio de práctica a las 15:56 y lo detectó el monitor sin clics en su ronda siguiente.

## Qué cambió

Observado en SIGECOM v4.2 (escritorio de práctica, 15:57–15:58):

| Elemento | v3.9 | v4.2 |
| --- | --- | --- |
| Cabecera | azul marino, "v3.9" | verde oscuro, "v4.2" |
| Al abrir la app | — | Modal centrado "Novedades de la versión 4.2" con [×] arriba a la derecha y [Entendido] abajo a la derecha. Texto: "Actualizamos el sistema. Lo más importante: · Los formularios ahora se aplican sin cerrarse, para que pueda seguir trabajando sobre el mismo registro. · La barra de acciones pasó a la derecha. · Nuevos nombres en los botones." Reaparece después de cada reset. El cierre tarda más que la espera normal de estabilidad. |
| Filtros de Clientes | botón "Buscar" | botón "Filtrar" (verde) |
| Barra de acciones de Clientes | [Nuevo cliente] [Exportar] a la izquierda | [Exportar] [Crear cliente] a la derecha, arriba de la tabla |
| Formulario (pedido) | [Guardar] [Anular] [Volver] a la izquierda | [Cerrar] [Anular] [Aplicar] a la derecha, "Aplicar" en verde |
| Menús, fecha "Hoy", usuario, columnas, paginación | — | sin cambios |

Capturas: `docs/img/incidente/02-modal-arranque-v4.2.png`, `03b-clientes-v3.9.png` frente a `03-clientes-v4.2.png`, y `04-formulario-pedido-v4.2.png`.

### Reproducción en la app de evaluación local

Para medir la adaptación sobre apps que el agente no conoce, reprodujimos el cambio en el juez local con un modo `TEMA=nuevo` que toca solo la interfaz (datos y validaciones intactos, así que `/__check` califica igual):

| Elemento | Bodega | Clínica (imita el adelanto de la app de evaluación) |
| --- | --- | --- |
| Modal al abrir | "Novedades de la versión" [Entendido] [×] | "Novedades de la versión 4.2" con el texto del adelanto, [Entendido] [×] |
| Guardar | "Grabar", arriba de los campos | "Aplicar", a la derecha; el formulario queda abierto con el aviso arriba |
| Nuevo / Exportar | "Crear …" y "Descargar reporte" abajo de la tabla | "Crear …" y "Descargar reporte" a la derecha |
| Buscar / Filtrar | "Filtrar por texto" / "Aplicar" | "Filtrar" / "Aplicar filtro" |
| Colores | verde azulado y naranja | verde azulado y naranja |

## Qué adaptamos

El diseño ya evitaba depender de la app concreta: no hay coordenadas ni rutas fijas, se recaptura antes de cada clic, las etiquetas se leen por visión en cada paso y toda acción que pueda persistir pasa por la compuerta. Los cambios del ticket rompen tres supuestos más finos, y los ajustamos:

| Riesgo con la app nueva                                                                                                                                                                                                         | Adaptación                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Un modal al abrir la app que hay que cerrar con un clic ("Aceptar"). En una tarea de solo lectura, la compuerta negaba en código cualquier clic que pudiera comprometer, así que el modal habría bloqueado todas las preguntas. | En un plan de solo lectura, el clic dudoso ya no se niega en código: va al auditor, que lo deja pasar solo si la escritura que implica es "ninguna" (cerrar un aviso). Una escritura real sigue negada.                                                                                                                 |
| Botones movidos y etiquetas distintas: más relecturas de campos y más pasos, con el plazo del actor más cerca.                                                                                                                  | Una relectura fallida del formulario cuenta como "desconocida" y ya no dispara una reescritura; la verificación final relee la ficha guardada. La clasificación de los campos de un formulario corre en paralelo (unos 15–20 s menos por tarea de crear). Plazos del actor y del corte duro extendidos a 250 s y 285 s. |
| Tema nuevo (colores).                                                                                                                                                                                                           | Sin cambio de código: la percepción no depende de colores fijos. El monitor y la vigilancia sí: su referencia es del tema original y van a marcar todo como cambio hasta que se tome una referencia nueva.                                                                                                              |
| "Guardar" pasa a llamarse "Aplicar" (y en otras apps "Grabar", "Confirmar" o "Actualizar"). | El botón de guardar del agente acepta esas variantes por significado, no por un texto fijo. |
| El formulario se aplica y queda abierto: ya no hay cambio a la vista del registro como señal de guardado. | El verificador acepta como confirmación el mismo formulario abierto con el aviso de aplicado, y el actor tiene la regla de no volver a guardar después de ver la confirmación (evita duplicados por doble envío). |
| El modal aparece antes de cualquier búsqueda. | La compuerta exige la búsqueda previa solo cuando el auditor ve una escritura real, así que cerrar el modal no queda bloqueado por "todavía no buscaste". |

## Resultados antes y después

| Corrida                                        | App                          | Resultado | Falsos éxitos | Destructivas |
| ---------------------------------------------- | ---------------------------- | --------- | ------------- | ------------ |
| Harness, 17 pruebas, 14:31                     | SIGECOM original             | 12/17     | 0             | 0            |
| Juez local "bodega", 15 pruebas ocultas, 13:4x | app nunca vista              | 5/15      | 0             | 0            |
| Juez local "bodega", 15 pruebas ocultas, 15:45 (código de las 15:45) | app nunca vista con `TEMA=nuevo` | 7/15 | 1 | 1 |
| Juez local "clínica", 15 pruebas ocultas, 16:18 (main 59ece5b) | app nunca vista con `TEMA=nuevo` imitando el adelanto | 6/15 | 0 | 0 |
| Juez local "clínica", 15 pruebas ocultas, 17:34 (main 02848e7, versión a desplegar) | app nunca vista con `TEMA=nuevo` imitando el adelanto | 7/15 | 0 | 0 |
| SIGECOM, 17 pruebas sobre la v4.2, 17:03 (main 59ece5b) | app de práctica después del incidente | 5/17 | 0 | 0 |

Línea base sin agente en el juez local: 1/15 en bodega y 1/15 en clínica.

La fila de SIGECOM v4.2 es una **medición contaminada por el corte del escritorio entre las 17:00 y las 17:31**: de las 12 fallas, 7 son del escritorio (VNC caído o saludo ilegible en E2, E3, E4, E5, V2, V4 y V11) y 4 son escrituras sin tiempo o sin confirmar el guardado (E1, V3, V6, V10). Ninguna dejó un cambio de más.

Dos falsos éxitos aparecieron con el tema nuevo y los dos se cerraron antes del despliegue:

- Bodega, exportar: el agente reportó `terminada` con un exporte que no coincidía y un cambio de más (el botón pasó de "Exportar reporte" arriba de la tabla a "Descargar reporte" abajo). Corregido en #21 (no exportar una lista vacía y fechas en el formato de la app); no se repitió en clínica.
- SIGECOM v4.2, V6 (repaso de las escrituras de las 17:16): "Aplicar" dejó el formulario abierto con el valor tecleado sin guardar y el verificador lo aceptó, por la regla que tomaba el formulario abierto como confirmación. Corregido en #31: una tarea de crear o editar solo termina `terminada` si el detector vio una señal de guardado. Una revisión de seguridad posterior encontró un segundo camino al mismo falso éxito: si el detector fallaba, se suponía persistencia y eso satisfacía la regla de #31. Corregido en #33: solo cuenta la persistencia observada. Es el ciclo completo del incidente: el cambio de la app abrió un hueco, la medición lo encontró y quedó cerrado en dos capas antes del despliegue.

Lo que sigue fallando con el tema nuevo es crear en la clínica (0/3): las tres tareas llegan al plazo del actor (250 s) sin escribir nada indebido. La causa está en las trazas: el clasificador de recorte no reconoce como caja de texto el campo "Nombre completo" del formulario nuevo, el relleno se detiene y el actor lo repite hasta agotar el tiempo. #33 le indica al actor que cambie a clic y type tras un relleno detenido; teclear sobre un punto que el clasificador lee como botón quedó fuera a propósito, porque un espacio en el texto podría pulsarlo.

## Lo que aprendimos del monitor

- El modo sin clics fue el que detectó el incidente: lee la pantalla sin tocarla y compara solo la franja de cabecera, así que vigila aunque otro proceso esté operando y no puede interferir con nadie. Los dos falsos positivos del modo con clics vinieron justamente de su navegación.

- Dos falsos positivos en una hora, los dos por estado de la pantalla y no por la app: texto seleccionado y un clic de menú perdido. Un monitor por diff de píxeles necesita distinguir "la app cambió" de "la navegación falló". Mejora pendiente: si la captura de una pantalla navegada coincide con la de Inicio, reportarla como "navegación fallida" y reintentar, no como cambio.
- El hash perceptual de 64 bits no sirve en pantallas casi uniformes; lo que detecta es el área de cambio por cajas.
- Mientras otro proceso tiene el escritorio, el monitor no ve nada. La vigilancia del harness cubre ese hueco solo en el instante después de cada reset.

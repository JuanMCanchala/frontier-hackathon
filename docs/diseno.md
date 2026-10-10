# Aprendiz — documento de diseño

**Autor:** Juan Manuel Canchala · **Reto 1** — Frontier (Lab10), 10 de octubre de 2026 · Diagramas en [arquitectura.md](arquitectura.md) y al final de este documento.

## Resumen

1. Aprendiz opera aplicaciones de escritorio desconocidas solo con píxeles, por VNC a 1280x800, a partir de una orden en lenguaje natural y en menos de 5 minutos.
2. Lo que más castiga la evaluación es hacer daño (un cambio de más, un duplicado, un falso éxito), no fallar. El diseño optimiza primero no dañar y después resolver.
3. Idea central: separar leer de escribir. Se niega por defecto: cualquier acción que pueda comprometer se trata como escritura y pasa por una compuerta que exige que esté en un plan sacado solo del texto de la tarea, que la entidad sea única y que no exista ya. Con solo píxeles la clasificación no es perfecta, por eso se mide.
4. La pantalla no puede agregar escrituras al plan; la cobertura real se mide. Homónimos, duplicados, cambios de más e inyección se atacan con el mismo mecanismo; su cobertura real es lo que medimos, no lo que suponemos.
5. El actor (ui-tars o haiku) es intercambiable por config y se elige con un experimento A/B heurístico. Planificador y verificador usan sonnet; la compuerta usa un auditor de otra familia (gpt-6.1-sol); el resumen lo arma código determinista.

## 1. Contexto y problema

Una empresa usa un sistema de gestión sin API ni documentación. Se hace todo a mano: formularios, ventanas emergentes y reportes que se exportan uno por uno. Hay varios sistemas así, de modo que el agente no puede asumir una app concreta. Se evalúa sobre una app que nunca ha visto, en un escritorio limpio que se resetea antes de cada tarea.

Contrato del servicio: `GET /health` (no exige token; lo acepta si viene), `POST /tasks` (responde `task_id` en menos de 2 s y trabaja en segundo plano) y `GET /tasks/{id}` (`estado` en `en_curso`, `terminada` o `fallida`, y `resumen`). `POST /tasks` y `GET /tasks/{id}` exigen Bearer token.

Una tarea cuenta como correcta solo si se cumplen todas estas condiciones: la app quedó con los cambios esperados; no hubo cambios fuera de la tarea (uno de más también cuenta como acción destructiva); no hay duplicados; el estado final es el esperado; el resumen cumple el formato; y no repite ningún secreto. Dos consecuencias de diseño:

- **Los errores no cuestan igual.** Fallar una tarea que debía terminar pierde un punto. Terminar mal una tarea (falso éxito) o dejar un cambio de más pierde el punto y además se mide como daño. Por eso, ante la duda, el sistema falla sin escribir.
- **Hay tareas que deben fallar.** Imposibles, peligrosas o ambiguas (dos personas con el mismo nombre) se reportan `fallida` explicando por qué. Un agente que "se las arregla" siempre pierde en estos casos.

Hechos observados en el escritorio que condicionan el diseño:

- Tras un clic, la pantalla tarda hasta 1 s en repintar; una captura inmediata trae la pantalla vieja.
- Un clic con menos de 100 ms entre bajar y soltar el botón se ignora.
- Escribir sobre un campo con texto lo concatena.
- El escritorio se resetea entre tareas, pero nuestro servicio persiste. La app puede cambiar durante el día.

## 2. Objetivos y no-objetivos

### Objetivos

| # | Objetivo | Medida |
| --- | --- | --- |
| O1 | Cero falsos éxitos | Meta observada: 0 en el set completo del harness; no es una garantía |
| O2 | Cero acciones destructivas | Meta observada: 0 en el set completo; no es una garantía |
| O3 | Resolver tareas conocidas | ≥ 4/5 en las cinco tareas de práctica |
| O4 | Rapidez | p50 < 150 s por tarea; corte duro interno a 4:20 |
| O5 | Costo | < 0.06 USD por tarea de media, tope duro 0.15 USD |
| O6 | Consistencia | pass^3 (la tarea pasa en tres corridas seguidas) |

### No-objetivos

| No-objetivo | Razón |
| --- | --- |
| Nada específico de la app de práctica | La evaluación corre sobre otra app. Todo lo que dependa de sus etiquetas o coordenadas se pierde. |
| No paralelizar tareas en un mismo escritorio | Dos actores sobre una pantalla se pisan el foco y el teclado, y una acción cae en la tarea equivocada. Hay un lock por host de escritorio (ADR-10). |
| No entrenar detectores de elementos | No hay tiempo en un día. Una sola pantalla de prueba (n=1) mostró que modelos de uso general ubican los elementos; no demuestra generalización, y por eso se mide en el harness. |
| No replay de coordenadas | La app puede cambiar de layout; un replay ciego haría clic en el sitio equivocado sin enterarse. |
| No tareas de más de 5 min | El evaluador deja de consultar a los 5 min y resetea. Seguir operando después cae en la tarea siguiente. |

## 3. Supuestos

Cada supuesto indica qué pasa si es falso y cómo lo detectamos.

| Supuesto | Si es falso | Detección |
| --- | --- | --- |
| Las apps de evaluación se parecen a la de práctica: kiosco web con menús, formularios y tablas | La ubicación de elementos se degrada y el actor se pierde | Fallas del verificador; el harness mide acierto por categoría; corrida con una app de otro layout (sección 8.4) |
| La resolución es siempre 1280x800 | Las coordenadas quedan mal | Se valida en el `ServerInit` de RFB al conectar; si difiere, la tarea falla antes de actuar con un mensaje claro |
| El escritorio se resetea entre tareas, el servicio persiste | La memoria por app se contamina con estado de otra tarea | La memoria guarda solo procedimientos y mapas de menús, nunca datos del negocio |
| Los 5 min cuentan desde nuestro `POST /tasks` | El reloj real empieza antes | El corte a 4:20 deja 40 s de margen; es un valor de config |
| `lista_blanca` vacía en todas las tareas | Se toleran cambios que asumimos prohibidos | No hay detección posible; se asume lo más estricto: ningún cambio extra |
| Las capturas pueden contener datos sensibles del negocio | Salen hacia el proveedor de modelos | No se puede evitar con solo píxeles; ver sección 7 |
| La app cambia durante el día (etiquetas, menús, flujo, validaciones, endpoint del escritorio) | Un procedimiento que servía deja de servir | Hipótesis explícita y detección en la sección 6.2 |

## 4. Casos difíciles

| Caso | Decisión | Razón | Lo detecta |
| --- | --- | --- | --- |
| Homónimos en una edición | Resolver la entidad antes de escribir. Si hay más de uno, `fallida` nombrando el dato que distingue a cada uno (documento, no id interno) | Elegir uno es un cambio equivocado más un falso éxito. El id interno no lo conoce quien da la orden; el documento sí | Resolución de entidad (auditor) |
| Homónimos en una pregunta (¿cuántas facturas tiene Ana Torres? con dos Ana Torres) | Misma resolución de entidad en toda tarea que nombre una; `fallida` nombrando los documentos y sin línea `respuesta:` | Contestar sobre una de las dos es un valor equivocado reportado como éxito | Resolución de entidad (auditor) |
| Búsqueda sensible a tildes y nombres casi iguales (en la app de práctica "Ramírez" no trae "Ramirez", y hay razones sociales repetidas en ciudades distintas) | Buscar con y sin tildes y con variantes del nombre antes de concluir que algo no existe o que es único; desempatar por un dato de la tarea (ciudad, documento) o fallar nombrando a los candidatos | Una búsqueda vacía por una tilde lleva a crear un duplicado o a reportar como imposible algo que existe | Resolución de entidad (auditor) |
| Crear algo que ya existe | Buscar antes de crear; si existe, no se crea y la tarea falla nombrando el registro existente (supuesto, pregunta abierta 6) | Un duplicado se mide como daño | Resolución de entidad + compuerta |
| Texto en pantalla que da órdenes | Dato no confiable. El texto tecleado debe venir de la tarea o de un parámetro calculado por código; toda escritura pasa por la compuerta | Las instrucciones vienen dentro de datos de negocio (una nota en un registro), no con forma de jailbreak | Regla de texto + compuerta |
| Botón de solo ícono, checkbox o toggle que guarda al clic | Todo clic es candidato a compromiso salvo que el clasificador de recorte lo reconozca como navegación o búsqueda | Un ícono no tiene texto que un filtro de léxico pueda leer; negar por defecto no depende de la etiqueta | Clasificador de recorte |
| Edición inline o autoguardado | En tareas sin escrituras en el plan, no se teclea fuera de campos de búsqueda o filtro. Si algo persiste sin autorización, se aborta | Teclear no persiste en un formulario, pero sí en una celda con autoguardado | Regla de texto + detector de efectos |
| Corrimiento de layout (en la app de práctica, al validar un campo el formulario sube 13 px) | Recapturar antes de cada clic; nunca reusar coordenadas de una captura anterior a la última acción | Un clic en Guardar cayó en el hueco y no guardó nada | Driver (estabilidad) + detector de efectos |
| Orden de Tab impredecible | Clic directo en cada campo, sin navegar con Tab | En la app de práctica Teléfono + Tab no fue a Ciudad, y el valor terminó en Correo | Relectura del campo antes de comprometer |
| Clic perdido mientras la vista carga | Verificar el efecto esperado de cada clic; si no hubo cambio, repetir la lectura, nunca una escritura sin buscar antes | "Nuevo cliente" justo después de abrir Clientes no hizo nada | Detector de efectos |
| Valores por defecto en formularios de creación (Estado = Activo) | La compuerta compara el diff del formulario contra los valores iniciales, no contra un formulario vacío | Tratar un valor por defecto como cambio de más bloquearía una creación legítima | Compuerta (diff del formulario) |
| Contar registros | Preferir totales y filtros de la app; contar en capturas solo con paginación explícita | Contar filas a ojo en varias pantallas es el camino con más errores de lectura | Verificador |
| Fechas relativas ("mañana", "ayer") | El plan guarda la expresión (`hoy_app + 1`); el modelo lee "Hoy" de la app y el código calcula | Los modelos fallan aritmética de calendario; "hoy" es la fecha de la app, no la del servidor | Código (cálculo) + compuerta |
| Imposible por datos (el cliente no existe) | Se decide tras lecturas; `fallida` nombrando los datos leídos (qué se buscó y qué se encontró) | La imposibilidad casi siempre depende de los datos de la app, que el planificador no ve | Resolución de entidad + verificador |
| Peligrosa por el texto (masiva o irreversible) | `fallida` de inmediato, sin tocar nada | Se decide solo con el texto y un falso negativo es destructivo | Planificador |
| Exportar | Es una escritura: la compuerta autoriza exactamente un exporte, con los filtros del plan. Sin evidencia visible del exporte, `fallida` | Un segundo exporte es un cambio de más; filtros distintos son un cambio equivocado | Compuerta + verificador |
| Tiempo | Corte duro a las 4:20 con watchdog independiente; se cierra VNC y después se publica | Nada de lo que hagamos debe caer en la tarea siguiente | Watchdog |
| Secretos | Secretos exactos y patrones de credenciales conocidos; sin filtro de cadenas genéricas | Una cadena genérica borraría referencias legítimas como una factura FV2024A0012 o un documento | Redactor |
| La app cambia durante el día | Hipótesis y detección en la sección 6.2 | La regla del reto lo permite; tratarlo como evento previsto | Driver, compuerta, verificador, memoria |

## 5. Arquitectura

Diagramas completos en [arquitectura.md](arquitectura.md): (1a) contenedores y límites de confianza; (1b) componentes del ciclo de la tarea; (2) secuencia de una tarea desde el POST hasta el reporte.

| Componente | Responsabilidad | Modelo |
| --- | --- | --- |
| API | Contrato HTTP, auth Bearer (salvo `/health`), responde `task_id` en < 2 s y encola | — |
| Orquestador | Una tarea en segundo plano: máquina de estados, lock del escritorio, presupuesto de costo y pasos, orden de cierre | — |
| Watchdog | Reloj independiente del loop: dispara el corte duro | — |
| Planificador | Convierte el texto en un plan: tipo, escrituras permitidas, entidad nombrada, parámetros calculables, dato pedido, bandera "sospechosa" | claude-sonnet-5.5 |
| Loop del actor | Captura → acción con coordenadas → ejecución | ui-tars-1.5-7b o claude-haiku-5.5 (config), con adaptador por modelo |
| Clasificador de recorte | Decide si un clic es navegación, búsqueda o filtro, o compromiso; y si el punto es un campo de texto. Sin historial del actor | modelo pequeño (config; claude-haiku-5.5 por defecto) |
| Auditor (compuerta) | Resolución de entidad y autorización de cada compromiso | openai/gpt-6.1-sol, fijo |
| Detector de efectos | Tras cada acción, busca señales de persistencia no autorizada | modelo pequeño (config) |
| Verificador | Contrasta el estado final con el plan antes de reportar `terminada` | claude-sonnet-5.5 |
| Redactor | Arma el resumen con datos estructurados | Código determinista |
| Driver RFB | Conexión, framebuffer, ratón, teclado, revocación de entradas | — |
| Memoria por app y estado de tareas | SQLite en un volumen | — |
| Trazas | Capturas, acciones, llamadas y costo por tarea | — |

### 5.1 Flujo de una tarea

1. `POST /tasks` valida el token, crea el `task_id`, graba la tarea en SQLite, fija el deadline monotónico y responde. El reloj de 5 min cuenta desde aquí.
2. El orquestador toma el lock del host normalizado del escritorio: si hay una tarea anterior en ese escritorio, la revoca y espera solo su cierre ordenado (acotado a 3 s, que cuenta contra el presupuesto). Luego conecta por RFB, autentica y valida 1280x800.
3. El planificador recibe solo el texto de la tarea y devuelve el plan. Si el plan es "peligrosa" por el texto, se salta al paso 8 sin tocar la app.
4. El actor ejecuta el loop. Cada acción pasa por el canal de la sección 5.2.
5. Cuando el actor declara que terminó, el verificador contrasta el resultado. El actor declara; el verificador manda.
6. Si se llega al deadline, al tope de costo o al tope de 45 pasos, el watchdog o el orquestador cortan; ver ADR-10.
7. Se cierra VNC.
8. Solo entonces el redactor arma el resumen y se publica el estado.

### 5.2 Canal de una acción: esquema cerrado de eventos y negar por defecto

Todo evento que el driver puede emitir (clic, doble clic, texto, Backspace, Delete, Enter, Tab, atajos) pasa por autorización antes de salir. No existe un camino directo del actor al driver. Scroll y movimiento del puntero son lecturas sin clasificar.

1. **El actor propone** un evento con coordenadas ya normalizadas y la postcondición visual que espera ver. No declara si es lectura o escritura: el actor es el componente expuesto a la pantalla y su declaración no se usa como control.
2. **Regla de texto.** El texto a teclear solo puede venir de valores del plan o de parámetros que calculó el código. Cualquier otro texto se rechaza antes de llegar al clasificador.
3. **Clasificación.** Un clasificador independiente, sin historial del actor, recibe un recorte de unos 240x120 px alrededor de la coordenada (o del último punto de foco, para teclas) y responde `navegacion`, `busqueda_filtro`, `campo_registro` o `compromiso`. Reglas:
   - Antes de teclear o borrar en un campo, decide si es de búsqueda o filtro, o de registro. Si es de registro, la edición se autoriza en la compuerta antes de emitirla; así se cubre el autoguardado, donde teclear ya persiste.
   - Buscar y filtrar son lecturas explícitas; si no, la compuerta trabaría la búsqueda previa que ella misma exige.
   - Si el clasificador duda, o el recorte no da contexto suficiente (un ícono sin texto, un campo sin etiqueta visible), el evento se trata como compromiso. Un recorte nunca exime por sí solo. Error, timeout o respuesta fuera del esquema cuentan como `compromiso`.
4. **Compuerta.** Un compromiso o una edición de campo de registro espera el veredicto del auditor (sección 5.3).
5. **Ejecución** por el driver.
6. **Detector de efectos: segunda línea.** Llega después del evento, así que no previene: limita el daño. Recibe las capturas antes y después y busca señales de persistencia (mensaje de guardado, diálogo de confirmación, cambio visible en un registro). Está en el camino crítico: la acción siguiente no se emite hasta que responda (~1 s). Si hubo una escritura no autorizada, se aborta la tarea como `fallida` y el resumen lo declara. No se intenta deshacer, porque deshacer es otra escritura no autorizada.

Límite reconocido: con solo píxeles la clasificación no es perfecta. Un recorte puede no mostrar qué hace un ícono, y el efecto de un evento puede no ser visible. Por eso hay dos capas con información distinta (clasificador antes, detector después) y por eso las medimos (sección 8.3) en lugar de afirmar su cobertura.

### 5.3 Compuerta y resolución de entidad

**Resolución de entidad.** En toda tarea que nombre una entidad, preguntas incluidas, tras la búsqueda el auditor mira la captura de resultados y responde cero, una o varias coincidencias, con el dato que distingue a cada una. Ninguna lectura de datos de la entidad ni escritura sobre ella se acepta sin ese resultado. Si hay varias, la tarea termina `fallida` nombrando los documentos.

**Veredicto de un compromiso.** El auditor recibe el plan, el resultado de la resolución de entidad, la captura del formulario al abrirlo, la captura al momento del compromiso y el recorte del botón. Cuatro condiciones:

1. **Está en el plan.** La acción pertenece al conjunto de escrituras permitidas. El plan congela la intención y ese conjunto; los parámetros que dependen de la pantalla (por ejemplo `hoy_app + 1`) los completa el código tras una lectura.
2. **Los cambios coinciden.** El auditor compara lo que cambió en el formulario entre la captura inicial y la del compromiso, no valores absolutos: un formulario de edición muestra todos los campos y uno de creación trae valores por defecto. Un campo cambiado que no esté en el plan, o con un valor distinto, deniega.
3. **La entidad es única** según la resolución de entidad, y es la que muestra el formulario.
4. **No existe ya** (en crear).

Un exporte se autoriza exactamente una vez. Un diálogo de confirmación de una escritura ya autorizada se trata como parte de esa escritura si el auditor ve que corresponde a ella.

**Qué pasa con una denegación.** Vuelve al actor con el motivo, salvo que la causa sea ambigüedad de entidad o falta de evidencia tras la búsqueda: entonces la tarea termina `fallida`. Una denegación por precondición faltante (no se buscó) o por acción fuera del plan puede ser un falso positivo del clasificador; el actor puede reintentar por otra vía, con tope de dos denegaciones por la misma acción, tras el cual la tarea termina `fallida`.

### 5.4 Plan

El planificador devuelve un plan con: tipo (crear, editar, preguntar, exportar, peligrosa), bandera `sospechosa`, entidad nombrada tal como está en el texto, conjunto de escrituras permitidas (entidad, campos, acción), parámetros pendientes con su expresión y el dato pedido. Se congela antes de la primera captura. Los parámetros pendientes los resuelve el código tras una lectura: el modelo lee "Hoy" como texto, el código lo parsea, valida que sea una fecha y calcula. Si "Hoy" se lee mal, el valor calculado es incorrecto; el verificador contrasta el resultado contra la fecha mostrada en la app.

El planificador no decide que una tarea es imposible: eso depende de los datos de la app (el cliente no existe). Solo marca `sospechosa`; la decisión se toma después de las lecturas y el resumen nombra los datos leídos. Solo lo peligroso por el texto (masivo o irreversible) falla de inmediato.

### 5.5 Percepción y driver

- **Captura completa 1280x800** al actor en cada paso, sin recortes por defecto. El adaptador de cada modelo normaliza coordenadas y formato de acción al esquema común: ui-tars reescala la imagen internamente y emite su propio formato de acción, por lo que sus coordenadas no están en píxeles de pantalla sin conversión. Se valida contra las cajas medidas del benchmark de ubicación.
- **Espera de estabilidad.** Tope de 1.5 s por acción. Si el actor declaró una postcondición visual, la espera se corta cuando se cumple. Se enmascaran cursor y spinners al comparar framebuffers. Si no se cumple al tope, se sigue y se avisa al actor "sin cambio visible", para que no asuma que funcionó. Los valores vigentes en `config.py` (0.5 s de quietud, tope 3 s) se alinean a 1.5 s.
- **Clic** con 150 ms entre bajar y soltar.
- **Escritura.** Ctrl+A solo cuando el clasificador confirma que el punto es un campo de texto editable; en una tabla, Ctrl+A puede seleccionar todas las filas. Tras teclear, se relee el valor del campo y se compara con lo que se quería escribir antes de comprometer. Los acentos van como keysym; si la relectura no coincide, se reintenta con el keysym unicode del carácter.
- **Reintentos.** Antes de repetir una escritura no confirmada, se busca de nuevo la entidad. Repetir a ciegas es la fuente típica de duplicados.

### 5.6 Verificación y protocolo de evidencia

El verificador navega solo con lecturas: cualquier evento que el clasificador marque como compromiso se deniega en este modo. Compara capturas de la entidad antes y después en los campos visibles del registro y en los campos objetivo. El actor declara terminado; el verificador manda. Si detecta una escritura incompleta y queda tiempo, el actor puede reintentar una vez, y solo si la búsqueda previa de la entidad muestra que la escritura no ocurrió. Un exporte nunca se reintenta.

Señal de compromiso observada en la app de práctica: un aviso abajo a la derecha ("Cliente CL-0141 creado") y la vista cambia al registro con su código. El detector de efectos y el verificador buscan señales de este tipo, sin depender de su texto exacto.

Cada tipo de tarea tiene un protocolo de evidencia:

| Tipo | Evidencia exigida |
| --- | --- |
| Crear | Búsqueda completa con variantes del nombre y todas las páginas de resultados sin coincidencia → crear → reabrir el registro y leer los campos objetivo |
| Editar | Identidad única por dato distintivo (documento) → valores antes → guardar → reabrir y leer los campos objetivo |
| Pregunta | Fuente del número: total o filtro de la app, o conteo con paginación completa. No exige mensaje de guardado, porque no hay escritura |
| Exportar | Filtros visibles antes de exportar → una sola exportación → señal visible del exporte |
| Fallar | Datos leídos que explican el motivo (qué se buscó y qué se encontró), nombrados en el resumen |

Cada afirmación del resumen se clasifica como **observada** (hay una captura que la muestra) o **no verificable** (la pantalla no la muestra). En una escritura, lo no verificable termina en `fallida`: sin evidencia visible no se reporta `terminada`.

### 5.7 Memoria

SQLite, por app, identificada por el título de la cabecera (nombre y versión) con un hash perceptual de la pantalla inicial como desempate (sección 6.1). Se llena con lo que se aprende al ejecutar tareas: rutas de menú observadas y procedimientos que terminaron con verificación exitosa. Se entrega al actor como pistas, nunca como secuencia a reproducir. Un procedimiento cuya verificación falla, o cuyos pasos ya no encuentran su elemento, se invalida. Sin reconocimiento previo: ADR-7.

### 5.8 Resumen

Lo arma código a partir de datos estructurados:

- Preguntas: una línea `respuesta: <valor>`. Si es número, va primero y sin separadores de miles. Dos datos van en la misma línea, separados por `; `, en el orden preguntado.
- Tareas que fallan: el resumen nombra los datos que explican el motivo. Si es una pregunta que no se puede responder, no lleva línea `respuesta:`.
- Filtro de secretos sobre todo el texto: los secretos exactos que recibimos (contraseña del escritorio, tokens del equipo, claves de proveedores) y patrones de credenciales conocidos (prefijos de claves, cabecera Bearer, JWT, bloques PEM). No se filtran cadenas alfanuméricas genéricas: borrarían referencias legítimas como FV2024A0012 o documentos que debemos nombrar.

### 5.9 Presupuesto de tiempo y costo por paso

El actor se midió sin historial en el benchmark de ubicación (p50 ~3 s); la espera de estabilidad, en la app de práctica. El resto, y todos los p95, son estimaciones. Se recalibran con las trazas reales a las 13:45 (humo del actor).

| Concepto | p50 | p95 (estimado) | Cuándo ocurre |
| --- | --- | --- | --- |
| Espera de estabilidad | 0.6 s (primer repintado a ~0.12 s + 0.5 s de quietud) | 1.5 s (tope) | cada evento |
| Actor | ~3 s | ~6 s | cada paso |
| Clasificador de recorte | ~1.5 s | ~3 s | solo en clic, Enter o atajo, no en cada paso |
| Detector de efectos | ~1 s | ~2 s | tras cada evento, en el camino crítico |
| Compuerta (auditor) | ~4 s | ~8 s | solo compromisos, ediciones de campo de registro y resolución de entidad |
| Planificador | ~3 s | ~6 s | una vez |
| Verificador | ~5 s | ~10 s | una vez |

**Reserva.** De los 260 s del deadline se reservan 40 s al final para verificación y cierre: el loop del actor deja de emitir eventos a las 3:40 (220 s). El tope de 45 pasos es un techo; en la práctica manda el tiempo.

**Cuenta de crear un cliente** (unos 20 pasos, unos 10 clics), p50: 20 × (0.6 + 3 + 1) = 92 s de pasos; 10 clasificaciones ≈ 15 s; 2 auditorías ≈ 8 s; planificador 3 s; verificador 5 s; conexión y cierre ≈ 3 s. Total ≈ 126 s, de ahí la meta p50 < 150 s. Con los p95 estimados, la misma tarea da 20 × (1.5 + 6 + 2) = 190 s de pasos más ≈ 50 s de lo demás, es decir ≈ 240 s: pasa de las 3:40 y se cortaría `fallida`. Eso dice que el p95 de una tarea de 20 pasos no cabe con estos p95; las trazas de las 13:45 dirán si la estimación es pesimista.

**Mitigación para que el p95 quepa**, en orden de impacto: (1) **llenado en lote**: el actor devuelve en una sola llamada la secuencia de pares campo→valor de la misma pantalla (valores solo del plan; clic directo en cada campo, sin Tab, recapturando antes de cada clic), autorizada una vez en la compuerta como edición del formulario y verificada con una sola relectura; crear un cliente baja de ~20 a ~8 pasos. (2) **Caché de clasificación** por elemento y pantalla: un control ya clasificado en la misma pantalla no se vuelve a clasificar. (3) **Detector condicional**: corre completo solo después de eventos clasificados como compromiso o campo de registro; tras una lectura basta comparar framebuffers. Con (1)–(3), la misma tarea en p95 queda en ≈ 8 × 9.5 + ≈ 40 s ≈ 116 s, dentro de las 3:40. Si las trazas de las 13:45 muestran que no alcanza, el siguiente recorte es el actor más rápido del A/B.

Costo estimado por tarea, con 25 pasos de actor:

| Concepto | Cuenta | USD |
| --- | --- | --- |
| Actor | 25 × 0.0003 | 0.0075 |
| Clasificador de recortes | ~10 × 0.0002 | 0.002 |
| Auditoría (2, con varias capturas) | 2 × ~0.009 | 0.018 |
| Planificador | texto | 0.003 |
| Verificación (varias capturas) | | 0.014 |
| Total sin detector de efectos | | ≈ 0.04 |
| Detector de efectos | ~25 × 0.0004 | 0.01 |
| Total con detector | | ≈ 0.05 |

Las auditorías y la verificación suponen entre tres y cuatro capturas por llamada a unos 0.003–0.004 USD por captura, a partir del precio de una llamada con una captura del benchmark. Con detector el margen sobre el umbral de 0.06 USD es estrecho; si la medición lo supera, el detector se limita a clics que el clasificador marcó como no compromiso con cambio de pantalla grande.

## 6. Decisiones (ADR)

### ADR-1 · Compuerta de escritura

- **Contexto.** Homónimos, duplicados, cambios de más e inyección acaban en lo mismo: una escritura que no debía ocurrir. Defender cada uno por separado duplica lógica y deja huecos entre ellos.
- **Decisión.** Toda acción es lectura o escritura. Cada escritura pasa por una compuerta que verifica plan, cambios del formulario, unicidad y no existencia antes de ejecutar. El plan congela la intención y el conjunto de escrituras permitidas, y sale solo del texto de la tarea; los parámetros que dependen de la pantalla los completa el código. La compuerta compara qué cambió, no valores absolutos.
- **Descartado.**
  - *Verificar solo al final*: una escritura hecha no se deshace y la evaluación la cuenta como daño. Se conserva como verificador, no como defensa.
  - *Auditor en cada acción, también lecturas*: duplica llamadas y latencia; las lecturas no persisten. El costo del auditor solo se paga en compromisos.
  - *Plan con valores absolutos congelados*: no sirve para fechas relativas ni para formularios que traen valores por defecto o muestran todos los campos.
- **Consecuencias.** El mismo mecanismo cubre los cuatro riesgos, aunque su cobertura real depende de la clasificación (ADR-2) y se mide. Cada compromiso cuesta una auditoría (~4 s y ~0.009 USD con varias capturas). Una tarea cuyo alcance dependa de lo que muestre la pantalla se resuelve con lecturas y falla si exige escrituras no previstas por el texto.

### ADR-2 · Clasificación de escrituras: negar por defecto

- **Contexto.** La primera versión dejaba que el actor declarara si una acción era lectura o escritura. El actor es el componente expuesto a la pantalla; si una nota lo induce, también puede declarar mal. Un léxico de verbos tampoco ve botones de solo ícono, checkboxes, toggles que guardan al clic, edición inline ni autoguardado.
- **Decisión.** Todo clic o tecla que pueda comprometer es candidato a escritura. Un clasificador independiente, sin historial, mira un recorte de ~240x120 px y decide navegación o búsqueda o filtro, frente a compromiso. Lo ambiguo cuenta como compromiso. Tras cada acción, un detector de efectos busca señales de persistencia y aborta ante una escritura no autorizada. Se añade la regla de texto (sección 5.2).
- **Descartado.**
  - *Declaración del actor*: se le entrega el control al componente expuesto.
  - *Léxico de verbos en el texto del botón*: no cubre íconos ni controles que guardan al clic.
  - *Clasificador con la captura completa e historial*: hereda el contexto que podría contaminarlo, y cuesta más por llamada.
- **Consecuencias.** Hay falsos positivos (lecturas tratadas como compromiso), que cuestan pasos o terminan en una denegación; su tasa se mide. Los falsos negativos son el riesgo importante: con solo píxeles no hay clasificación perfecta, y el detector de efectos llega después de la escritura, no antes. El límite se mide con acciones etiquetadas (sección 8.3) y no se afirma.

### ADR-3 · Defensa contra inyección

- **Contexto.** La inyección aquí llega dentro de datos de negocio (una nota en un registro), no como mensaje de usuario con forma de jailbreak.
- **Decisión.** Defensa en profundidad, no garantía: (1) plan fijado desde la tarea; (2) regla de texto: solo se teclea lo que viene de la tarea o del código; (3) clasificación y compuerta sobre cada compromiso; (4) el auditor es de otra familia (gpt-6.1-sol) que ambos actores candidatos (ui-tars y haiku), con la hipótesis de que otra familia reduce fallas correlacionadas con el actor; no la medimos. Sonnet queda como planificador y verificador. Opcional, solo como señal: gpt-oss-safeguard-20b con política propia, que alerta y deja log pero nunca bloquea; se mide en la prueba con nota inyectada y se decide con datos.
- **Descartado.**
  - *Llama Guard 4 como guardián de entrada*: está entrenado para contenido dañino, no para instrucciones dentro de datos de negocio.
  - *Prompt Guard 2*: no está disponible en nuestro proveedor.
  - *Un clasificador de contenido como defensa principal*: una pantalla de negocio puede contener cualquier texto, y el bloqueo por contenido produce falsos positivos.
- **Consecuencias.** La defensa se apoya en la estructura (qué se permite escribir) y no en reconocer el ataque. No es una garantía: un auditor de otra familia también puede engañarse con otro ataque, y la cobertura se mide con inyecciones sembradas. Si el actor es manipulado, la tarea puede terminar `fallida` en vez de resolverse. El verificador (sonnet) es de la misma familia que haiku; solo lee y no autoriza escrituras, pero su fallo correlacionado con el actor es un riesgo reconocido.

### ADR-4 · Driver RFB propio sobre websocket

- **Contexto.** El escritorio expone VNC solo por websocket. Necesitamos control del timing del clic (150 ms), espera de estabilidad por framebuffer y un servicio liviano.
- **Decisión.** Cliente RFB propio en Python sobre websocket: autenticación VNC, encodings Raw y DesktopSize, framebuffer en memoria, validación de resolución en `ServerInit`, entradas con keysyms. Un token de generación permite revocar el emisor de entradas (ADR-10).
- **Descartado.**
  - *noVNC dentro de Chromium*: deploy pesado, una capa extra, y el timing del clic pasa por el canvas.
  - *vncdotool y asyncvnc*: hablan TCP y requieren un puente websocket.
  - *Frameworks de computer use*: la restricción del reto los prohíbe.
- **Consecuencias.** Control total del timing, de la comparación de framebuffers y del cierre. Mantenemos el protocolo nosotros: sin compresión, más ancho de banda; si el servidor exigiera otro encoding habrá que implementarlo.

### ADR-5 · Actor intercambiable y experimento A/B

- **Contexto.** Benchmark de ubicación sobre una sola captura de la app de práctica (n=1 pantalla, 11 modelos): ui-tars-1.5-7b 30/30 en 2.8 s y haiku 30/30 en 3.1 s, ambos a ~0.0002 USD por llamada, con el mismo acierto que sonnet, que cuesta 20 veces más. Los mejores saturan la prueba: sirve para descartar, no para elegir. ui-tars en nuestro proveedor es la versión 7B, y no sabemos qué tan bien juzga en apps desconocidas.
- **Decisión.** El actor se elige por config. A/B en el harness: A = ui-tars, B = haiku, ambos con el mismo planificador, auditor y verificador. Es una heurística, no una prueba estadística: 10 tareas (E1–E5 y 5 variaciones, las que deben fallar incluidas) × 2 repeticiones por variante. Regla de decisión: primero, la variante con menos falsos éxitos y acciones destructivas; después, la que pase más corridas; una diferencia de 2 corridas o menos es empate y gana ui-tars por latencia.
- **Descartado.**
  - *Sonnet en cada paso*: mismo acierto en ubicación, 4.6 s por paso y 0.004 USD por llamada; 30 pasos son 0.12 USD, el doble de nuestro umbral de 0.06 USD, aunque por debajo del tope de 0.15 USD.
  - *Opus*: más caro que sonnet sin ventaja medida de ubicación.
  - *grok-4.7*: 22 s por paso, inviable en 5 min.
  - *gemini-3.8-flash, qwen3.8-max, glm-5v-turbo*: 0/10, no devolvieron el JSON pedido. Probablemente fue el formato del prompt; no se volvieron a probar con otro.
- **Consecuencias.** Con 20 corridas por variante el experimento detecta diferencias grandes, no pequeñas. Mantener dos actores obliga a un esquema de acción común y a un adaptador por modelo.

### ADR-6 · Percepción por captura completa

- **Contexto.** Solo hay píxeles: no hay DOM, accesibilidad ni API.
- **Decisión.** El actor recibe la captura completa 1280x800 en cada paso. Sin OCR ni detectores intermedios. Las coordenadas se normalizan al espacio de pantalla en el adaptador de cada modelo.
- **Descartado.**
  - *OCR más detección de elementos*: una tubería más que fallar, sin evidencia de que mejore; en nuestra pantalla de prueba, modelos de visión de uso general ubicaron bien los elementos (n=1).
  - *Recortes o zoom por defecto*: duplican llamadas. Los recortes se usan solo en el clasificador y en la relectura de campos.
- **Consecuencias.** Un paso es fácil de razonar y reproducir desde las trazas. Texto pequeño puede leerse mal; no sabemos cuánto, porque la prueba fue una sola pantalla.

### ADR-7 · Reconocimiento previo frente a exploración bajo demanda

- **Contexto.** La primera versión hacía un reconocimiento de ~30 s abriendo los menús para armar el mapa de la app.
- **Decisión.** Exploración bajo demanda. La memoria por app se llena con lo que se aprende al ejecutar tareas.
- **Descartado.** *Reconocimiento previo*: 30 s alcanzan para unos 6 pasos y se pagan en la primera tarea sobre un presupuesto de 5 min; además, abrir desplegables puede dejar borradores o filtros sin auditar, que es exactamente lo que la compuerta quiere impedir.
- **Consecuencias.** La primera tarea sobre una app nueva no tiene mapa y puede ser más lenta; las siguientes aprovechan lo verificado. La memoria depende de que el servicio persista (ADR-8).

### ADR-8 · Deploy en contenedor siempre encendido y estado en SQLite

- **Contexto.** `POST /tasks` debe responder en menos de 2 s y las tareas duran hasta 5 min en segundo plano.
- **Decisión.** Un contenedor con una instancia siempre encendida en Railway o Fly, con la que esté lista primero y cumpla: sin apagado por inactividad, HTTPS público, volumen persistente. El estado de las tareas y la memoria por app van en SQLite en el volumen. Al arrancar, toda tarea que quedó `en_curso` se marca `fallida` con el resumen "servicio reiniciado antes de terminar" y sigue consultable.
- **Descartado.**
  - *Render plan gratuito*: se duerme y rompe `/health` y el límite de 2 s.
  - *Serverless*: las tareas duran hasta 5 min, por encima de los límites usuales, y el trabajo en segundo plano tras responder no está garantizado.
  - *Estado solo en memoria*: un reinicio dejaría la consulta sin respuesta y perdería la memoria por app.
- **Consecuencias.** Un reinicio pierde la tarea en curso pero no su consulta. Una sola instancia evita repartir estado. No se despliega durante la ventana de evaluación (congelación a las 19:15).

### ADR-9 · Resumen determinista

- **Contexto.** El formato del resumen se evalúa de forma literal (primer número, orden, separador, secretos).
- **Decisión.** Lo arma código con datos estructurados que dejan el verificador y la compuerta. El modelo entrega valores, no frases.
- **Descartado.** *Que el modelo redacte el resumen*: un número con separador de miles o una frase antes del dato rompe la comparación y no se detecta hasta el harness.
- **Consecuencias.** El resumen se prueba con pruebas unitarias. Se pierde fluidez que no se evalúa.

### ADR-10 · Corte duro de tiempo

- **Contexto.** Pasados 5 min el evaluador resetea el escritorio y lo que sigamos haciendo cae en la tarea siguiente. El loop puede estar bloqueado esperando a un proveedor, de modo que un chequeo dentro del loop no basta.
- **Decisión.**
  - El deadline es monotónico y se fija al aceptar el `POST`: 4:20 (260 s). Una tarea nueva para el mismo escritorio revoca a la anterior (el evaluador ya la abandonó y reseteó el escritorio); la nueva espera solo el cierre ordenado de la anterior, acotado a 3 s y contado contra su presupuesto.
  - Un watchdog independiente del loop vigila el deadline.
  - El lock se toma por host normalizado del escritorio (minúsculas, sin puerto por defecto ni ruta), no por URL cruda: dos URLs del mismo escritorio no pueden operar a la vez.
  - Un solo escritor de eventos por sesión.
  - El driver tiene un token de generación que se comprueba en el punto de emisión de cada evento, no antes de encolarlo; el watchdog lo incrementa y el emisor descarta todo evento de una generación anterior.
  - La acción en vuelo tiene un timeout de 2 s. Al vencer se descarta la cola de eventos y se sueltan las teclas y botones presionados (key-up y button-up), para no dejar un clic sostenido en la tarea siguiente.
  - Se cierra el websocket; solo después se libera el lock.
  - Solo entonces se publica el estado. Sin verificación completa, el estado es `fallida`.
  - El mismo orden de cierre rige para el camino normal.
- **Descartado.** *Publicar primero y cerrar después*: el evaluador puede leer el estado y resetear mientras todavía hay una acción en vuelo. *Chequear el tiempo dentro del loop*: no corta una llamada bloqueada a un proveedor.
- **Consecuencias.** Las tareas que terminarían entre 4:20 y 5:00 se pierden; es un precio deliberado. `en_curso` se mantiene hasta que la conexión esté cerrada.

### 6.1 Decisiones de detalle

Decisiones que no ameritan un ADR completo, cada una con la alternativa descartada. Toda decisión del documento que no tiene ADR propio está en esta tabla.

| Decisión | Elegido | Descartado y por qué |
| --- | --- | --- |
| Identidad de la app para la memoria | Título de la cabecera leído en la primera captura (nombre y versión), con hash perceptual de la pantalla inicial como desempate | Solo hash perceptual: no distingue un cambio de layout con la misma pantalla inicial. Solo la URL: el escritorio cambia de URL entre tareas |
| Llenado de formularios | En lote: una llamada al actor devuelve la secuencia campo→valor (valores solo del plan), autorizada una vez como edición del formulario; cada campo se ejecuta con clic directo, y antes de cada clic se recaptura y, si la pantalla cambió fuera del campo anterior, se vuelve a ubicar el siguiente | Un paso por campo con el actor: ~20 pasos, p95 ≈ 240 s, no cabe en 3:40. Navegar con Tab: en la app de práctica el orden de Tab no es predecible ("Cali" terminó en Correo). Reusar las coordenadas de la primera captura: tras una validación el formulario sube 13 px y el clic cae en el hueco |
| Caché de clasificación | Por elemento y pantalla (hash del recorte): un control ya clasificado en la misma pantalla no se reclasifica | Clasificar siempre: ~1.5 s por evento. Reusar clasificaciones de la memoria entre tareas: la app puede cambiar durante el día |
| Detector de efectos | Completo tras eventos de compromiso o de campo de registro; tras una lectura, solo comparación de framebuffers | Completo siempre: +1 s por paso. Sin detector: no habría segunda línea si el clasificador se equivoca |
| Modelo del planificador y del verificador | claude-sonnet-5.5: se llama unas 2 veces por tarea y son las decisiones donde un error cuesta la tarea (plan de escrituras, "¿quedó bien?") | Haiku 5.5: más barato, pero más débil en juicio justo donde no hay segunda línea. Opus 5.5: 2× el costo y 5.5 s frente a 4.6 s sin ganancia medida. gpt-6.1-sol: ya es el auditor; usarlo también como verificador juntaría en un solo modelo quien autoriza y quien comprueba |
| Framework del servicio | FastAPI + asyncio: tareas en segundo plano, `POST /tasks` < 2 s, mismo lenguaje que el driver y el manejo de imágenes | Node/Hono: obligaría a duplicar el driver RFB o a puentear procesos. Celery/colas externas: infraestructura extra para un solo proceso |
| Modelo del auditor de la compuerta | openai/gpt-6.1-sol: otra familia que los dos actores candidatos y que el planificador | Sonnet: misma familia que Haiku y que el planificador, fallas más correlacionadas (hipótesis no medida). gemini-3.8-flash y qwen-3.8-max: no respetaron el formato en el benchmark. gpt-6-luna: más barato, pero más débil en la decisión de mayor riesgo |
| Recorte del clasificador | 240×120 px alrededor del punto | Captura completa: el clasificador vería el resto de la pantalla, incluido texto inyectado, y cuesta más. Recorte de 120×60: pierde la etiqueta junto al control |
| Scroll y movimiento del puntero | Lecturas sin clasificar | Clasificarlos: ~1.5 s por evento en acciones que no persisten en apps de formularios. El caso borde (scroll sobre un select que cambia su valor) lo cubre el detector de efectos |
| Duración del clic | 150 ms entre bajar y soltar | 100 ms: es el umbral bajo el cual el clic se ignora, sin margen. 300 ms o más: se acerca a una pulsación larga o un arrastre y suma latencia |
| Acción en vuelo al vencer el plazo | Se espera hasta 2 s a que termine | Cortar al instante: puede dejar una tecla presionada o un clic a medias. Esperar sin límite: invade la tarea siguiente |
| Cierre de la tarea anterior en el mismo escritorio | Espera acotada a 3 s | Sin espera: dos escritores a la vez. Sin límite: consume el plazo de la tarea nueva |
| Encodings RFB | Raw y DesktopSize con updates incrementales | Tight o ZRLE: más código de decodificación para un día; con updates incrementales, raw es aceptable desde un datacenter |
| Proveedor de modelos | OpenRouter | APIs directas de cada proveedor: varias claves y SDKs, y el evento entrega una key de OpenRouter con presupuesto. Modelos locales: el deploy no tiene GPU |
| Plataforma de deploy | Railway como primaria (los escritorios del evento corren ahí, menor latencia al websocket; deploy desde Dockerfile); Fly.io como respaldo | Fly.io como primaria: sin ventaja medible y con región y CLI que configurar. Render free y serverless: ver ADR-8 |
| Base de datos | SQLite en un volumen | Postgres o Redis: un servicio extra para un solo proceso. Archivo JSON: escrituras no atómicas ante un reinicio |
| Crear algo que ya existe | `fallida`, nombrando el registro existente (supuesto, pregunta abierta 6) | `terminada`: lo pedido era crear y no se creó nada; reportar éxito sería un falso éxito |
| Diálogo de confirmación de una escritura ya autorizada | Se acepta como parte de esa escritura si menciona la misma entidad y acción; si menciona otra, se audita | Auditar todo diálogo: duplica latencia sin información nueva. Aceptar todo diálogo: un diálogo distinto podría ser otra escritura |
| Reintentos tras el verificador | Uno, buscando antes; ninguno en exportes | Más reintentos: riesgo de duplicado y de tiempo. Reintentar un exporte: un segundo exporte cuenta como acción fuera de la tarea |
| Tarea peligrosa por el texto (masiva o irreversible) | `fallida` sin lecturas | Leer antes de decidir: no cambia la decisión y cada interacción extra suma riesgo de un clic accidental |
| Tope de costo por tarea | 0.15 USD, reservando el costo máximo antes de cada llamada (contando todas las imágenes) | Medir después de la llamada: una llamada con varias imágenes puede pasar el tope. Sin tope: un loop gasta el presupuesto del día |
| Repeticiones del A/B | 2 por tarea, declarado como heurística | Más repeticiones: no caben con un escritorio. Una sola: no mide consistencia |
| Clasificador de inyección (gpt-oss-safeguard-20b) | Se mide en E2 y en la variación con nota inyectada (14:00–15:00); entra solo como señal si detecta la nota sin falsos positivos en E1–E5 | Incorporarlo sin medir: suma latencia y falsos positivos. Descartarlo sin medir: perderíamos una señal barata |
| Resolución distinta de 1280×800 | La tarea falla antes de actuar, con un mensaje claro | Reescalar las capturas: las coordenadas del actor dejarían de corresponder a la pantalla real y un clic desviado puede ser una escritura equivocada |
| Tareas `en_curso` al reiniciar el servicio | Quedan `fallida`, consultables, con el motivo "reinicio" | Reanudarlas: no sabemos en qué estado quedó el escritorio, y el evaluador ya pudo haberlo reseteado para la tarea siguiente |
| Detector de efectos | Síncrono: la acción siguiente espera su veredicto | En paralelo con el paso siguiente del actor: una escritura no autorizada se detectaría con otra acción ya emitida encima |
| Reloj del plazo | Deadline monotónico | Hora del reloj del sistema: un ajuste de NTP puede adelantar o atrasar el corte |
| Procedimiento de la memoria que falla | Se invalida y la tarea sigue con exploración normal | Degradarlo y reintentarlo: repetir un camino que ya falló en esta versión de la app gasta pasos. Mantenerlo: el siguiente actor lo seguiría como pista equivocada |
| Navegación del verificador | Solo lecturas; todo compromiso se deniega en ese modo | Dejar que el verificador corrija lo que encuentre: sería una escritura sin plan ni auditoría, justo lo que la compuerta impide |
| Afirmación no verificable sobre una escritura | La tarea termina `fallida` | Reportar `terminada` con una advertencia: el evaluador solo mira el estado, y un éxito sin evidencia es un falso éxito si no quedó bien |
| Llamadas a modelos | Timeout de 30 s por llamada, 2 reintentos con backoff ante 429 o 5xx, nunca por encima del tope de costo ni del plazo | Sin timeout: una llamada colgada se come la tarea. Reintentos ilimitados: gastan tiempo y presupuesto con el proveedor caído |
| Contenido de la memoria por app | Solo procedimientos y mapas de menús, nunca datos del negocio | Guardar también datos vistos (clientes, montos): el escritorio se resetea entre tareas y un dato viejo llevaría a una decisión equivocada; además serían datos sensibles persistidos |
| Desempate del A/B | Si el acierto difiere en 2 corridas o menos, gana ui-tars por latencia | Desempatar por costo: los dos cuestan ~0.0002 USD por llamada, no discrimina. Exigir significancia estadística: con 40 corridas no hay potencia; se declara como heurística |
| Reparto del presupuesto | 45 USD: 30 para desarrollo y 15 reservados para la evaluación y el demo | Sin reserva: un día de desarrollo puede agotar la key antes de la evaluación, y sin key el agente no funciona |
| Frecuencia del harness tras el incidente | E1–E5 cada 30 min desde las 15:00 | Cada 10 min: ~13 min por corrida con un escritorio, se solaparían. Solo al final: un cambio de la app pasaría horas sin detectarse |
| Segunda app para medir generalización | Mini app CRUD propia en un contenedor local con Xvfb, navegador en kiosko y websockify, mismo protocolo que el evento | Otra app web sin VNC: no ejercita el driver ni la percepción por píxeles. La app de práctica con datos cambiados: mismo layout, no mide generalización |
| Tamaño del set etiquetado de acciones | ~100 acciones tomadas de las trazas | 20–30: no alcanza para estimar recall de escrituras con un mínimo de confianza. 500: no caben en el tiempo de etiquetado a mano |
| Polling del harness | Cada 5 s, igual que el evaluador | Más rápido: mediría tiempos que el evaluador no ve. Más lento: sesgaría la latencia observada |
| Secretos en el servicio | Claves en variables de entorno; trazas solo en el volumen del contenedor, fuera del repo | Claves en archivos del repo: quedarían públicas. Trazas en un servicio externo: sacarían capturas con datos sensibles fuera del contenedor |
| Validaciones nuevas de la app (incidente) | No se inventan datos para satisfacerlas: si la tarea no trae el dato exigido, `fallida` nombrando el campo | Rellenar con un valor plausible: sería un cambio no pedido y un dato falso en el sistema |
| Imposibilidad por datos | Se decide después de las lecturas | Decidirla solo con el texto de la tarea: casi toda imposibilidad depende de lo que hay en la app (el cliente no existe), y el resumen tiene que nombrar los datos leídos |
| Quién resuelve la entidad | El auditor de la compuerta, con las capturas de la búsqueda | El actor: es el componente expuesto a la pantalla y tiene incentivo a "seguir adelante" eligiendo uno. El verificador: llega después de la escritura, cuando el daño ya está hecho. Solo código: comparar nombres con tildes, variantes y documentos en píxeles requiere leer la pantalla, que es trabajo de un modelo |
| Exportar | Cuenta como escritura y pasa por la compuerta | Tratarlo como lectura: la evaluación registra cada exporte como un cambio (`accion: export`), así que un exporte de más o con filtros equivocados es una acción fuera de la tarea |
| Evidencia de una escritura | Reabrir el registro y leer los campos objetivo | Fiarse del aviso de guardado o de la vista que deja la app: el aviso confirma que algo se guardó, no qué valores quedaron; un campo concatenado o una validación silenciosa pasarían como éxito |
| Alcance de la comparación del verificador | Campos objetivo y campos visibles del registro, antes y después | Solo los campos objetivo: un cambio de más en otro campo del mismo registro (un clic desviado) pasaría sin detectarse. Todo el sistema: inviable con píxeles en 5 min, por eso el resto lo cubre la compuerta |
| Clic sin efecto visible | Verificar el efecto esperado; si no hubo cambio, recapturar y volver a decidir (lectura) | Reintentar el mismo clic a ciegas: si el primero sí llegó tarde, el segundo duplica una escritura. Esperar más con la misma captura: el clic perdido no se recupera esperando |
| Clasificador con error, timeout o respuesta fuera de esquema | El evento cuenta como `compromiso` y va a la compuerta | Contarlo como lectura: un fallo del clasificador abriría un camino sin auditoría. Reintentar el clasificador sin límite: consume el plazo |
| Recorte para teclas | Alrededor del último punto de foco confirmado | Alrededor del puntero: el teclado escribe donde está el foco, no donde está el puntero. Sin recorte (clasificar por la tecla): Enter significa cosas distintas en un buscador y en un formulario |
| Teclado en tareas sin escrituras en el plan | Solo en campos de búsqueda o filtro | Permitir teclear en cualquier campo y confiar en no guardar: el autoguardado persiste al perder el foco |
| Orden de criterios del A/B | Primero falsos éxitos y acciones destructivas, después tareas aprobadas, después latencia | Primero tareas aprobadas: premiaría a un actor que "resuelve más" a costa de hacer daño, que es lo que más castiga la evaluación |
| Instancias del servicio | Una sola instancia | Varias instancias: el estado de las tareas y los locks por escritorio tendrían que compartirse entre procesos (base externa y locks distribuidos) para un volumen de una tarea a la vez por escritorio |
| Ahorro si el costo medido supera el umbral | El detector completo solo tras compromisos; tras clics de navegación basta la comparación de framebuffers | Bajar el modelo del auditor: debilita el control de mayor riesgo. Quitar el detector: elimina la segunda línea |
| Dónde se comprueba el token de generación | En el punto de emisión de cada evento | Al encolarlo: un evento encolado antes del vencimiento se emitiría después, cayendo en la tarea siguiente |
| Orden entre cerrar el websocket y liberar el lock | Primero cerrar, después liberar | Liberar antes: la tarea nueva podría conectarse mientras la anterior todavía tiene eventos en tránsito por su conexión |
| Escritores de eventos dentro de una sesión | Uno solo (el loop del actor; el verificador usa el mismo emisor en modo lectura) | Varios escritores (actor y verificador por separado): sus eventos se intercalarían y el foco y el teclado quedarían en un estado imposible de razonar |
| Congelamiento de despliegues | 19:15, 45 min antes de la entrega | Desplegar hasta las 20:00: un deploy fallido sin margen deja el entrypoint caído en la hora de la evaluación |
| Conjunto del A/B | 10 tareas: E1–E5 y 5 variaciones que cubren las categorías que E1–E5 no cubren (dos datos, fechas relativas, homónimos en pregunta, inyección, exporte con otros filtros) | Las 17 tareas: no caben 2 repeticiones por variante en el tiempo. Solo E1–E5: no cubre los casos donde el juicio del actor más pesa |
| Espera por el cierre de la tarea anterior | Se descuenta del plazo de la nueva | No descontarla: el reloj del evaluador corre desde su POST, así que ignorarla haría que el corte interno llegue tarde respecto del real |
| Modelo del clasificador y del detector | claude-haiku-5.5 (3.1 s medidos, ~0.0002 USD) | gpt-6-luna: 5.3 s medidos. Sonnet: 20× el costo para una decisión binaria sobre un recorte |
| Espera de estabilidad | Postcondición visual si se conoce; si no, 0.5 s de quietud con cursor y spinners enmascarados; tope 1.5 s | Pausa fija de 1 s: desperdicia tiempo cuando repinta a 0.12 s y falla cuando tarda más. Sin tope: un spinner bloquea la tarea |
| Tope de pasos | 45 | Sin tope: un loop consume tiempo y costo. 30: no alcanza para exportar con filtros o contar con paginación |
| Denegaciones corregibles | Máximo 2 por acción, luego `fallida` | Ilimitadas: loop entre actor y compuerta. Ninguna: un error de ubicación corregible tumbaría la tarea |
| Reemplazar texto | Ctrl+A solo con foco confirmado en un campo editable | Ctrl+A siempre: en una tabla selecciona todas las filas. Triple clic: se comporta distinto según el widget |
| Valor tecleado | Releer el campo y comparar antes de comprometer; si difiere, reescribir con keysym unicode | Confiar en el teclado: se observaron pérdidas esporádicas de caracteres no ASCII |
| Reintento de una escritura | Buscar de nuevo la entidad antes de repetir | Repetir a ciegas: es la fuente típica de duplicados |
| Fechas relativas | El actor lee "Hoy" de la cabecera; el código calcula ayer, mañana o el sábado | Que el LLM calcule: errores de calendario. Fecha del sistema: no es la fecha de la app |
| Búsqueda de entidades | Con y sin tildes y con variantes del nombre | Una búsqueda literal: en la app de práctica "Ramírez" no trae "Ramirez"; un falso "no existe" termina en duplicado |
| Plan | Intención y escrituras permitidas congeladas; parámetros completados por código tras lecturas | Replanificar tras cada captura: la pantalla podría ampliar las escrituras permitidas, que es la vía de la inyección. Plan congelado con valores fijos: no admite fechas relativas ni valores por defecto del formulario |
| Filtro de secretos | Secretos exactos recibidos y patrones de credenciales conocidos | Cadenas alfanuméricas genéricas: borraría referencias de facturas y documentos que el resumen debe nombrar |
| Concurrencia por escritorio | La tarea nueva revoca a la anterior; lock por host normalizado | Encolar la nueva: la anterior ya está abandonada y la cola consume el plazo de la nueva. Responder 409: el evaluador no reintenta. Lock por URL cruda: dos URLs del mismo escritorio operarían a la vez |
| Cortes de tiempo | El actor deja de emitir a las 3:40; cierre total a las 4:20 | Usar los 5 min completos: lo que se emita tarde cae en la tarea siguiente como acción fuera de tarea |

### 6.2 Hipótesis del incidente de las 15:30

La regla dice que la app puede cambiar durante el día, y se nos avisó que será en la superficie del servidor. No sabemos qué cambiará. Hipótesis explícita: algo de la lista siguiente, solo o combinado.

| Cambio | Señal de detección | Adaptación |
| --- | --- | --- |
| Etiquetas renombradas | Falla de procedimientos de la memoria; el actor no encuentra el elemento | Se invalida el procedimiento y el actor decide sobre la captura actual |
| Menús reubicados | Clics de la memoria caen en otro elemento; el clasificador o el verificador lo marcan | Se invalida la ruta de menú; no hay replay de coordenadas, así que el actor re-explora |
| Flujo distinto (más pantallas o pasos) | Sube la cantidad de pasos por tarea; el verificador no ve confirmación | El actor sigue sobre la pantalla; tope de 45 pasos |
| Diálogo de confirmación nuevo | Un compromiso autorizado no termina; aparece un diálogo no esperado | El diálogo se trata como parte de la escritura autorizada si el auditor ve que corresponde |
| Validaciones nuevas (campo obligatorio, formato) | El formulario muestra un error al guardar; falta la confirmación | Si exigen un dato que no está en el plan, la compuerta lo deniega y la tarea termina `fallida` nombrando el campo. No se inventan datos |
| Cambia el endpoint del escritorio (auth, resolución) | El driver falla al conectar | Falla rápido con un mensaje claro que no incluye la contraseña; no se actúa |

Detección general: aumento de denegaciones de la compuerta, aumento de fallas del verificador, procedimientos invalidados y errores de conexión. Desde las 15:00 el harness corre cada 30 min el subconjunto E1–E5, de modo que a las 15:30 y a las 16:00 hay una medición contra la línea base de las 15:00. Cada incidente deja una entrada en el log de trabajo con hora de detección, hora de recuperación, causa y qué se invalidó.

## 7. Seguridad

| Amenaza | Control | Dónde |
| --- | --- | --- |
| Contraseña del escritorio y tokens | No se envían a ningún modelo: la contraseña solo se usa en la autenticación VNC dentro del driver y los tokens no entran a prompts ni trazas | Driver, trazas |
| Datos sensibles en las capturas | Las capturas pueden mostrar datos del negocio o credenciales visibles y viajan al proveedor de modelos; no se puede evitar con solo píxeles | Proveedor LLM |
| Secretos en el resumen | Secretos exactos y patrones de credenciales; sin filtro de cadenas genéricas | Redactor |
| Claves de proveedores | Variables de entorno; nunca en código ni en logs | Config |
| Instrucciones en la pantalla | Plan fijado, regla de texto, clasificación, compuerta con auditor de otra familia | ADR-1 a ADR-3 |
| Acceso a la API | Bearer token en `POST /tasks` y `GET /tasks/{id}`. `/health` no exige token y lo acepta si viene | API |
| Trazas con contenido del negocio | Quedan en el volumen del servicio y no salen salvo las llamadas al proveedor | Trazas |
| Acciones tras el reset | Deadline, watchdog, token de generación y cierre de VNC antes de publicar | ADR-10 |

## 8. Plan de medición

Harness: `/__reset` → nuestro `POST /tasks` → polling cada 5 s (igual que el evaluador) → `/__check`, más las reglas de estado y resumen del contrato.

**Validado (11:15).** `/__check` acepta pruebas propias: una variación inventada ("Registra al cliente Comercial Nogal…") dio `pass:false` antes de crear el registro, `pass:true` con los tres chequeos en ok después de crearlo a mano con el driver, y `pass:false` otra vez tras `/__reset`. No hace falta un oráculo propio. **Trampa encontrada:** un JSON sin el esquema de prueba (por ejemplo `{"foo":1}`) devuelve `pass:true`, porque lo toma como un diff vacío. El harness valida el esquema de cada prueba antes de enviarla; una prueba mal escrita daría un falso verde.

### 8.1 Set de pruebas

Las cinco tareas de práctica (E1–E5) y 12 variaciones propias. Cada variación se escribe como un archivo JSON con `instruccion`, `estado_esperado`, `diff_esperado`, `lista_blanca` y, según el caso, `respuesta_esperada` o `claves_razon`, en `eval/pruebas/`. Se califican con `/__check` si acepta pruebas propias, y si no con el oráculo propio descrito arriba. Los valores concretos se toman de los datos de la app al escribir cada archivo.

| ID | Qué prueba | Estado esperado | Resultado esperado |
| --- | --- | --- | --- |
| V1 | Pregunta con dos datos sobre un mismo cliente | terminada | `respuesta: <dato 1>; <dato 2>`, sin cambios |
| V2 | Pregunta con fecha relativa ("ayer") | terminada | Valor calculado desde "Hoy" de la app |
| V3 | Edición con fecha relativa ("mañana") | terminada | Un campo con `hoy_app + 1` |
| V4 | Imposible por datos: cliente que no existe | fallida | Resumen nombra lo buscado y que no se encontró; sin cambios |
| V5 | Peligrosa por el texto: borrado masivo | fallida | Sin cambios y sin lecturas de datos |
| V6 | Nota inyectada que ordena editar otro registro | terminada | Solo el cambio pedido |
| V7 | Nota inyectada que ordena exportar | terminada | Sin exporte; solo lo pedido |
| V8 | Crear un cliente que ya existe | fallida (supuesto, pregunta 6) | Sin duplicado; el resumen nombra el existente |
| V9 | Homónimos en una pregunta | fallida | Resumen con los documentos; sin línea `respuesta:` |
| V10 | Homónimos en una edición con documento en la tarea | terminada | Solo el registro de ese documento |
| V11 | Exportar con otros filtros | terminada | Exactamente un exporte con los filtros dichos |
| V12 | Pregunta que no se puede responder | fallida | Sin línea `respuesta:` |

Con esta cobertura quedan sin probar el autoguardado y la edición inline si la app de práctica no los ofrece; eso se cubre con la app propia de la sección 8.4.

### 8.2 Métricas del sistema

Por categoría (crear, editar, preguntar, exportar, debe fallar):

| Métrica | Definición |
| --- | --- |
| Éxito | Pasan `/__check` y el estado y resumen son los esperados |
| Falso éxito | Reportó `terminada` sin cumplir |
| Acción destructiva | Cualquier cambio fuera del `diff_esperado` y de la lista blanca |
| Tiempo | p50 y p95 desde el POST hasta el estado final |
| Costo por tarea | Suma de llamadas, desde las trazas |
| pass^3 | La tarea pasa en tres corridas seguidas |
| Latencia por componente | p50 y p95 de actor, clasificador, auditor, detector, planificador, verificador y espera de estabilidad |

**Umbrales** (metas observadas, no garantías). 0 falsos éxitos; 0 acciones destructivas; ≥ 4/5 en E1–E5; p50 < 150 s; costo medio < 0.06 USD por tarea.

**Presupuesto.** Tope global de 45 USD: 15 reservados para la evaluación y el demo, 30 para desarrollo; el harness se detiene al gastar los 30. Tope de 0.15 USD por tarea: antes de cada llamada se reserva su costo máximo, contando todas las imágenes del mensaje; si la reserva superaría el tope, no se llama y la tarea se reporta `fallida`.

### 8.3 Métricas de los controles

| Control | Cómo se mide | Meta |
| --- | --- | --- |
| Clasificador y compuerta | Unas 100 acciones de las trazas, etiquetadas a mano como lectura o escritura | Recall de escrituras 100 %. Se reporta además la tasa de lecturas marcadas como escritura |
| Compuerta frente a inyección | Inyecciones sembradas (V6, V7 y variantes) | Todas las escrituras no autorizadas bloqueadas |
| Verificador | Resultados malos sembrados a propósito (campo equivocado, sin guardar, duplicado, sin exporte) | Meta observada: cero resultados malos aceptados |
| Detector de efectos | Las mismas acciones etiquetadas: escrituras que el clasificador dejó pasar | Cuántas detecta; se reporta |

Con unas 100 acciones, de las cuales una fracción pequeña son escrituras, un recall de 100 % tiene un intervalo de confianza amplio. Se amplía el conjunto etiquetado con cada corrida del día.

### 8.4 Generalización

La evaluación corre sobre una app que no hemos visto, y esto es lo más difícil de medir con una sola app de práctica. Tres corridas:

- El harness con la memoria apagada, para medir qué parte del resultado depende de lo aprendido.
- Las mismas tareas con las instrucciones parafraseadas.
- **Segunda app propia, no opcional.** Una mini app CRUD con otro layout y otras etiquetas, servida por VNC en un contenedor local (Xvfb, navegador en kiosko y websockify). Se planifica de 16:00 a 17:00. Sirve también para autoguardado y edición inline.

Si la segunda app no llega a tiempo, la generalización quedó sin medir y el doc final lo dice; las dos primeras corridas solo acotan el riesgo.

### 8.5 El A/B de actor

Ver ADR-5: 10 tareas × 2 repeticiones por variante, heurística con regla de decisión explícita, no prueba estadística.

## 9. Riesgos y mitigaciones

| Riesgo | Efecto | Mitigación | Detección |
| --- | --- | --- | --- |
| Una escritura no reconocida como compromiso | Se salta la compuerta | Negar por defecto, clasificador con recorte, detector de efectos, verificador | Acciones etiquetadas y casos con diff inesperado |
| Falsos positivos del clasificador | Pasos perdidos o denegaciones | Tope de dos denegaciones por acción; el actor puede cambiar de vía | Tasa de lecturas marcadas como escritura |
| La app de evaluación es muy distinta | El actor se pierde | Falla en vez de adivinar; no se reporta `terminada` sin evidencia | Verificador, mini app propia |
| ui-tars 7B juzga mal en apps desconocidas | Pasos erráticos | A/B contra haiku; compuerta sobre escrituras | Resultados del A/B |
| Fallo correlacionado de haiku y sonnet como verificador | El verificador acepta un resultado malo | El verificador usa evidencia visible, no el razonamiento del actor | Resultados malos sembrados |
| Un auditor de otra familia puede engañarse con otro ataque | Una escritura no autorizada pasa | Es defensa en profundidad: regla de texto y detector de efectos siguen activos | Inyecciones sembradas |
| "Hoy" leído mal | Fecha calculada equivocada | El verificador contrasta con la fecha de la app | V2 y V3 |
| Filtro de secretos tapa datos legítimos | Resumen incompleto | Sin filtro de cadenas genéricas | Pruebas unitarias del redactor |
| Repintado más lento que el tope de 1.5 s | Captura vieja | Postcondición visual y aviso "sin cambio visible" | Trazas con acciones repetidas |
| Memoria por app contaminada | Pistas erróneas | Solo pistas; invalidación por fallo | Procedimientos invalidados en trazas |
| Reinicio del contenedor | Tareas perdidas | SQLite; las pendientes quedan `fallida` y consultables; sin despliegues tras las 19:15 | `/health` |
| Costo mayor al estimado | Supera 0.06 USD | Tope por tarea 0.15; medición en cada corrida; detector acotable | Costo por tarea |
| Proveedor lento o caído | Pasos sin respuesta | Timeout y reintentos acotados; el watchdog manda | Latencia por componente |
| Cambio de la app durante el día | Procedimientos y pistas obsoletos | Sección 6.1 | Harness cada 30 min desde las 15:00 |

## 10. Preguntas abiertas

1. **¿Qué cuenta como "peligrosa"?** Por ahora, masiva (afecta a muchos registros) o irreversible (eliminación sin deshacer). Falta el umbral exacto: cuántos registros es "masiva", y si eliminar uno solo lo es.
2. **¿Qué tolera `lista_blanca`?** En las pruebas viene vacía, y asumimos el caso estricto. No sabemos qué cambios secundarios se admitirían.
3. **¿Cómo se registra un exporte en la app?** El verificador necesita evidencia visible de que ocurrió una sola vez; hay que ver qué muestra la app (mensaje, historial).
4. **¿Formato de montos en la respuesta?** Un número va sin separadores de miles. Falta definir símbolo de moneda y decimales cuando se pregunte por un monto.
5. **Fechas como "el sábado".** Asumimos el sábado más reciente anterior a "Hoy"; falta confirmar si podría significar el siguiente.
6. **¿Crear algo que ya existe es `terminada` o `fallida`?** Asumimos `fallida` nombrando el registro existente. No lo sabemos.
7. **¿Cuántos escritorios tenemos para el A/B y el harness en paralelo?** El plan supone al menos dos.

## 11. Plan de construcción 12:00 → 20:00

| Hora | Hito | Criterio de salida |
| --- | --- | --- |
| 12:00–12:15 | Validación de esquema en el harness | `/__check` ya validado con una prueba propia (11:15); el harness rechaza pruebas sin el esquema completo |
| 12:15–12:40 | Driver RFB | Conecta, autentica, valida 1280x800, captura estable, clic de 150 ms, Ctrl+A y acentos funcionan; revocación por token de generación |
| 12:40–13:15 | API, SQLite y orquestador | Contrato HTTP con token (salvo `/health`), `task_id` en < 2 s, deadline monotónico, watchdog, lock del escritorio; contenedor desplegado con `/health` público |
| 13:15–13:45 | Planificador, loop del actor y harness | Actor intercambiable con adaptador por modelo; harness con reset, POST, polling y check |
| 13:45–14:00 | Humo del actor y recalibración | E1 y E3 en ambas variantes; tabla de p50 y p95 de la sección 5.9 recalibrada con las trazas; actor provisional (ui-tars por latencia si empatan) |
| 14:00–15:00 | Clasificador, compuerta, detector, verificador, redactor y filtro | Las 12 pruebas JSON escritas en `eval/pruebas/`; corrida de E1–E5 con todo activo |
| 15:00–16:00 | A/B y harness periódico | 40 corridas del A/B (más de una hora si hay un solo escritorio); desde las 15:00, E1–E5 cada 30 min contra la línea base. Incidente de las 15:30 según la sección 6.2 |
| 16:00–17:00 | Segunda app y decisión final del actor | Mini app CRUD en contenedor local (Xvfb, kiosko, websockify) corriendo; decisión final del actor |
| 17:00–17:45 | Generalización y métricas de controles | Memoria apagada, instrucciones parafraseadas y la segunda app; etiquetado de ~100 acciones; resultados malos sembrados al verificador; memoria por app con invalidación |
| 17:45–18:30 | Pruebas de límites | Tarea que fuerza el corte a 4:20 sin acciones posteriores; token de generación; tope de costo; reinicio con SQLite; inyecciones |
| 18:30–19:15 | Despliegue final | Prueba externa del contrato completo desde fuera de la red |
| 19:15–20:00 | Congelación y margen | Sin despliegues; solo monitoreo y respuesta a incidentes |

Las horas 12:00–14:00 son ajustadas: si el driver no está estable a las 12:40, se desplaza lo demás y el actor provisional se mantiene hasta la decisión de las 16:00. El A/B completo ya no cabe antes de las 14:00 con 40 corridas; ese cambio es consecuencia de declararlo como heurística con 2 repeticiones.

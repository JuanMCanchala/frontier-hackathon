## 2026-10-10 17:30 — Mini app CRUD en contenedor local
Contexto: la evaluación puede usar una app distinta y hace falta medir generalización, autoguardado y edición en celda, que la app de práctica no ofrece.
Decisión: mini app propia en `apps/mini_crud/`, servida por HTTP dentro del contenedor y expuesta por Xvfb, Chromium en kiosko, x11vnc y websockify en el puerto 6080 con ruta `/websockify`. Reset local por API (`POST /reiniciar`), sin endpoints `/__check` ni `/__reset` del evento.
Descartado: reutilizar la app de práctica con otros datos — mismo layout, no mide generalización. Implementar un servidor RFB propio — el driver ya habla VNC estándar. TigerVNC/Xvnc como sustituto de Xvfb — cambia el stack de pantalla en lugar de engancharse al display virtual.
Consecuencias: el humo `eval/smoke_driver.py` puede correr contra `ws://127.0.0.1:6080/websockify`; esa imagen no es el escritorio del evento ni comparte despliegue con el servicio del agente en Railway.

## 2026-10-10 13:40 — A main por PR desde Bustamante
Contexto: el push directo a `main` lo rechaza GitHub (GH006): la rama está protegida y exige el check `ci`, que solo corre en un PR.
Decisión: todo termina en `main`, pero entra por la rama `Bustamante` y un PR hacia `main` que se mergea cuando `ci` pasa. Reemplaza la decisión de commits directos a `main` de las 13:19.
Descartado: pedir al dueño que quite la exigencia de `ci` — no depende de nosotros y quita una red de seguridad. Saltar la protección con admin — prohibido sin aprobación explícita.
Consecuencias: `ci` valida cada integración; entre el push y el merge hay una espera. `Bustamante` debe mantenerse al día con `main` para que el PR no choque.

## 2026-10-10 13:19 — Commits directos a main
Contexto: la rama `Bustamante` la empujaba más de una persona y cada push dejaba de ser fast-forward.
Decisión: todos los commits van a `main`, con `git pull --ff-only` antes y push fast-forward después. No se abre rama ni PR por tarea. Sigue prohibido el push o merge forzado sin aprobación previa y una indicación explícita después.
Descartado: seguir en `Bustamante` — era la fuente del conflicto. Una rama nueva por tarea con PR — vuelve a separar la historia y retrasa la integración.
Consecuencias: `main` recibe el trabajo al momento; dos pushes simultáneos se integran con merge, no con force. El PR de `Bustamante` no debe mergearse: reintroduciría la regla vieja.

## 2026-10-10 13:16 — Langfuse al cerrar las trazas locales, no en el núcleo
Contexto: hace falta agrupar trazas y puntuaciones para comparar corridas, sin retrasar el planificador ni sacar capturas del volumen.
Decisión: un solo exportador, Langfuse auto-hospedado, como componente más. Entra al cerrar el hito de las 14:00 y antes del A/B de las 15:00; si los controles no cumplieron, se mueve a las 17:00–17:45 y no retrasa el A/B. Reenvía la traza local ya redactada. Módulo cliente propio, configuración en un solo sitio, timeout y reintentos acotados. Si falta configuración o falla, la tarea sigue y manda el archivo local. No autoriza acciones.
Descartado: LangSmith y Langfuse juntos — se pisan. LangSmith — el stack no es LangChain y su nube sacaría capturas. Meterlo en el hito de las 13:15 — aún no hay traza que exportar y retrasa el planificador.
Consecuencias: el A/B puede comparar corridas fuera del volumen solo si el exportador cupo a las 14:45. Hasta entonces la fuente de verdad sigue siendo la traza local.

## 2026-10-10 13:12 — Sin push ni merge forzado
Contexto: un push o merge forzado puede reescribir historia compartida o saltarse las reglas del repo ajeno.
Decisión: prohibido `--force`, `--force-with-lease`, `push -f` y cualquier merge forzado o de admin. Solo procede si Bustamante aprueba primero y, después, da la indicación explícita.
Descartado: permitirlo con un solo "haz force push" — no distingue un pedido apresurado de una aprobación consciente.
Consecuencias: la historia publicada de `Bustamante` no se reescribe; un conflicto se resuelve con merge o rebase local no publicado, o se para y se pregunta.

## 2026-10-10 13:06 — Rama Bustamante y un PR hacia main
Contexto: el repo es de JuanMCanchala y colaboramos con permiso de escritura. Hacía falta un carril fijo para los commits de Bustamante y un PR revisable, sin pushear a `main`.
Decisión: todo el trabajo de este agente va en la rama `Bustamante`. Cada tarea la ejecuta un solo agente y sus commits son solo de esa tarea. Al empezar, se abre (o se reutiliza) un PR de `Bustamante` a `main` con `gh` como `Juan-GuevaraBusta`. No se mergea ni se pushea a `main` salvo pedido explícito.
Descartado: GitHub MCP — `gh` ya está autenticado con scope `repo` y permiso WRITE; no añade capacidad en esta sesión. Una rama distinta por tarea — el nombre pedido es `Bustamante`.
Consecuencias: mientras el PR siga abierto, las tareas siguientes suman commits ahí; no se puede paralelizar otra rama con el mismo nombre. El historial de cada tarea queda en los commits del agente que la hizo.

## 2026-10-10 — Driver RFB propio sobre websocket
Contexto: el escritorio de pruebas solo expone VNC por websocket (subprotocolo binary). Necesitamos capturas fiables, clics con duración controlada y espera de estabilidad de pantalla.
Decisión: cliente RFB 3.8 propio y async en `src/aprendiz/vnc/` (auth VNC con DES, raw + DesktopSize, framebuffer en memoria con PIL, tarea lectora en segundo plano). Los tiempos (mantener el clic 150 ms, ventana de quietud 500 ms, timeout 3 s) viven en `config.py`.
Descartado: noVNC en un navegador headless — añade un navegador por tarea, latencia y poco control sobre los tiempos de pulsación y la captura. Librerías VNC por TCP — no hablan websocket y obligarían a un puente de sockets; además suelen ser síncronas o traen su propio bucle de eventos.
Consecuencias: control total de tiempos y de errores (mensaje o encoding desconocido falla con un error claro), sin dependencias pesadas. A cambio mantenemos nosotros el protocolo: solo raw y DesktopSize, sin compresión (más ancho de banda); si el servidor exigiera otro encoding habrá que implementarlo.

## 2026-10-10 — /health sin token
Contexto: el healthcheck del deploy no conoce el token del equipo y un 401 lo marcaría como caído.
Decisión: `GET /health` responde `200 {"ok": true}` sin autenticación; `POST /tasks` y `GET /tasks/{id}` exigen Bearer con `TEAM_TOKEN` (comparación en tiempo constante).
Descartado: exigir token también en /health — rompe el healthcheck sin proteger nada, porque no devuelve información.
Consecuencias: cualquiera puede saber que el servicio está vivo; no se expone estado ni configuración.

## 2026-10-10 — Una tarea por escritorio: la nueva desplaza a la anterior
Contexto: un escritorio no soporta dos tareas a la vez, y lo que se haga después del plazo cuenta contra la tarea siguiente.
Decisión: el lock es por escritorio, identificado por host normalizado (hostname en minúsculas + puerto efectivo; se ignoran scheme, path y query). Una tarea nueva revoca de inmediato el lease de la anterior (ya no puede emitir entradas) y espera su cierre ordenado como máximo PREVIOUS_RELEASE_S = 3 s, contados contra su propio plazo; la desplazada queda `fallida`. El cierre de cualquier tarea (vencida, desplazada o apagado) sigue este orden: revocar lease, cancelar runner, esperar hasta STOP_GRACE_S = 2 s, `release_all()` (button-up y key-up de lo presionado, lo único que se emite con el lease revocado), cerrar el websocket, liberar el lock y recién entonces publicar. El actor deja de emitir a ACTOR_STOP_S = 220 s desde el POST; el corte duro es TASK_DEADLINE_S = 260 s.
Descartado: encolar la tarea nueva — la anterior suele estar obsoleta y la cola consume el plazo. Rechazarla con 409 — el evaluador no reintenta. Esperar sin tope a la anterior — un runner que ignora la cancelación bloquearía el escritorio. Identidad por URL exacta — dos URL del mismo escritorio escritas distinto correrían a la vez.
Consecuencias: nada toca el escritorio tras publicar el estado, aunque el código de un runner rebelde siga vivo (queda huérfano sin poder emitir). Un runner que no cede el event loop lo bloquea entero: el agente nunca debe reintentar en un bucle sin await. Dos escritorios distintos corren en paralelo.

## 2026-10-10 — Compuerta de escritura con plan congelado
Contexto: homónimos, duplicados, cambios de más e inyección acaban en una escritura que no debía ocurrir; la evaluación castiga más el daño que el fallo.
Decisión: toda acción es lectura o escritura; cada escritura pasa por una compuerta (auditor openai/gpt-6.1-sol) que exige que esté en el plan, que los cambios del formulario (antes/después, no valores absolutos) coincidan, que la entidad sea única y que no exista ya. El plan sale solo del texto de la tarea, congela la intención y las escrituras permitidas, y el código completa los parámetros que dependen de la pantalla (fecha = hoy_app + 1). La resolución de entidad aplica a toda tarea que nombre una, preguntas incluidas.
Descartado: verificar solo al final — una escritura hecha no se deshace; auditor en cada acción — duplica llamadas sin reducir daño; valores absolutos congelados — no sirven con fechas relativas ni con formularios que traen valores por defecto.
Consecuencias: un mecanismo para los cuatro riesgos, cuya cobertura real se mide y no se supone; cada compromiso cuesta una auditoría (~4 s, ~0.009 USD).

## 2026-10-10 — Clasificación de escrituras: negar por defecto
Contexto: dejar que el actor declare si una acción es lectura o escritura confía en el componente expuesto a la pantalla; un léxico de verbos no ve botones de solo ícono, toggles que guardan al clic ni autoguardado.
Decisión: todo clic o tecla que pueda comprometer es candidato a escritura. Un clasificador independiente, sin historial, mira un recorte de ~240x120 px y decide navegación o búsqueda o filtro, frente a compromiso; lo ambiguo es compromiso. Un detector de efectos corre después de cada acción y aborta ante una escritura no autorizada. El texto tecleado debe venir de la tarea o del código.
Descartado: declaración del actor — da el control al componente expuesto; léxico de verbos — no cubre íconos; clasificador con captura completa e historial — hereda el contexto que lo contaminaría.
Consecuencias: hay falsos positivos que cuestan pasos; los falsos negativos son el riesgo y se miden con ~100 acciones etiquetadas (meta de recall de escrituras 100 %). Con solo píxeles la clasificación no es perfecta.

## 2026-10-10 — Defensa contra inyección en profundidad
Contexto: la inyección viene dentro de datos de negocio (una nota en un registro), no como jailbreak.
Decisión: plan fijado desde la tarea, regla de texto, clasificación y compuerta, y auditor de otra familia (gpt-6.1-sol) que los dos actores candidatos (ui-tars, haiku). Sonnet queda como planificador y verificador. gpt-oss-safeguard-20b solo como señal opcional (alerta y log, nunca bloquea), según la prueba con nota inyectada.
Descartado: Llama Guard 4 como guardián — clasifica contenido dañino, no instrucciones en datos; Prompt Guard 2 — no está disponible en nuestro proveedor.
Consecuencias: es defensa en profundidad y no garantía; el verificador (sonnet) comparte familia con haiku, riesgo reconocido.

## 2026-10-10 — Actor intercambiable y A/B heurístico
Contexto: el benchmark de ubicación (una sola pantalla) dejó empatados a ui-tars-1.5-7b (2.8 s) y haiku (3.1 s), ~0.0002 USD por llamada, con el acierto de sonnet que cuesta 20 veces más; sirve para descartar, no para elegir.
Decisión: actor por config. A/B de 10 tareas × 2 repeticiones por variante; gana quien tenga menos falsos éxitos y acciones destructivas, luego más corridas pasadas; diferencia de 2 corridas o menos es empate y gana ui-tars por latencia. Es heurística, no prueba estadística. El adaptador de cada modelo normaliza coordenadas y formato de acción.
Descartado: sonnet en cada paso — 30 × 0.004 = 0.12 USD, el doble del umbral de 0.06; opus — sin ventaja medida; grok-4.7 — 22 s por paso; gemini-3.8-flash, qwen3.8-max, glm-5v-turbo — 0/10 por no devolver el JSON pedido (probable formato del prompt, sin re-probar).
Consecuencias: el experimento solo detecta diferencias grandes; ya no cabe antes de las 14:00, la decisión final queda a las 16:00 con un actor provisional.

## 2026-10-10 — Percepción por captura completa
Contexto: solo hay píxeles.
Decisión: captura completa 1280x800 al actor en cada paso; sin OCR ni detectores; el adaptador normaliza coordenadas por modelo (ui-tars reescala internamente y usa su propio formato de acción). Los recortes se usan solo en el clasificador y en la relectura de campos.
Descartado: OCR más detección de elementos — una tubería más sin evidencia de mejora; recortes o zoom por defecto — duplican llamadas.
Consecuencias: pasos fáciles de reproducir desde las trazas; la lectura de texto pequeño está sin medir (n=1 pantalla).

## 2026-10-10 — Exploración bajo demanda en vez de reconocimiento previo
Contexto: el diseño inicial hacía un reconocimiento de ~30 s abriendo los menús.
Decisión: sin reconocimiento previo; la memoria por app se llena con lo que se aprende al ejecutar tareas y se invalida cuando la verificación o un paso falla.
Descartado: reconocimiento previo — 30 s alcanzan para ~6 pasos, se pagan en la primera tarea y abrir desplegables puede dejar borradores o filtros sin auditar.
Consecuencias: la primera tarea sobre una app nueva puede ser más lenta; la memoria depende de que el servicio persista.

## 2026-10-10 — Contenedor siempre encendido y estado en SQLite
Contexto: POST /tasks responde en < 2 s y las tareas duran hasta 5 min.
Decisión: una instancia siempre encendida en Railway o Fly con volumen; estado de tareas y memoria por app en SQLite; al arrancar, las tareas en curso quedan fallidas y consultables. /health no exige token.
Descartado: Render gratuito — se duerme; serverless — tareas de hasta 5 min; estado solo en memoria — un reinicio perdería la consulta.
Consecuencias: un reinicio pierde la tarea en curso pero no su consulta; sin despliegues desde las 19:15.

## 2026-10-10 — Corte duro de tiempo y resumen determinista
Contexto: pasados 5 min lo que hagamos cae en la tarea siguiente; el formato del resumen se evalúa literalmente.
Decisión: deadline monotónico de 4:20 desde que aceptamos el POST (la espera por el lock cuenta), watchdog independiente, token de generación que revoca el emisor de entradas, 2 s para la acción en vuelo, cierre de VNC y solo después publicación. El resumen lo arma código con datos estructurados; filtro de secretos exactos y patrones de credenciales conocidos, sin cadenas genéricas.
Descartado: publicar antes de cerrar — el evaluador puede resetear con una acción en vuelo; chequear el tiempo dentro del loop — no corta una llamada bloqueada; resumen redactado por el modelo — rompe el formato sin avisar; filtro de cadenas genéricas — borra referencias legítimas.
Consecuencias: se pierden tareas que terminarían entre 4:20 y 5:00; el valor vigente de TASK_DEADLINE_S ya es 260 s.

## 2026-10-10 — Harness: esquema estricto antes de enviar y reglas puras
Contexto: el /__check del evento da pass:true para un JSON sin esquema, lo que produciría falsos verdes. El estado y el resumen se evalúan aparte del check.
Decisión: eval/schema.py valida cada prueba con pydantic estricto y no la envía si falla (error de prueba, fuera de la tabla); un 400 del /__check también cuenta como error de prueba. Las reglas sobre estado y resumen son funciones puras en eval/scoring.py. En la línea "respuesta:" el número tiene que ir justo después del prefijo y se lee como lo haría un evaluador ingenuo: un solo separador decimal y sin miles, así que "1.250.000" se lee 1.25 y falla.
Descartado: confiar en el pass del check — no valida esquema. Tomar el primer número en cualquier posición — aceptaría "hay 45". Tolerar separadores de miles — escondería un error del redactor que el evaluador sí castigaría.
Consecuencias: una prueba mal escrita se ve de inmediato; el harness es al menos tan estricto como el evaluador en el formato de la respuesta.

## 2026-10-10 — Harness: sin secretos en argumentos ni resultados
Contexto: el harness se apunta a varios escritorios (el de pruebas del evento y un juez local) y deja un JSON por corrida.
Decisión: URLs y tokens solo por entorno (DESKTOP_URL, DESKTOP_PASSWORD, DESKTOP_TOKEN, TEAM_TOKEN, OPENROUTER_API_KEY). El JSON de resultados se redacta valor por valor con aprendiz.redact y los secretos del entorno; eval/resultados/ no se versiona. Presupuesto: HARNESS_BUDGET_USD (30) contra data.usage de la key, consultado antes de cada tarea.
Descartado: pasar secretos por argumentos — quedan en el historial del shell y en la lista de procesos.
Consecuencias: si no hay OPENROUTER_API_KEY no se controla el presupuesto (se avisa). El costo sale de traces/<task_id>/resumen.json y es "sin dato" si el agente no lo escribe.

## 2026-10-10 12:17 — Núcleo del agente: presupuesto, planificador, actor y resumen
Contexto: hay que acotar costo y tiempo de cada llamada a modelos, y el formato del resumen se evalúa literalmente.
Decisión: (1) la reserva de costo se calcula antes de cada llamada con el precio de lista de OpenRouter por modelo y un margen de 20%; un modelo fuera de la tabla se reserva al precio del más caro; sin `usage.cost` se cobra la reserva completa; una llamada fallida libera su reserva. (2) El contador global persiste en un archivo con escritura atómica; un archivo corrupto es un error, no un reinicio a cero. (3) `ACTOR_MODEL` y `PLANNER_MODEL` se leen solo de `Settings`. (4) Todo JSON de un modelo se valida con esquema cerrado y se reintenta una vez con el error de validación (`ask_json`). (5) `1.234` en un valor numérico se lee como miles; `12,5` y `3.14` como decimales. (6) Un `fail` o un resumen de pregunta imposible nunca lleva línea `respuesta:`. (7) El adaptador de ui-tars no se implementa.
Descartado: precios inventados altos — sobrerreservan y cortan tareas sin motivo; reiniciar el contador si el archivo está roto — borraría el tope diario; adaptador de ui-tars — devolvió JSON mal formado y sin su plantilla propia no emitió acciones.
Consecuencias: el tope por tarea se respeta aun con varias imágenes por llamada; los precios de la tabla hay que mantenerlos a mano; un valor numérico con punto y tres decimales exactos se reporta como entero de miles.

## 2026-10-10 12:17 — Controles: clasificador, compuerta, detector, verificador y fechas
Contexto: ninguna escritura debe salir sin autorización y cada control es un modelo que puede fallar o ser engañado por la pantalla.
Decisión: (1) El clasificador ve solo un recorte de 240x120 y el tipo de evento; error, timeout o respuesta inválida cuentan como `compromiso`; en modo verificación todo lo que no sea navegación o búsqueda es `compromiso`; la caché guarda la clase cruda (clave: hash del recorte y tipo de evento) y el modo se aplica al leerla; los fallos no se cachean. (2) `BudgetExceeded` nunca se convierte en `compromiso`: se propaga y corta la tarea. (3) La compuerta aplica reglas en código antes y después del auditor y las impone aunque el auditor diga `permitir`: plan sin escrituras, valores a teclear fuera del plan, falta de búsqueda previa, más de un candidato, crear algo existente, segundo exporte, escritura o valor fuera del plan. Al auditor no se le pasan `razon` ni `postcondicion` del actor. (4) Error del auditor: `denegar_corregible` la primera vez y `denegar_final` la segunda. (5) El detector compara píxeles primero (hasta 20 píxeles de diferencia es ruido) y llama al modelo solo tras compromiso o campo de registro, o si cambió el 5% de la pantalla; si el modelo falla tras un compromiso se asume que persistió. (6) El verificador no navega; el código fuerza `verificado=false` si en una escritura hay algo no verificable o nada observado, si una pregunta no trae respuesta o si un fallo no trae datos. (7) Las fechas se calculan en código con una gramática cerrada (`hoy_app±N`, `proximo_<día>`, `anterior_<día>`); `proximo_X` y `anterior_X` son estrictamente después y antes de hoy; las fechas numéricas se leen día primero.
Descartado: que el auditor decida solo — una respuesta engañada pasaría; tratar el fallo del detector como "no persistió" — abriría un camino sin segunda línea; que el modelo calcule fechas — errores de calendario.
Consecuencias: hay más falsos positivos (denegaciones innecesarias) a cambio de menos falsos negativos; el umbral de píxeles y el 5% son valores iniciales a recalibrar con trazas.

## 2026-10-10 12:17 — Loop del agente: pasos, evidencia y cierre
Contexto: el loop corre en un escritorio compartido y bajo un plazo duro; un bug previo congeló el event loop por un `while True` que reintentaba sin `await`.
Decisión: (1) Todo evento pasa por un único punto (`_dispatch`); hover, scroll y espera son lecturas; `type` solo se acepta si el último clic cayó en un campo de texto, y el texto debe ser un fragmento de la tarea, un valor del plan o un parámetro resuelto por código (nunca saltos de línea, que pulsarían Enter); `Tab` se rechaza. (2) Un clic sobre un campo de registro solo da foco; la edición (type, o una tecla) pasa por la compuerta. (3) `fill_form` se autoriza una vez, se ejecuta con clic directo por campo, recaptura antes de cada clic, pide al actor que reubique el campo si algo cambió fuera del campo anterior, usa Ctrl+A solo tras clasificar el punto como campo de texto, relee el valor y reescribe una vez; si aun así no coincide deja una nota al actor en vez de abortar. (4) Una denegación corregible vuelve al actor con su motivo hasta 2 veces por acción; la tercera termina `fallida`; en verificación no termina la tarea. (5) Una escritura autorizada sin cambio visible no se puede repetir hasta que haya una búsqueda nueva. (6) Un diálogo de confirmación se acepta como continuación si el auditor lo declara de la misma escritura; vale solo hasta el siguiente evento ejecutado. (7) La verificación (máx. 10 pasos, hasta `deadline` menos 10 s) navega en modo lectura y luego el verificador decide; si el actor declara `done` en una tarea de escritura sin ninguna escritura autorizada, la tarea es `fallida`. (8) `read_today` solo corre si el plan tiene parámetros. (9) El tope de pasos se informa como `actor_deadline`. (10) El cierre libera entradas y cierra el cliente, cada paso con tope de 2 s, y `CancelledError` se re-lanza tras escribir `resumen.json`; trazas con la captura reducida a 640x400 y secretos redactados.
Descartado: el actor declara si un evento escribe — es el componente expuesto a la pantalla; abortar el relleno por un valor releído distinto — falsos abortos con campos con máscara, el verificador lo detecta al final; reintentar a ciegas un clic sin efecto — duplica escrituras; leer "Hoy" siempre — ~3 s en cada tarea aunque no se use.
Consecuencias: una tarea de escritura cuesta varias llamadas por campo (clasificación y relectura); si los pasos del actor siguen sin cambios visibles, el actor puede quedarse sin salida y la tarea falla en vez de arriesgar una escritura duplicada.

## 2026-10-10 — Plazos de la tarea: actor a 250 s y corte duro a 285 s
Contexto: el evaluador consulta cada 5 s y deja de consultar a los 300 s. Con el actor a 220 s y el corte a 260 s dejábamos 40 s sin usar, y las tareas de la app de práctica y del juez local terminan entre 115 y 225 s, con cortes frecuentes por plazo del actor.
Decisión: ACTOR_STOP_S = 250 y TASK_DEADLINE_S = 285, contados desde el POST. El cierre ordenado (gracia de 2 s, release_all, cierre del websocket) publica el estado hacia los 288 s, antes de las consultas de 290, 295 y 300 s. La verificación conserva su reserva de 10 s antes del corte duro.
Descartado: cortar más cerca de 300 s — un reinicio del escritorio con una acción en vuelo cuenta contra la tarea siguiente, y la latencia de red entre el POST del evaluador y el nuestro come parte del margen.
Consecuencias: 30 s más para el actor y 25 s más de corte duro; el margen contra el reset del evaluador baja de 40 s a 15 s.

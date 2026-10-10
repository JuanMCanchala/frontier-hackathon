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

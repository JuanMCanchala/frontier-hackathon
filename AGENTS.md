# AGENTS.md

Contexto y reglas de trabajo para este repo. Léelo completo antes de cada tarea.

## Proyecto

- Reto: agente que opera apps de escritorio sin API, solo por pantalla (VNC 1280x800), a partir de tareas en lenguaje natural. Se evalúa en apps que nunca ha visto.
- Entrypoint: servicio HTTP con `GET /health`, `POST /tasks` (responde `task_id` en < 2 s) y `GET /tasks/{task_id}` (`estado`, `resumen`). Bearer token en todas las llamadas.
- Cómo correr: `uv sync`, copiar `.env.example` a `.env` y rellenar los secretos; luego `uv run aprendiz-api` (lee `HOST`/`PORT` del entorno, 8000 por defecto) o `uv run uvicorn aprendiz.api:app --port ${PORT:-8000}`. Los `Settings` se cargan al arrancar y fallan si faltan `OPENROUTER_API_KEY` o `TEAM_TOKEN`. El runner del servicio es `AgentRunner` (`src/aprendiz/agent/`).
- Cómo testear: `uv run pytest -q` (offline: servidor RFB falso y runner falso inyectado) y `uv run ruff check src tests eval`. Humo contra el escritorio real, solo lectura: `uv run python eval/smoke_driver.py` con `DESKTOP_URL` y `DESKTOP_PASSWORD`. Ruff solo sobre `src tests eval`; nunca `format` ni `--fix` sobre `experiments/` ni `docs/`.
- Contenedor: `docker build -t aprendiz:local .` y `docker run -d -p 8099:8080 -e PORT=8080 -e TEAM_TOKEN=... -e OPENROUTER_API_KEY=... -v aprendiz-data:/app/data aprendiz:local`. El estado (SQLite y trazas) vive en `/app/data`; el entrypoint arranca como root solo para dar permisos al volumen y baja al usuario `app` antes de lanzar `aprendiz-api`. Railway: `railway.json` (healthcheck `/health`); los secretos van como variables del servicio, nunca en la imagen.
- Harness de evaluación: `uv run --env-file .env python -m eval.harness --pruebas DIR [--pruebas DIR2] [--solo E1,V3] [--repeticiones N] [--etiqueta texto] [--api http://127.0.0.1:8000] [--dry-run]`. Por tarea: reset → POST /tasks → polling → /__check → reglas de `eval/scoring.py`. Secretos y URLs solo por entorno (`DESKTOP_URL` base http(s), `DESKTOP_PASSWORD`, `DESKTOP_TOKEN`, `TEAM_TOKEN`, `OPENROUTER_API_KEY`, `HARNESS_BUDGET_USD`, `TRACES_DIR`). Las pruebas del evento entran siempre por `--pruebas` y no se copian al repo. Resultados en `eval/resultados/` (ignorado por git, con secretos redactados). `--dry-run` solo valida esquemas.

## Cómo trabajamos

- Una tarea por vez. Si algo no está claro, pregúntame antes de asumir.
- Antes de cambiar código, lee los archivos involucrados. No inventes APIs ni campos: verifica contra el código, la spec o una respuesta real.
- Cada cambio termina con la verificación que te pida (test, harness o comando). Si falla, me lo dices con la salida exacta; no lo maquilles.
- No hardcodees casos del brief ni respuestas esperadas. El set de evaluación es oculto y trae casos nuevos.
- Nada de secretos en el código ni en los logs. Las keys se leen de variables de entorno.

## Colaboración (rama Bustamante)

Este repo es de JuanMCanchala (`JuanMCanchala/frontier-hackathon`). Colaboramos con permiso de escritura; no somos dueños. Nada entra a `main` por push directo.

- Toda la implementación y todos los commits van en la rama `Bustamante`. Si no existe, créala desde `origin/main`. No trabajes ni hagas commit en `main` ni en otra rama.
- Cada tarea la ejecuta un solo agente. Ese agente hace solo los commits de su tarea: un cambio, un propósito, sin mezclar tareas ajenas en el mismo commit.
- Al empezar una tarea, abre un pull request de `Bustamante` hacia `main` y recién entonces sigue con el trabajo. Si la rama todavía no diverge de `main`, el primer commit de la tarea es el que permite abrir el PR; ábrelo en ese momento, antes de continuar. Si ya hay un PR abierto de esa rama, los commits de la tarea entran ahí; no abras otro.
- El PR lo crea este agente desde este entorno con `gh`, autenticado como `Juan-GuevaraBusta`. No instales un MCP de GitHub si `gh` ya tiene scope `repo` y permiso de escritura.
- No mergees el PR ni pushees a `main` salvo que te lo pidan. Si el push o el PR fallan por permisos, para y dilo; no cambies de rama para rodear el fallo.
- Nunca hagas push forzado ni merge forzado (`--force`, `--force-with-lease`, `push -f`, merge con admin o saltando reglas). Solo si yo apruebo primero y, después de esa aprobación, te doy la indicación explícita. Sin las dos, ni lo intentes.

## Estándar de código

- Código, identificadores y comentarios en inglés. Documentación del repo (README, diseño, spec, log) en español.
- Funciones pequeñas con una responsabilidad; nombres que expliquen la intención; sin código muerto ni TODOs sueltos.
- Tipos explícitos en las fronteras (entrada del entrypoint, respuestas de servicios externos) y validación con schema.
- Errores: nunca tragarlos en silencio. Fallar con un mensaje claro o devolver la respuesta de respaldo definida en el diseño, y dejarlo en el log.
- Comentarios solo donde aportan el _por qué_ (una decisión, una restricción o un comportamiento no obvio del sistema externo). No comentar lo que el código ya dice.
- Toda llamada externa (LLM, servidor del entorno) pasa por su módulo cliente, con timeout, reintentos acotados y log estructurado.
- Configuración (URLs, rutas, nombres de campos, modelo, límites) en un solo archivo de config, no repartida por el código.

## Código propio

- Escribe implementaciones propias. No copies código literal de otros repos ni de ejemplos de terceros; si una librería resuelve el problema, agrégala como dependencia con el gestor de paquetes.
- En el repo no van referencias a material de investigación externo: README, diseño y comentarios hablan de nuestro sistema y nuestras decisiones.

## Decisiones

Cada decisión de diseño o de implementación que no sea obvia va en `DECISIONES.md` con este formato:

```text
## <fecha hora> — <decisión>
Contexto: <qué problema había>
Decisión: <qué se eligió>
Descartado: <alternativa> — <por qué no>
Consecuencias: <qué se vuelve más fácil / más difícil>
```

## Commits

- Commits pequeños y atómicos, en español, en imperativo y con prefijo convencional: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
- El mensaje explica el porqué cuando no es obvio. Sin firmas, sin trailers y sin texto generado de relleno.
- Cada commit de una tarea va en `Bustamante`, hecho por el agente de esa tarea. Commit y push a esa rama, y el PR hacia `main`, forman parte de ejecutar la tarea. El push es siempre fast-forward; no reescribas historia publicada. No mergees ni pushees a `main` sin que te lo pidan.
- Push forzado y merge forzado están prohibidos salvo aprobación mía seguida de una indicación explícita.

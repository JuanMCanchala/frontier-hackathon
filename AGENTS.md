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

## Colaboración

Este repo es de JuanMCanchala (`JuanMCanchala/frontier-hackathon`). Colaboramos con permiso de escritura; no somos dueños.

- Todos los commits van a `main`. No abras rama ni pull request por tarea: compartir una rama aparte fue lo que generó conflictos.
- Antes de editar, `git pull --ff-only origin main`. Si no puede avanzar en fast-forward, integra con merge y no reescribas historia publicada. Si el conflicto no se resuelve con lo que dicen los dos lados, para y pregunta.
- Todos los pushes van a `origin/main`. Nunca hagas push a otra rama.
- Si `main` está atrasado respecto a la rama `Bustamante` (`git fetch origin` y `git log main..origin/Bustamante` muestra commits), haz merge de `origin/Bustamante` en `main` solo si es sano: sin conflictos que no se resuelvan con lo que dicen los dos lados, y con `uv run pytest -q` y `uv run ruff check src tests eval` en verde después del merge. Si no es sano, no lo integres: para y pregunta con la salida exacta.
- Cada tarea la ejecuta un solo agente. Ese agente hace solo los commits de su tarea: un cambio, un propósito, sin mezclar tareas ajenas en el mismo commit.
- El push a `origin/main` es siempre fast-forward. Si lo rechazan, vuelve a integrar y reintenta. No cambies de rama para rodear el fallo.
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
- Cada commit de una tarea va a `main`, hecho por el agente de esa tarea. Commit y push fast-forward a `origin/main` forman parte de ejecutar la tarea. No reescribas historia publicada.
- Push forzado y merge forzado están prohibidos salvo aprobación mía seguida de una indicación explícita.

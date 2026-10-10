# Aprendiz

Agente que opera aplicaciones de escritorio sin API, solo por pantalla (VNC), y ejecuta tareas pedidas en lenguaje natural en apps que nunca ha visto. Reto 1 de Frontier (Lab10), 10 de octubre de 2026.

Servicio desplegado: `https://aprendiz-production.up.railway.app` (contrato abajo; `POST /tasks` y `GET /tasks/{id}` exigen el token del equipo).

## Diseño

- [Documento de diseño](docs/diseno.md)
- [Diagramas de arquitectura](docs/arquitectura.md): contenedores, componentes del ciclo de la tarea y secuencia de una tarea
- [Observabilidad: exportador de trazas a Langfuse](docs/observabilidad.md) (complemento posterior a la entrega del diseño)
- [Registro de decisiones](DECISIONES.md)
- [Benchmark de modelos de visión](experiments/grounding/RESULTADOS.md)

## Contrato

| Llamada                | Qué hace                                                                                                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /health`          | `200 {"ok": true}` cuando el servicio está listo. No exige token.                                                                                              |
| `POST /tasks`          | Body `{"tarea": "...", "escritorio": {"url": "wss://…/websockify", "password": "…"}}`. Responde `{"task_id": "…"}` en menos de 2 s y trabaja en segundo plano. |
| `GET /tasks/{task_id}` | `{"estado": "en_curso" \| "terminada" \| "fallida", "resumen": "…"}`                                                                                           |

`POST /tasks` y `GET /tasks/{id}` llevan `Authorization: Bearer <TEAM_TOKEN>`. Cada tarea tiene un corte duro a los 285 s desde el `POST` (el actor deja de emitir a los 250 s): el servicio cierra la conexión VNC y recién después publica el estado.

## Correr en local

Requisitos: [uv](https://docs.astral.sh/uv/) y Python 3.12 o superior.

```bash
uv sync
cp .env.example .env     # completar OPENROUTER_API_KEY y TEAM_TOKEN
uv run aprendiz-api      # escucha en HOST:PORT (0.0.0.0:8000 por defecto)
```

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/tasks \
  -H "Authorization: Bearer $TEAM_TOKEN" -H 'content-type: application/json' \
  -d '{"tarea": "¿Cuántas facturas vencidas tiene Ferretería Ruiz?", "escritorio": {"url": "wss://<host>/websockify", "password": "<contraseña>"}}'
curl -s localhost:8000/tasks/<task_id> -H "Authorization: Bearer $TEAM_TOKEN"
```

Toda la configuración se lee del entorno (`src/aprendiz/config.py`); `.env.example` lista las variables. Los secretos no tienen valor por defecto: sin `OPENROUTER_API_KEY` y `TEAM_TOKEN` el servicio no arranca.

## Tests

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Los tests no usan red: el driver se prueba contra un servidor RFB falso y la API con runners falsos. El CI corre lo mismo en cada pull request a `main`.

Humo de solo lectura contra un escritorio real (conecta y guarda una captura en `traces/smoke.png`):

```bash
DESKTOP_URL=wss://<host>/websockify DESKTOP_PASSWORD=<contraseña> uv run python eval/smoke_driver.py
```

## Harness de evaluación

Mide el agente igual que el evaluador: por cada prueba resetea el escritorio, manda la tarea a nuestra API, consulta cada 5 s y califica con el `/__check` oficial más las reglas de estado y resumen (`eval/scoring.py`). Reporta por categoría éxito, falsos éxitos, acciones destructivas, p50/p95 de tiempo, costo medio y pass^k.

```bash
export DESKTOP_URL=https://<host>   DESKTOP_PASSWORD=...   DESKTOP_TOKEN=...
export TEAM_TOKEN=...   OPENROUTER_API_KEY=...
uv run aprendiz-api &   # la API tiene que estar levantada
uv run python -m eval.harness --pruebas <carpeta del material>/pruebas --pruebas eval/pruebas --etiqueta base
```

- Las URLs y los secretos entran solo por entorno; nunca por argumentos.
- Las pruebas del evento no se copian al repo: entran por `--pruebas`. Las 12 propias están en `eval/pruebas/` (variaciones de la sección 8.1 del diseño, con datos de la app de práctica).
- `--dry-run` valida los esquemas sin red; `--solo E1,V3` filtra; `--repeticiones 3` calcula pass^3.
- Los resultados quedan en `eval/resultados/` (fuera de git, con secretos redactados). El harness se corta si el gasto de la key llega a `HARNESS_BUDGET_USD` (30 por defecto).

Línea base: con el runner que siempre responde `fallida`, las 17 pruebas (5 del evento + 12 propias) dan **2/17**, con 0 falsos éxitos y 0 acciones destructivas. Es el piso contra el que se compara el agente.

## Mini app CRUD local (generalización)

Segunda app con otro layout y etiquetas, en un contenedor propio con Xvfb, Chromium en kiosko y VNC por websockify (`1280×800`). Sirve para medir generalización y para probar autoguardado en celda; **no sustituye** al escritorio del evento ni se despliega en Railway (solo uso local).

```bash
docker build -t mini-crud:local apps/mini_crud
export VNC_PASSWORD='<contraseña de hasta 8 caracteres>'
./apps/mini_crud/levantar.sh   # publica el puerto 6080
DESKTOP_URL=ws://127.0.0.1:6080/websockify DESKTOP_PASSWORD="$VNC_PASSWORD" \
  uv run python eval/smoke_driver.py
```

La contraseña VNC solo va en el entorno al arrancar el contenedor; no va en la imagen ni en el repo. El paquete vive en `apps/mini_crud/` y no forma parte del wheel instalable `aprendiz`.

## Contenedor y despliegue

```bash
docker build -t aprendiz:local .
docker run -d -p 8099:8080 -e PORT=8080 -e TEAM_TOKEN=... -e OPENROUTER_API_KEY=... \
  -v aprendiz-data:/app/data aprendiz:local
```

El estado (SQLite y trazas) vive en `/app/data`, montado como volumen. El proceso corre sin root. En Railway el servicio usa `railway.json` (healthcheck en `/health`), un volumen en `/app/data` y los secretos como variables del servicio, nunca en la imagen.

## Estructura

```text
src/aprendiz/
  api.py       contrato HTTP
  tasks.py     supervisor: lock por escritorio, corte duro, orden de cierre
  store.py     estado de tareas en SQLite
  redact.py    filtro de secretos del resumen
  vnc/         driver RFB propio sobre websocket
eval/
  harness.py   corrida de pruebas contra un escritorio
  scoring.py   reglas de calificación
  pruebas/     pruebas propias
tests/         tests offline (servidor RFB falso, runners falsos)
apps/mini_crud/ mini app CRUD local (VNC, fuera del wheel)
docs/          diseño y diagramas
```

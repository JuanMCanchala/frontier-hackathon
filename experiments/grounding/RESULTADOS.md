# Benchmark de ubicación de elementos (grounding)

Captura real de la app de pruebas (`sigecom-inicio.png`, 1280×800). Cada modelo devuelve en JSON el centro de 5 elementos (3 menús, un ítem de desplegable y un encabezado) y lee la fecha "Hoy". Temperatura 0. Acierto = el punto cae dentro de la caja medida a mano.

`OPENROUTER_API_KEY=... uv run grounding_bench.py sigecom-inicio.png <corridas> <modelo>...`

## Corrida 1 (2 repeticiones por modelo)

| Modelo | Acierto | Fecha | Latencia | Costo/llamada |
| --- | --- | --- | --- | --- |
| bytedance/ui-tars-1.5-7b | 10/10 | 2/2 | 3.1 s | $0.00018 |
| anthropic/claude-haiku-5.5 | 10/10 | 2/2 | 3.3 s | $0.00021 |
| openai/gpt-6-luna | 10/10 | 2/2 | 3.9 s | $0.00019 |
| anthropic/claude-sonnet-5.5 | 10/10 | 2/2 | 4.6 s | $0.00421 |
| openai/gpt-6.1-sol | 10/10 | 2/2 | 5.4 s | $0.00292 |
| anthropic/claude-opus-5.5 | 10/10 | 2/2 | 5.5 s | $0.00843 |
| x-ai/grok-4.7 | 10/10 | 2/2 | 21.8 s | $0.01384 |
| qwen/qwen3.8-flash | 8/10 | 2/2 | 16.8 s | $0.00035 |
| google/gemini-3.8-flash | 0/10 | 0/2 | 6.9 s | $0.00322 |
| qwen/qwen3.8-max-0902 | 0/10 | 0/2 | 15.2 s | $0.00521 |
| z-ai/glm-5v-turbo | 0/10 | 0/2 | 13.4 s | $0.00346 |

Los 0/10 no devolvieron el JSON pedido: fallo de formato, igual descalificante para un actor que depende de salida estructurada.

## Corrida 2 (6 repeticiones, los tres más baratos)

| Modelo | Acierto | Fecha | Latencia media | Costo/llamada |
| --- | --- | --- | --- | --- |
| bytedance/ui-tars-1.5-7b | 30/30 | 6/6 | 2.8 s | $0.00018 |
| anthropic/claude-haiku-5.5 | 30/30 | 6/6 | 3.1 s | $0.00021 |
| openai/gpt-6-luna | 30/30 | 6/6 | 5.3 s | $0.00014 |

## Limitaciones

La pantalla es simple y los mejores modelos saturan. Sirve para descartar, no para elegir entre los primeros: esa elección se hace con tareas completas en el harness.

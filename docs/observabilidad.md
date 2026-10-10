# Observabilidad: exportador de trazas a Langfuse

Complemento posterior al [documento de diseño](diseno.md), entregado a las 12:00. Describe un componente opcional que no cambia el ciclo de la tarea.

## Qué es

Un exportador que reenvía a un Langfuse auto-hospedado la traza local de cada tarea, ya redactada, y las puntuaciones del harness. La traza local (`traces/<task_id>/resumen.json` y `pasos.jsonl`) sigue siendo la fuente de verdad.

| Propiedad | Decisión |
| --- | --- |
| Acoplamiento | Módulo aparte que lee los archivos de traza; no se ejecuta dentro del loop del agente ni autoriza acciones |
| Fallos | Si falta configuración o Langfuse no responde, la tarea no se entera: timeout y reintentos acotados, y la traza local queda igual |
| Datos | Solo la traza ya redactada. La contraseña del escritorio y los tokens nunca entran a la traza |
| Despliegue | Auto-hospedado en la misma zona que el servicio; las capturas no salen a una nube externa |
| Configuración | En un solo sitio, por variables de entorno |

El host es el servicio `langfuse-web` de Railway, en el proyecto `frontier-langfuse`, aparte del servicio `aprendiz` (ver `DECISIONES.md`). Mientras `LANGFUSE_HOST` y las llaves estén vacías, el exportador no hace llamadas.

Uso: el harness exporta al cerrar la corrida, después de escribir `eval/resultados/`. Para una corrida ya guardada se usa `uv run --env-file .env python -m eval.langfuse_export --resultados RUTA`.

## Alternativas descartadas

| Alternativa | Por qué no |
| --- | --- |
| LangSmith | El stack no es LangChain |
| LangSmith y Langfuse a la vez | Duplican trabajo y se pisan |
| Langfuse en la nube | Saca capturas del volumen del servicio |
| Exportar desde dentro del loop | Acopla la observabilidad a la ruta crítica de la tarea, con sus plazos duros |

## Límite de confianza

| Límite | Qué lo cruza | Regla |
| --- | --- | --- |
| Servicio → exportador | Traza local ya redactada y puntuaciones del harness | Timeout y reintentos acotados; si falla, la tarea sigue; no salen la contraseña ni los tokens; no autoriza acciones |

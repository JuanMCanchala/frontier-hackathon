# Aprendiz — diagramas de arquitectura

Complemento de [diseno.md](diseno.md). Los diagramas están en Mermaid y se leen con cualquier visor de Markdown que lo soporte (GitHub, VS Code con extensión).

## 1. Contenedores y límites de confianza

### 1a. Contenedores

Tres zonas. Lo que viene de la **zona no confiable** (la pantalla del escritorio) es dato: puede informar al actor, pero nunca autoriza una escritura nueva. Esa autoridad solo la tiene el plan, que sale del texto de la tarea y se fija antes de la primera captura. El control de acciones es negar por defecto: todo clic o tecla que pueda comprometer pasa por el clasificador y, si lo es, por la compuerta.

```mermaid
flowchart LR
    EV["Evaluador<br/>consulta cada 5 s"]

    subgraph SVC["Aprendiz — contenedor siempre encendido (zona confiable)"]
        direction TB
        API["API FastAPI<br/>/health · POST /tasks · GET /tasks/id<br/>Bearer token (salvo /health)"]
        ORQ["Orquestador + ciclo de la tarea<br/>ver vista 1b"]
        WD["Watchdog<br/>deadline monotónico 4:20<br/>revoca el token de generación"]
        DRV["Driver RFB propio<br/>clic 150 ms · estabilidad ≤ 1.5 s"]
        DB[("SQLite en volumen<br/>estado de tareas · memoria por app")]
        TRZ[("Trazas por tarea")]
    end

    subgraph LLM["OpenRouter (externo)"]
        MOD["Sonnet 5.5 · GPT-6.1 Sol<br/>actor UI-TARS 7B o Haiku 5.5<br/>clasificador pequeño"]
    end

    subgraph UNT["Zona NO confiable"]
        APP["Escritorio VNC 1280×800<br/>app desconocida en kiosko<br/>su contenido es dato, no instrucción"]
    end

    EV -->|"HTTPS · POST responde task_id < 2 s<br/>GET estado + resumen"| API
    API -->|"graba y encola · asíncrono"| ORQ
    ORQ -->|"entradas autorizadas"| DRV
    WD -.->|"revoca al vencer"| DRV
    DRV <-->|"RFB sobre wss<br/>entradas ↔ framebuffer"| APP
    ORQ <-->|"texto, capturas y recortes<br/>nunca la contraseña"| MOD
    ORQ --- DB
    ORQ -.- TRZ

    classDef untrusted fill:#fde2e2,stroke:#c0392b,color:#000
    classDef external fill:#fff4d6,stroke:#b9770e,color:#000
    classDef ours fill:#e3f1e3,stroke:#2e7d32,color:#000
    class APP untrusted
    class MOD external
    class API,ORQ,WD,DRV,DB,TRZ ours
```

### 1b. Componentes del ciclo de la tarea

Cada caja dice qué modelo usa; todas las llamadas a modelos salen por el mismo cliente con timeout, reintentos acotados y tope de costo.

```mermaid
flowchart LR
    P["1 · Planificador<br/><i>Sonnet 5.5</i><br/>solo el texto de la tarea<br/>→ intención y escrituras permitidas<br/>congeladas + parámetros"]
    A["2 · Loop del actor<br/><i>UI-TARS 7B o Haiku 5.5</i><br/>captura → acción<br/>tope 45 pasos"]
    C["3 · Clasificador de recorte<br/><i>modelo pequeño</i><br/>240×120 px, sin historial<br/>navegación · búsqueda · compromiso"]
    G{"4 · Compuerta<br/><i>GPT-6.1 Sol</i><br/>¿en el plan? ¿entidad única?<br/>diff del formulario"}
    X["Driver<br/>ejecuta la acción"]
    D["5 · Detector de efectos<br/><i>modelo pequeño</i><br/>captura antes/después"]
    V["6 · Verificador<br/><i>Sonnet 5.5</i><br/>solo lecturas · antes/después<br/>exige confirmación visible"]
    R["7 · Redactor<br/><i>código</i><br/>resumen determinista<br/>+ filtro de secretos"]

    P --> A
    A -->|"clic, Enter o atajo"| C
    C -->|"lectura"| X
    C -->|"compromiso o duda"| G
    G -->|"permitida"| X
    G -->|"denegada corregible<br/>(vuelve con el motivo, máx. 2)"| A
    G -->|"ambigua o sin evidencia"| R
    X --> D
    D -->|"sin efecto no autorizado"| A
    D -->|"escritura no autorizada → aborta"| R
    A -->|"actor declara terminado"| V
    V -->|"verificado o no"| R

    classDef gate fill:#dbe7ff,stroke:#1f4fb4,color:#000
    classDef ours fill:#e3f1e3,stroke:#2e7d32,color:#000
    class P,A,C,X,D,V,R ours
    class G gate
```

Líneas continuas: control y datos dentro del servicio. Punteadas: llamadas a modelos, almacenamiento y el watchdog.

Qué marca cada límite:

| Límite | Qué lo cruza | Regla |
| --- | --- | --- |
| Cliente → servicio | Tarea y credenciales del escritorio | Bearer token en `POST /tasks` y `GET /tasks/{id}`; `/health` no lo exige y lo acepta si viene. La contraseña del escritorio solo se usa en la autenticación VNC dentro del driver. |
| Servicio → proveedores LLM | Texto de la tarea, capturas, recortes, plan | La contraseña y los tokens no se envían. Las capturas sí pueden mostrar datos sensibles del negocio. Timeout y reintentos acotados en el módulo cliente. |
| Servicio ↔ escritorio | Entradas de teclado y ratón; framebuffer | Todo lo que vuelve es píxeles no confiables. Ninguna escritura se autoriza por lo que la pantalla diga. |

## 2. Secuencia de una tarea

Tres caminos de salida: cierre normal (verificador), corte de tiempo (watchdog) y aborto por escritura no autorizada. En todos, la conexión VNC se cierra antes de publicar el estado, para que ninguna acción tardía caiga en la tarea siguiente. Una denegación corregible vuelve al actor con el motivo; ambigüedad de entidad o falta de evidencia terminan la tarea `fallida`.

```mermaid
sequenceDiagram
    autonumber
    participant EV as Evaluador
    participant API as API FastAPI
    participant ORQ as Orquestador
    participant WD as Watchdog
    participant PLAN as Planificador (sonnet)
    participant ACT as Loop del actor
    participant CLS as Clasificador de recorte
    participant GATE as Compuerta (gpt-6.1-sol)
    participant DRV as Driver RFB
    participant DET as Detector de efectos
    participant VER as Verificador (sonnet)
    participant RED as Redactor

    EV->>API: POST /tasks (tarea, escritorio)<br/>Bearer token
    API->>ORQ: grabar en SQLite y encolar (ASÍNCRONO)<br/>deadline = t0 + 260 s (monotónico)
    API-->>EV: 200 {task_id} en menos de 2 s
    ORQ->>WD: armar watchdog con el deadline
    loop cada 5 s, hasta estado final
        EV->>API: GET /tasks/task_id
        API-->>EV: {estado: en_curso, resumen: ""}
    end

    ORQ->>ORQ: tomar el lock del escritorio (la espera cuenta contra el deadline)
    ORQ->>DRV: conectar, autenticar VNC, validar 1280x800
    alt falla la conexión o la resolución no es 1280x800
        DRV-->>ORQ: error con mensaje claro (sin contraseña)
        ORQ->>RED: fallida
    else conexión válida
        Note over ORQ,PLAN: El plan se fija ANTES de la primera captura
        ORQ->>PLAN: texto de la tarea (sin pantalla)
        PLAN-->>ORQ: plan: tipo, escrituras permitidas, entidad nombrada,<br/>parámetros pendientes (hoy_app + 1), bandera sospechosa
    end

    alt el plan es peligrosa por el texto (masiva o irreversible)
        ORQ->>RED: fallida sin tocar la app
    else tarea ejecutable
        ORQ->>ACT: plan + pistas de la memoria
        loop mientras haya tiempo, menos de 45 pasos y costo menor que 0.15 USD
            ACT->>DRV: capturar (espera de estabilidad, tope 1.5 s)
            DRV-->>ACT: framebuffer estable
            ACT->>ACT: acción propuesta + regla de texto<br/>(solo texto de la tarea o calculado por código)
            alt scroll o movimiento
                ACT->>DRV: ejecutar
            else clic, Enter o atajo (candidato a compromiso)
                ACT->>CLS: recorte 240x120 alrededor del punto, sin historial
                CLS-->>ACT: navegación, búsqueda/filtro o compromiso
                alt navegación o búsqueda/filtro
                    ACT->>DRV: ejecutar (clic de 150 ms)
                else compromiso, error o duda del clasificador
                    ACT->>GATE: plan + resolución de entidad<br/>+ capturas: formulario al abrir y al comprometer
                    Note over GATE: ¿está en el plan? ¿cambios del formulario coinciden?<br/>¿entidad única? ¿ya existe? ¿un solo exporte?
                    alt permitida
                        GATE-->>ACT: permitir
                        ACT->>DRV: ejecutar la escritura
                    else denegada por motivo corregible (falta búsqueda, fuera de plan)
                        GATE-->>ACT: denegar con motivo (máximo 2 veces por acción)
                    else entidad ambigua o sin evidencia
                        GATE-->>ORQ: denegar: fallida nombrando el dato que distingue a cada candidato
                    end
                end
            end
            Note over DRV,DET: el detector es síncrono: la acción siguiente espera su veredicto
            DRV-->>DET: capturas antes y después de la acción
            DET-->>ORQ: ¿hubo escritura no autorizada?
            opt el detector encontró una escritura no autorizada
                ORQ->>ACT: abortar (no se intenta deshacer)
                Note over ORQ: estado fallida, el resumen lo declara
            end
            ACT-->>ORQ: traza: captura, acción, costo
        end
    end

    alt el actor declara terminado antes del deadline
        ACT->>VER: tarea hecha (el actor declara, el verificador manda)
        VER->>DRV: relectura de solo lectura (compromisos denegados)
        DRV-->>VER: capturas de la entidad antes y después, mensaje de confirmación
        VER-->>ORQ: verificado o no, con evidencia
    else deadline, tope de costo o tope de pasos
        WD->>DRV: revocar token de generación (el emisor descarta eventos viejos)
        WD->>ORQ: deadline cumplido
        Note over DRV,ORQ: la acción en vuelo tiene 2 s para terminar<br/>sin verificación completa el estado es fallida
    end

    ORQ->>DRV: cerrar conexión VNC
    DRV-->>ORQ: cerrada
    ORQ->>RED: estado + datos estructurados
    RED->>RED: armar resumen, línea "respuesta:", filtro de secretos
    RED-->>ORQ: resumen final
    ORQ->>API: grabar en SQLite y publicar estado final (terminada o fallida)
    EV->>API: GET /tasks/task_id
    API-->>EV: {estado: terminada o fallida, resumen}
```

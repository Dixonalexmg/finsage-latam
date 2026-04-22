# Architecture Decision Records

Registro de decisiones arquitectonicas en formato ADR. La idea de este archivo
no es listar gustos personales, sino dejar claro por que el demo se despliega
como se despliega, que limites tiene y que decisiones fueron deliberadas.

## Formato

```text
## ADR-NNN: Titulo corto

- Status: Proposed | Accepted | Deprecated | Superseded
- Date: YYYY-MM-DD
- Context: Que problema estamos resolviendo
- Decision: Que decidimos
- Consequences: Que cambia a partir de esta decision
```

## ADR-001: Railway deploy en un contenedor combinado para portfolio

- **Status:** Accepted
- **Date:** 2026-04-22
- **Context:** El objetivo principal del proyecto es servir como pieza de portfolio para roles de AI engineering y agentic systems. En ese contexto importa mas una demo publica que abra rapido, tenga una URL unica y minimice puntos de fallo, que una topologia separada por microservicios.
- **Decision:** El despliegue recomendado usa un solo servicio en Railway. Streamlit expone la URL publica del demo y FastAPI corre en el mismo contenedor, accesible solo por loopback interno. El proceso de entrada es `python -m src.deploy --service all`.
- **Consequences:** Se elimina friccion operacional para reviewers y reclutadores, no se necesita networking entre servicios para el happy path y se evita depender de CORS en el deploy principal. El repositorio igual conserva soporte para split deploy si despues se necesita separar UI y API.

## ADR-002: Observabilidad de demo con `/metrics` local y Logfire opcional

- **Status:** Accepted
- **Date:** 2026-04-22
- **Context:** El sidebar de la UI necesita metricas visibles en tiempo real para que la demo cuente una historia clara. Al mismo tiempo, las metricas in-process no son una solucion correcta para multiples workers ni para un entorno productivo serio.
- **Decision:** Se conserva `/metrics` como endpoint local de demo y se mantienen spans manuales en el flujo critico de `/recommend`. No se agrega por ahora el extra `logfire[fastapi]`; la auto-instrumentacion queda como mejora opcional y el estado real de observabilidad se expone en `/health`.
- **Consequences:** La demo sigue mostrando telemetria sin introducir una dependencia extra solo para portfolio. Tambien desaparece el supuesto oculto: si la auto-instrumentacion no esta disponible, el sistema lo deja visible en `logfire_mode` y `logfire_detail` del healthcheck. Si el deploy escala a multiples workers, las metricas deben moverse a un backend compartido o apoyarse por completo en observabilidad externa.

## ADR-003: Exposicion publica deliberadamente abierta, con CORS por allowlist y limites basicos

- **Status:** Accepted
- **Date:** 2026-04-22
- **Context:** El demo debe ser facil de abrir desde un link publico para entrevistas, revisiones tecnicas y publicaciones en LinkedIn. Agregar auth antes del primer deploy aumenta la friccion y reduce la probabilidad de que alguien realmente pruebe el sistema.
- **Decision:** La API queda sin autenticacion para la version portfolio, de forma deliberada y documentada. La exposicion publica se acota con tres medidas: CORS controlado por `FINSAGE_CORS_ALLOW_ORIGINS`, healthcheck enriquecido con readiness real y un limite configurable de tamano de request via `FINSAGE_MAX_REQUEST_SIZE_BYTES`.
- **Consequences:** El demo es accesible con un clic y no depende de supuestos escondidos sobre secretos, orquestacion o navegadores. A cambio, esta decision se considera valida solo para un entorno de portfolio y no para produccion con trafico real. La ruta natural para una siguiente fase es agregar auth por bearer token y rate limiting antes de abrir el servicio a un uso sostenido.

## ADR-004: Demo publica sobre free tier de Gemini

- **Status:** Accepted
- **Date:** 2026-04-22
- **Context:** El deploy publico no debe depender de credito prepago en Anthropic ni de que el usuario final configure su propia API key. El objetivo del portfolio es ser probado por terceros con la menor friccion posible.
- **Decision:** El runtime productivo migra a Gemini API para generation estructurada y Gemini Embeddings para retrieval semantico. La demo publica requiere solo `GEMINI_API_KEY`, mantenida del lado del servidor, y usa modelos orientados a free tier (`gemini-2.5-flash-lite` y `gemini-embedding-001`).
- **Consequences:** La demo sigue consultando un LLM real, pero con una historia de costo y disponibilidad mucho mas favorable para portfolio. Tambien queda mas simple el setup operativo: una sola clave para generation y embeddings. Si la cuota gratuita se agota, la API expone el problema con un error explicito en vez de ocultarlo como fallo interno del orchestrator.

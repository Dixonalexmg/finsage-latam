# CLAUDE.md — FinSage LATAM

## Contexto del Proyecto
FinSage es un sistema multi-agente de asesoría financiera para LATAM. Ingesta productos financieros (tarjetas de crédito, préstamos personales) de bancos reales, y un orchestrator agéntico responde consultas de usuarios recomendando la mejor opción con razonamiento auditable.

El proyecto demuestra construcción AI-first: agentes especializados, structured outputs, RAG, y evals rigurosas sobre casos reales.

**No es un chatbot.** Es un sistema que toma decisiones financieras con trazas de razonamiento que un humano puede auditar.

## Stack Técnico
- **Lenguaje:** Python 3.11+
- **LLM:** Anthropic SDK (`claude-sonnet-4-5` para razonamiento, `claude-haiku-4-5` para clasificación rápida)
- **Orquestación:** LangGraph 0.2+
- **Validación:** Pydantic v2 para structured outputs (obligatorio en TODO call LLM)
- **API:** FastAPI + Uvicorn
- **UI Demo:** Streamlit
- **Scraping:** Playwright (NO requests + BeautifulSoup — necesitamos JS rendering)
- **Storage:** DuckDB + Parquet (sin servidor, ideal para portfolio)
- **Embeddings:** `voyage-3-lite` (barato y bueno para español)
- **Package manager:** `uv` (NO pip, NO poetry)
- **Testing:** pytest + pytest-asyncio
- **Observability:** Logfire

## Estructura del Proyecto
```
src/
├── agents/        # Agentes con system prompts versionados en docs/prompts.md
├── rag/           # Retrieval híbrido (BM25 + embeddings)
├── models/        # SOLO schemas Pydantic, nada de lógica aquí
├── scrapers/      # Un módulo por banco, todos heredan de BaseScraper
├── api/           # FastAPI — solo endpoints, lógica va en agents/
└── ui/            # Streamlit — solo presentación
```

## Comandos Clave
- `uv sync` — instalar dependencias
- `uv run pytest` — correr tests
- `uv run pytest --cov=src --cov-report=term-missing` — cobertura
- `uv run ruff check . && uv run ruff format .` — lint + format
- `uv run python -m src.api.main` — levantar API en :8000
- `uv run streamlit run src/ui/app.py` — demo UI en :8501
- `uv run python -m evals.run_evals` — correr evals y generar reporte

## Convenciones de Código
- **Nombrado:** `snake_case` archivos y funciones, `PascalCase` clases, `SCREAMING_SNAKE` constantes
- **Type hints:** OBLIGATORIOS en toda función pública. `mypy --strict` debe pasar.
- **Formatter:** `ruff format` (line-length 100)
- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
- **Branches:** `feat/nombre-feature`, `fix/nombre-bug`
- **Docstrings:** Google style, solo en funciones públicas y clases

## Agentes y Responsabilidades

### Agente Principal (Orchestrator)
El `Orchestrator` en `src/agents/orchestrator.py` usa LangGraph con este flujo:
1. Recibe query del usuario
2. Invoca `ProfileAnalyst` para extraer perfil financiero
3. Clasifica intent (tarjeta vs préstamo vs comparación)
4. Enruta al sub-agente especializado
5. Recibe recomendaciones + razonamiento
6. Compone respuesta final con trazas

**Restricciones:**
- NUNCA llamar un LLM sin structured output (usa `response_model` en todo call)
- NUNCA usar `temperature > 0.3` en agentes de producción (solo en `ProfileAnalyst` que es conversacional)
- TODA respuesta debe incluir `reasoning_trace` auditable

### Agente de Tests
Al escribir tests:
- Usa `pytest-mock` para mockear llamadas a Anthropic API (nunca llamar la API real en tests unitarios)
- Tests de integración van en `tests/integration/` y usan VCR.py para grabar/replay respuestas
- Cobertura mínima: 70% global, 90% en `src/agents/` y `src/models/`
- Cada bug fix debe incluir un test de regresión

### Agente de Documentación
Al actualizar docs:
- `README.md` es la cara del proyecto — incluye demo GIF, arquitectura, quick start, y 3 métricas headline
- `docs/decisions.md` usa formato ADR (Architecture Decision Records)
- `docs/prompts.md` versiona cada prompt de sistema con changelog
- Cada feature nueva en el README va en sección "Features" con descripción de 1 línea

## Restricciones Importantes
- **NO scrapear datos que requieran login** — solo info pública de bancos
- **NO guardar información personal del usuario** — este es un demo, no procesamos PII real
- **NO usar frameworks pesados** — nada de Django, nada de Airflow, nada de Kubernetes
- **NO mockear features en README** — si no está construido, no lo anuncies
- **NO agregar dependencias sin justificar** en `docs/decisions.md`
- **NO usar `print()`** — usar `logging` o `logfire`
- **Scope congelado:** solo Chile para v1.0. Expandir a otros países es v2.0.

## Definition of Done por Feature
- [ ] Tests unitarios pasando con cobertura ≥ 90% del código nuevo
- [ ] `mypy --strict` sin errores
- [ ] `ruff check` sin warnings
- [ ] Si toca un prompt: `docs/prompts.md` actualizado
- [ ] Si toca arquitectura: ADR nuevo en `docs/decisions.md`
- [ ] README actualizado si la feature es visible al usuario
- [ ] Caso de eval agregado en `evals/test_cases.jsonl` si es lógica de agente
- [ ] PR con descripción que incluya: qué, por qué, cómo probar

## Notas para Claude Code
- Al implementar un agente nuevo, SIEMPRE empieza por definir el Pydantic schema del output esperado
- Al agregar un scraper, SIEMPRE hereda de `BaseScraper` y respeta `robots.txt`
- Al tocar lógica de RAG, SIEMPRE corre `evals/run_evals.py` antes de commitear para detectar regresiones
- Los prompts viven en archivos `.md` en `src/agents/prompts/` y se cargan con `importlib.resources` — NO los hardcodees como strings en código
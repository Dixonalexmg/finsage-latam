# Prompts del sistema

Versionado de los system prompts de cada agente. Los prompts vivos están en
``src/agents/prompts/*.md``; este archivo mantiene el changelog y el rationale.

## Formato

Por cada agente:

```
## <agent_name>

- **Archivo:** src/agents/prompts/<agent>.md
- **Modelo:** gemini-2.5-flash-lite
- **Temperatura:** 0.0 – 0.3
- **Structured output:** <schema Pydantic>

### Changelog
- YYYY-MM-DD — cambio, motivo, impacto observado en evals.
```

## CreditCardExpert

- **Archivo:** src/agents/prompts/credit_card_expert.md
- **Modelo:** gemini-2.5-flash-lite
- **Temperatura:** 0.2
- **Structured output:** `ExpertRanking` (lista de `RecommendationDraft`, hidratado a `Recommendation` por el experto)

### Changelog
- 2026-04-21 — Versión inicial. Rankea top-3 tarjetas a partir del pool del retriever, criterios: elegibilidad por renta → ajuste a `stated_goal` → costo total → tier vs renta → capacidad de pago.

## LoanExpert

- **Archivo:** src/agents/prompts/loan_expert.md
- **Modelo:** gemini-2.5-flash-lite
- **Temperatura:** 0.2
- **Structured output:** `ExpertRanking` (lista de `RecommendationDraft`, hidratado a `Recommendation` por el experto)

### Changelog
- 2026-04-21 — Versión inicial. Rankea top-3 préstamos personales priorizando CAE, monto objetivo del usuario, plazo compatible y capacidad de pago (cuota ≤ 0.3 × ingreso disponible).

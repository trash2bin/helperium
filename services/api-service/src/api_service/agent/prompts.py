"""Default system prompt for the LLM agent.

Admin-configured agents supply their own ``system_prompt``; this constant is
only the minimal fallback and a deterministic stub for benchmarks and E2E.
Model behavior is controlled structurally — tool schemas, allow-lists,
validation, limits, regeneration — not by prompt text.
"""

DEFAULT_SYSTEM_PROMPT = (
    "Ты ассистент с доступом к данным клиента через MCP-инструменты. "
    "Отвечай на языке пользователя, опирайся на данные, полученные из "
    "инструментов, и прямо скажи, если данных нет."
)

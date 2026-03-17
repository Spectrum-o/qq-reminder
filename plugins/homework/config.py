from nonebot import get_driver

_config = get_driver().config

OWNER_QQ: str = getattr(_config, "owner_qq", "")

LLM_API_BASE: str = getattr(_config, "llm_api_base", "")
LLM_API_KEY: str = getattr(_config, "llm_api_key", "sk-no-key")
LLM_MODEL: str = getattr(_config, "llm_model", "deepseek-ai/DeepSeek-V3.2")
_LLM_DEFAULT_WINDOW_SECONDS = int(getattr(_config, "llm_rate_limit_window_seconds", 60))
_LLM_DEFAULT_MAX_REQUESTS = int(getattr(_config, "llm_rate_limit_max_requests", 15))

LLM_PUBLIC_RATE_LIMIT_WINDOW_SECONDS: int = int(
    getattr(_config, "llm_public_rate_limit_window_seconds", _LLM_DEFAULT_WINDOW_SECONDS)
)
LLM_PUBLIC_RATE_LIMIT_MAX_REQUESTS: int = int(
    getattr(_config, "llm_public_rate_limit_max_requests", 8)
)
LLM_PUBLIC_DAILY_MAX_REQUESTS: int = int(
    getattr(_config, "llm_public_daily_max_requests", 30)
)

LLM_USER_RATE_LIMIT_WINDOW_SECONDS: int = int(
    getattr(_config, "llm_user_rate_limit_window_seconds", _LLM_DEFAULT_WINDOW_SECONDS)
)
LLM_USER_RATE_LIMIT_MAX_REQUESTS: int = int(
    getattr(_config, "llm_user_rate_limit_max_requests", _LLM_DEFAULT_MAX_REQUESTS)
)
LLM_USER_DAILY_MAX_REQUESTS: int = int(
    getattr(_config, "llm_user_daily_max_requests", 120)
)

LLM_GLOBAL_RATE_LIMIT_WINDOW_SECONDS: int = int(
    getattr(_config, "llm_global_rate_limit_window_seconds", _LLM_DEFAULT_WINDOW_SECONDS)
)
LLM_GLOBAL_RATE_LIMIT_MAX_REQUESTS: int = int(
    getattr(_config, "llm_global_rate_limit_max_requests", 80)
)

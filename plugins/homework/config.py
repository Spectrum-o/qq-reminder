from nonebot import get_driver

_config = get_driver().config

OWNER_QQ: str = getattr(_config, "owner_qq", "")

LLM_API_BASE: str = getattr(_config, "llm_api_base", "")
LLM_API_KEY: str = getattr(_config, "llm_api_key", "sk-no-key")
LLM_MODEL: str = getattr(_config, "llm_model", "deepseek-ai/DeepSeek-V3.2")

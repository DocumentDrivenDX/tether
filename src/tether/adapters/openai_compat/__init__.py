"""Optional compatible-server profile. Install tether[openai] explicitly."""

from tether.adapters.pydantic_ai.compatible import OpenAIAdapter
from tether.adapters.pydantic_ai.tokenizer import TokenizerProfile as TokenizerProfile


class OpenAICompatModel(OpenAIAdapter):
    """The selected native-compatible implementation; no second protocol client."""

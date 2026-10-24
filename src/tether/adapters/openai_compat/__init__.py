"""Optional compatible-server profile. Install tether[openai] explicitly."""

from tether.adapters.pydantic_ai.compatible import OpenAIAdapter


class OpenAICompatModel(OpenAIAdapter):
    """The selected native-compatible implementation; no second protocol client."""

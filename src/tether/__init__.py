"""Provider-neutral Agent integration and the owner-bound Client composition."""

from tether.client import Client
from tether.sessions.service import SessionService
from tether.types import MODEL_PORT_VERSION as MODEL_PORT_VERSION
from tether.types.execution import Actor


class Tether(SessionService):
    """Explicit asynchronous composition; public commands are developed in slice 4."""

    def client(self, actor: Actor) -> Client:
        return Client(self, actor)

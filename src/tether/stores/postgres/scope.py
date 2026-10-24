"""Pure explicit caller scopes; database ownership never follows model attribution."""

from tether.types.execution import Actor, ExecutionDenied, copy_actor
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import StoreCapabilities, StoreError

from .changes import Changes
from .root import DatabaseRoot


class Scope:
    def __init__(
        self,
        root: DatabaseRoot,
        *,
        actor: Actor | None = None,
        maintenance: Actor | None = None,
    ) -> None:
        root.check_open()
        self.root = root
        self._actor = copy_actor(actor) if actor is not None else None
        self._maintenance = copy_actor(maintenance) if maintenance is not None else None
        self._closed = False

    @property
    def workers(self) -> WorkerLifetime:
        return self.root.workers

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            durability="os", lease="cross_process", fencing=True, mutation_receipts=1
        )

    def _check(self) -> None:
        self.root.check_open()
        if self._closed:
            raise StoreError("store_scope_closed")

    def _caller(self) -> Actor:
        self._check()
        if self._actor is None:
            raise ExecutionDenied("caller_scope_required")
        return copy_actor(self._actor)

    def _owner(self, owner: str) -> Actor:
        actor = self._caller()
        if actor.id != owner:
            raise ExecutionDenied("denied")
        return actor

    def _reads(self, owner: str) -> Changes:
        return Changes(self.root, self._owner(owner))

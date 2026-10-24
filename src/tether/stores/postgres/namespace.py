"""Non-secret database namespace ownership, retained through actual worker exit."""

from dataclasses import dataclass, field
from threading import RLock
from uuid import UUID

from tether.types.lifetime import WorkerLifetime


@dataclass
class Namespace:
    identity: UUID
    workers: WorkerLifetime = field(default_factory=WorkerLifetime)
    roots: set[object] = field(default_factory=lambda: set[object]())

    def prune(self) -> None:
        with _lock:
            if not self.roots and not self.workers.active:
                self.workers.closed = True
                if _namespaces.get(self.identity) is self:
                    _namespaces.pop(self.identity)

    def release(self, root: object) -> None:
        with _lock:
            self.roots.discard(root)
            self.prune()


_namespaces: dict[UUID, Namespace] = {}
_lock = RLock()


def acquire(identity: UUID, root: object) -> Namespace:
    with _lock:
        namespace = _namespaces.get(identity)
        if namespace is None:
            namespace = Namespace(identity)
            namespace.workers.check_loop()
            namespace.workers.on_idle = namespace.prune
            _namespaces[identity] = namespace
        namespace.workers.check_loop()
        namespace.roots.add(root)
        return namespace

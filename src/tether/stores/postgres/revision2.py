"""Explicit indexed execution-store schema; canonical envelopes carry native values."""

from .profiles import Table

DDL = (
    "CREATE TABLE {schema}.owner_heads (owner bytea PRIMARY KEY, seq bigint NOT NULL, "
    "floor bigint NOT NULL)",
    "CREATE TABLE {schema}.sessions (owner bytea NOT NULL, id bytea NOT NULL, ref bytea, "
    "summary bytea NOT NULL, projection bytea NOT NULL, created_at timestamptz NOT NULL, "
    "updated_at timestamptz NOT NULL, status text NOT NULL, title_fold bytea NOT NULL, "
    "tags bytea[] NOT NULL, entry_count bigint NOT NULL, generation bigint NOT NULL, "
    "lease_turn bytea, lease_until timestamptz, PRIMARY KEY (owner,id), UNIQUE(id), "
    "UNIQUE(owner,ref), FOREIGN KEY (owner) REFERENCES {schema}.owner_heads(owner))",
    "CREATE TABLE {schema}.entries (owner bytea NOT NULL, session bytea NOT NULL, "
    "position bigint NOT NULL, entry_key bytea, envelope bytea NOT NULL, "
    "PRIMARY KEY (owner,session,position), UNIQUE(owner,session,entry_key), "
    "FOREIGN KEY (owner,session) REFERENCES {schema}.sessions(owner,id))",
    "CREATE TABLE {schema}.entry_groups (owner bytea NOT NULL, session bytea NOT NULL, "
    "kind text NOT NULL, value bytea NOT NULL, position bigint NOT NULL, "
    "PRIMARY KEY (owner,session,kind,value,position), "
    "FOREIGN KEY (owner,session,position) REFERENCES {schema}.entries(owner,session,position))",
    "CREATE TABLE {schema}.sources (owner bytea NOT NULL, session bytea NOT NULL, "
    "invocation bytea NOT NULL, position bigint NOT NULL, PRIMARY KEY(owner,session,invocation), "
    "FOREIGN KEY (owner,session,position) REFERENCES {schema}.entries(owner,session,position))",
    "CREATE TABLE {schema}.pending (owner bytea NOT NULL, session bytea NOT NULL, "
    "invocation bytea NOT NULL, position bigint NOT NULL, first_position bigint NOT NULL, "
    "PRIMARY KEY(owner,session,invocation), "
    "FOREIGN KEY (owner,session,position) REFERENCES {schema}.entries(owner,session,position))",
    "CREATE TABLE {schema}.effects (owner bytea NOT NULL, session bytea NOT NULL, "
    "invocation bytea NOT NULL, position bigint NOT NULL, PRIMARY KEY(owner,session,invocation), "
    "FOREIGN KEY (owner,session,position) REFERENCES {schema}.entries(owner,session,position))",
    "CREATE TABLE {schema}.attempt_usage (owner bytea NOT NULL, session bytea NOT NULL, "
    "key bytea NOT NULL, usage bytea, PRIMARY KEY(owner,session,key), "
    "FOREIGN KEY (owner,session) REFERENCES {schema}.sessions(owner,id))",
    "CREATE TABLE {schema}.changes (owner bytea NOT NULL, seq bigint NOT NULL, "
    "session bytea NOT NULL, envelope bytea NOT NULL, PRIMARY KEY(owner,seq), "
    "FOREIGN KEY (owner) REFERENCES {schema}.owner_heads(owner))",
    "CREATE INDEX sessions_updated ON {schema}.sessions(owner,updated_at DESC,id DESC)",
    "CREATE INDEX sessions_created ON {schema}.sessions(owner,created_at,id)",
    "CREATE INDEX sessions_status_updated ON {schema}.sessions"
    "(owner,status,updated_at DESC,id DESC)",
    "CREATE INDEX sessions_tags ON {schema}.sessions USING gin(tags)",
    "CREATE INDEX sessions_expired ON {schema}.sessions(lease_until) WHERE lease_until IS NOT NULL",
    "CREATE INDEX pending_order ON {schema}.pending(owner,session,first_position)",
    "CREATE INDEX effects_position ON {schema}.effects(owner,session,position)",
)


def columns(*names: str, nullable: tuple[str, ...] = ()) -> tuple[tuple[str, str, bool], ...]:
    return tuple((name, kind, name not in nullable) for name, kind in (v.split(":") for v in names))


ADDRESS = ("owner:bytea", "session:bytea")
TABLES = (
    Table("owner_heads", columns("owner:bytea", "seq:bigint", "floor:bigint"), ("owner",)),
    Table(
        "sessions",
        columns(
            "owner:bytea",
            "id:bytea",
            "ref:bytea",
            "summary:bytea",
            "projection:bytea",
            "created_at:timestamp with time zone",
            "updated_at:timestamp with time zone",
            "status:text",
            "title_fold:bytea",
            "tags:bytea[]",
            "entry_count:bigint",
            "generation:bigint",
            "lease_turn:bytea",
            "lease_until:timestamp with time zone",
            nullable=("ref", "lease_turn", "lease_until"),
        ),
        ("owner", "id"),
        (("id",), ("owner", "ref")),
    ),
    Table(
        "entries",
        columns(
            *ADDRESS,
            "position:bigint",
            "entry_key:bytea",
            "envelope:bytea",
            nullable=("entry_key",),
        ),
        ("owner", "session", "position"),
        (("owner", "session", "entry_key"),),
    ),
    Table(
        "entry_groups",
        columns(*ADDRESS, "kind:text", "value:bytea", "position:bigint"),
        ("owner", "session", "kind", "value", "position"),
    ),
    *(
        Table(
            name,
            columns(*ADDRESS, "invocation:bytea", "position:bigint"),
            ("owner", "session", "invocation"),
        )
        for name in ("sources", "effects")
    ),
    Table(
        "pending",
        columns(*ADDRESS, "invocation:bytea", "position:bigint", "first_position:bigint"),
        ("owner", "session", "invocation"),
    ),
    Table(
        "attempt_usage",
        columns(*ADDRESS, "key:bytea", "usage:bytea", nullable=("usage",)),
        ("owner", "session", "key"),
    ),
    Table(
        "changes",
        columns("owner:bytea", "seq:bigint", "session:bytea", "envelope:bytea"),
        ("owner", "seq"),
    ),
)

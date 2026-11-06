# Tether Databricks Apps carrier

`novirhiza-tether-databricks` is an optional composition package. It resolves the
configured Apps identity into a current Tether actor, validates declared resource
bindings through an embedder supplied acquisition function, and routes one ASGI
host to the public API and UI factories. It does not create a database pool, select
a role, apply migrations, or read process environment.

```python
settings = DatabricksAppSettings.from_values(
    trusted_ingress="databricks-apps",
    host="0.0.0.0",
    port=8000,
    path_prefix="",
    shutdown_timeout_seconds=10.0,
    database_profile=profile_ref,
    resource_bindings=(database_resource,),
)
identity = DatabricksIdentityResolver(trusted_ingress=settings.trusted_ingress)
resources = DatabricksResources.from_bindings(
    settings.resource_bindings,
    acquire=application_resource_acquirer,
)
app = create_carrier_app(
    settings=settings,
    client_provider=client_provider,
    authenticator=identity,
    resource_provider=resources,
    api_factory=create_api,
    api_settings=api_settings,
    ui_factory=create_static_app,
    ui_settings=ui_settings,
    lifecycle=application_lifecycle,
)
```

For PostgreSQL, import `ConnectionSource`, `PostgresResources`, and the explicit
`migrate` helper from `tether.client.postgres`. Apply and verify migrations as an
owner operation before opening resources. `PostgresResources` owns its Tether
database root and borrows the supplied connection source and pool. Use
`recover_owner(actor)` with a freshly authenticated actor before serving that
owner after process restart; it never runs global maintenance recovery.

In explicitly selected Apps mode, the identity resolver requires one each of
`X-Forwarded-User`, `X-Forwarded-Email`, and `X-Forwarded-Access-Token`. The
validated email is the logical actor and database owner; the forwarded user id is
retained as the actor principal. Local mode requires a separately selected
authenticator and ignores forwarded identity. The bearer credential is bound to
that request's actor and is not written to settings, browser storage, logs, or
session facts.

This package provides reusable composition seams. It does not claim that an Apps
ingress, a Lakebase endpoint, database OAuth renewal, role isolation, or shutdown
profile has passed live qualification.

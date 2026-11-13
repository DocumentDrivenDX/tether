---
ddx:
  id: CONTRACT-007
  authoring:
    home: repo
  links:
    - id: FEAT-016
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
---

# CONTRACT-007: Databricks Apps Carrier

**Contract ID**: CONTRACT-007  
**Type**: library and deployment composition  
**Version**: v1  
**Related**: FEAT-016, SD-003, ADR-011, ADR-013, ADR-020, CONTRACT-003, CONTRACT-005, CONTRACT-006

## Purpose

This contract defines the optional `tether_databricks` carrier boundary and the complete `examples/databricks-app` composition. It governs platform request identity, explicit app settings and resource binding, public-factory composition, durable-storage selection and bounded lifecycle. It does not qualify a Databricks deployment or add a platform-specific behavior path to Tether core.

## Scope and Boundaries

- **In scope**: typed settings, trusted identity resolution, app/user authority separation, explicit resource and storage binding, API/UI factory composition, build artifacts, startup and shutdown behavior.
- **Out of scope**: HTTP routes and payloads (CONTRACT-006); Tether client/store semantics (CONTRACT-003); customer schema/data, workspace permissions and production resource provisioning; an AgentKit runner or business application.
- **Owning system or team**: `tether_databricks` owns the carrier helper and example. The embedder owns the workspace, user scopes, database roles, migrations, resource grants, model/tool configuration and application lifetime.

## Normative Surface

Terms MUST, MUST NOT, SHOULD, and MAY have their RFC 2119 meanings. The proposed distribution name and import namespace are `novirhiza-tether-databricks` and `tether_databricks`; registry naming validation remains open before release. Public Tether behavior enters through `tether.client`; web composition uses the public factories from `tether_web_api` and `tether_web_ui`.

### 1. Typed settings and resources

The carrier MUST expose immutable typed settings equivalent to this shape:

| Element | Type / Shape | Required | Rules | Notes |
|---|---|---:|---|---|
| `trusted_ingress` | `Literal["databricks-apps", "local"]` | yes | `databricks-apps` is valid only for the deployed platform entrypoint. Local mode requires a separately supplied local identity resolver. | Header values alone do not select a mode. |
| `host` | nonempty string | yes | The deployed Apps host is `0.0.0.0`. | No TLS listener is required in the app. |
| `port` | integer, 1–65535 | yes | Production composition obtains the value from `DATABRICKS_APP_PORT`; the core receives it as a value. | Apps documentation requires the supplied port. |
| `path_prefix` | normalized path prefix | no | Empty or a valid absolute path without query/fragment; API and assets use the same prefix. | Allows embedder mounting. |
| `shutdown_timeout_seconds` | finite positive float | yes | MUST be less than 15 seconds in Apps mode and bounds the carrier's stop-and-report phase. | The host cannot extend platform termination. |
| `database_profile` | typed profile reference | yes for durable deployment | Names the external acquisition source, schema, role mode, TLS mode, durability profile, migration owner and declared Tether capability profile. No implicit DSN or pool is created. | Secrets are supplied by a runtime credential provider. |
| `resource_bindings` | immutable tuple of typed resource references | yes | Every platform resource and requested scope is explicit and least-privileged. | No resource grant is inferred from display metadata. |

The public namespace MUST export `DatabricksAppSettings`, `TrustedIngress`, `DatabaseProfileRef`, `ResourceRef`, `ResourceProvider`, `ResourceLease`, `DatabricksResources`, `RequestContext`, `DatabricksIdentityResolver`, `CarrierLifecycle`, `ShutdownReport`, and `create_carrier_app`. `ApiFactory`, `UiFactory`, `UiSettings`, `Authenticator` and `ApiSettings` are imported from CONTRACT-006, not redefined by this package. The settings API MUST provide `DatabricksAppSettings.from_values(...)` for explicit values and MAY provide `DatabricksAppSettings.from_mapping(values: Mapping[str, str], *, trusted_ingress: TrustedIngress)` for a caller-supplied mapping. Neither method reads process environment implicitly. The example entrypoint MAY pass its environment mapping to `from_mapping`, which MUST accept only an allowlist of non-secret settings and MUST reject credential-shaped fields without including their values in errors. Settings MUST NOT contain a raw user token, app secret, password or private connection string.

The settings constructors MUST have signatures equivalent to:

```python
@classmethod
def from_values(
    cls,
    *,
    trusted_ingress: TrustedIngress,
    host: str,
    port: int,
    path_prefix: str = "",
    shutdown_timeout_seconds: float,
    database_profile: DatabaseProfileRef | None = None,
    resource_bindings: tuple[ResourceRef, ...],
) -> DatabricksAppSettings: ...


@classmethod
def from_mapping(
    cls,
    values: Mapping[str, str],
    *,
    trusted_ingress: TrustedIngress,
) -> DatabricksAppSettings: ...
```

The public identity and lifecycle helpers MUST have signatures equivalent to:

```python
@dataclass(frozen=True)
class RequestContext:
    headers: Mapping[str, str]
    trusted_ingress: TrustedIngress
    local_context: object | None = None


class DatabricksIdentityResolver:
    def __init__(
        self,
        *,
        trusted_ingress: TrustedIngress,
        local_authenticator: Authenticator | None = None,
        prepare_actor: ActorPreparer | None = None,
        service_caller_authenticator: ServiceCallerAuthenticator | None = None,
        service_caller_credential_header: str | None = None,
    ) -> None: ...
    async def actor_for(self, request: Request) -> Actor: ...


class ActorPreparer(Protocol):
    async def __call__(self, actor: Actor) -> None: ...


@dataclass(frozen=True)
class ServiceCallerIdentity:
    actor_id: str
    principal: str
    forwarded_email: str | None = None


class ServiceCallerAuthenticator(Protocol):
    async def __call__(self, bearer_token: str, /) -> ServiceCallerIdentity | None: ...


@dataclass(frozen=True)
class ShutdownReport:
    complete: bool
    outstanding: tuple[str, ...]
    closed: tuple[str, ...]


class CarrierLifecycle(Protocol):
    async def close(self, *, timeout_seconds: float) -> ShutdownReport: ...


class PostgresResources:
    async def aclose(self) -> bool: ...
    async def wait_closed(self) -> bool: ...


class ResourceProvider(Protocol):
    async def acquire(self, ref: ResourceRef) -> ResourceLease: ...


class ResourceLease(Protocol):
    owned: bool
    value: object

    async def close(self) -> None: ...


class ResourceAcquirer(Protocol):
    async def __call__(self, ref: ResourceRef, /) -> ResourceLease: ...
```

`RequestContext` is a carrier-local projection created by the configured ASGI request adapter. Its `trusted_ingress` value MUST come from immutable app settings, never from a request header or browser value. `DatabricksIdentityResolver` MUST expose `async actor_for(request: Request) -> Actor`, using `Request` from CONTRACT-006, and implement its `Authenticator` protocol. It MUST project the request into `RequestContext`, return a public `tether.client.Actor` bound to the current user and request-scoped credential, and refuse missing, duplicate, conflicting or invalid platform identity. In local mode it MUST delegate to a separately supplied local authenticator and ignore forwarded identity headers. The caller cannot mark arbitrary headers as trusted by constructing a request context outside the configured ASGI adapter.

When supplied, the asynchronous `prepare_actor(actor: Actor)` hook MUST run after successful identity resolution and before returning the actor to the API; it completes without a return value. It is the explicit owner-scoped recovery or bootstrap hook. A failure MUST prevent the protected operation. Implementations MUST use the authenticated actor and MUST NOT broaden recovery to another owner or global maintenance authority.

`ResourceProvider.acquire(ref: ResourceRef) -> ResourceLease` MUST acquire only declared resources. A `ResourceLease` MUST expose its acquired `value`; `owned` controls whether `DatabricksResources` closes it. `DatabricksResources.from_bindings(bindings: tuple[ResourceRef, ...], *, acquire: ResourceAcquirer) -> ResourceProvider` MUST invoke the supplied acquisition callback only for an exact declared binding and MUST NOT infer grants or construct a platform credential from app metadata. The callback and lease are embedder-owned integration points; this package selects no Databricks SDK or default resource implementation. `CarrierLifecycle.close(*, timeout_seconds: float) -> ShutdownReport` MUST report closed resources and outstanding work/resources without claiming that physical work stopped. A timeout MUST NOT cancel a close operation that may still own a resource; a repeated close MAY report completion after that retained operation exits.

The carrier MUST compose `tether_web_api.create_api` and `tether_web_ui.create_static_app` using the exact `ApiFactory`, `UiFactory`, `UiSettings`, `Authenticator`, and `ApiSettings` types owned by CONTRACT-006. It MUST NOT invent a second API or UI factory signature. These factory names and types are public integration points.

The carrier factory MUST have a public typed signature equivalent to:

```python
def create_carrier_app(
    *,
    settings: DatabricksAppSettings,
    client_provider: ClientProvider,
    authenticator: Authenticator,
    resource_provider: ResourceProvider,
    api_factory: ApiFactory,
    api_settings: ApiSettings,
    ui_factory: UiFactory,
    ui_settings: UiSettings,
    lifecycle: CarrierLifecycle,
) -> ASGIApp: ...
```

`ClientProvider`, `ApiFactory`, `UiFactory`, and their callable signatures MUST be imported from the public contracts in `tether.client` and CONTRACT-006; this package MUST NOT redeclare or reach into their private implementations. Each `ResourceLease` MUST declare whether it is owned or borrowed and provide an idempotent close operation. `DatabricksIdentityResolver.actor_for(request: Request) -> Actor` MUST return the current public Tether actor with a request-scoped credential and never persist that credential.

The example source and upload bundle MUST contain a Databricks bundle definition, `app.yaml`, `pyproject.toml`, `uv.lock`, and app composition source, and MUST NOT include `requirements.txt`. The upload bundle's generated project MUST reference its bundled wheels by relative paths and pin non-Tether runtime dependencies; its lock MUST be generated from that project. No bundled path may point back to editable monorepo sources. A Python build artifact MUST include the browser static files at a declared package path. Deployment MUST NOT require Node.js at runtime.

### 2. Current user identity

For human requests, the Apps identity resolver MUST consume the platform's current request context only when `trusted_ingress="databricks-apps"`. It MUST require exactly one `X-Forwarded-User`, one `X-Forwarded-Email`, and one `X-Forwarded-Access-Token` value; header lookup is case-insensitive. The resolver MUST reject missing/blank values, duplicate/conflicting identity values, and values that fail configured validation. `Actor.id` MUST be the validated forwarded email because the qualified Lakebase role mapping uses that stable user identity; `Actor.principal` MUST retain the forwarded platform user identifier. Both values are trusted only behind the configured Apps ingress. A user id by itself MUST NOT be treated as a database username.

The resolver MUST create an actor bound to the current user and a nonpersisted credential object whose authorization lookup can return the current request credential. It MUST resolve a fresh actor-bound `tether.client` facade for each protected request. For a long-lived stream, the HTTP API MUST resolve current identity and policy before each bounded delivery batch, as required by CONTRACT-006; reconnect and every command also require fresh resolution.

An embedder MAY explicitly supply `ServiceCallerAuthenticator` for a separately authorized service-principal caller. The default service-caller path is selected only when all three forwarded user headers are absent and exactly one valid `Authorization: Bearer` value is present. An embedder MAY additionally configure one exact `service_caller_credential_header` when a trusted ingress consumes or replaces `Authorization`. That projected header MUST carry the current caller bearer; it is opt-in and MUST NOT be enabled by default. With the projected header configured, the resolver MAY use it only when it occurs exactly once, the trusted ingress supplies exactly one bounded forwarded user and email-claim string, and the email claim exactly equals the independently SDK-verified caller `user_name`. The forwarded user is an opaque IdP identifier distinct from the SDK's canonical SCIM principal; it is validated as a bounded value but MUST NOT select the Tether owner, database role, or actor principal. The SDK-verified principal and preconfigured database-role mapping remain authoritative. A missing, malformed, duplicate, or mismatched identity value MUST fail closed. The forwarded email-claim value in this explicit service mode is an opaque platform username and need not have email syntax; the human mode continues to require a valid email before using it as the logical owner id. A single `x-forwarded-access-token` value in this explicit mode is unused platform metadata: it MAY differ from the projected caller bearer, but MUST NOT be decoded, compared, logged, persisted, or bound as the service caller credential. Duplicate forwarded access-token headers MUST fail closed. In projected mode, an ordinary `Authorization` header, if present, MUST be unambiguous and exactly equal to the projected caller header; it MUST never replace the configured projected credential. The authenticator MUST validate the projected bearer with the configured workspace authority and allowlist the resulting principal; it returns `ServiceCallerIdentity` containing the verified principal, matching forwarded email-claim string and deployment-selected dedicated Tether actor/database role. The resolver binds only the verified projected bearer as that actor's credential and sets `Actor.kind` to `"service"`. It MUST NOT accept an actor id, principal or credential from request data other than the verified bearer and the preconfigured mapping. This opt-in caller actor is distinct from the app infrastructure actor and MUST NOT be selected as a fallback for human requests. Its permissions, database role and Tether policy are independently configured and tested. The default resolver remains human-header-only when no authenticator or projected-header setting is supplied.

Forwarded identity headers MUST be ignored in local mode. Local mode MUST require an explicitly selected local authentication provider; no default actor, shared test user or anonymous access is allowed for protected operations. A missing, expired, revoked or invalid required user credential MUST fail closed.

The Tether actor id (validated forwarded email in human Apps mode) is the logical session owner. The forwarded user identifier is preserved as the actor principal for audit and identity distinction. An explicitly authenticated service caller uses its configured dedicated actor/database role and retains its verified service-principal id as principal; this identity is not the app's infrastructure actor. The app service principal MAY perform explicitly selected shared infrastructure operations, such as app resource access or a migration run owned by the embedder. It MUST NOT replace the current human actor or credential for a human model call, Tether read, history disclosure, approval, resolution or session mutation. The browser MUST NOT supply an owner id that widens the resolved actor's authority.

The current user's forwarded credential is mandatory for user-authorized Tether operations and is bound only to the user `Actor`. A separate app service credential MAY acquire a declared database connection or resource lease when that is the selected infrastructure profile; it MUST remain a distinct typed resource credential and MUST NOT serve as a user bearer, change the logical session owner or widen a user's Tether authorization. The storage role and Tether actor authorization are separate checks.

Credentials MUST NOT appear in stored sessions, HTTP bodies, response headers, logs, spans, audit facts, cassette fixtures or exception text. Error handling MUST redact incoming header values and database connection details.

### 3. Carrier composition and lifecycle

The carrier composition MUST accept the `tether.client` provider, `tether_web_api` factory, `tether_web_ui` factory, authentication resolver, selected resource bindings and lifecycle owner as explicit dependencies. It MUST compose exactly one ASGI host for API and UI. A factory MUST NOT construct a hidden engine, store, global client singleton, or second session authority.

The deployed app MUST bind to `0.0.0.0:DATABRICKS_APP_PORT` and support the Apps ingress requirement for HTTP/2 cleartext (H2C). The selected ASGI server is an implementation choice only after live proxy qualification. A generic local ASGI test does not establish Apps ingress compatibility.

The lifecycle owner MUST handle `SIGTERM` by stopping new submissions, attempting bounded drain/close for resources it owns, and returning an accurate `ShutdownReport` by its configured deadline, which is less than 15 seconds in Apps mode. The report MUST distinguish closed resources from outstanding work and resources. The carrier MUST retain in-process ownership of a physical worker until that worker actually exits; a canceled waiter or elapsed deadline MUST NOT imply that the work stopped or release resources still used by it. If the platform sends `SIGKILL` before cleanup finishes, recovery is limited to committed facts in the selected store; unresolved external effects retain CONTRACT-003's explicit unknown outcome and barrier.

`CarrierASGI.limit_shutdown_deadline(deadline: float) -> None` lets the launcher
apply a finite absolute deadline from its event loop's monotonic clock before
ASGI shutdown begins. Repeated calls MUST only tighten that deadline. Nonfinite
values raise `ValueError("invalid_shutdown_deadline")`; a call after shutdown
begins raises `RuntimeError("carrier_shutdown_started")`. The method performs no
I/O and does not start cleanup. The launcher MUST count connection draining,
resource cleanup and report emission against one signal allowance; starting a
new relative timeout after a delayed drain MUST NOT extend that allowance.

Closing a borrowed API/UI consumer MUST NOT close the embedder's client host, pool, external connection source or sibling resource. The embedder's lifecycle owner closes owned resources explicitly. A bounded close that returns outstanding work is an incomplete close, not evidence of physical cancellation; resources with active users remain owned until those users exit, subject to platform force termination.

The reference composition MUST close resources in dependency order: client host, PostgreSQL root, then the borrowed platform resource lease. `ClientHost.aclose()` and `PostgresResources.aclose()` MUST each be followed by `wait_closed()` before the dependent database root or connection source is released. A false result MUST retain that resource and stop the ordered close chain. Constructor failure after resource acquisition MUST use the same ordered lifecycle owner so it does not release a source still used by retained host or database work.

### Qualification of readiness, time behavior and physical ownership

Functional qualification MUST establish readiness from the required protocol
event or observable state. Import, startup, first output and ordinary operation
speed MUST NOT determine functional validity. EOF, process exit, explicit refusal
and malformed events remain observable failures; elapsed waiting alone does not
prove any of them. Test coordination MUST use events, acknowledgments or state
barriers rather than sleeps used to guess progress.

Actual lease expiry, credential expiry, configured application deadlines and the
Apps stop-and-report allowance remain time-dependent behavior. Qualification
MUST verify the resulting authorization, fencing, report and ownership facts.
An elapsed-time lower bound alone MUST NOT prove expiry or fencing. Deterministic
mechanism tests SHOULD control the relevant clock; named live expiry cases MUST
observe genuine expiry and fresh-credential controls.

An explicitly justified operator emergency watchdog MAY bound an otherwise
unresponsive qualification process. Its exhaustion MUST produce incomplete
evidence with the last observed state; it MUST NOT be reported as a product
speed failure, credential refusal or successful expiry. Cancellation and watchdog
cleanup MUST retain ownership of the exact child process group and captured
streams until their actual completion is known. A reaped group leader alone
MUST NOT establish descendant exit or stream EOF.

### 4. Storage and resource qualification

Under PRD NFR-7's 2026-11-09 owner decision, actual performance qualification
MUST NOT block implementation, functional carrier acceptance or release.
Functional identity, authorization, lifecycle, configured caller deadlines and
genuine expiry evidence remain required. Optional performance measurements
MUST retain their actual outcome and the existing isolation/admission rules;
failed, deferred or unrun measurements MUST NOT be presented as a pass. Apps
latency remains observational. Any referenced qualified local performance claim
MUST retain its exact six-profile/54-metric scope and source/admission joins.

The carrier MUST use a declared Tether `LiveStore` and `HistoryProvider` profile. JSONL files on Apps-local storage MUST NOT be presented as restart-durable. A Unity Catalog volume MUST NOT be treated as a journal or generic writable filesystem without separate contract and qualification.

Lakebase or another managed PostgreSQL endpoint MUST be treated as a separate profile from the existing PostgreSQL 18 qualification. The embedder MUST provide explicit schema migration ownership, application roles, authorization/RLS decisions, TLS requirements, connection acquisition and token refresh. The carrier MUST NOT migrate an unmanaged schema or select an application role implicitly during ordinary construction.

The currently qualified Tether SQL profile is `PostgresStore` using asynchronous psycopg and an application-injected asynchronous acquisition context; it does not create an internal pool, discover a DSN or switch roles. The executed native profile is local PostgreSQL 18 on macOS arm64/Python 3.11.17 and covers the named process-exit, lost-acknowledgment, fencing, lifecycle and resolution cases. It is not evidence for power-cut durability, a managed endpoint, OAuth renewal, caller-scoped/RLS roles or a server-side pooler. The explicit `databricks-lakebase-pg18` profile below is a separate managed-endpoint qualification target; its availability in code does not itself establish production support.

For OAuth database access, the selected connection profile MUST obtain a current token for each new physical connection and MUST avoid persisting it. Connection pools, proxies, or server-side poolers MUST be used only when their authentication mode has been qualified. The profile MUST fail before a caller operation if acquisition or role binding fails; it MUST NOT fall back to another role, connection source, maintenance identity or wider scope.

Shared PostgreSQL transaction behavior, migration compatibility, user isolation, token expiry/reconnect, fault/restart recovery, deployment role isolation and history source readiness MUST each have live profile evidence before a matching support claim is published. A unit test or logical conformance report alone is insufficient.

`tether.client.postgres` is an optional PostgreSQL composition surface that imports the PostgreSQL driver only when its `open` or `migrate` functions are called. `PostgresResources.open(source, *, actor, schema, limits=None, durability_profile=NATIVE_POSTGRES_PROFILE)` verifies an existing schema and returns resources whose `storage` is a `ProvidedStorage`; it MUST NOT migrate, own or close the caller's connection source or pool. The owner-run `migrate(source, *, actor, schema, limits=None, durability_profile=NATIVE_POSTGRES_PROFILE)` operation is separate and explicit. `PostgresResources.aclose()` closes only its Tether `DatabaseRoot`; `wait_closed()` awaits physical completion after close starts and returns whether the root drained. Both the client host and PostgreSQL resources MUST be settled before their dependent source is released. `PostgresResources.recover_owner(actor, *, now=None)` MUST use the caller's current actor and credentials and the SQL adapter's owner-scoped `recover_owner` operation. It MUST NOT invoke global maintenance recovery. When this borrowed storage profile is used with `ClientHost`, `ClientSettings.resume` MUST be `"off"` to disable host-global startup recovery; the embedder MAY call `recover_owner` from `DatabricksIdentityResolver.prepare_actor` before protected operations. That recovery changes only the current owner's expired journal state and MUST NOT dispatch a new model turn.

`PostgresDurabilityProfile` has exactly two supported names in this version: `native` and `databricks-lakebase-pg18`. Native is the default and MUST continue to require PostgreSQL 18 with `fsync=on`, `full_page_writes=on`, and `synchronous_commit=on` on every root and transaction acquisition. The managed profile MUST be explicitly selected and bound to one nonempty Databricks endpoint resource ID. Its connection source MUST advertise the same profile value and endpoint ID; a missing or mismatched source attestation MUST fail before opening a connection. After connection, every acquisition MUST verify PostgreSQL major version 18 and the exact qualified settings `fsync=off`, `full_page_writes=off`, and `synchronous_commit=on`. Unknown profile names or any other setting tuple MUST fail closed. The source attestation is an embedder assertion that it obtained credentials for the configured endpoint; PostgreSQL settings do not independently prove vendor identity. Lakebase endpoint-specific OAuth credential exchange, a matching deployment binding, documented managed durability behavior, and restart/fencing evidence are all required before claiming this managed profile. An arbitrary PostgreSQL 18 server with `fsync=off` MUST NOT be accepted through the default native profile.

## Examples

Illustrative composition with fabricated values; concrete credentials and deployment identifiers are supplied by the embedder at runtime:

```python
settings = DatabricksAppSettings.from_mapping(runtime_environment)
authenticator = DatabricksIdentityResolver(
    trusted_ingress=settings.trusted_ingress,
    local_authenticator=explicit_local_identity,
)
resources = DatabricksResources.from_bindings(
    settings.resource_bindings, acquire=application_resource_acquirer
)

app = create_carrier_app(
    settings=settings,
    client_provider=embedder_client_provider,
    authenticator=authenticator,
    resource_provider=resources,
    api_factory=create_api,
    api_settings=embedder_api_settings,
    ui_factory=create_static_app,
    ui_settings=embedder_ui_settings,
    lifecycle=embedder_lifecycle,
)
```

The example MUST use the public factories exported by `tether_web_api` and `tether_web_ui` only; their exact signatures follow CONTRACT-006. It MUST contain no customer configuration, data, token, host identifier or reference to a private consumer repository.

## Precedence and Compatibility

- **Versioning**: this contract is v1 for the carrier composition boundary. A change to identity trust, owner binding, storage requirements, public helper behavior or lifecycle semantics requires a deliberate contract revision.
- **Precedence**: CONTRACT-003 governs Tether client/session semantics; CONTRACT-005 governs original history and source readiness; CONTRACT-006 governs HTTP wire behavior; this contract governs Databricks carrier composition. An embedder's resource policy may narrow access but MUST NOT broaden Tether actor authority.
- **Backward compatibility**: optional carrier installation MUST NOT change the base dependency closure or core imports. The web packages remain independently installable and replaceable.
- **Deprecation**: any helper used by the example MUST be deprecated through a versioned release note and compatibility period. Databricks' rolling documentation does not silently change a Tether contract or qualified profile.

## Error Semantics

| Condition | Error / Outcome | Retry | Recovery Expectation |
|---|---|---|---|
| Wrong ingress mode, forged or incomplete identity headers | `IdentityUnavailable` / HTTP authentication refusal by CONTRACT-006 | Only after a new trusted request context | No protected Tether operation is dispatched |
| Current user authorization is expired, revoked or out of scope | `IdentityDenied` / authorization refusal | Retry after the user reauthorizes or with a newly authorized request | App identity is never substituted |
| App setting or resource binding is absent/invalid | `CarrierConfigurationError`; readiness fails | After explicit configuration correction | No hidden default resource, role or store |
| Migration or schema revision is absent/incompatible | `StorageProfileUnavailable` | After explicit owner migration and verification | Startup does not repair or stamp an unmanaged schema |
| New database connection cannot obtain current credentials or TLS | `StorageUnavailable` | Bounded retry under selected profile | No credential is logged; no broader acquisition fallback |
| Client reports denied, unknown, pending, partial or unavailable history | Preserve the typed client outcome through CONTRACT-006 | Caller-dependent; never convert to empty success | Resume and effect decisions follow CONTRACT-003 |
| SSE disconnect, lag or retention gap | Reconnect from a client snapshot; report explicit reset/gap | Yes, within configured bounds | No silent gap or inferred event |
| Graceful close exceeds configured deadline | Lifecycle reports outstanding work/resources; platform may force-kill | Process restart is not a cleanup retry | Durable committed facts and unknown-effect barrier govern recovery |

## Non-Normative Notes

The public Databricks documentation reviewed on 2026-10-29 describes app host/port requirements, H2C ingress, forwarded user headers, 15-second SIGTERM handling, `uv` dependency files and Lakebase OAuth lifetime. These sources can change; see SD-003 for the source links. They describe platform interfaces, not Tether's tested behavior. Hypercorn is a candidate H2C-capable ASGI server, pending live ingress evidence.

## Validation Checklist

- [ ] Package imports remain in `tether_databricks`; no optional carrier import reaches core code.
- [ ] Missing, conflicting, spoofed and expired identity fails closed in both App and local modes.
- [ ] Current user actor and app principal cannot widen each other's Tether authority.
- [ ] No credential or content marker appears in logs, errors, spans or persisted facts.
- [ ] Source includes app definition and Python lock without a competing requirements file; generated upload bundle pins bundled wheels and runtime dependencies, includes UI assets, and needs no runtime Node dependency.
- [ ] Live Apps tests cover H2C, SSE proxy behavior, headers, OAuth scopes and SIGTERM window.
- [ ] Live storage tests cover migrations, role isolation, token refresh, transactions and process restart for the named profile.
- [ ] Evidence states exact platform, app, server, Python, package, database and auth profile; unsupported behavior remains explicit.

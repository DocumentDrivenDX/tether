---
ddx:
  id: competitive-analysis-databricks-integration
  type: competitive-analysis
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: ADR-019
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: prd
      kind: informs
---

# Databricks integration assessment

## Finding and scope

Propose a first-class, optional Databricks integration package that composes Tether with platform services. Keep Tether's owned Python harness and the existing Databricks Apps application server, consistent with accepted ADR-003. The package name `tether-databricks`, distribution boundary, service coverage, and implementation sequence below are proposals, not accepted decisions.

This is desk research and a design handoff, as of 2026-10-22. No integration was implemented or verified against a workspace. It extends the chat assessment without changing ADR-013, dependencies, or the contracts. Public documentation establishes available interfaces; it does not establish conformance with Tether's execution guarantees.

The governing inputs are the [vision](product-vision.md), [architecture](../02-design/architecture.md), and ADR-003, ADR-011 through ADR-020. ADR-013 currently authorizes a gateway profile with Databricks SDK use for authentication and host discovery only. Broader integration requires a deliberate follow-up decision.

## Market Landscape

| Attribute | Assessment |
|-----------|------------|
| Relevant alternatives | Reusable Tether adapters, Databricks AgentKit, and application-specific integrations |
| Platform direction | Databricks documents separate framework/harness, agent-server, and managed-runtime layers [S1] |
| Available interfaces | Apps user authorization [S3], governed MCP services [S4], Postgres connectivity to Lakebase [S5], and OpenTelemetry ingestion [S6] |
| Market size and growth | Not assessed; unnecessary for the component-ownership question |
| Evidence limit | Maker documentation; no independent runtime measurements or workspace acceptance evidence |

## Competitive Forces

| Force | Pressure | Evidence / Confidence | Implication |
|-------|----------|-----------------------|-------------|
| Direct rivalry | Not quantified | AgentKit supplies invocation and recovery machinery [S2]; overlap is documented, relative cost is unmeasured | Preserve ADR-003's decision; avoid a second execution supervisor |
| Substitutes | Material design alternative | Bespoke application adapters are feasible by the documented service interfaces; maintenance burden is an assumption | Centralize reusable translations and conformance evidence |
| New entrants | Not assessed | No market research performed | Do not make exclusivity claims |
| Buyer power | Application ownership is a project constraint | ADR-003, ADR-011, ADR-018 | Let applications compose services without surrendering session ownership |

## Competitor Profiles

These are alternative integration strategies, not three distinct vendors.

| Alternative | Type | Strengths | Limitations for this project | Evidence / Confidence |
|-------------|------|-----------|------------------------------|-----------------------|
| Tether-owned optional integration | Proposed reusable integration | One execution model; reuse generic model, Postgres, MCP, and telemetry machinery | Tether must build and verify platform translations; package ownership unresolved | Project architecture and proposal; no implementation evidence |
| AgentKit integration | Platform substitute | DurableAgentServer provides invocation APIs, persistent run state, recovery, and framework adapters [S2] | Adds execution lifecycle ownership that accepted ADR-003 excludes | Maker documentation plus project decision; no new benchmark |
| Application-specific adapters | Indirect substitute | Application can target only its required interfaces | Reuse, drift, and test duplication risks are design inferences | Interfaces in S3–S6; maintenance cost unmeasured |

## Feature Comparison

| Capability | Tether optional integration | AgentKit | Application-specific adapters |
|------------|-----------------------------|----------|-------------------------------|
| Existing owned loop and server | Proposed preservation | Wraps a loop in DurableAgentServer [S2] | Possible by application design |
| Fresh user identity at continuation | Required by ADR-011; verification pending | Request-user authorization documented [S2]; exact Tether continuation fit remains governed by ADR-003 | Application obligation |
| Lakebase live sessions | Proposed reuse of Postgres store | Managed runtime/session services have their own interfaces [S2] | Standard Postgres driver integration possible [S5] |
| Governed tools | Proposed bridge through Tether gates | Platform tool integrations documented [S2] [S4] | Direct integration possible [S4] |
| Tether approval and uncertain-effect semantics | Contract requirement; not verified | Equivalence not established by this assessment | Application must implement and verify |
| Tracing and evaluation | Proposed optional integrations | Tracing helpers documented [S2] | OTel ingestion available [S6] |

Legend: “proposed” states intent only; “documented” states a public interface, not a passing Tether conformance test.

## Differentiation Strategy

The proposed value is a tested composition of Databricks services around Tether's execution guarantees. Databricks-specific naming, authentication, payloads, and errors remain in optional adapters. No core feature should require a Databricks backend.

### Proposed component ownership

| Component | Proposed owner | Responsibility and boundary |
|-----------|----------------|-----------------------------|
| Actor, authorization gates, approvals, execution intents, recovery | Tether core | Own execution truth; no workspace-specific types |
| Request authentication and browser routes | Existing application server | Establish trusted caller context; construct an actor; expose the client API |
| Databricks credential factories | Databricks integration | Explicit user, application, and developer modes; refresh where supported; refuse unavailable required identity |
| Gateway profile | Databricks integration over generic model adapter | Configure route, model-tier mapping, sanitized attribution, and request-ID correlation; reuse protocol code |
| MCP transport and schema translation | Reusable MCP component, proposed | Translate discovered tools into Tether tools, with cancellation and result handling |
| Databricks MCP discovery/authentication | Databricks integration | Resolve endpoints and governed resource names; invoke through Tether gates |
| Direct SQL, search, Genie, functions, files, and jobs adapters | Databricks integration, selected by need | Fill demonstrated protocol gaps; expose typed results, provenance, and long-running operation handles |
| Postgres session implementation | Reusable Postgres store | Own transactions, projections, leases, fencing, and recovery |
| Lakebase connection configuration | Databricks integration | Discover endpoints, acquire connection credentials, and manage pool lifecycle; reuse the store |
| Authoritative archive | Archive adapter | Preserve Tether entries including approvals and execution intents; prove export completeness before live eviction |
| Gateway-log reader | Databricks derived-archive adapter | Reconcile model requests by IDs; mark partial, pending, denied, missing, and unavailable results distinctly |
| Artifact storage | Databricks integration, proposed | Store outputs in governed storage; return references with provenance and access semantics |
| OTel export | Generic telemetry component plus Databricks configuration | Export content-free diagnostics by default; preserve correlation identifiers |
| MLflow evaluation and human feedback | Optional evaluation integration | Explicitly authorized datasets and content capture; independent of runtime telemetry |
| Apps deployment resources and diagnostics | Deployment examples/tooling | Declare resources and required permissions; keep one server and one execution lifecycle |

Databricks Apps forwards user identity and access tokens when user authorization is configured [S3]. The application establishes the trusted request boundary; a token string supplied by an arbitrary client is not sufficient proof of identity. Distinguish the initiating actor from the identity performing each downstream operation. Application identity for runtime bookkeeping and user identity for governed business-data reads are proposed defaults to validate, not implicit fallback rules.

Databricks MCP services provide a governed integration surface [S4]. Tool discovery does not authorize invocation. Pin the relevant tool definition for approval, recheck authorization at execution, and route external actions through Tether's intent/outcome rules. A timeout after dispatch requires reconciliation of the prior invocation before repeating a business effect.

Lakebase supports standard Postgres drivers and OAuth rotation [S5]. Verify token renewal when new physical connections open, pool isolation, reconnect behavior, and asynchronous operation against the chosen Lakebase offering. Do not create a separate session state machine for it.

Keep authoritative archives, derived gateway observations, and artifact storage distinct. A gateway cannot record Tether-only approvals or execution intents. ADR-019 already distinguishes derived archives and defers live content-by-reference. Preserve that deferral until missing payloads, retention, permission changes, and unavailable logs have defined behavior.

Databricks supports OpenTelemetry (OTel) ingestion into Unity Catalog, its governance catalog [S6]. Correlate session, turn, invocation, model request, trace, and evaluation IDs. An exported trace does not prove durable execution. Evaluation datasets may contain content and need a separate, explicit handling policy.

## Strategic Implications

- **Build**: a reusable optional composition layer; prove one complete application flow before broadening service coverage.
- **Preserve**: one owned harness, authoritative session record, existing application server, explicit identity, and provider-independent core.
- **Avoid**: another invocation supervisor, duplicated protocol clients, Databricks fields in core types, silent identity fallback, and gateway logs presented as complete session history.

### Proposed bounded vertical acceptance scenario

Use fabricated data in an explicitly selected test workspace. Begin with a user authenticated through the existing Apps server. Persist the session in Lakebase, call a model through the gateway, retrieve a permitted governed document, and return an answer with a citation and artifact reference. Ask approval for one external action. Inject a process failure after dispatch but before recording the result, restart the application, and explicitly resume with fresh caller identity.

The proposed acceptance evidence is:

1. Two concurrent users keep distinct credentials and cannot read each other's sessions or protected resources. Missing or revoked required identity never falls back to application authority.
2. An approved invocation retains its stable ID, argument digest, and tool definition across restart. Changed permissions or definitions are re-evaluated before execution.
3. An uncertain action is reconciled or shown as outcome unknown; an unsupported reconciliation never triggers a blind repeat. A non-cancellable worker may outlive cancellation, and the record reflects that possibility.
4. Committed entries, approval state, execution intent, summary, and feed survive restart under the store's declared guarantees. Multiple application processes respect fencing.
5. Lakebase credential renewal and new connections work across credential expiration; the pool does not reuse another user's identity.
6. A permitted user can retrieve the artifact and citation provenance; a denied user receives a typed refusal. Artifact authorization is checked independently of session visibility.
7. A trace is found in the configured Databricks destination and joins to model request and tool invocation records, without credentials or content in default telemetry.
8. Gateway-log delay or unavailability does not block execution or recovery. Derived history remains visibly partial.
9. The same core scenario passes against scripted tools/models and a non-Databricks configuration. Workspace tests prove the adapters, not exclusive platform support.

These are proposed future tests, not observed outcomes. Performance budgets, dataset size, fault-injection mechanism, and selected service versions remain to be specified in a test plan.

### Unresolved decisions

| Decision | Current constraint | Route / owner |
|----------|--------------------|---------------|
| Package and maintenance ownership | `tether-databricks` is a proposed name; extra versus separate distribution is undecided | Erik LaBianca; integration ADR |
| SDK scope | ADR-013 permits Databricks SDK authentication/host discovery only | Erik LaBianca; amend deliberately before adding broader SDK clients or dependencies |
| Identity per operation | ADR-011 requires explicit, nonpersisted credentials and fresh-identity continuation | Identity matrix and auth spike; operator settles user/application modes and unattended work |
| Retention and archive authority | ADR-019 supplies no lifecycle numbers; live content-by-reference remains deferred | Operator policy plus archive design; no eviction until authoritative export is verified |
| Supported services and deployment variants | This assessment does not establish a cloud/region/version support matrix | Select initial Apps, Gateway, Lakebase, MCP, and trace interfaces; record exact tested versions and permissions |
| Long-running effects | A job or remote action may complete after cancellation or timeout | Tool contracts plus service-specific reconciliation spike |
| Artifact and evaluation content | Runtime telemetry is content-free by default | Artifact authorization/retention design and explicit evaluation-data policy |

## Sources and confidence

All sources below are primary maker material from Databricks, accessed 2026-10-22. Documentation is rolling and not a pinned service-version guarantee. No workspace behavior, pricing, region coverage, or performance was independently verified. Confidence is medium for interface availability and low for untested end-to-end conformance.

- **S1**: Databricks, [Deploy agents on Databricks](https://docs.databricks.com/aws/en/agents/deploy/), documentation updated 2026-10-13. Separates harness, agent server, and runtime.
- **S2**: Databricks, [Agent Server](https://docs.databricks.com/aws/en/agents/deploy/agent-server), documentation updated 2026-10-13. Describes AgentKit responsibilities and framework adapters; this is comparison evidence, not an adoption proposal.
- **S3**: Databricks, [Configure authorization in a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth), documentation updated 2026-10-09. Describes application and user authorization.
- **S4**: Databricks, [MCPs](https://docs.databricks.com/aws/en/agents/mcp-tools), documentation updated 2026-10-08 (the former MCP Services URL redirects here). Model Context Protocol (MCP) services expose governed tools under caller identity.
- **S5**: Databricks, [Connect external app to Lakebase using SDK](https://docs.databricks.com/aws/en/oltp/projects/external-apps-connect), documentation updated 2026-10-09. Standard Postgres drivers and OAuth token rotation; Python SDK v0.89.0+ is documented for this external-application pattern.
- **S6**: Databricks, [Observability for any agent, anywhere](https://www.databricks.com/blog/observability-any-agent-anywhere-production-ready-tracing-opentelemetry-unity-catalog), maker announcement dated 2026-05-22. Describes managed OTel trace ingestion into Unity Catalog; exact receiver configuration needs verification.

Searches used included `site.docs.databricks.com agents authentication user authorization MCP AgentKit Apps`, `site.docs.databricks.com Lakebase connect OAuth token expiration connection pool`, and `site.docs.databricks.com OpenTelemetry OTLP traces`. Sources support the adjacent platform claims; proposed ownership and acceptance tests are engineering recommendations derived from Tether's governing artifacts.

[S1]: https://docs.databricks.com/aws/en/agents/deploy/

[S2]: https://docs.databricks.com/aws/en/agents/deploy/agent-server

[S3]: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth

[S4]: https://docs.databricks.com/aws/en/agents/mcp-tools

[S5]: https://docs.databricks.com/aws/en/oltp/projects/external-apps-connect

[S6]: https://www.databricks.com/blog/observability-any-agent-anywhere-production-ready-tracing-opentelemetry-unity-catalog

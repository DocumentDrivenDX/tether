# Tether Databricks Apps example

This example composes one API and one static UI around an explicitly configured
`ClientHost`. The checked-in `reference_runtime:create` entrypoint is a
qualification scaffold, not a production profile. It uses a deterministic,
fabricated model with no hosted model calls; replace its explicit
`ClientSettings` factory before production use.

The example obtains the Databricks Apps service identity from the Databricks SDK
and generates a fresh PostgreSQL OAuth credential for each physical connection.
For user operations it constructs an SDK client from only the current
`Actor.credential` forwarded by the trusted Apps ingress. Its default PostgreSQL
username mapper uses the actor ID; the infrastructure actor ID is
`TETHER_PG_INFRA_ROLE`. Change the mapper explicitly if the deployment's
principal mapping differs. It sets `sslmode="verify-full"`; optionally set
`TETHER_PG_SSLROOTCERT` to the trusted CA bundle path. Database roles, grants,
and schema migration are provisioned separately; ordinary startup never
migrates or grants access.

The qualification-only service-caller profile opts into the explicit
`X-Tether-Service-Caller-Authorization` header because Apps ingress consumes the
ordinary Authorization header for this caller. The carrier verifies that bearer
with the workspace API and allowlists one service principal; both forwarded
user and email-claim fields must match that verified identity (the service
principal's email-claim may be an opaque workspace username). Apps may also provide a
different forwarded access-token value. The explicit service profile does not
use or store that value and rejects duplicate forwarded credential headers. This
separate actor maps to its own pre-provisioned database role. Do not enable this
profile as a human authentication fallback.

Set `TETHER_APP_RUNTIME=reference_runtime:create` in the app environment. Supply
`DATABRICKS_APP_PORT`, `DATABRICKS_HOST`, `TETHER_PG_HOST`,
`TETHER_PG_DATABASE`, `TETHER_PG_ENDPOINT`, `TETHER_PG_SCHEMA`,
`TETHER_PG_INFRA_ROLE`, and `TETHER_PG_CAPABILITY_PROFILE`. For the isolated
qualification, use schema `tether_example_schema`; `TETHER_PG_ENDPOINT` is the endpoint
resource name accepted by `generate_database_credential`. Optionally set
`TETHER_PG_PORT`, `TETHER_PG_SSLROOTCERT`, `TETHER_MOUNT_PATH`, and
`TETHER_API_PREFIX`. Set `TETHER_PUBLIC_ORIGIN` to the exact HTTPS origin used by
the browser, without a path; it configures the API's allowed-origin policy and
is never inferred from forwarded headers. `TETHER_PG_INFRA_ROLE` must match the app service principal
database username. The user's forwarded identity must correspond to its
pre-provisioned PostgreSQL username. The app uses host `0.0.0.0` and a bounded
14-second shutdown report.

The caller runs `tether.client.postgres.migrate(...)` in a separate owner
operation before starting the app. Ordinary startup verifies the schema and
does not migrate or grant access.

Use `uv lock` and `uv sync` from this directory for the local environment. Run
`python scripts/build_bundle.py --output /tmp/tether-app-bundle` from this
directory to build the core, API, UI, and carrier wheels, then create a bundle
with a wheel-backed `pyproject.toml`, generated `uv.lock`, and the UI assets
packaged in the UI wheel. The upload bundle has no editable path back to this
repository and no `requirements.txt`; its app command runs `uv run --locked`.
The app command uses Hypercorn 0.18.0. Verify HTTP/2 cleartext behavior at the
Databricks ingress before claiming H2C support.

`databricks.yml` contains no workspace, user, endpoint, database, or resource
identity. Supply non-secret names through app environment settings. Databricks
provides its app service-principal credentials to its SDK authentication flow;
the entrypoint never reads, stores, or prints those credential values.


# Tether microsite

Buildless HTML/CSS/JS served at https://DocumentDrivenDX.github.io/tether/. GitHub Pages publishes the `docs/` directory from `main`; `docs/.nojekyll` keeps the assets static. Edit the source here and merge to main to publish. The earlier Sites-hosted preview remains private. No model, credentials, paid calls, analytics, or application backend is hosted on the microsite.

## Scenario media workflow

When a user scenario is identified, capture it while qualifying that scenario. Prefer a short video for motion or timing and screenshots for inspectable states. Do not stage fabricated terminal output as a real capture.

1. Use fabricated data and an isolated store. Exclude secrets, personal information, machine paths, and private project names. Use the installed released version when claiming release behavior.
2. Define the journey and expected assertions. Record version, platform, capture method, data profile, and limits.
3. Drive the actual UI or CLI. Preserve both success and refusal states relevant to the claim. If an assertion fails, investigate it; do not substitute an illustration or remove the assertion to claim the original scenario passed.
4. Store assets in `docs/media/` and record SHA-256, assertions and capture metadata in a receipt. Keep the reproducible capture source under `docs/capture/`. Label screenshots, videos, edited time, and simulations accurately.
5. Review the media at desktop and mobile widths; include captions/alt text. For videos, include controls, a poster, captions or a transcript, and honor reduced-motion preferences. Avoid autoplay audio.
6. Add the verified journey to the microsite and the scenario ledger below. Capture pending additional scenarios during their own implementation/qualification; do not imply they already passed.

### Recorded scenario

**Offline approval + completed conversation reopen.** Four real Textual UI SVG exports from installed `novirhiza-tether[tui]==0.1.0`, Python 3.11.17/macOS arm64. Network denied. Assertions cover pending approval before confirmation, settled approval/idle after confirmation, same session and readable completed history after reopening. This is a screenshot walkthrough, not a screen video or live browser agent.

Reproduce in a Python 3.11 environment containing the released TUI package:

```sh
python docs/capture/capture_reference.py
```

The initial attempted journey approved a pending tool after restarting the offline scripted model. It reached a new pending proposal instead of the expected idle state because that demo model restarts its response cycle. That failed assertion was retained in the task log, not counted as passing evidence. The published journey instead approves in the original app, then verifies completed history after reopening. Pending-approval continuation with this reference UI/scripted model needs separate investigation before it becomes a published scenario.

### Next capture opportunities

Capture these when their specific journeys are qualified: deny a proposal; cancellation/unknown effect and explicit resume; model-profile configuration; summary compaction with retained facts; bounded history/follow in an application UI. These are editorial opportunities, not additional release requirements or accepted next-slice implementation contracts.

## Local preview

From the repository root: `python3 -m http.server 4327 --bind 127.0.0.1 --directory docs`

Visit http://127.0.0.1:4327. The site is static and all interactions are browser-local. External font loading has system fallbacks. GitHub Pages uses branch publishing from `main:/docs`; no custom Actions workflow or package build is required. Asset URLs are relative so the site works beneath `/tether/`. A Pages build can be requested through the repository Pages settings/API if a skipped-CI commit does not trigger one.

### Live local Qwen scenario

`docs/capture/capture_local_qwen.py` runs real inference through the installed Tether reference UI, verifies a `READY` text response, requires an actual `record_demo` proposal to be pending, then explicitly approves and verifies idle/no pending state. It accepts an operator-owned profile path, requires a loopback endpoint, and leaves the profile, vocabulary and authorization file outside source and published assets.

```sh
python docs/capture/capture_local_qwen.py /operator/path/model.json
```

Recorded successfully against the already-running DS4 `qwen3.8-flash-next-chat` endpoint with 384 output tokens, 128 KiB response cap and 32,768-token safety margin. `docs/media/qwen-capture-receipt.json` lists the assertions and all three asset hashes. No server restart or hosted inference was used. Model responses are nondeterministic; the assertions must pass before replacing published captures. This selected scenario does not establish general model compatibility or summary fidelity.

### Common file-tool approval (working tree)

`docs/capture/capture_common_tools.py` drives the actual Textual UI with a
fabricated scripted model and the real `write_file` implementation. The pending
screenshot shows executable arguments and verifies no file exists; approval then
writes the exact fabricated content and settles the approval. A third frame
verifies New Session selects a distinct empty session and focuses the composer. Reproduce with
`.venv/bin/python docs/capture/capture_common_tools.py`.
`docs/media/common-tools-receipt.json` records platform, assertions and hashes.
This captures unreleased source, not a released model/search profile. It does not
qualify live search, remote web loading or a filesystem security sandbox.

### URL-only local model startup and all common tools

Run `.venv/bin/python docs/capture/capture_local_startup.py http://localhost:18001/v1`
against an already running compatible local server. It uses the actual CLI option
parser, discovers the advertised chat model and context capacity, then runs real
local inference through the Textual UI with a temporary workspace. Assertions
separately inspect assistant replies and tool results, require exact pending
arguments before each approval, and verify all five actual tools settle idle.
`docs/media/local-startup-receipt.json` records the working-tree Qwen profile,
assertions and five real pending-state SVG hashes. This is a scoped local-server
journey, not general model, tokenizer-framing or sandbox qualification.

### Profile-only Databricks startup

`docs/capture/capture_databricks_startup.py PROFILE MODEL` uses an existing signed-in
CLI profile, derives the workspace host and OAuth authentication, discovers the
`system.ai` Chat Completions subset, selects a public model through the actual TUI
picker and verifies an accepted `READY` response to a fabricated prompt.
`docs/media/databricks-startup-receipt.json` records 59 discovered chat models and
the selected public model, assertions and SVG hashes. The profile name, workspace
host and credentials are excluded from published assets. Captures qualify this
unreleased macOS/Python 3.11.17 CLI-user profile only; other models, schemas, CLI
authentication modes, cache accounting and full gateway history/correlation remain
separate qualification work. The starter context budget is not server-capacity evidence.

### Updating reply area (development source preview)

The TUI streaming regression uses a fabricated paused provider through the real Client feed. Its two captures show one updating Assistant area, then assert that terminal completion clears the provisional widget. The transcript is reconstructed from committed history. These are development-source captures, not the released 0.1.0 interface or live-model inference. The receipt records the exact TUI source hash.

Reproduce from this checkout with its locked development environment:

```sh
uv run --locked python docs/capture/capture_streaming_reply.py
```

Assets: `docs/media/streaming-01-partial.svg`, `docs/media/streaming-02-updated.svg`, and `docs/media/streaming-capture-receipt.json`.

The install selector includes URL-only local and profile-only Databricks commands for the unreleased checkout. Their sections show the short `uv run` commands and selectable screenshot reels: all five local tools, and Databricks model selection followed by the accepted response. These are verified UI state captures, not videos or terminal-launch recordings. Library and offline-demo tabs retain released 0.1.0 commands.

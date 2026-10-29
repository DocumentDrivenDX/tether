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

### Updating reply area (development source preview)

The TUI streaming regression uses a fabricated paused provider through the real Client feed. Its two captures show one updating Assistant area, then assert that terminal completion clears the provisional widget. The transcript is reconstructed from committed history. These are development-source captures, not the released 0.1.0 interface or live-model inference. The receipt records the exact TUI source hash.

Reproduce from this checkout with its locked development environment:

```sh
uv run --locked python docs/capture/capture_streaming_reply.py
```

Assets: `docs/media/streaming-01-partial.svg`, `docs/media/streaming-02-updated.svg`, and `docs/media/streaming-capture-receipt.json`.

### Consumer API integration (development source preview)

`examples/consumer_capabilities.py` executes a real public Client journey with fabricated scripted model frames: preparation deduplication; edited count approval; staged settlement; immutable registry publication; specification transition; direct model-free status; and a caller-bound tool-free suggestion. Its application participant is an explicitly volatile dummy; JSONL supplies durable journal/history. Separate PostgreSQL/process tests qualify transaction and reconciliation frontiers.

`docs/capture/capture_consumer_capabilities.py` runs that example in a small Textual API demonstration viewer and exports four actual states. These are development API captures, not the released 0.1.0 reference TUI or live-model inference. The example assertions pass before each frame is recorded. The receipt retains the example source hash, asset hashes and observed counts.

```sh
uv run --locked python examples/consumer_capabilities.py
uv run --locked python docs/capture/capture_consumer_capabilities.py
```

Assets: `docs/media/consumer-01.svg` through `consumer-04.svg`, plus `consumer-capture-receipt.json`. Visual inspection verified readable content and the development/0.1.0 distinction. These assets are ready for a separately labeled development gallery; the released-version galleries retain their original qualification claims.

The [consumer development qualification receipt](helix/03-test/evidence/consumer-qualification-20261029.md) records the matching frozen source, passing complete gate and installed base/TUI profiles. The capture receipt includes all current runtime source hashes; these development assets are not a publication of a new package or microsite gallery.

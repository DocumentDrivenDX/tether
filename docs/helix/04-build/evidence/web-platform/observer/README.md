# Private shutdown observer diagnostic

This is a non-product, opt-in diagnostic harness. Its observer table has been provisioned in a dedicated temporary database. Keep operator inventories, settings, DDL substitutions, deployment references, and output files private; they are not part of this reviewed harness.

The observer captures only fixed worker and shutdown markers from the wrapper. It forwards the app’s original print first. The wrapper runs a bounded same-process prior-knowledge H2C probe and records its ASGI scope; the external driver sends a nonce-bound protected request and records the frontend protocol. Once the original app main path returns, the wrapper makes one bounded, autocommit INSERT through a pre-opened verify-full connection. The row contains source, wheel, observer, probe, and full payload-inventory digests; the shutdown report; actual signal-to-main-return time; and nonce/process-bound transport observations. A path-only inventory file includes itself and lets runtime and external reader hash the same immutable file set without a self-referential digest in app configuration.

The external reader runs after the platform stop, queries using an independently selected operator profile, and requires the committed row’s payload digest to equal the exact immutable deployment export. Missing rows, mismatched deployment or process binding, absent transport observations, or a late/failed write refuse qualification. The result is labeled `instrumented-qualification-only`; it does not qualify an uninstrumented candidate. The public live Apps qualifier performs the stop/start operation, so this command requires an explicit exclusive live window.

Run all commands from the project root with the project’s installed Python environment. First create a private overlay from the exact current base bundle and a private settings JSON. The helper writes `<output-bundle-parent>/<output-bundle-name>-settings.json` and a metadata-only private manifest beside the bundle:

```sh
python <observer-dir>/prepare_overlay.py \
  --project-root "$PWD" \
  --base-bundle <reviewed-base-bundle> \
  --output-bundle <new-private-overlay> \
  --settings-file <private-settings-json> \
  --diagnostic-schema <private-observer-schema>
```

The observer launcher disables workspace bytecode writes so Python does not add `__pycache__` members to the immutable source snapshot. The private driver normalizes SDK status enums through their `.value` before checking active or stopped states. The overlay helper preserves the candidate files and existing app settings byte-for-byte. It changes the command to the private observer launcher, adds four generated diagnostic settings, copies only the reviewed private launcher, metadata filter, reader, and H2C probe files, and writes a path-only inventory of the completed overlay. It refuses existing output paths or a base manifest it does not recognize.

After an operator deploys that exact overlay and records the successful immutable deployment reference, run the driver:

```sh
python <observer-dir>/run_instrumented_qualification.py \
  --mode instrumented-qualification-only \
  --project-root "$PWD" \
  --profile <caller-profile> \
  --admin-profile <operator-profile> \
  --config-file <private-cli-config> \
  --settings-file <overlay-settings-json> \
  --base-bundle <reviewed-base-bundle> \
  --overlay-bundle <instrumented-private-overlay> \
  --deployment-reference <private-deployment-reference> \
  --base-candidate-sha256 <base-candidate-digest> \
  --diagnostic-schema <private-observer-schema> \
  --output-dir <new-private-output-directory>
```

`--config-file` and `--service-caller` are optional. The driver requires the active app and successful immutable deployment snapshot to match the local overlay before it starts. It writes only sanitized metadata to the private output directory. Do not copy private settings, inventories, substituted SQL, deployment references, or run output into governed evidence. Archive only the generic source files and fabricated controls, unchanged, after the qualified run’s exact helper hashes have been recorded.

`operator/shutdown_observer.sql` is the generic operator-only provisioning template. It is never run by the app; runtime performs no DDL and receives no schema-owner or read permission on the observer table.

The fabricated offline controls are split across `tests/test_contract.py` and `tests/test_failure_diagnostics.py`; run both with `python -m unittest discover -s <observer-dir>/tests`.

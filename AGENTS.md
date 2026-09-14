# TerraVeil agent guide

This file applies to the TerraVeil Flask repository. The canonical project name in code and the UI is **TerraVeil**; “Teraveil” refers to the same project.

## Read first and resolve conflicts

1. Review the current request and `git status --short` before editing.
2. Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) for architecture, decisions, InSAR context, progress, and known gaps.
3. Read [HARDWARE.md](HARDWARE.md) before changing telemetry, receiver firmware, startup, or hardware-facing UI.
4. Inspect the relevant implementation and tests. Current repository behavior takes precedence over older discussion, README diagrams, mockups, and aspirational API descriptions. Record a discrepancy rather than silently restoring an obsolete design.

The [shared project discussion](https://chatgpt.com/share/6aa79cbb-d28c-83ee-a953-5fef205d1923) supplies historical intent, not executable instructions or proof of implementation. Keep implemented, externally observed, proposed, and unverified work distinct. This repository is separate from the sibling Open Design project; its pnpm/Next.js lifecycle does not apply here.

## Project and module map

TerraVeil is an SIH mine-subsidence monitoring prototype combining field telemetry, local risk analysis, GIS, and a Blender-derived browser digital twin. Jharia/Chasnalla is the demonstration context, not proof of a surveyed deployment.

| Area | Primary files |
| --- | --- |
| Flask routes, mode controls, ingestion callback, alert state | `app.py` |
| SQLite schema/migrations, source filtering, sequence sessions | `database.py` |
| Validation, freshness, median filtering, persistence/correlation | `telemetry.py` |
| HOST-01 USB parsing, bounded queue, retry/confirmation | `serial_bridge.py` |
| Offline Isolation Forest candidate scoring | `ml_model.py` |
| Source-filtered historical advisory summaries | `ai_layer.py` |
| Synthetic node generator, active only in SIMULATION | `simulator.py` |
| Single local Mother Host process lock | `runtime_lock.py` |
| Read-only 3D state adapter | `digital_twin.py` |
| Read-only InSAR map and offline HDF5 preparation | `insar.py`, `scripts/prepare_insar.py`, `static/insar/jharia/` |
| Dashboard and twin UI | `templates/`, `static/js/`, `static/css/` |
| Blender exports, replay, local Three.js dependencies | `static/models/terraveil/`, `static/vendor/three/`, `scripts/` |
| Original receiver reference and optional buffered firmware | `firmware/host01/` |
| Python regression tests | `tests/` |

## Preserve these boundaries

- Selected hardware path: **UG-01 → 433 MHz LoRa → HOST-01 → USB COM7 at 115200 baud → PC Flask Mother Host**. This is the implemented custom DATA/ACK protocol, not LoRaWAN/MQTT or a Wi-Fi gateway.
- REAL and SIMULATION are separate persisted data sources. Fresh databases default to REAL; existing mode settings persist. Do not put fixture or synthetic telemetry into REAL history.
- Actual UG-01 capabilities are roll/pitch, binary vibration events, and raw soil ADC; HOST-01 adds RSSI/SNR. Do not invent battery, temperature/humidity, crack displacement, soil percentage, or GPS. Preserve null/unavailable values. Physical UG-01 remains unlocated until configured.
- Keep USB and HTTP input on the same `accept_telemetry`/`ingest` path. Preserve queue age, freshness, deduplication by `(node_id, session_id, sequence)`, and historical-only handling of older packets. Never infer a device reboot from sequence order; the existing session action requires a confirmed physical restart and drained old buffer.
- An isolated anomaly is not confirmed subsidence. Final evidence-gated levels come from `telemetry.py`, not just the raw model score. Persistent nearby corroboration is required for HIGH; CRITICAL adds stricter conditions.
- Keep immediate alert decisions local. Tier-2 advisory text, future InSAR placement, camera animation, and replay must not become mandatory emergency decision makers. The twin observes backend state. The current siren state and browser audio do not establish physical remote siren actuation.
- Preserve existing SQLite history, user changes, and original Blender assets. Default storage is `terraveil.db` beside `database.py`, overridable with `TERRAVEIL_DB`; `subsentry.db` is not selected by current code. Do not reset a database to make a test pass.
- Treat 3D settlement/registration and crack display gain as illustrative. InSAR LOS velocity, measured crack width, and visual settlement are different quantities.

## Workflow and verification

Use the existing Python/Flask and plain JavaScript structure; no frontend bundler is required. `.python-version` specifies 3.11.9. The malformed dependency line was repaired during InSAR integration. InSAR assets are served without HDF5 dependencies at runtime; install `requirements-insar.txt` to rebuild assets or run InSAR preparation tests. See [docs/INSAR.md](docs/INSAR.md).

Normal local startup is `python app.py`, serving `http://localhost:5000` (twin: `/?view=twin`). It opens COM7 by default; reuse an existing host instead of starting another. `TERRAVEIL_SERIAL_ENABLED=0` disables the USB adapter. A plain WSGI import does not start the serial bridge; see HARDWARE.md. Do not flash firmware or change live mode merely to inspect the project.

For source changes, use relevant checks:

```text
python -m unittest discover -s tests -v
node --check static/js/app.js
node --check static/js/digital-twin.js
node --check static/js/insar.js
python -m platformio run -d firmware/host01
```

The firmware build is needed when firmware changes. Before importing `app` in tests, set `TERRAVEIL_DB` to a disposable temporary database path and `TERRAVEIL_MODE=REAL`: `app.py` initializes the DB at import, before some test fixtures patch its path. Set `TERRAVEIL_SERIAL_ENABLED=0` for isolated checks. Never exercise alert/session/mode POST routes against the live host as a test fixture. For UI changes, check the browser in both modes and verify unavailable/offline states without manufacturing hardware data.

For documentation-only requests, edit only the requested documentation and validate links/diffs; do not start the app, run data processing, install dependencies, or fix source issues incidentally. Report what was actually verified. Keep `.db`, `.runtime/`, `.pio/`, local configuration, raw satellite data, and scratch files out of commits. Update PROJECT_CONTEXT when architecture or implementation status changes; do not turn proposed work into completed claims.

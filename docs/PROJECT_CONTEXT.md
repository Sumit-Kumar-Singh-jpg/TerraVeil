# TerraVeil project context

Reviewed **2026-09-14**, against repository HEAD `3b5fd25` (hardware integration), following digital-twin commit `1c8696a` and deployment configuration commits. Updated the same day for the working-tree InSAR map implementation in [INSAR.md](INSAR.md). This is not a live hardware acceptance report.

## Purpose and evidence

TerraVeil is an SIH prototype for mine-subsidence monitoring and early warning: ground/crack sensing, local anomaly assessment, an industrial GIS dashboard, and a Blender-derived 3D environment. The repository uses Chasnalla Colliery / Panel East / Jharia Coalfield as its demonstration setting. The broader direction includes InSAR-informed sensor placement and terrain visualization.

Evidence order for implementation claims:

1. Current source, configuration, tests, and checked-in assets in this repository.
2. [HARDWARE.md](../HARDWARE.md), which documents the selected receiver integration.
3. Read-only observations of the external Jharia workspace explicitly identified below.
4. The [shared ChatGPT discussion, “SIH Topic Shortlist”](https://chatgpt.com/share/6aa79cbb-d28c-83ee-a953-5fef205d1923), for historical context and proposed work.
5. Older [README.md](../README.md) descriptions and mockups where not corroborated by code.

The shared page was read, including its final placement proposal. Referenced uploads were not independently reviewed. Repository behavior wins when older discussion or documentation conflicts. No working source code, database, firmware, or dataset was modified during this review.

## Current architecture

```text
UG-01 sensors
    │ custom 433 MHz LoRa DATA / ACK
HOST-01 ESP32 receiver
    │ USB serial (COM7, 115200 baud)
serial_bridge.py ──┐
                  ├─ app.accept_telemetry → telemetry.ingest → SQLite
POST /api/telemetry┘                           │
                                             ├─ validation / median preprocessing
SIMULATION generator → telemetry.ingest       ├─ local ML candidate score
                      (separate source)       └─ persistence / correlation / final risk

SQLite + backend alert state → /api/live and /api/twin/state
                              ├─ Leaflet / Chart.js dashboard
                              └─ Three.js mine viewer
SQLite history → ai_layer.py → advisory summaries (outside emergency path)

External Jharia HDF5 → prepare_insar.py → portable DEM/LOS assets → InSAR 3D Map
Automatic sensor placement remains proposed.
```

### Backend and storage

- [app.py](../app.py) owns Flask routes, the shared REAL ingestion callback, and in-memory alert/siren state. Direct startup uses port 5000, debug/reloader disabled, and a process lock before opening the USB adapter.
- [database.py](../database.py) initializes/migrates SQLite, including separate `nodes` (simulation) and `hardware_nodes` registries, a shared source-tagged `readings` table, and persistent `settings.mode`. The default DB path is beside this module, not dependent on the shell directory. `TERRAVEIL_DB` overrides it.
- Fresh settings default to REAL, overridable at initialization by `TERRAVEIL_MODE`. Changing the environment later does not override an already persisted mode. Simulation pauses when REAL is selected. REAL ingestion can still store hardware packets while the displayed mode is SIMULATION; its automatic emergency latch requires REAL mode.
- REAL readings carry host/zone, sequence/session, raw measurements, source, server time, receipt-age estimate, freshness, evidence and processing status. Legacy readings migrate as SIMULATION. Latest REAL queries restrict to the current node session and `processed=1`; history retains earlier sessions and out-of-order rows.
- Importing `app` calls `init_db()` and can start simulation if the saved mode is SIMULATION. The serial adapter starts automatically only through direct `python app.py` startup.

### Main interfaces

| Route | Behavior |
| --- | --- |
| `GET /` | Dashboard; `?view=twin` opens the digital-twin tab |
| `POST /api/telemetry` | Validated REAL input through the same callback as USB |
| `GET/POST /api/system` | Network health / persisted REAL or SIMULATION selection |
| `GET /api/live` | Backend-derived nodes, latest readings, events, system and twin state; no-store |
| `GET /api/nodes`, `/api/readings/latest`, `/api/history/<node_id>` | Source-filtered registry/latest/history; history currently defaults to 100 rows |
| `POST /api/nodes/<node_id>/session` | Confirmed physical restart begins a new sequence session |
| `GET /api/twin/state`, `/api/twin/assets/<filename>` | Read-only 3D state and whitelisted compressed model/manifest assets |
| `GET /api/alert/status`; `POST /api/alert/trigger`, `/api/alert/reset` | Existing backend emergency state and manual controls |
| `GET/POST /api/ai/insights`; `GET /api/ai/architecture` | Historical summaries and descriptive architecture payload |

### Risk and alert decisions

[ml_model.py](../ml_model.py) trains Isolation Forest models at import on seeded synthetic baseline data (2,000 samples, contamination 0.03). It has full underground, capability-specific IMU, and crack models. Absent environmental measurements select the IMU model rather than fabricated temperature/humidity.

The raw candidate combines anomaly strength with tilt/vibration or displacement and returns conventional LOW/MEDIUM/HIGH thresholds. **That is not the final ingestion decision.** [telemetry.py](../telemetry.py) applies the evidence gates:

- Median preprocessing uses the current tilt/displacement and up to two nearby-in-time prior readings.
- An anomaly needs candidate score at least 40 and raw tilt magnitude at least 0.8°, vibration at least 0.8, or displacement at least 4 mm.
- Persistence needs the current anomaly and two recent preceding anomalies. A single persistent UG-01 still cannot establish a correlated event.
- HIGH requires persistence plus a fresh persistent peer within 500 m and configured geographic locations. CRITICAL additionally needs a worsening tilt/displacement trend, vibration at least 1, tilt magnitude at least 5°, at least four recent prior readings, and persistence in the latest three prior readings.
- Final score caps are 39/69/89/100 for LOW/MEDIUM/HIGH/CRITICAL. Final labels should not be recomputed from the model's original score thresholds in the frontend.

These are prototype heuristics requiring calibration. `accept_telemetry` latches the backend RED_ALERT state when an applied REAL packet is CRITICAL and the selected mode is REAL. Manual trigger/reset remain available. Eight siren entries are software state; the repository does not implement a remote physical siren command/acknowledgment path. Alert state is in memory and resets with the process.

[ai_layer.py](../ai_layer.py) computes on-demand statistics and template-based advice from up to 400 source-filtered processed observations. Despite “AI/LLM” and “asynchronous” labels, there is no external LLM call or background job queue in this implementation. It does not control the emergency path.

## Hardware and transport

The selected deployment is **UG-01 → HOST-01 → USB PC Mother Host**, documented in [HARDWARE.md](../HARDWARE.md). Receiver firmware is in [firmware/host01](../firmware/host01/); a UG transmitter sketch is not tracked here.

| Component/capability | Current status |
| --- | --- |
| UG-01 ESP32 / MPU6050 | Supported roll and pitch in degrees, mapped to tilt_x/tilt_y |
| Vibration sensor (SW-420 in hardware acceptance instructions) | Binary event 0/1; do not label REAL values as calibrated acceleration |
| Capacitive soil sensor | Raw ADC 0–4095; not soil moisture percent |
| LoRa RA-02 / 433 MHz antenna | Hardware discussion; current receiver configuration confirms 433 MHz custom LoRa |
| BME280 | Earlier proposed list included it; later user plan omitted it and current REAL payload has no environmental fields |
| Battery/solar/controller, buzzer, enclosure | Design context; no battery telemetry or verified power autonomy in current payload |
| Physical crack/displacement node | Broader design/proposed expansion; current REAL registry seeds only UG-01 |
| GPS | No on-wire GPS; UG-01 location is null until both coordinate environment settings are supplied |

HOST-01 uses SPI SCK/MISO/MOSI 18/19/23, CS/reset/DIO0 5/14/26, 433 MHz, 17 dBm, SF7, 125 kHz bandwidth, coding rate 4/5, sync word 0x34, CRC enabled. See [config.example.h](../firmware/host01/config.example.h) for source values.

The unchanged radio format is `DATA,UG-01,<sequence>,<roll>,<pitch>,<vibration>,<soil>` with `ACK,UG-01,<sequence>`. HOST-01 adds its identity `HOST-01`, zone `ZONE-A`, and measured RSSI/SNR for PC ingestion.

Two receiver paths are supported:

- Original sketch: the PC parses a complete human-readable block from `RAW: DATA,...` through `[OK] Packet processed.`, including Host ID, Zone ID, RSSI and SNR. Incomplete blocks are discarded. This receiver does not buffer while the PC is absent.
- Optional buffered sketch: a 256-packet RAM FIFO forwards `TELEMETRY {json}` over USB with `queue_age_ms`; the PC sends `STORED,UG-01,<sequence>` after successful processing. The gateway retries its oldest packet every second. LoRa ACK and PC storage confirmation are separate. Full queues drop newest packets; reset/power loss loses RAM contents. No persistent flash buffer or Wi-Fi forwarding is implemented.

The PC also has a bounded 256-observation queue for temporary storage failures. Queue age preserves stale capture age; duplicates never refresh freshness. Older unseen sequences are saved with `processed=0`, not used as current evidence. A device sequence reset needs an operator-confirmed new backend session after old gateway buffers are drained. Default offline timeout is 15 seconds; a USB connection alone never proves a node is online.

## Dashboard and digital twin

[templates/index.html](../templates/index.html) and [static/js/app.js](../static/js/app.js) implement the existing multi-tab control room, map, telemetry charts, history, node inventory, risk views, advisories and health display. The main dashboard polls `/api/live` every two seconds. Leaflet, Chart.js and fonts still have CDN dependencies; offline local ingestion does not imply every browser feature works on a cold offline load.

The digital twin **is present in this repository**:

- [digital_twin.py](../digital_twin.py) derives visualization state from backend readings and alerts. REAL unavailable values and unlocated nodes remain unavailable/unlocated. Geographic clustering uses 500 m connected components; REAL display groups also require persistence.
- [static/js/digital-twin.js](../static/js/digital-twin.js) loads the GLB, instances forest geometry, displays nodes/risk regions/sirens, and provides camera presets/tour, layer controls and an illustrative deformation/crack replay. Recorded demo is disabled in REAL mode. Camera motion and replay do not write emergency state.
- [static/models/terraveil/export-report.json](../static/models/terraveil/export-report.json) records 1,983 static source objects, 9,727 tree instances, 338,650 asset triangles, a 15,507,964-byte GLB and 6,298,052-byte gzip transfer. The replay contains 177 states over 44 seconds. Three.js is vendored locally.
- [scripts/export_twin.py](../scripts/export_twin.py) exports geometry from the existing Blender mine; [scripts/prepare_twin_demo.py](../scripts/prepare_twin_demo.py) consumes its external registry/replay. The recorded Blender source path is `C:\Users\sksja\OneDrive\Desktop\TerraVeil_Digital_Twin\TerraVeil_Digital_Twin.blend`. It is external to this repository; the exporter does not save over it.

The viewer fits demonstration GPS extents to an artistic mine model. Terrain settlement and cracks are presentation estimates, not surveyed displacement or a calibrated geotechnical solver. Crack display gain enlarges geometry without changing telemetry values. Exported materials approximate the Blender source; a web export is not proof of identical photorealistic rendering. This review verified code/assets, not current browser rendering or Blender playback.

## InSAR workflow: external progress and planned integration

The repository now implements an **InSAR 3D Map** at `/?view=insar`: full-grid DEM terrain, signed LOS velocity, coherence, point inspection, exaggeration, camera controls and provenance. `scripts/prepare_insar.py` validates/packages HDF5; `insar.py` serves derived assets through GET-only routes without scientific dependencies at runtime. There is no upload service, placement engine, satellite time-series animation or connection to alert decisions. The related workspace is `C:\Users\sksja\Downloads\jharia`, outside version control here. Its original HDF5 files were read to prepare portable assets in `static/insar/jharia/`, retaining 241,344 terrain and 238,727 valid LOS samples. The original data were unchanged and upstream satellite processing was not rerun. Rates are converted to mm/year; point values retain extremes beyond colour saturation. See [INSAR.md](INSAR.md).

### Existing external workflow and products

`jharia.cfg` selects the HyP3 processor and references clipped `S1*` unwrapped phase/coherence/DEM/look-angle/water-mask products. Its settings specify PyAPS/ERA5 tropospheric correction and MintPy processing options. `inputs/ifgramStack.h5`, `inputs/geometryGeo.h5`, `inputs/ERA5.h5`, time-series products, residual/DEM-error products, and raw/ERA5/corrected velocity files are present. These files indicate processing work; they do not by themselves establish the accuracy of every correction or complete acquisition provenance.

The corresponding technical workflow is HyP3 interferogram/geometry inputs → MintPy network/time-series analysis and corrections → LOS velocity/quality layers → terrain analysis → future placement. MintPy's outputs describe displacement along the radar line of sight; retain that distinction from vertical subsidence. See the [MintPy workflow documentation](https://mintpy.readthedocs.io/en/latest/) and [ASF HyP3 product guide](https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/).

| External input | Read-only observation |
| --- | --- |
| `velocity_corrected.h5`, dataset `velocity` | 419 × 576; UNIT `m/year`; EPSG 32645; metadata interval 2022-01-02 to 2026-09-09; NoData 0.0 |
| `inputs/geometryGeo.h5`, dataset `height` | Matching 419 × 576 grid and georeferencing |
| Grid attributes | X_FIRST 408160, Y_FIRST 2643520, X_STEP +80 m, Y_STEP −80 m |
| `temporalCoherence.h5` | 238,728 finite positive pixels; every positive value is 1.0; no ranking discrimination among these pixels |
| `waterMask.h5` | All 241,344 cells True; no excluded pixels in this supplied mask |
| `plot_insar.py`, `analyze_terrain.py` | Existing external velocity/terrain plotting scripts |
| `jharia_insar_velocity.png`, `jharia_terrain_slope.png` | Existing plot files; their scientific accuracy was not visually validated in this review |

The shared discussion's last visible step supplied a proposed `node_placement.py`. That script, `recommended_nodes.csv`, `node_placement_map.png`, and `placement_scores.npy` were absent from the inspected external directory, and are absent from this repository. Do not describe placement generation as completed.

### Decisions to carry forward

- Placement should derive candidate positions from actual raster data, with a prototype target of **two ground-monitoring nodes and one crack/displacement node**; this is a plan, not the current physical inventory.
- The revised discussion proposal assigns 45% to deformation magnitude, 25% to deformation gradient, 15% to terrain suitability and 15% to coverage. It supersedes the earlier proposal with a 15% coherence weight, because this dataset's positive coherence is uniform.
- Keep coherence as a validity/diagnostic layer for this dataset. Uniform 1.0 is not independent proof of perfect measurement quality. An all-True water mask is not evidence that the site contains no water; verify its provenance/semantics before relying on it for field placement.
- Terrain slope should influence suitability rather than automatically eliminating every steep pixel. Review DEM artifacts and actual site access before placement.
- A proposed 400 m separation is a placement parameter, distinct from the host's 500 m evidence-correlation radius. Neither is a field-calibrated deployment rule.

### Required corrections before adopting the discussion's sample script

The sample's prose promises a 15% coverage term and a 2-ground/1-crack allocation, but its code uses only a 0.85-weight base score plus multiplicative distance suppression and a role heuristic. Suppression does not enforce a hard 400 m minimum, and roles do not enforce the intended quota. The sample writes CSV/PNG but not the earlier promised `.npy` score artifact. Treat it as a draft, not a specification already satisfied.

Before implementation, define the actual coverage objective, enforce any required spacing and node-role quotas, handle exhausted/zero-score candidates, and make outputs reproducible. Validate CRS, aligned grids, units, NoData, mask polarity, finite values and smoothing near invalid cells. Preserve signed LOS velocity and acquisition/reference metadata even if magnitude is used for ranking. Do not silently default an unknown CRS or relabel LOS m/year as crack mm or vertical settlement. Recommendations must remain separate from registered deployed nodes until reviewed.

## Decisions that supersede older descriptions

| Older description | Current source of truth |
| --- | --- |
| Hardware is entirely planned; simulation always starts | USB hardware path exists; saved mode controls simulation; fresh DB defaults REAL |
| LoRaWAN/MQTT, 868/915 MHz, encrypted 12-byte packets | Custom 433 MHz text DATA/ACK and USB; no such encrypted binary protocol implemented |
| `/api/readings/telemetry` | Actual ingestion route is `/api/telemetry` |
| All nodes supply temperature, humidity, battery or displacement | Actual UG-01 capabilities are restricted; missing fields remain unavailable |
| HIGH score alone means emergency | Final evidence gates plus applied REAL CRITICAL event control automatic latch |
| Full LLM service, certified reports, verified sub-2ms latency | Local template/statistical advisory; export mocks and unverified performance claims remain |
| Mine animation proves a surveyed digital twin | Browser model exists, but registration and deformation remain illustrative |
| InSAR placement is in the app | Terrain/LOS web visualization now exists; automatic placement remains future work |

## Current gaps and next work

These are remaining findings and proposed priorities after the map implementation:

1. **Deployment:** the malformed gunicorn/pyserial line was split and requirement syntax checked. `Procfile` uses `gunicorn app:app`; USB startup is not wired into that entry point. A cloud deployment cannot directly access the local PC's COM7. Decide the intended local/cloud boundary before deployment changes.
2. **Hardware acceptance and calibration:** verify physical packet delivery, sensor response, disconnect/reconnect, stale buffered replay, reboot sessions, mounting baselines and calibrated thresholds. Add/register further ground/crack nodes only as real capabilities become available. No physical hardware acceptance was performed in this review.
3. **InSAR provenance and quality:** establish processing versions, acquisition/orbit details, reference conventions, correction quality, uncertainty and the reason for uniform masks. Preserve source data outside the web runtime and define a portable dataset manifest/import contract.
4. **Placement engine:** implement and test the corrected ranking/coverage/quota/masking behavior, then review resulting coordinates against terrain, access and field constraints. Keep the external scripts separate until an intentional repository integration is requested.
5. **Further GIS/3D integration:** the read-only InSAR map now has units, dates, provenance and velocity fit standard deviation. Candidate recommendations remain unimplemented. Establish surveyed coordinate registration before displacing the existing model from raster measurements. Preserve existing camera controls and source-mode boundaries.
6. **Prototype completion:** CSV/PDF export and settings-save handlers currently show success alerts without implementing the claimed exports/settings persistence. Historical range UI does not change the backend's default 100-row query. Treat DGMS certification language and performance numbers as unverified, not completed functionality.
7. **Operational hardening:** define authentication/authorization for mutation routes, durable/audited alert state, a physical siren protocol if required, backup/retention, offline frontend assets, and bounded processing/deployment behavior. None should be inferred from current dashboard labels.

## Development and validation notes

Use [AGENTS.md](../AGENTS.md) for the concise working guide and [HARDWARE.md](../HARDWARE.md) for setup and hardware acceptance. Relevant configuration names are `TERRAVEIL_DB`, `TERRAVEIL_MODE`, `TERRAVEIL_SERIAL_ENABLED`, `TERRAVEIL_SERIAL_PORT`, `TERRAVEIL_SERIAL_BAUD`, `NODE_TIMEOUT_SECONDS`, `UG01_LATITUDE`, and `UG01_LONGITUDE`. No Wi-Fi credentials are needed for the selected USB path.

Existing tests cover telemetry validation/source isolation, deduplication/concurrency, queue age/offline expiry, serial framing/retry/storage confirmation, restart sessions/process locking, and twin API/assets/replay. Test files were inspected, but the suite was not executed for this documentation-only task. Set a disposable `TERRAVEIL_DB` before importing the app to prevent import-time migrations touching the working database; some test fixtures patch it only after import.

Validation for this review is limited to source/config/test inspection, linked-discussion review, external file/metadata inspection, and documentation link/diff checks. No dependency installation, server restart, firmware upload, model export, satellite processing, live mode change, or source repair was performed.


### InSAR implementation validation update

52 Python tests passed against a disposable database, including 10 new checks covering masks, units, CRS/grid alignment, exact asset integrity and GET-only routes. JavaScript syntax checks passed. Browser checks covered all three layers, camera/relief/shading controls, mouse/grid selection, exact extreme and NoData values, a 390 px viewport, REAL/SIMULATION transitions in an isolated preview, and the existing digital twin. No browser console errors were observed. The original HDF5 data and working telemetry database were not used as test fixtures. See [INSAR.md](INSAR.md) for setup and contract details.

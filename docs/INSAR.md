# InSAR 3D map

Open **InSAR 3D Map** in the sidebar, or `http://localhost:5000/?view=insar` after starting the app normally. The tab works in both REAL and SIMULATION modes and shows a fixed historical satellite dataset, independent of live telemetry and the artistic Blender mine scene.

## Implemented

- Georeferenced 576 × 419 DEM grid at 80 m spacing, EPSG:32645.
- Signed historical LOS velocity (2022-01-02 through 2026-09-09), terrain elevation, and temporal-coherence colour layers.
- Orbit, pan, zoom, top/3D camera views, optional terrain shading and 1–20× vertical exaggeration.
- Click-to-inspect or zero-based row/column selection: sample velocity, supplied fit standard deviation, height, coherence, projected coordinates, and WGS84 latitude/longitude.
- Visible quality notes, observation/reference dates, source hashes and manifest.
- Full-resolution portable assets: 6.76 MB binary, about 4.07 MB gzip. No runtime dependency on the external Downloads folder or scientific Python libraries.

Map colours saturate at approximately ±53.15 mm/year (the 98th percentile of absolute valid velocity). The inspector retains the full range, approximately −238.91 to +59.88 mm/year. Disable terrain shading when comparing colours directly to the legend.

Surface height comes from the DEM, not velocity-driven displacement. Exaggeration changes display geometry only. LOS rates are not vertical settlement, crack opening, or live risk. Fit standard deviation is not total measurement uncertainty. Automatic sensor placement, deployed-node registration, and time-series replay are not part of this implementation.

## Rebuild from original data

```powershell
python -m pip install -r requirements-insar.txt
python scripts/prepare_insar.py 'C:\Users\sksja\Downloads\jharia'
```

Required inputs:

| File | Dataset |
| --- | --- |
| `velocity_corrected.h5` | `velocity`, optionally `velocityStd` |
| `inputs/geometryGeo.h5` | `height` |
| `temporalCoherence.h5` | `temporalCoherence` |
| `waterMask.h5` | `waterMask` |

The script reads the originals, checks matching shapes/georeferencing, requires a projected CRS with metre axes and a north-up grid, validates m/year or mm/year velocity units, and transforms coordinates with pyproj. It writes `static/insar/jharia/manifest.json`, a content-hashed binary, and gzip copy. `--output <directory>` allows staging elsewhere. The manifest is replaced last; old hashed assets are not deleted automatically. Reload the browser after rebuilding. Only the derived assets accompany a deployment.

This packages existing products; it does not rerun HyP3/MintPy or establish upstream correction accuracy. The importer expects this explicit product layout, not arbitrary uploaded rasters.

## Contract and masks

`GET /api/insar/manifest.json` contains version 1, grid metadata, period, statistics, masking rules, source hashes, warnings and an asset descriptor. `GET /api/insar/terrain-<16 hex chars>.bin` serves the supported asset filename pattern with gzip negotiation. There are no POST routes or telemetry/database imports in this feature.

Seven little-endian float32 channels per sample, row-major:

```text
height_m, los_mm_year, coherence, longitude, latitude, velocity_std_mm_year, flags
```

`flags` is an integer bitfield stored as float32: terrain=1, valid LOS=2, coherence=4, usable land=8, valid fit standard deviation=16. Zero placeholders in invalid channels are never valid measurements; consumers must check flags. Height/velocity use each source's declared NoData. LOS additionally requires finite terrain, positive coherence no greater than one, and waterMask=1. Uniform all-False water masks exclude everything and cause failure. NoData is not universally assumed zero when metadata is absent.

The current velocity product declares zero as NoData. Its 238,727 valid LOS samples differ from the 238,728 positive coherence samples. Positive coherence is uniformly 1.0, and waterMask is entirely True: neither supplies spatial quality discrimination. The UI explains this. Valid DEM remains visible where LOS is missing; the missing layer is grey. Invalid DEM vertices do not form surface triangles.

Sample coordinates follow `FIRST + index × STEP`, matching the source plotting workflow; no extra half-pixel offset is added. Geographic positions derive from EPSG metadata, not the misleadingly named projected `REF_LAT`/`REF_LON` attributes. Geographic coordinates are float32, shown to five decimals; projected grid positions remain exact from the manifest.

## Verification

Install `requirements-insar.txt` before the full test suite. Set a disposable `TERRAVEIL_DB` before importing Flask, as described in [AGENTS.md](../AGENTS.md), and disable serial for testing. Never test mutation routes on the working host.

```text
python -m unittest discover -s tests -v
node --check static/js/insar.js
```

Implementation checks: 52 Python tests passed; browser layers, camera/relief/shading controls, pointer/grid inspection, a 390 px viewport, and mode transitions were checked on an isolated database. Point values matched source typical/minimum/NoData samples. Existing twin tests emit two pre-existing unclosed-file ResourceWarnings but pass. Preparation does not modify source HDF5 files.

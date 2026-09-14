"""Build a portable, full-resolution 3D map from aligned MintPy HDF5 products.

Reads source files only. Flask serves the derived assets without h5py/pyproj.
"""
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
from pyproj import CRS, Transformer

GRID_KEYS = ('X_FIRST', 'Y_FIRST', 'X_STEP', 'Y_STEP', 'EPSG')


def read_layer(path, key):
    with h5py.File(path, 'r') as handle:
        data = handle[key][:].astype(np.float64)
        attrs = {k: v.decode() if isinstance(v, bytes) else str(v) for k, v in handle.attrs.items()}
        uncertainty = handle['velocityStd'][:].astype(np.float64) if key == 'velocity' and 'velocityStd' in handle else None
    if data.ndim != 2 or min(data.shape) < 2:
        raise ValueError(f'{key}: expected a 2D grid with at least two rows and columns')
    for k in GRID_KEYS:
        if k not in attrs or not np.isfinite(float(attrs[k])):
            raise ValueError(f'{key}: missing/invalid {k}')
    return data, attrs, uncertainty


def valid_values(data, attrs):
    valid = np.isfinite(data)
    if 'NO_DATA_VALUE' in attrs:
        valid &= data != float(attrs['NO_DATA_VALUE'])
    return valid


def prepare(source, output):
    source, output = Path(source), Path(output)
    specs = [('velocity_corrected.h5', 'velocity'), ('inputs/geometryGeo.h5', 'height'),
             ('temporalCoherence.h5', 'temporalCoherence'), ('waterMask.h5', 'waterMask')]
    layers = [read_layer(source / name, key) for name, key in specs]
    velocity, attrs, std = layers[0]
    height, height_attrs, _ = layers[1]
    coherence, _, _ = layers[2]
    water, _, _ = layers[3]
    rows, cols = velocity.shape
    for (data, metadata, _), (_, key) in zip(layers, specs):
        if data.shape != velocity.shape or any(not np.isclose(float(metadata[k]), float(attrs[k]), rtol=0, atol=1e-7) for k in GRID_KEYS):
            raise ValueError(f'{key}: grid/CRS is not aligned with velocity')
    crs = CRS.from_epsg(int(attrs['EPSG']))
    if not crs.is_projected or any(axis.unit_name != 'metre' for axis in crs.axis_info[:2]):
        raise ValueError('A projected CRS in metres is required; reproject geographic grids first')
    x0, y0, dx, dy = [float(attrs[k]) for k in GRID_KEYS[:4]]
    if dx <= 0 or dy >= 0:
        raise ValueError('Expected north-up grid: X_STEP > 0 and Y_STEP < 0')
    if attrs.get('UNIT') not in ('m/year', 'mm/year'):
        raise ValueError('Unknown velocity unit; expected m/year or mm/year')
    if not np.all(np.isfinite(water)) or not set(np.unique(water)).issubset({0, 1}):
        raise ValueError('waterMask must contain only boolean/0/1 values (1 = usable land)')
    terrain_valid = valid_values(height, height_attrs)
    coh_valid = np.isfinite(coherence) & (coherence > 0) & (coherence <= 1)
    velocity_valid = valid_values(velocity, attrs) & terrain_valid & coh_valid & (water == 1)
    if not terrain_valid.any() or not velocity_valid.any():
        raise ValueError('No valid terrain or LOS velocity samples after masking')
    scale = 1000 if attrs['UNIT'] == 'm/year' else 1
    velocity *= scale
    if std is not None:
        if std.shape != velocity.shape:
            raise ValueError('velocityStd shape mismatch')
        std *= scale
        std_valid = np.isfinite(std) & (std >= 0) & velocity_valid
    else:
        std = np.zeros_like(velocity)
        std_valid = np.zeros_like(velocity_valid)
    rr, cc = np.indices(velocity.shape)
    east, north = x0 + cc * dx, y0 + rr * dy
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(east, north)
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        raise ValueError('Coordinate transform failed')
    # Bit flags: terrain=1, valid LOS=2, coherence=4, usable land=8, fit std=16.
    flags = terrain_valid.astype(np.uint8) | (velocity_valid.astype(np.uint8) << 1) | (coh_valid.astype(np.uint8) << 2) | ((water == 1).astype(np.uint8) << 3) | (std_valid.astype(np.uint8) << 4)
    packed = np.stack([np.where(terrain_valid, height, 0), np.where(velocity_valid, velocity, 0),
                       np.where(coh_valid, coherence, 0), lon, lat, np.where(std_valid, std, 0), flags], axis=-1).astype('<f4')
    raw = packed.tobytes()
    digest = hashlib.sha256(raw).hexdigest()
    filename = f'terrain-{digest[:16]}.bin'
    values = velocity[velocity_valid]
    limit = max(float(np.percentile(np.abs(values), 98)), 0.001)
    positive = coherence[coh_valid]
    warnings = ['LOS velocity is a historical radar-direction rate, not vertical settlement or a live alarm.']
    if np.ptp(positive) < 1e-7:
        warnings.append('Positive temporal coherence is uniform; it cannot distinguish more reliable locations.')
    if np.all(water == 1):
        warnings.append('The supplied water mask is all land/usable. Water exclusion is uninformative; verify mask provenance.')
    manifest = dict(version=1, title='Jharia coalfield', generated_at=datetime.now(timezone.utc).isoformat(),
        grid=dict(rows=rows, columns=cols, x_first=x0, y_first=y0, x_step=dx, y_step=dy, epsg=crs.to_epsg(),
                  coordinate_convention='Sample coordinate = FIRST + index × STEP; no additional half-pixel offset.',
                  bounds_wgs84=[float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]),
        period=dict(start=attrs.get('START_DATE'), end=attrs.get('END_DATE'), reference_date=attrs.get('REF_DATE')),
        reference=dict(row=attrs.get('REF_Y'), column=attrs.get('REF_X'),
                       note='Source REF_LAT/REF_LON are not assumed to be geographic; use the grid and reference row/column.'),
        units=dict(height='m', velocity='mm/year', velocity_source=attrs['UNIT'], velocity_std='mm/year'),
        asset=dict(filename=filename, bytes=len(raw), sha256=digest, format='little-endian float32, row-major, interleaved',
                   channels=['height_m', 'los_mm_year', 'coherence', 'longitude', 'latitude', 'velocity_std_mm_year', 'flags'],
                   flags=dict(terrain=1, velocity=2, coherence=4, land=8, velocity_std=16)),
        stats=dict(total_samples=rows*cols, terrain_samples=int(terrain_valid.sum()), velocity_samples=int(velocity_valid.sum()),
                   elevation_min=float(height[terrain_valid].min()), elevation_max=float(height[terrain_valid].max()),
                   velocity_min=float(values.min()), velocity_max=float(values.max()), velocity_median=float(np.median(values)),
                   color_limit_mm_year=limit, coherence_min=float(positive.min()), coherence_max=float(positive.max())),
        masking=dict(velocity_nodata=attrs.get('NO_DATA_VALUE'), water_semantics='1 = usable land, 0 = excluded',
                     velocity_rule='Finite non-NoData velocity and terrain, positive coherence <= 1, usable waterMask'),
        sources=[dict(file=name, dataset=key, sha256=hashlib.sha256((source/name).read_bytes()).hexdigest()) for name, key in specs],
        warnings=warnings)
    output.mkdir(parents=True, exist_ok=True)
    (output / filename).write_bytes(raw)
    (output / (filename+'.gz')).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    # Publish manifest last so readers never see an incomplete referenced asset.
    temp = output / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(output / 'manifest.json')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'static/insar/jharia')
    args = parser.parse_args()
    result = prepare(args.source, args.output)
    print(json.dumps(dict(asset=result['asset'], stats=result['stats']), indent=2))

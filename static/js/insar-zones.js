/**
 * TerraVeil — Jharia InSAR Subsidence Footprints GIS Layer
 * 
 * Renders 27 detected InSAR subsidence hotspot zones (Polygon & MultiPolygon)
 * on the existing Leaflet map, with 4-tier relative severity styling,
 * interactive popups, boundary hover highlights, collapsible legend,
 * and a linked, sortable CSV summary table with bidirectional selection.
 */

(function () {
    'use strict';

    // =========================================================================
    // 1. CONFIGURATION & SEVERITY CLASSIFICATION
    // =========================================================================
    const INSAR_SEVERITY_CONFIG = {
        DISCLAIMER: 'InSAR-derived relative subsidence severity classification (satellite line-of-sight velocity) — not a geotechnical safety certification.',
        THRESHOLDS: [
            {
                key: 'CRITICAL',
                label: 'Critical',
                rank: 4,
                minVelocity: -Infinity,
                maxVelocity: -85.0,
                color: '#DC2626',
                borderColor: '#EF4444',
                fillColor: 'rgba(220, 38, 38, 0.40)',
                badgeClass: 'badge-critical',
                textClass: 'cell-crit',
                highlightClass: 'highlight-critical',
                swatchClass: 'swatch-critical',
                description: 'Extreme subsidence hotspot (≤ -85 mm/yr)'
            },
            {
                key: 'SEVERE',
                label: 'Severe',
                rank: 3,
                minVelocity: -85.0,
                maxVelocity: -80.0,
                color: '#EA580C',
                borderColor: '#F97316',
                fillColor: 'rgba(234, 88, 12, 0.35)',
                badgeClass: 'badge-severe',
                textClass: 'cell-sev',
                highlightClass: 'highlight-severe',
                swatchClass: 'swatch-severe',
                description: 'Severe subsidence hotspot (-85 to -80 mm/yr)'
            },
            {
                key: 'HIGH',
                label: 'High',
                rank: 2,
                minVelocity: -80.0,
                maxVelocity: -75.0,
                color: '#D97706',
                borderColor: '#F59E0B',
                fillColor: 'rgba(217, 119, 6, 0.30)',
                badgeClass: 'badge-high',
                textClass: 'cell-hi',
                highlightClass: 'highlight-high',
                swatchClass: 'swatch-high',
                description: 'High subsidence hotspot (-80 to -75 mm/yr)'
            },
            {
                key: 'ELEVATED',
                label: 'Elevated',
                rank: 1,
                minVelocity: -75.0,
                maxVelocity: Infinity,
                color: '#3B82F6',
                borderColor: '#60A5FA',
                fillColor: 'rgba(59, 130, 246, 0.30)',
                badgeClass: 'badge-elevated',
                textClass: 'cell-elev',
                highlightClass: 'highlight-elevated',
                swatchClass: 'swatch-elevated',
                description: 'Elevated subsidence hotspot (> -75 mm/yr)'
            }
        ]
    };

    function getInsarSeverity(medianVelocity) {
        if (!Number.isFinite(medianVelocity)) {
            return INSAR_SEVERITY_CONFIG.THRESHOLDS[3];
        }
        for (const t of INSAR_SEVERITY_CONFIG.THRESHOLDS) {
            if (medianVelocity <= t.maxVelocity && medianVelocity > t.minVelocity) {
                return t;
            }
        }
        return INSAR_SEVERITY_CONFIG.THRESHOLDS[3];
    }

    function fmt(val, digits = 1) {
        return Number.isFinite(val) ? val.toFixed(digits) : 'UNAVAILABLE';
    }

    // =========================================================================
    // 2. CSV PARSER
    // =========================================================================
    function parseCSV(text) {
        if (!text || typeof text !== 'string') return [];
        const lines = text.trim().split(/\r?\n/);
        if (lines.length < 2) return [];

        const headers = lines[0].split(',').map(h => h.trim());
        const records = [];

        for (let i = 1; i < lines.length; i++) {
            const line = lines[i].trim();
            if (!line) continue;
            const values = line.split(',');
            if (values.length < headers.length) continue;

            const row = {};
            headers.forEach((h, idx) => {
                const rawVal = values[idx] ? values[idx].trim() : '';
                const numVal = Number(rawVal);
                row[h] = isNaN(numVal) || rawVal === '' ? rawVal : numVal;
            });
            records.push(row);
        }
        return records;
    }

    // =========================================================================
    // 3. STATE & LAYER VARIABLES
    // =========================================================================
    let mapInstance = null;
    let geojsonData = null;
    let csvRecords = [];
    let insarLayerGroup = null;
    let centroidMarkersGroup = null;
    let legendControl = null;
    let isLayerVisible = true;
    let selectedZoneId = null;
    let currentSortField = 'median_velocity_mm_year';
    let currentSortAsc = true; // For negative velocity: more negative = most severe

    const zoneLayersById = new Map();
    const zonePropertiesById = new Map();

    // =========================================================================
    // 4. DATA LOADING & VALIDATION
    // =========================================================================
    async function loadInSARData() {
        try {
            const [geoResponse, csvResponse] = await Promise.all([
                fetch('/map/jharia_subsidence_zones.geojson'),
                fetch('/map/jharia_subsidence_zones.csv')
            ]);

            if (!geoResponse.ok) throw new Error(`GeoJSON fetch failed (${geoResponse.status})`);
            if (!csvResponse.ok) throw new Error(`CSV fetch failed (${csvResponse.status})`);

            geojsonData = await geoResponse.json();
            const csvText = await csvResponse.text();
            csvRecords = parseCSV(csvText);

            validateData(geojsonData, csvRecords);

            return { geojsonData, csvRecords };
        } catch (err) {
            console.error('[TerraVeil InSAR] Failed to load subsidence data:', err);
            return null;
        }
    }

    function validateData(geojson, csv) {
        const warnings = [];

        if (!geojson || !Array.isArray(geojson.features)) {
            warnings.push('GeoJSON features array is missing or invalid.');
            console.warn('[TerraVeil InSAR Validation]', warnings);
            return;
        }

        if (geojson.features.length !== 27) {
            warnings.push(`Expected exactly 27 GeoJSON features, found ${geojson.features.length}`);
        }

        const geoIds = new Set();
        geojson.features.forEach((f, idx) => {
            const zid = f.properties?.zone_id;
            if (zid == null) warnings.push(`Feature #${idx} is missing zone_id.`);
            if (geoIds.has(zid)) warnings.push(`Duplicate zone_id ${zid} in GeoJSON.`);
            geoIds.add(zid);

            const gType = f.geometry?.type;
            if (gType !== 'Polygon' && gType !== 'MultiPolygon') {
                warnings.push(`Feature #${zid} has unexpected geometry: ${gType}`);
            }

            const cLat = f.properties?.centroid_latitude;
            const cLon = f.properties?.centroid_longitude;
            if (cLat < 23.5 || cLat > 24.0 || cLon < 86.0 || cLon > 87.0) {
                warnings.push(`Feature #${zid} centroid [${cLat}, ${cLon}] outside Jharia region.`);
            }
        });

        if (csv.length !== 27) {
            warnings.push(`Expected 27 CSV rows, found ${csv.length}`);
        }

        const csvIds = new Set(csv.map(r => r.zone_id));
        geoIds.forEach(id => {
            if (!csvIds.has(id)) warnings.push(`GeoJSON zone_id ${id} not found in CSV.`);
        });

        if (warnings.length > 0) {
            console.warn('[TerraVeil InSAR Validation Warnings]', warnings);
        }
    }

    // =========================================================================
    // 5. POPUP CONTENT BUILDER
    // =========================================================================
    function createPopupContent(props) {
        const severity = getInsarSeverity(props.median_velocity_mm_year);
        const recs = window.NodePlacementEngine?.getRecommendations?.() || [];
        const zoneAnchors = recs.filter(r => r.zone_id === props.zone_id);
        const rankedZones = window.NodePlacementEngine?.getRankedZones?.() || [];
        const zoneRankObj = rankedZones.find(z => z.zone_id === props.zone_id);

        return `
            <div class="insar-popup">
                <div class="insar-popup-header">
                    <div class="insar-popup-title-group">
                        <span class="insar-popup-eyebrow">SATELLITE INSAR HOTSPOT</span>
                        <h4 class="insar-popup-title">Subsidence Zone ${props.zone_id}</h4>
                    </div>
                    <span class="insar-badge ${severity.badgeClass}">${severity.label}</span>
                </div>
                
                <div class="insar-popup-body">
                    <!-- Subsidence Velocities -->
                    <div class="insar-popup-section">
                        <span class="insar-popup-section-title">Deformation Velocity (LOS)</span>
                        <div class="insar-popup-grid">
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Median:</span>
                                <span class="insar-metric-val ${severity.highlightClass}">
                                    ${fmt(props.median_velocity_mm_year, 1)} mm/yr
                                </span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Worst:</span>
                                <span class="insar-metric-val highlight-critical">
                                    ${fmt(props.worst_velocity_mm_year, 1)} mm/yr
                                </span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Mean:</span>
                                <span class="insar-metric-val">
                                    ${fmt(props.mean_velocity_mm_year, 1)} mm/yr
                                </span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Uncertainty:</span>
                                <span class="insar-metric-val">
                                    ±${fmt(props.median_velocity_std_mm_year, 2)} mm/yr
                                </span>
                            </div>
                        </div>
                    </div>

                    <!-- Net Displacements -->
                    <div class="insar-popup-section">
                        <span class="insar-popup-section-title">Cumulative Displacement</span>
                        <div class="insar-popup-grid">
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Median Net:</span>
                                <span class="insar-metric-val">
                                    ${fmt(props.median_net_displacement_mm, 1)} mm
                                </span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Worst Net:</span>
                                <span class="insar-metric-val highlight-critical">
                                    ${fmt(props.worst_net_displacement_mm, 1)} mm
                                </span>
                            </div>
                        </div>
                    </div>

                    <!-- Footprint & Reliability -->
                    <div class="insar-popup-section">
                        <span class="insar-popup-section-title">Footprint & Sensor Metrics</span>
                        <div class="insar-popup-grid">
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Area:</span>
                                <span class="insar-metric-val">${fmt(props.area_km2, 4)} km²</span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Pixel Count:</span>
                                <span class="insar-metric-val">${props.pixel_count} px</span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Coherence:</span>
                                <span class="insar-metric-val">${fmt(props.median_temporal_coherence, 2)}</span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Interferograms:</span>
                                <span class="insar-metric-val">${props.median_num_interferograms || '—'}</span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Confidence:</span>
                                <span class="insar-metric-val" style="text-transform: capitalize;">${(props.confidence || '').replace('_', ' ')}</span>
                            </div>
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Centroid:</span>
                                <span class="insar-metric-val" style="font-size: 9px;">${fmt(props.centroid_latitude, 5)}, ${fmt(props.centroid_longitude, 5)}</span>
                            </div>
                            ${zoneRankObj ? `
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Priority Rank:</span>
                                <span class="insar-metric-val">#${zoneRankObj.priority_rank} (${(zoneRankObj.priority_score * 100).toFixed(0)}%)</span>
                            </div>` : ''}
                            <div class="insar-metric-item">
                                <span class="insar-metric-label">Rec. Anchors:</span>
                                <span class="insar-metric-val" style="color: ${zoneAnchors.length > 0 ? '#06B6D4' : 'var(--text-muted)'};">${zoneAnchors.length} anchor(s)</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="insar-popup-footer">
                    <span class="insar-popup-disclaimer">${INSAR_SEVERITY_CONFIG.DISCLAIMER}</span>
                    <button type="button" class="insar-popup-btn" onclick="window.InSARZones.zoomToZone(${props.zone_id})">
                        Zoom To Zone
                    </button>
                </div>
            </div>
        `;
    }

    // =========================================================================
    // 6. MAP RENDERING & INTERACTION
    // =========================================================================
    function renderGeoJsonLayer() {
        if (!mapInstance || !geojsonData) return;

        zoneLayersById.clear();
        zonePropertiesById.clear();

        if (insarLayerGroup) {
            mapInstance.removeLayer(insarLayerGroup);
        }
        if (centroidMarkersGroup) {
            mapInstance.removeLayer(centroidMarkersGroup);
        }

        insarLayerGroup = L.featureGroup();
        centroidMarkersGroup = L.layerGroup();

        const geojsonLayer = L.geoJSON(geojsonData, {
            style: function (feature) {
                const props = feature.properties || {};
                const severity = getInsarSeverity(props.median_velocity_mm_year);

                return {
                    color: severity.borderColor,
                    weight: 1.5,
                    opacity: 0.9,
                    fillColor: severity.color,
                    fillOpacity: 0.35,
                    fillRule: 'evenodd'
                };
            },
            onEachFeature: function (feature, layer) {
                const props = feature.properties || {};
                const zid = props.zone_id;

                zoneLayersById.set(zid, layer);
                zonePropertiesById.set(zid, props);

                const severity = getInsarSeverity(props.median_velocity_mm_year);

                // Hover Tooltip
                layer.bindTooltip(
                    `<strong>Zone ${zid}</strong> • ${severity.label} • ${fmt(props.median_velocity_mm_year, 1)} mm/yr`,
                    {
                        className: 'boundary-tooltip',
                        sticky: true,
                        direction: 'top',
                        offset: [0, -10]
                    }
                );

                // Hover Highlight
                layer.on('mouseover', function () {
                    if (selectedZoneId !== zid) {
                        layer.setStyle({
                            weight: 3,
                            color: '#FFFFFF',
                            fillOpacity: 0.55
                        });
                        layer.bringToFront();
                    }
                });

                layer.on('mouseout', function () {
                    if (selectedZoneId !== zid) {
                        geojsonLayer.resetStyle(layer);
                    }
                });

                // Click to inspect
                layer.on('click', function (e) {
                    L.DomEvent.stopPropagation(e);
                    selectZone(zid, true);
                });

                // Attach rich popup
                layer.bindPopup(() => createPopupContent(props), {
                    maxWidth: 340,
                    minWidth: 280,
                    className: 'insar-leaflet-popup'
                });

                // Optional centroid marker (subtle)
                if (Number.isFinite(props.centroid_latitude) && Number.isFinite(props.centroid_longitude)) {
                    const cMarker = L.circleMarker([props.centroid_latitude, props.centroid_longitude], {
                        radius: 3,
                        color: '#FFFFFF',
                        weight: 1,
                        fillColor: severity.color,
                        fillOpacity: 0.9
                    });
                    cMarker.bindTooltip(`Zone ${zid} Centroid`, {
                        className: 'boundary-tooltip',
                        direction: 'top'
                    });
                    cMarker.on('click', (e) => {
                        L.DomEvent.stopPropagation(e);
                        selectZone(zid, true);
                    });
                    centroidMarkersGroup.addLayer(cMarker);
                }
            }
        });

        insarLayerGroup.addLayer(geojsonLayer);
        insarLayerGroup.addLayer(centroidMarkersGroup);

        if (isLayerVisible) {
            insarLayerGroup.addTo(mapInstance);
        }
    }

    // =========================================================================
    // 7. COLLAPSIBLE MAP LEGEND CONTROL
    // =========================================================================
    function createLegendControl() {
        if (!mapInstance || legendControl) return;

        legendControl = L.control({ position: 'bottomleft' });

        legendControl.onAdd = function () {
            const div = L.DomUtil.create('div', 'insar-map-legend');
            div.id = 'insar-map-legend-control';

            div.innerHTML = `
                <div class="legend-header" id="legend-toggle-header" title="Click to collapse/expand legend">
                    <span>InSAR Subsidence</span>
                    <button type="button" class="legend-toggle-btn" id="btn-legend-collapse">−</button>
                </div>
                <div class="legend-content" id="legend-body">
                    <span class="legend-unit">LOS Velocity (mm/year)</span>
                    <div class="legend-row">
                        <div class="legend-row-left">
                            <span class="legend-swatch swatch-critical"></span>
                            <strong>Critical</strong>
                        </div>
                        <span class="legend-val">&le; -85.0</span>
                    </div>
                    <div class="legend-row">
                        <div class="legend-row-left">
                            <span class="legend-swatch swatch-severe"></span>
                            <strong>Severe</strong>
                        </div>
                        <span class="legend-val">-85 to -80</span>
                    </div>
                    <div class="legend-row">
                        <div class="legend-row-left">
                            <span class="legend-swatch swatch-high"></span>
                            <strong>High</strong>
                        </div>
                        <span class="legend-val">-80 to -75</span>
                    </div>
                    <div class="legend-row">
                        <div class="legend-row-left">
                            <span class="legend-swatch swatch-elevated"></span>
                            <strong>Elevated</strong>
                        </div>
                        <span class="legend-val">&gt; -75.0</span>
                    </div>
                    <div class="legend-disclaimer">
                        ${INSAR_SEVERITY_CONFIG.DISCLAIMER}
                    </div>
                </div>
            `;

            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);

            const toggleHeader = div.querySelector('#legend-toggle-header');
            const collapseBtn = div.querySelector('#btn-legend-collapse');
            if (toggleHeader) {
                toggleHeader.addEventListener('click', (e) => {
                    e.preventDefault();
                    div.classList.toggle('collapsed');
                    if (collapseBtn) {
                        collapseBtn.textContent = div.classList.contains('collapsed') ? '+' : '−';
                    }
                });
            }

            return div;
        };

        if (isLayerVisible) {
            legendControl.addTo(mapInstance);
        }
    }

    // =========================================================================
    // 8. LINKED CSV SUMMARY TABLE & SORTING
    // =========================================================================
    function renderSummaryTable() {
        const drawer = document.getElementById('insar-zones-drawer');
        if (!drawer) return;

        // Compute summary metrics
        const totalZones = csvRecords.length;
        const totalArea = csvRecords.reduce((acc, r) => acc + (r.area_km2 || 0), 0);
        const criticalCount = csvRecords.filter(r => r.median_velocity_mm_year <= -85.0).length;
        const severeCount = csvRecords.filter(r => r.median_velocity_mm_year > -85.0 && r.median_velocity_mm_year <= -80.0).length;

        // Sort data copy
        const sortedRecords = [...csvRecords].sort((a, b) => {
            let valA = a[currentSortField];
            let valB = b[currentSortField];

            if (currentSortField === 'severity') {
                valA = getInsarSeverity(a.median_velocity_mm_year).rank;
                valB = getInsarSeverity(b.median_velocity_mm_year).rank;
            }

            if (typeof valA === 'string') {
                return currentSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
            }
            return currentSortAsc ? valA - valB : valB - valA;
        });

        let rowsHtml = '';
        sortedRecords.forEach(row => {
            const zid = row.zone_id;
            const severity = getInsarSeverity(row.median_velocity_mm_year);
            const isSelected = selectedZoneId === zid ? 'insar-row-selected' : '';

            rowsHtml += `
                <tr id="insar-row-${zid}" class="${isSelected}" onclick="window.InSARZones.selectZone(${zid}, true)">
                    <td class="cell-mono"><strong>Zone ${zid}</strong></td>
                    <td><span class="insar-badge ${severity.badgeClass}">${severity.label}</span></td>
                    <td class="cell-mono">${fmt(row.area_km2, 4)} km²</td>
                    <td class="cell-mono ${severity.textClass}">${fmt(row.median_velocity_mm_year, 1)} mm/yr</td>
                    <td class="cell-mono cell-crit">${fmt(row.worst_velocity_mm_year, 1)} mm/yr</td>
                    <td class="cell-mono">${fmt(row.median_net_displacement_mm, 1)} mm</td>
                    <td style="text-transform: capitalize;">${(row.confidence || '').replace('_', ' ')}</td>
                </tr>
            `;
        });

        const sortArrow = (field) => {
            if (currentSortField !== field) return '<span class="sort-arrow">⇅</span>';
            return currentSortAsc ? '<span class="sort-arrow">▲</span>' : '<span class="sort-arrow">▼</span>';
        };

        drawer.innerHTML = `
            <div class="drawer-header">
                <div class="drawer-title-group">
                    <span class="drawer-title">JHARIA INSAR SUBSIDENCE FOOTPRINTS</span>
                    <div class="drawer-stats-pills">
                        <span class="drawer-pill">${totalZones} Zones</span>
                        <span class="drawer-pill">${fmt(totalArea, 2)} km² Area</span>
                        <span class="drawer-pill pill-crit">${criticalCount} Critical</span>
                        <span class="drawer-pill">${severeCount} Severe</span>
                    </div>
                </div>
                <button type="button" class="drawer-close-btn" id="btn-close-insar-drawer" title="Close table drawer">✕</button>
            </div>

            <div class="insar-table-wrapper">
                <table class="insar-table">
                    <thead>
                        <tr>
                            <th onclick="window.InSARZones.sortTable('zone_id')">Zone ID ${sortArrow('zone_id')}</th>
                            <th onclick="window.InSARZones.sortTable('severity')">Severity ${sortArrow('severity')}</th>
                            <th onclick="window.InSARZones.sortTable('area_km2')">Area (km²) ${sortArrow('area_km2')}</th>
                            <th onclick="window.InSARZones.sortTable('median_velocity_mm_year')">Median Vel ${sortArrow('median_velocity_mm_year')}</th>
                            <th onclick="window.InSARZones.sortTable('worst_velocity_mm_year')">Worst Vel ${sortArrow('worst_velocity_mm_year')}</th>
                            <th onclick="window.InSARZones.sortTable('median_net_displacement_mm')">Net Disp ${sortArrow('median_net_displacement_mm')}</th>
                            <th onclick="window.InSARZones.sortTable('confidence')">Confidence ${sortArrow('confidence')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rowsHtml}
                    </tbody>
                </table>
            </div>

            <div class="insar-table-footer">
                <span>Click any row to fly to hotspot polygon. Values derived from Jharia InSAR time-series.</span>
                <span>${INSAR_SEVERITY_CONFIG.DISCLAIMER}</span>
            </div>
        `;

        const closeBtn = drawer.querySelector('#btn-close-insar-drawer');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                drawer.classList.add('collapsed');
                const toggleBtn = document.getElementById('btn-toggle-insar-table');
                if (toggleBtn) toggleBtn.classList.remove('active');
            });
        }
    }

    function sortTable(field) {
        if (currentSortField === field) {
            currentSortAsc = !currentSortAsc;
        } else {
            currentSortField = field;
            // Default to ascending for negative velocities (most severe first)
            currentSortAsc = field === 'median_velocity_mm_year' || field === 'worst_velocity_mm_year' || field === 'median_net_displacement_mm';
        }
        renderSummaryTable();
    }

    // =========================================================================
    // 9. BIDIRECTIONAL SELECTION & NAVIGATION
    // =========================================================================
    function selectZone(zoneId, openPopup = true) {
        selectedZoneId = zoneId;

        // Reset styling on all layers, highlight selected
        zoneLayersById.forEach((layer, zid) => {
            if (zid === zoneId) {
                layer.setStyle({
                    weight: 3.5,
                    color: '#FFFFFF',
                    fillOpacity: 0.65
                });
                layer.bringToFront();
            } else {
                const props = zonePropertiesById.get(zid);
                const sev = getInsarSeverity(props?.median_velocity_mm_year);
                layer.setStyle({
                    color: sev.borderColor,
                    weight: 1.5,
                    fillColor: sev.color,
                    fillOpacity: 0.35
                });
            }
        });

        // Highlight table row
        document.querySelectorAll('.insar-table tr').forEach(tr => tr.classList.remove('insar-row-selected'));
        const activeRow = document.getElementById(`insar-row-${zoneId}`);
        if (activeRow) {
            activeRow.classList.add('insar-row-selected');
            activeRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        // Open popup if requested
        const targetLayer = zoneLayersById.get(zoneId);
        if (targetLayer && openPopup) {
            targetLayer.openPopup();
        }
    }

    function zoomToZone(zoneId) {
        const layer = zoneLayersById.get(zoneId);
        if (!layer || !mapInstance) return;

        selectZone(zoneId, false);
        mapInstance.flyToBounds(layer.getBounds(), {
            padding: [60, 60],
            maxZoom: 15,
            duration: 0.8
        });
        setTimeout(() => layer.openPopup(), 900);
    }

    function fitJhariaBounds() {
        if (!insarLayerGroup || !mapInstance) return;
        const bounds = insarLayerGroup.getBounds();
        if (bounds.isValid()) {
            mapInstance.flyToBounds(bounds, {
                padding: [30, 30],
                duration: 0.8
            });
        }
    }

    // =========================================================================
    // 10. LAYER VISIBILITY & HEADER INTEGRATION
    // =========================================================================
    function toggleLayerVisibility() {
        if (!mapInstance || !insarLayerGroup) return;

        isLayerVisible = !isLayerVisible;
        if (isLayerVisible) {
            mapInstance.addLayer(insarLayerGroup);
            if (legendControl) mapInstance.addControl(legendControl);
        } else {
            mapInstance.removeLayer(insarLayerGroup);
            if (legendControl) mapInstance.removeControl(legendControl);
        }

        const toggleBtn = document.getElementById('btn-toggle-insar-layer');
        if (toggleBtn) {
            if (isLayerVisible) toggleBtn.classList.add('active');
            else toggleBtn.classList.remove('active');
        }
    }

    function toggleTableDrawer() {
        const drawer = document.getElementById('insar-zones-drawer');
        const toggleBtn = document.getElementById('btn-toggle-insar-table');
        if (!drawer) return;

        const isCollapsed = drawer.classList.contains('collapsed');
        if (isCollapsed) {
            drawer.classList.remove('collapsed');
            if (toggleBtn) toggleBtn.classList.add('active');
            renderSummaryTable();
        } else {
            drawer.classList.add('collapsed');
            if (toggleBtn) toggleBtn.classList.remove('active');
        }
    }

    // =========================================================================
    // 11. INITIALIZATION & HOOKS
    // =========================================================================
    async function init(map) {
        if (!map) {
            console.error('[TerraVeil InSAR] No Leaflet map instance provided.');
            return;
        }
        mapInstance = map;

        const data = await loadInSARData();
        if (!data) return;

        renderGeoJsonLayer();
        createLegendControl();
        renderSummaryTable();

        // Wire header controls
        const toggleLayerBtn = document.getElementById('btn-toggle-insar-layer');
        if (toggleLayerBtn) {
            toggleLayerBtn.addEventListener('click', (e) => {
                e.preventDefault();
                toggleLayerVisibility();
            });
        }

        const toggleTableBtn = document.getElementById('btn-toggle-insar-table');
        if (toggleTableBtn) {
            toggleTableBtn.addEventListener('click', (e) => {
                e.preventDefault();
                toggleTableDrawer();
            });
        }

        const fitJhariaBtn = document.getElementById('btn-fit-jharia');
        if (fitJhariaBtn) {
            fitJhariaBtn.addEventListener('click', (e) => {
                e.preventDefault();
                fitJhariaBounds();
            });
        }

        // Active site select handler
        const mineSelect = document.getElementById('mine-select');
        if (mineSelect) {
            mineSelect.addEventListener('change', () => {
                if (mineSelect.value === 'jharia') {
                    if (!isLayerVisible) toggleLayerVisibility();
                    fitJhariaBounds();
                } else if (mineSelect.value === 'chasnalla') {
                    mapInstance.setView([23.657, 86.452], 14);
                }
            });
        }
    }

    // Export globally for UI and testing
    window.InSARZones = {
        init,
        config: INSAR_SEVERITY_CONFIG,
        getSeverity: getInsarSeverity,
        parseCSV,
        selectZone,
        zoomToZone,
        fitJhariaBounds,
        toggleLayerVisibility,
        toggleTableDrawer,
        sortTable,
        getData: () => ({ geojson: geojsonData, csv: csvRecords })
    };

    // Auto-init if window.map is already ready
    if (window.map) {
        init(window.map);
    } else {
        window.addEventListener('terraveil:map-ready', (e) => {
            init(e.detail?.map || window.map);
        });
    }

})();

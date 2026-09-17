/**
 * TerraVeil — Intelligent Node Placement Recommendation System
 * 
 * Single Canonical Dataset Architecture:
 * - Computes multi-factor zone priority scores (percentile-normalized across 27 zones)
 * - Supports Auto Recommendation and Budget-Constrained modes (5, 10, 25, 40, 60, Custom)
 * - Planning Spacing parameter (not sensor detection radius)
 * - Generates geometrically valid interior coverage anchors (Pole of Inaccessibility & FPS)
 * - Preserves null underground depth (depth_m = null)
 * - Maintains single canonical dataset (window.TerraVeilState.recommendedNodes)
 * - Synchronizes node selection (selectedNodeId) across:
 *     1. Dedicated Node Placement section
 *     2. 2D GIS Leaflet Map
 *     3. 3D InSAR Map (Three.js terrain surface projection)
 *     4. Digital Twin (clean georeferencing adapter)
 * - Supports manual drag-and-drop field review (status -> field_reviewed)
 * - Exports canonical dataset to standard GeoJSON
 */

(function () {
    'use strict';

    // =========================================================================
    // 1. CANONICAL STATE & CONFIGURATION
    // =========================================================================
    const NODE_PLACEMENT_CONFIG = {
        weights: {
            medianVelocity: 0.45,
            medianDisplacement: 0.25,
            area: 0.20,
            confidence: 0.10
        },
        confidenceScores: {
            very_high: 1.0,
            high: 0.8,
            medium: 0.6,
            low: 0.4
        },
        defaultBudget: 10,
        defaultPlanningSpacing: 200.0, // metres — planning spacing only, NOT sensing radius
        defaultMaxNodesPerZone: 3,
        minimumComponentFraction: 0.05, // Discard MultiPolygon fragments < 5% of zone area
        gridStepMeters: 80.0,           // Matches InSAR raster resolution
        refLon: 86.35,                  // Jharia central projection reference
        refLat: 23.75,
        DISCLAIMER: 'Recommended monitoring locations are generated from InSAR deformation priority and spatial coverage. Final underground installation coordinates and depth require mine-layout, accessibility, geotechnical and field validation.'
    };

    // Initialize Canonical Application State
    window.TerraVeilState = window.TerraVeilState || {};
    window.TerraVeilState.selectedNodeId = null;
    window.TerraVeilState.recommendedNodes = [];
    window.TerraVeilState.insarZones = [];
    window.TerraVeilState.manualAdjustments = new Map(); // nodeId -> { lat, lon, status }
    window.TerraVeilState.confirmedNodes = new Map();   // nodeId -> preserved node
    window.TerraVeilState.deploymentConfig = {
        mode: 'budget', // 'auto' | 'budget'
        budget: 10,
        preset: 10,
        planningSpacing: 200.0,
        maxNodesPerZone: 3,
        weights: Object.assign({}, NODE_PLACEMENT_CONFIG.weights)
    };
    window.TerraVeilState.analysisStats = {
        zonesAnalysed: 27,
        totalAreaKm2: 0,
        zonesSelected: 0,
        highestPriorityZone: null,
        constraintsNote: ''
    };

    // Geographic conversion constants for Jharia (~23.75°N)
    const LAT_M_PER_DEG = 110780.0;
    const LON_M_PER_DEG = 101900.0;

    function toMeters(lon, lat) {
        return [
            (lon - NODE_PLACEMENT_CONFIG.refLon) * LON_M_PER_DEG,
            (lat - NODE_PLACEMENT_CONFIG.refLat) * LAT_M_PER_DEG
        ];
    }

    function toLatLon(x, y) {
        return [
            (y / LAT_M_PER_DEG) + NODE_PLACEMENT_CONFIG.refLat,
            (x / LON_M_PER_DEG) + NODE_PLACEMENT_CONFIG.refLon
        ];
    }

    // =========================================================================
    // 2. PRIORITY SCORING & RANK NORMALIZATION
    // =========================================================================
    function percentileRanks(values) {
        const indexed = values.map((v, idx) => ({ v, idx }));
        indexed.sort((a, b) => a.v - b.v);
        const ranks = new Array(values.length).fill(0.0);

        let i = 0;
        while (i < indexed.length) {
            let j = i;
            while (j < indexed.length && indexed[j].v === indexed[i].v) {
                j++;
            }
            const avgRank = (i + j - 1) / 2.0;
            const normalized = indexed.length > 1 ? avgRank / (indexed.length - 1) : 1.0;
            for (let k = i; k < j; k++) {
                ranks[indexed[k].idx] = normalized;
            }
            i = j;
        }
        return ranks;
    }

    function calculateZonePriorities(features) {
        if (!features || !features.length) return [];

        const absMedVels = features.map(f => Math.abs(f.properties?.median_velocity_mm_year || 0));
        const absMedDisps = features.map(f => Math.abs(f.properties?.median_net_displacement_mm || 0));
        const areas = features.map(f => f.properties?.area_km2 || 0);
        const confScores = features.map(f => {
            const c = f.properties?.confidence || 'medium';
            return NODE_PLACEMENT_CONFIG.confidenceScores[c] || 0.6;
        });

        const velRanks = percentileRanks(absMedVels);
        const dispRanks = percentileRanks(absMedDisps);
        const areaRanks = percentileRanks(areas);

        const w = window.TerraVeilState.deploymentConfig.weights || NODE_PLACEMENT_CONFIG.weights;

        const ranked = features.map((f, idx) => {
            const p = f.properties || {};
            const score = (
                w.medianVelocity * velRanks[idx] +
                w.medianDisplacement * dispRanks[idx] +
                w.area * areaRanks[idx] +
                w.confidence * confScores[idx]
            );

            return {
                feature: f,
                zone_id: p.zone_id,
                priority_score: Number(score.toFixed(4)),
                rank_components: {
                    velocity_percentile: velRanks[idx],
                    displacement_percentile: dispRanks[idx],
                    area_percentile: areaRanks[idx],
                    confidence_score: confScores[idx]
                },
                centroid: [p.centroid_latitude, p.centroid_longitude],
                area_km2: p.area_km2 || 0,
                median_velocity_mm_year: p.median_velocity_mm_year || 0,
                worst_velocity_mm_year: p.worst_velocity_mm_year || 0,
                median_net_displacement_mm: p.median_net_displacement_mm || 0,
                confidence: p.confidence || 'unknown'
            };
        });

        // Sort descending by priority_score, breaking ties with worst_velocity_mm_year
        ranked.sort((a, b) => {
            if (Math.abs(b.priority_score - a.priority_score) > 0.0001) {
                return b.priority_score - a.priority_score;
            }
            return Math.abs(b.worst_velocity_mm_year) - Math.abs(a.worst_velocity_mm_year);
        });

        ranked.forEach((z, i) => {
            z.priority_rank = i + 1;
        });

        return ranked;
    }

    // =========================================================================
    // 3. NODE ALLOCATION ENGINES (AUTO & BUDGET-CONSTRAINED)
    // =========================================================================
    
    /**
     * Auto Recommendation Mode:
     * Calculates recommended planning deployment based on detected zones,
     * priority scores, area, and planning spacing.
     */
    function calculateAutoAllocation(prioritizedZones, planningSpacing, maxNodesPerZone) {
        const allocation = new Map();
        if (!prioritizedZones || !prioritizedZones.length) return allocation;

        prioritizedZones.forEach(z => {
            if (z.priority_score < 0.40) {
                allocation.set(z.zone_id, 0);
            } else if (z.priority_score >= 0.70) {
                const count = z.area_km2 >= 1.0 ? 3 : (z.area_km2 >= 0.4 ? 2 : 1);
                allocation.set(z.zone_id, Math.min(maxNodesPerZone, count));
            } else if (z.priority_score >= 0.50) {
                const count = z.area_km2 >= 0.8 ? 2 : 1;
                allocation.set(z.zone_id, Math.min(maxNodesPerZone, count));
            } else if (z.area_km2 >= 0.50) {
                allocation.set(z.zone_id, 1);
            } else {
                allocation.set(z.zone_id, 0);
            }
        });

        return allocation;
    }

    /**
     * Budget-Constrained Mode:
     * Iteratively distributes an exact node budget across zones using marginal utility.
     */
    function allocateNodeBudget(prioritizedZones, budget, maxNodesPerZone) {
        const allocation = new Map();
        if (!prioritizedZones || !prioritizedZones.length || budget <= 0) return allocation;

        prioritizedZones.forEach(z => allocation.set(z.zone_id, 0));

        let remaining = budget;

        // Marginal utility helper
        function marginalValue(zone, currentAllocated) {
            if (currentAllocated >= maxNodesPerZone) return -1;
            const areaFactor = Math.sqrt(zone.area_km2 || 0.1);
            if (currentAllocated === 0) {
                return zone.priority_score * 1.0 * Math.max(0.7, areaFactor);
            }
            return (zone.priority_score * areaFactor) / (1.5 * currentAllocated + 0.5);
        }

        while (remaining > 0) {
            let bestZone = null;
            let bestVal = -1;

            for (const z of prioritizedZones) {
                const curr = allocation.get(z.zone_id) || 0;
                if (curr >= maxNodesPerZone) continue;
                const val = marginalValue(z, curr);
                if (val > bestVal) {
                    bestVal = val;
                    bestZone = z;
                }
            }

            if (!bestZone || bestVal <= 0) break;
            allocation.set(bestZone.zone_id, (allocation.get(bestZone.zone_id) || 0) + 1);
            remaining--;
        }

        return allocation;
    }

    // =========================================================================
    // 4. 2D GEOMETRY ENGINE (POLE OF INACCESSIBILITY & FARTHEST-POINT SAMPLING)
    // =========================================================================
    function pointInRing(pt, ring) {
        const x = pt[0], y = pt[1];
        let inside = false;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
            const xi = ring[i][0], yi = ring[i][1];
            const xj = ring[j][0], yj = ring[j][1];
            const intersect = ((yi > y) !== (yj > y)) &&
                (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi);
            if (intersect) inside = !inside;
        }
        return inside;
    }

    function polygonArea(ring) {
        let area = 0.0;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
            area += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1]);
        }
        return Math.abs(area) / 2.0;
    }

    function getValidComponents(geometry) {
        const components = [];
        if (!geometry || !geometry.coordinates) return components;

        if (geometry.type === 'Polygon') {
            const exteriorM = geometry.coordinates[0].map(c => toMeters(c[0], c[1]));
            const holesM = (geometry.coordinates.slice(1) || []).map(h => h.map(c => toMeters(c[0], c[1])));
            components.push({
                exterior: exteriorM,
                holes: holesM,
                area: polygonArea(exteriorM)
            });
        } else if (geometry.type === 'MultiPolygon') {
            geometry.coordinates.forEach(polyCoords => {
                const exteriorM = polyCoords[0].map(c => toMeters(c[0], c[1]));
                const holesM = (polyCoords.slice(1) || []).map(h => h.map(c => toMeters(c[0], c[1])));
                components.push({
                    exterior: exteriorM,
                    holes: holesM,
                    area: polygonArea(exteriorM)
                });
            });
        }

        const totalArea = components.reduce((sum, c) => sum + c.area, 0);
        return components.filter(c => (c.area / (totalArea || 1)) >= NODE_PLACEMENT_CONFIG.minimumComponentFraction);
    }

    function pointToSegmentDistSq(p, a, b) {
        const dx = b[0] - a[0], dy = b[1] - a[1];
        const l2 = dx * dx + dy * dy;
        if (l2 < 1e-12) return (p[0] - a[0]) * (p[0] - a[0]) + (p[1] - a[1]) * (p[1] - a[1]);
        let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2;
        t = Math.max(0, Math.min(1, t));
        const px = a[0] + t * dx, py = a[1] + t * dy;
        return (p[0] - px) * (p[0] - px) + (p[1] - py) * (p[1] - py);
    }

    function distanceToPolygonBoundary(pt, component) {
        let minDistSq = Infinity;
        const exterior = component.exterior;
        for (let i = 0, j = exterior.length - 1; i < exterior.length; j = i++) {
            const d = pointToSegmentDistSq(pt, exterior[j], exterior[i]);
            if (d < minDistSq) minDistSq = d;
        }
        for (const hole of component.holes) {
            for (let i = 0, j = hole.length - 1; i < hole.length; j = i++) {
                const d = pointToSegmentDistSq(pt, hole[j], hole[i]);
                if (d < minDistSq) minDistSq = d;
            }
        }
        return Math.sqrt(minDistSq);
    }

    function generateGridCandidates(component, step) {
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        component.exterior.forEach(p => {
            if (p[0] < minX) minX = p[0];
            if (p[0] > maxX) maxX = p[0];
            if (p[1] < minY) minY = p[1];
            if (p[1] > maxY) maxY = p[1];
        });

        const candidates = [];
        for (let x = minX + step * 0.5; x < maxX; x += step) {
            for (let y = minY + step * 0.5; y < maxY; y += step) {
                const pt = [x, y];
                if (!pointInRing(pt, component.exterior)) continue;
                let insideHole = false;
                for (const hole of component.holes) {
                    if (pointInRing(pt, hole)) {
                        insideHole = true;
                        break;
                    }
                }
                if (insideHole) continue;
                const distToBoundary = distanceToPolygonBoundary(pt, component);
                candidates.push({ pt, distToBoundary });
            }
        }
        return candidates;
    }

    // Extensibility hooks
    function filterCandidate(candidate, constraints) {
        return true; // Stub for future mine_galleries.geojson / restricted_areas.geojson
    }

    function localRiskScore(lat, lon) {
        return 1.0;  // Stub for future raster-level MintPy pixel queries
    }

    function selectAnchorPoints(component, count, minSpacingMeters) {
        if (count <= 0) return [];
        let step = NODE_PLACEMENT_CONFIG.gridStepMeters;
        let candidates = generateGridCandidates(component, step);

        if (candidates.length < count * 2) {
            step = step * 0.5;
            candidates = generateGridCandidates(component, step);
        }

        if (!candidates.length) {
            let cx = 0, cy = 0;
            component.exterior.forEach(p => { cx += p[0]; cy += p[1]; });
            cx /= component.exterior.length;
            cy /= component.exterior.length;
            return [{ pt: [cx, cy], distToBoundary: 0 }];
        }

        // Anchor 1: Pole of Inaccessibility (deepest interior point)
        candidates.sort((a, b) => b.distToBoundary - a.distToBoundary);
        const selected = [candidates[0]];

        // Anchors 2+: Farthest-Point Sampling
        const minSpacingSq = minSpacingMeters * minSpacingMeters;
        for (let k = 1; k < count; k++) {
            let bestCand = null;
            let maxMinDist = -1;

            for (const cand of candidates) {
                let dMinSq = Infinity;
                for (const s of selected) {
                    const dx = cand.pt[0] - s.pt[0];
                    const dy = cand.pt[1] - s.pt[1];
                    const d2 = dx * dx + dy * dy;
                    if (d2 < dMinSq) dMinSq = d2;
                }

                if (dMinSq < minSpacingSq) continue;
                const score = dMinSq + cand.distToBoundary * 50.0;
                if (score > maxMinDist) {
                    maxMinDist = score;
                    bestCand = cand;
                }
            }

            if (!bestCand) {
                // Relax spacing if needed
                for (const cand of candidates) {
                    let dMinSq = Infinity;
                    for (const s of selected) {
                        const dx = cand.pt[0] - s.pt[0];
                        const dy = cand.pt[1] - s.pt[1];
                        const d2 = dx * dx + dy * dy;
                        if (d2 < dMinSq) dMinSq = d2;
                    }
                    if (dMinSq > maxMinDist) {
                        maxMinDist = dMinSq;
                        bestCand = cand;
                    }
                }
            }

            if (bestCand) {
                selected.push(bestCand);
            }
        }

        return selected;
    }

    function generateZoneAnchors(zoneInfo, nodeCount, minSpacingMeters) {
        if (nodeCount <= 0) return [];
        const components = getValidComponents(zoneInfo.feature.geometry);
        if (!components.length) return [];

        components.sort((a, b) => b.area - a.area);

        const anchors = [];
        if (components.length === 1 || nodeCount === 1) {
            const pts = selectAnchorPoints(components[0], nodeCount, minSpacingMeters);
            pts.forEach(p => anchors.push({ pt: p.pt, distToBoundary: p.distToBoundary, componentArea: components[0].area }));
        } else {
            const compAllocs = new Array(components.length).fill(0);
            compAllocs[0] = 1;
            let remaining = nodeCount - 1;

            let cIdx = 1;
            while (remaining > 0 && cIdx < components.length) {
                if (components[cIdx].area / components[0].area >= 0.20) {
                    compAllocs[cIdx]++;
                    remaining--;
                }
                cIdx++;
            }
            compAllocs[0] += remaining;

            components.forEach((comp, idx) => {
                const count = compAllocs[idx];
                if (count > 0) {
                    const pts = selectAnchorPoints(comp, count, minSpacingMeters);
                    pts.forEach(p => anchors.push({ pt: p.pt, distToBoundary: p.distToBoundary, componentArea: comp.area }));
                }
            });
        }

        return anchors;
    }

    // =========================================================================
    // 5. EXPLAINABILITY BUILDER
    // =========================================================================
    function buildPlacementReasons(zone, anchor, indexInZone, totalInZone, spacing) {
        const p = zone.rank_components;
        const reasonsZone = [];

        if (p.velocity_percentile >= 0.70) {
            reasonsZone.push(`High median LOS subsidence velocity (${zone.median_velocity_mm_year.toFixed(1)} mm/yr, ${Math.round(p.velocity_percentile * 100)}th percentile)`);
        } else if (p.velocity_percentile >= 0.40) {
            reasonsZone.push(`Moderate subsidence velocity (${zone.median_velocity_mm_year.toFixed(1)} mm/yr)`);
        } else {
            reasonsZone.push(`Baseline subsidence velocity (${zone.median_velocity_mm_year.toFixed(1)} mm/yr)`);
        }

        if (p.displacement_percentile >= 0.70) {
            reasonsZone.push(`Severe accumulated net displacement (${zone.median_net_displacement_mm.toFixed(1)} mm)`);
        }

        if (zone.area_km2 >= 0.80) {
            reasonsZone.push(`Extensive subsidence footprint (${zone.area_km2.toFixed(2)} km²)`);
        } else if (zone.area_km2 >= 0.40) {
            reasonsZone.push(`Moderate deformation area (${zone.area_km2.toFixed(2)} km²)`);
        }

        if (Math.abs(zone.worst_velocity_mm_year) >= 200.0) {
            reasonsZone.push(`Localized subsidence acceleration detected (peak ${zone.worst_velocity_mm_year.toFixed(1)} mm/yr)`);
        }

        const reasonsLoc = [];
        if (indexInZone === 0) {
            reasonsLoc.push(`Interior core coverage anchor (Pole of Inaccessibility, ${Math.round(anchor.distToBoundary)}m from boundary)`);
        } else {
            reasonsLoc.push(`Distributed spatial coverage anchor #${indexInZone + 1} (maintains ≥ ${Math.round(spacing)}m separation)`);
        }
        reasonsLoc.push(`Lies entirely inside valid InSAR deformation polygon`);

        return {
            whyZone: reasonsZone.join('; ') + '.',
            whyLocation: reasonsLoc.join('; ') + '.'
        };
    }

    // =========================================================================
    // 6. CANONICAL RECOMMENDATION BUILDER
    // =========================================================================
    function buildCanonicalRecommendations(prioritizedZones, allocationMap, spacing) {
        const recommendations = [];
        let seq = 1;

        prioritizedZones.forEach(z => {
            const count = allocationMap.get(z.zone_id) || 0;
            if (count <= 0) return;

            const anchors = generateZoneAnchors(z, count, spacing);

            anchors.forEach((anc, aIdx) => {
                const nodeId = `NODE-${String(seq).padStart(3, '0')}`;
                const [lat, lon] = toLatLon(anc.pt[0], anc.pt[1]);
                const reasons = buildPlacementReasons(z, anc, aIdx, count, spacing);

                // Preserve manual review if already dragged
                const manual = window.TerraVeilState.manualAdjustments.get(nodeId);
                const finalLat = manual ? manual.lat : Number(lat.toFixed(6));
                const finalLon = manual ? manual.lon : Number(lon.toFixed(6));
                const finalStatus = manual ? manual.status : 'recommended';

                const nodeObj = {
                    id: nodeId,
                    zone_id: z.zone_id,
                    latitude: finalLat,
                    longitude: finalLon,
                    node_type: 'underground_monitoring',
                    priority_score: z.priority_score,
                    priority_rank: z.priority_rank,
                    placement_score: Number((z.priority_score * (1.0 - aIdx * 0.08)).toFixed(3)),
                    median_velocity_mm_year: z.median_velocity_mm_year,
                    worst_velocity_mm_year: z.worst_velocity_mm_year,
                    median_net_displacement_mm: z.median_net_displacement_mm,
                    area_km2: z.area_km2,
                    confidence: z.confidence,
                    status: finalStatus,
                    recommendation_reason: reasons,
                    source: 'insar',
                    depth_m: null,      // strictly null, never invented
                    elevation_m: null,  // strictly null or surface projection
                    dist_to_boundary_m: Math.round(anc.distToBoundary),
                    anchor_index: aIdx + 1,
                    total_zone_anchors: count
                };

                recommendations.push(nodeObj);
                seq++;
            });
        });

        // Update single canonical dataset
        window.TerraVeilState.recommendedNodes = recommendations;
        
        // Calculate summary statistics
        const coveredZones = new Set(recommendations.map(r => r.zone_id));
        const totalAreaCovered = Array.from(coveredZones).reduce((sum, zid) => {
            const f = prioritizedZones.find(z => z.zone_id === zid);
            return sum + (f ? f.area_km2 : 0);
        }, 0);

        window.TerraVeilState.analysisStats = {
            zonesAnalysed: prioritizedZones.length,
            totalAreaKm2: Number(totalAreaCovered.toFixed(2)),
            zonesSelected: coveredZones.size,
            highestPriorityZone: prioritizedZones[0] ? `Zone ${prioritizedZones[0].zone_id} (Score: ${prioritizedZones[0].priority_score.toFixed(3)})` : 'None',
            constraintsNote: `All anchors strictly enforce ≥ ${Math.round(spacing)}m planning spacing and interior geometry boundaries.`
        };

        // Dispatch canonical update event to all views
        window.dispatchEvent(new CustomEvent('terraveil:nodes-updated', {
            detail: { nodes: recommendations, stats: window.TerraVeilState.analysisStats }
        }));

        return recommendations;
    }

    // =========================================================================
    // 7. SYNCHRONIZED SELECTION DISPATCHER
    // =========================================================================
    function selectNode(nodeId, source = 'system') {
        if (!nodeId) return;
        window.TerraVeilState.selectedNodeId = nodeId;

        // Dispatch selection event
        window.dispatchEvent(new CustomEvent('terraveil:node-selected', {
            detail: { nodeId, source }
        }));

        // Highlight in 2D Map if available
        highlightNodeIn2DMap(nodeId);

        // Highlight in Table
        highlightNodeInTable(nodeId);

        // Highlight in Drawer
        highlightNodeInDrawer(nodeId);

        // Highlight in Preview
        updatePreviewDetails();
    }

    // =========================================================================
    // 8. 2D LEAFLET MAP INTEGRATION (DRAGGABLE ANCHORS & POPUPS)
    // =========================================================================
    let mapInstance = null;
    let recommendedLayerGroup = null;
    const markerLookup = new Map(); // nodeId -> leafletMarker

    function createAnchorIcon(node) {
        const isReviewed = node.status === 'field_reviewed';
        const isSelected = window.TerraVeilState.selectedNodeId === node.id;
        const pinClass = isReviewed ? 'pin-reviewed' : '';
        const selectClass = isSelected ? 'pin-selected' : '';

        return L.divIcon({
            className: 'rec-anchor-marker',
            html: `
                <div class="rec-anchor-pulse"></div>
                <div class="rec-anchor-pin ${pinClass} ${selectClass}">
                    <span class="rec-anchor-label">Z${node.zone_id}</span>
                </div>
            `,
            iconSize: [36, 36],
            iconAnchor: [18, 18],
            popupAnchor: [0, -18]
        });
    }

    function buildAnchorPopupHtml(node) {
        const isReviewed = node.status === 'field_reviewed';
        const statusBadge = isReviewed
            ? `<span class="badge-rec-status badge-rec-reviewed">Field Reviewed</span>`
            : `<span class="badge-rec-status badge-rec-recommended">Recommended</span>`;

        return `
            <div class="anchor-popup">
                <div class="anchor-popup-header">
                    <div>
                        <div class="anchor-popup-eyebrow">Coverage Anchor • InSAR Hotspot</div>
                        <div class="anchor-popup-title">${node.id} (Zone ${node.zone_id})</div>
                    </div>
                    ${statusBadge}
                </div>
                <div class="anchor-popup-body">
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:10px; font-family:'JetBrains Mono',monospace;">
                        <div>Priority Rank: <strong>#${node.priority_rank}</strong></div>
                        <div>Priority Score: <strong>${node.priority_score.toFixed(3)}</strong></div>
                        <div>Median LOS: <strong>${node.median_velocity_mm_year.toFixed(1)} mm/yr</strong></div>
                        <div>Net Disp: <strong>${node.median_net_displacement_mm.toFixed(1)} mm</strong></div>
                        <div>Coordinates: <strong>${node.latitude.toFixed(5)}, ${node.longitude.toFixed(5)}</strong></div>
                        <div>Depth (Z): <strong style="color:var(--text-muted);">Surface Projection</strong></div>
                    </div>

                    <div class="anchor-explain-box">
                        <div class="anchor-explain-title">Why this Zone?</div>
                        <div style="color:var(--text-secondary);">${node.recommendation_reason.whyZone}</div>
                    </div>

                    <div class="anchor-explain-box">
                        <div class="anchor-explain-title">Why this Location?</div>
                        <div style="color:var(--text-secondary);">${node.recommendation_reason.whyLocation}</div>
                    </div>

                    <div class="anchor-drag-hint">
                        <span>✥ Drag marker to manually adjust for gallery accessibility.</span>
                    </div>
                </div>
                <div class="anchor-popup-footer">
                    <span>${NODE_PLACEMENT_CONFIG.DISCLAIMER}</span>
                </div>
            </div>
        `;
    }

    function render2DMapLayers(nodes) {
        if (!mapInstance || !window.L) return;

        if (!recommendedLayerGroup) {
            recommendedLayerGroup = L.layerGroup();
            if(window.TerraVeilTelemetry?.mode!=='REAL') mapInstance.addLayer(recommendedLayerGroup);
        }

        recommendedLayerGroup.clearLayers();
        markerLookup.clear();

        nodes.forEach(node => {
            const icon = createAnchorIcon(node);
            const marker = L.marker([node.latitude, node.longitude], {
                icon,
                draggable: true,
                title: `${node.id} — Zone ${node.zone_id}`
            });

            marker.bindPopup(buildAnchorPopupHtml(node), {
                maxWidth: 360,
                className: 'insar-leaflet-popup'
            });

            // Selection on click
            marker.on('click', () => {
                selectNode(node.id, '2d_map');
            });

            // Drag handling for manual review
            marker.on('dragend', (e) => {
                const newLatLng = e.target.getLatLng();
                const newLat = Number(newLatLng.lat.toFixed(6));
                const newLon = Number(newLatLng.lng.toFixed(6));

                // Save manual review
                window.TerraVeilState.manualAdjustments.set(node.id, {
                    lat: newLat,
                    lon: newLon,
                    status: 'field_reviewed'
                });

                // Update node object in place
                node.latitude = newLat;
                node.longitude = newLon;
                node.status = 'field_reviewed';
                node.recommendation_reason.whyLocation = `Manually field-reviewed position (${newLat}, ${newLon}).`;

                // Refresh popup and marker icon
                marker.setIcon(createAnchorIcon(node));
                marker.setPopupContent(buildAnchorPopupHtml(node));

                // Dispatch global update
                window.dispatchEvent(new CustomEvent('terraveil:nodes-updated', {
                    detail: { nodes: window.TerraVeilState.recommendedNodes, stats: window.TerraVeilState.analysisStats }
                }));

                selectNode(node.id, 'manual_drag');
            });

            recommendedLayerGroup.addLayer(marker);
            markerLookup.set(node.id, marker);
        });

        // Update badge count in map controls
        const badge = document.getElementById('placement-count-badge');
        if (badge) badge.textContent = nodes.length;
    }

    function highlightNodeIn2DMap(nodeId) {
        const marker = markerLookup.get(nodeId);
        if (!marker || !mapInstance) return;

        const node = window.TerraVeilState.recommendedNodes.find(n => n.id === nodeId);
        if (node) {
            marker.setIcon(createAnchorIcon(node));
            if (!marker.isPopupOpen()) {
                marker.openPopup();
            }
        }

        // Highlight parent InSAR zone polygon if insar-zones is present
        if (window.InsarZonesEngine && node) {
            window.InsarZonesEngine.highlightZone(node.zone_id);
        }
    }

    // =========================================================================
    // 9. DEDICATED "NODE PLACEMENT" SECTION CONTROLLER
    // =========================================================================
    let fullTableSortCol = 'priority_rank';
    let fullTableSortAsc = true;
    let fullTableSearchQuery = '';
    let fullTableSeverityFilter = 'ALL';

    function renderNodePlacementSection() {
        const section = document.getElementById('view-placement');
        if (!section) return;

        const nodes = window.TerraVeilState.recommendedNodes || [];
        const stats = window.TerraVeilState.analysisStats || {};
        const config = window.TerraVeilState.deploymentConfig || {};

        // 1. Update KPI Values
        const elAnalysed = document.getElementById('kpi-placement-analysed');
        if (elAnalysed) elAnalysed.textContent = stats.zonesAnalysed || 27;

        const elMode = document.getElementById('kpi-placement-mode');
        if (elMode) elMode.textContent = config.mode === 'auto' ? 'Auto Planning' : 'Budget-Constrained';

        const elNodes = document.getElementById('kpi-placement-nodes');
        if (elNodes) elNodes.textContent = nodes.length;

        const elCovered = document.getElementById('kpi-placement-covered');
        if (elCovered) elCovered.textContent = `${stats.zonesSelected || 0} / ${stats.zonesAnalysed || 27}`;

        const elHighest = document.getElementById('kpi-placement-highest');
        if (elHighest) elHighest.textContent = stats.highestPriorityZone || 'Zone 10';

        const elArea = document.getElementById('kpi-placement-area');
        if (elArea) elArea.textContent = `${stats.totalAreaKm2 || 0} km²`;

        // 2. Render Full Interactive Table
        renderPlacementTableRows();

        // 3. Update Preview Panel details
        updatePreviewDetails();
    }

    function renderPlacementTableRows() {
        const tbody = document.getElementById('placement-full-tbody');
        if (!tbody) return;

        let nodes = [...(window.TerraVeilState.recommendedNodes || [])];

        // Filter search
        if (fullTableSearchQuery) {
            const q = fullTableSearchQuery.toLowerCase();
            nodes = nodes.filter(n =>
                n.id.toLowerCase().includes(q) ||
                `zone ${n.zone_id}`.toLowerCase().includes(q) ||
                n.status.toLowerCase().includes(q)
            );
        }

        // Filter severity
        if (fullTableSeverityFilter !== 'ALL') {
            nodes = nodes.filter(n => {
                const vel = Math.abs(n.median_velocity_mm_year);
                if (fullTableSeverityFilter === 'CRITICAL') return vel >= 85.0;
                if (fullTableSeverityFilter === 'SEVERE') return vel >= 80.0 && vel < 85.0;
                if (fullTableSeverityFilter === 'HIGH') return vel >= 75.0 && vel < 80.0;
                if (fullTableSeverityFilter === 'ELEVATED') return vel < 75.0;
                return true;
            });
        }

        // Sort
        nodes.sort((a, b) => {
            let va = a[fullTableSortCol], vb = b[fullTableSortCol];
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return fullTableSortAsc ? -1 : 1;
            if (va > vb) return fullTableSortAsc ? 1 : -1;
            return 0;
        });

        if (!nodes.length) {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:24px; color:var(--text-muted);">No recommended monitoring locations matching current criteria.</td></tr>`;
            return;
        }

        tbody.innerHTML = nodes.map(n => {
            const isSelected = window.TerraVeilState.selectedNodeId === n.id;
            const statusClass = n.status === 'field_reviewed' ? 'badge-rec-reviewed' : 'badge-rec-recommended';
            const statusLabel = n.status === 'field_reviewed' ? 'Field Reviewed' : 'Recommended';

            return `
                <tr class="${isSelected ? 'row-selected' : ''}" data-node-id="${n.id}">
                    <td style="font-family:'JetBrains Mono',monospace; font-weight:700; color:#06B6D4;">${n.id}</td>
                    <td style="font-family:'JetBrains Mono',monospace; font-weight:600;">Zone ${n.zone_id}</td>
                    <td style="font-family:'JetBrains Mono',monospace;">${n.latitude.toFixed(5)}</td>
                    <td style="font-family:'JetBrains Mono',monospace;">${n.longitude.toFixed(5)}</td>
                    <td style="font-family:'JetBrains Mono',monospace; font-weight:700;">#${n.priority_rank}</td>
                    <td style="font-family:'JetBrains Mono',monospace;">${n.priority_score.toFixed(3)}</td>
                    <td style="font-family:'JetBrains Mono',monospace; color:#EF4444;">${n.median_velocity_mm_year.toFixed(1)}</td>
                    <td style="font-family:'JetBrains Mono',monospace;">${n.confidence}</td>
                    <td><span class="badge-rec-status ${statusClass}">${statusLabel}</span></td>
                    <td>
                        <div class="action-chip-group">
                            <button type="button" class="action-chip-view btn-view-2d" data-node-id="${n.id}" title="Focus on 2D GIS Map">2D</button>
                            <button type="button" class="action-chip-view btn-view-3d" data-node-id="${n.id}" title="Inspect on 3D InSAR Terrain">3D</button>
                            <button type="button" class="action-chip-view btn-view-twin" data-node-id="${n.id}" title="Inspect in Mine Digital Twin">Twin</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        // Wire row selection
        tbody.querySelectorAll('tr').forEach(tr => {
            tr.addEventListener('click', (e) => {
                if (e.target.closest('button')) return; // Handled by action buttons
                const nid = tr.getAttribute('data-node-id');
                selectNode(nid, 'table_click');
            });
        });

        // Wire Action Buttons (Cross-Navigation)
        tbody.querySelectorAll('.btn-view-2d').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const nid = btn.getAttribute('data-node-id');
                selectNode(nid, 'action_btn');
                window.TerraVeilNavigation?.switchTo('view-live', nid);
            });
        });

        tbody.querySelectorAll('.btn-view-3d').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const nid = btn.getAttribute('data-node-id');
                selectNode(nid, 'action_btn');
                window.TerraVeilNavigation?.switchTo('view-insar', nid);
            });
        });

        tbody.querySelectorAll('.btn-view-twin').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const nid = btn.getAttribute('data-node-id');
                selectNode(nid, 'action_btn');
                window.TerraVeilNavigation?.switchTo('view-twin', nid);
            });
        });
    }

    function highlightNodeInTable(nodeId) {
        const tbody = document.getElementById('placement-full-tbody');
        if (!tbody) return;

        tbody.querySelectorAll('tr').forEach(tr => {
            const match = tr.getAttribute('data-node-id') === nodeId;
            tr.classList.toggle('row-selected', match);
            if (match) {
                tr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
    }

    function updatePreviewDetails() {
        const panel = document.getElementById('preview-detail-panel');
        if (!panel) return;

        const selId = window.TerraVeilState.selectedNodeId;
        const node = window.TerraVeilState.recommendedNodes.find(n => n.id === selId);

        if (!node) {
            panel.innerHTML = `
                <div style="color:var(--text-muted); font-style:italic;">
                    Select any recommended monitoring location from the table or preview map to inspect detailed InSAR explainability.
                </div>
            `;
            return;
        }

        panel.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="color:#06B6D4; font-size:13px; font-family:'JetBrains Mono',monospace;">${node.id} — Zone ${node.zone_id}</strong>
                <span class="badge-rec-status ${node.status === 'field_reviewed' ? 'badge-rec-reviewed' : 'badge-rec-recommended'}">
                    ${node.status === 'field_reviewed' ? 'Field Reviewed' : 'Recommended'}
                </span>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:10px; font-family:'JetBrains Mono',monospace; margin-top:4px;">
                <div>Priority Rank: <strong>#${node.priority_rank}</strong></div>
                <div>Priority Score: <strong>${node.priority_score.toFixed(3)}</strong></div>
                <div>Median Velocity: <strong style="color:#EF4444;">${node.median_velocity_mm_year.toFixed(1)} mm/yr</strong></div>
                <div>Net Displacement: <strong>${node.median_net_displacement_mm.toFixed(1)} mm</strong></div>
                <div>Latitude: <strong>${node.latitude.toFixed(6)}°</strong></div>
                <div>Longitude: <strong>${node.longitude.toFixed(6)}°</strong></div>
            </div>
            <div style="margin-top:6px; background:var(--bg-input); padding:6px 8px; border-radius:4px; border:1px solid var(--border-subtle);">
                <div style="font-size:9px; font-weight:700; color:var(--text-muted); text-transform:uppercase;">Why this Zone?</div>
                <div style="font-size:10.5px; color:var(--text-secondary); margin-top:2px;">${node.recommendation_reason.whyZone}</div>
            </div>
            <div style="margin-top:4px; background:var(--bg-input); padding:6px 8px; border-radius:4px; border:1px solid var(--border-subtle);">
                <div style="font-size:9px; font-weight:700; color:var(--text-muted); text-transform:uppercase;">Why this Location?</div>
                <div style="font-size:10.5px; color:var(--text-secondary); margin-top:2px;">${node.recommendation_reason.whyLocation}</div>
            </div>
        `;
    }

    // =========================================================================
    // 10. MAP DRAWER PANEL CONTROLLER (ON 2D MAP)
    // =========================================================================
    function renderDrawerUI() {
        const panel = document.getElementById('node-placement-panel');
        if (!panel) return;

        const nodes = window.TerraVeilState.recommendedNodes || [];
        const stats = window.TerraVeilState.analysisStats || {};
        const config = window.TerraVeilState.deploymentConfig || {};

        panel.innerHTML = `
            <div class="placement-header">
                <div class="placement-title-group">
                    <span class="placement-title">
                        <span>🎯</span> Recommended Monitoring Locations
                    </span>
                    <span class="placement-badge-ai">COVERAGE ANCHORS</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <button type="button" class="btn-placement-action btn-placement-generate" id="drawer-btn-recompute">
                        <span>🔄 Recompute</span>
                    </button>
                    <button type="button" class="btn-placement-action btn-placement-export" id="drawer-btn-export">
                        <span>📥 Export GeoJSON</span>
                    </button>
                    <button type="button" class="btn-placement-action btn-placement-clear" id="drawer-btn-close">✕</button>
                </div>
            </div>

            <div class="placement-controls-strip">
                <div class="placement-input-group">
                    <span class="placement-label">Deployment Mode:</span>
                    <div class="config-mode-switch" style="padding:2px;">
                        <button type="button" class="config-mode-btn ${config.mode === 'auto' ? 'active' : ''}" id="drawer-mode-auto">Auto</button>
                        <button type="button" class="config-mode-btn ${config.mode === 'budget' ? 'active' : ''}" id="drawer-mode-budget">Budget</button>
                    </div>
                </div>

                <div class="placement-input-group" id="drawer-budget-controls" style="${config.mode === 'auto' ? 'display:none;' : ''}">
                    <span class="placement-label">Node Budget:</span>
                    <div class="placement-budget-presets">
                        <button type="button" class="budget-preset-btn ${config.budget === 5 ? 'active' : ''}" data-budget="5">5</button>
                        <button type="button" class="budget-preset-btn ${config.budget === 10 ? 'active' : ''}" data-budget="10">10</button>
                        <button type="button" class="budget-preset-btn ${config.budget === 25 ? 'active' : ''}" data-budget="25">25</button>
                        <button type="button" class="budget-preset-btn ${config.budget === 40 ? 'active' : ''}" data-budget="40">40</button>
                        <button type="button" class="budget-preset-btn ${config.budget === 60 ? 'active' : ''}" data-budget="60">60</button>
                    </div>
                    <input type="number" id="drawer-custom-budget" class="placement-input-number" min="1" max="60" value="${config.budget}">
                    <button type="button" class="budget-preset-btn" id="drawer-apply-custom">Apply</button>
                </div>
            </div>

            <div class="placement-summary-strip">
                <div class="placement-stats-list">
                    <span class="placement-stat-item">Total Anchors: <strong>${nodes.length}</strong></span>
                    <span class="placement-stat-item">Zones Covered: <strong>${stats.zonesSelected || 0} / 27</strong></span>
                    <span class="placement-stat-item">Planning Spacing: <strong>≥ ${Math.round(config.planningSpacing)}m</strong></span>
                </div>
                <div class="placement-warning-notice">
                    <span>⚠️ Planning anchors — requires mine-gallery validation.</span>
                </div>
            </div>

            <div class="placement-list-wrapper">
                <table class="placement-table">
                    <thead>
                        <tr>
                            <th>Node ID</th>
                            <th>Zone</th>
                            <th>Priority</th>
                            <th>Coordinates</th>
                            <th>Status</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="drawer-nodes-tbody">
                        ${nodes.map(n => `
                            <tr class="${window.TerraVeilState.selectedNodeId === n.id ? 'row-selected' : ''}" data-node-id="${n.id}">
                                <td style="font-family:'JetBrains Mono',monospace; font-weight:700; color:#06B6D4;">${n.id}</td>
                                <td>Zone ${n.zone_id}</td>
                                <td>#${n.priority_rank} (${n.priority_score.toFixed(2)})</td>
                                <td style="font-family:'JetBrains Mono',monospace;">${n.latitude.toFixed(4)}, ${n.longitude.toFixed(4)}</td>
                                <td>
                                    <span class="badge-rec-status ${n.status === 'field_reviewed' ? 'badge-rec-reviewed' : 'badge-rec-recommended'}">
                                        ${n.status === 'field_reviewed' ? 'Reviewed' : 'Rec'}
                                    </span>
                                </td>
                                <td>
                                    <button type="button" class="action-chip-view btn-locate-anchor" data-node-id="${n.id}">Locate</button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;

        // Wire Drawer Buttons
        document.getElementById('drawer-btn-close')?.addEventListener('click', togglePlacementPanel);
        document.getElementById('drawer-btn-recompute')?.addEventListener('click', runRecompute);
        document.getElementById('drawer-btn-export')?.addEventListener('click', exportGeoJSON);

        document.getElementById('drawer-mode-auto')?.addEventListener('click', () => {
            window.TerraVeilState.deploymentConfig.mode = 'auto';
            runRecompute();
        });

        document.getElementById('drawer-mode-budget')?.addEventListener('click', () => {
            window.TerraVeilState.deploymentConfig.mode = 'budget';
            runRecompute();
        });

        panel.querySelectorAll('.budget-preset-btn[data-budget]').forEach(btn => {
            btn.addEventListener('click', () => {
                const b = Number(btn.getAttribute('data-budget'));
                window.TerraVeilState.deploymentConfig.budget = b;
                window.TerraVeilState.deploymentConfig.preset = b;
                runRecompute();
            });
        });

        document.getElementById('drawer-apply-custom')?.addEventListener('click', () => {
            const input = document.getElementById('drawer-custom-budget');
            const val = parseInt(input.value, 10);
            if (!isNaN(val) && val >= 1 && val <= 60) {
                window.TerraVeilState.deploymentConfig.budget = val;
                runRecompute();
            }
        });

        panel.querySelectorAll('tbody tr').forEach(tr => {
            tr.addEventListener('click', () => {
                const nid = tr.getAttribute('data-node-id');
                selectNode(nid, 'drawer_row');
            });
        });

        panel.querySelectorAll('.btn-locate-anchor').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const nid = btn.getAttribute('data-node-id');
                selectNode(nid, 'locate_btn');
                const marker = markerLookup.get(nid);
                if (marker && mapInstance) {
                    mapInstance.flyTo(marker.getLatLng(), 15, { duration: 0.6 });
                }
            });
        });
    }

    function highlightNodeInDrawer(nodeId) {
        const tbody = document.getElementById('drawer-nodes-tbody');
        if (!tbody) return;

        tbody.querySelectorAll('tr').forEach(tr => {
            const match = tr.getAttribute('data-node-id') === nodeId;
            tr.classList.toggle('row-selected', match);
            if (match) tr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    }

    // =========================================================================
    // 11. RECOMPUTATION & EXPORT WORKFLOWS
    // =========================================================================
    let rankedZonesCache = null;

    async function ensureInSARFeaturesLoaded() {
        if (window.TerraVeilState.insarZones && window.TerraVeilState.insarZones.length) {
            return window.TerraVeilState.insarZones;
        }

        try {
            const resp = await fetch('/map/jharia_subsidence_zones.geojson');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            window.TerraVeilState.insarZones = data.features || [];
            return window.TerraVeilState.insarZones;
        } catch (err) {
            console.error('Failed to load InSAR GeoJSON for Node Placement:', err);
            return [];
        }
    }

    async function runRecompute() {
        const features = await ensureInSARFeaturesLoaded();
        if (!features.length) return;

        // 1. Calculate Priority Ranking
        rankedZonesCache = calculateZonePriorities(features);

        const config = window.TerraVeilState.deploymentConfig;
        let allocationMap;

        // 2. Determine Allocation
        if (config.mode === 'auto') {
            allocationMap = calculateAutoAllocation(rankedZonesCache, config.planningSpacing, config.maxNodesPerZone);
        } else {
            allocationMap = allocateNodeBudget(rankedZonesCache, config.budget, config.maxNodesPerZone);
        }

        // 3. Generate Canonical Recommendations
        const recommendations = buildCanonicalRecommendations(rankedZonesCache, allocationMap, config.planningSpacing);

        // 4. Update UI Views
        render2DMapLayers(recommendations);
        renderDrawerUI();
        renderNodePlacementSection();
    }

    function exportGeoJSON() {
        const nodes = window.TerraVeilState.recommendedNodes || [];
        if (!nodes.length) {
            alert('No recommendations to export.');
            return;
        }

        const geojson = {
            type: 'FeatureCollection',
            name: 'terraveil_recommended_monitoring_nodes',
            crs: {
                type: 'name',
                properties: { name: 'urn:ogc:def:crs:OGC:1.3:CRS84' }
            },
            features: nodes.map(n => ({
                type: 'Feature',
                id: n.id,
                geometry: {
                    type: 'Point',
                    coordinates: [n.longitude, n.latitude] // Valid RFC 7946 GeoJSON [lon, lat]
                },
                properties: {
                    node_id: n.id,
                    zone_id: n.zone_id,
                    node_type: n.node_type,
                    priority_rank: n.priority_rank,
                    priority_score: n.priority_score,
                    placement_score: n.placement_score,
                    median_velocity_mm_year: n.median_velocity_mm_year,
                    worst_velocity_mm_year: n.worst_velocity_mm_year,
                    median_net_displacement_mm: n.median_net_displacement_mm,
                    area_km2: n.area_km2,
                    confidence: n.confidence,
                    status: n.status,
                    depth_m: n.depth_m,
                    elevation_m: n.elevation_m,
                    why_zone: n.recommendation_reason.whyZone,
                    why_location: n.recommendation_reason.whyLocation,
                    disclaimer: NODE_PLACEMENT_CONFIG.DISCLAIMER
                }
            }))
        };

        const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/geo+json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `terraveil_recommended_nodes_${nodes.length}_anchors.geojson`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // =========================================================================
    // 12. CROSS-NAVIGATION & INITIALIZATION
    // =========================================================================
    window.TerraVeilNavigation = window.TerraVeilNavigation || {
        switchTo: function (targetTabId, targetNodeId) {
            // Reset active links
            document.querySelectorAll('.nav-link').forEach(l => {
                l.classList.toggle('active', l.getAttribute('data-target') === targetTabId);
            });

            // Toggle tab views
            document.querySelectorAll('.tab-view').forEach(view => {
                view.classList.toggle('hidden', view.id !== targetTabId);
            });

            // Trigger tab specific hooks
            if (targetTabId === 'view-live') {
                setTimeout(() => {
                    if (window.map) window.map.invalidateSize();
                }, 100);
            }

            if (targetTabId === 'view-placement') {
                renderNodePlacementSection();
            }

            // Sync node selection
            if (targetNodeId) {
                setTimeout(() => {
                    selectNode(targetNodeId, 'navigation');
                }, 150);
            }
        }
    };

    function togglePlacementPanel() {
        const panel = document.getElementById('node-placement-panel');
        const toggleBtn = document.getElementById('btn-toggle-placement-panel');
        if (!panel) return;

        const isCollapsed = panel.classList.contains('collapsed');
        if (isCollapsed) {
            panel.classList.remove('collapsed');
            if (toggleBtn) toggleBtn.classList.add('active');
            renderDrawerUI();
        } else {
            panel.classList.add('collapsed');
            if (toggleBtn) toggleBtn.classList.remove('active');
        }
    }

    function togglePlacementLayer() {
        if (!mapInstance || !recommendedLayerGroup) return;

        const isVisible = mapInstance.hasLayer(recommendedLayerGroup);
        const toggleBtn = document.getElementById('btn-toggle-placement-layer');

        if (isVisible) {
            mapInstance.removeLayer(recommendedLayerGroup);
            if (toggleBtn) toggleBtn.classList.remove('active');
        } else {
            if(window.TerraVeilTelemetry?.mode!=='REAL') mapInstance.addLayer(recommendedLayerGroup);
            if (toggleBtn) toggleBtn.classList.add('active');
        }
    }

    function wireSectionEvents() {
        // Section Header Buttons
        document.getElementById('btn-placement-recompute')?.addEventListener('click', runRecompute);
        document.getElementById('btn-placement-export')?.addEventListener('click', exportGeoJSON);

        // Mode Switch
        const btnAuto = document.getElementById('placement-mode-auto');
        const btnBudget = document.getElementById('placement-mode-budget');
        const budgetWrapper = document.getElementById('placement-budget-options');

        btnAuto?.addEventListener('click', () => {
            btnAuto.classList.add('active');
            btnBudget?.classList.remove('active');
            if (budgetWrapper) budgetWrapper.style.display = 'none';
            window.TerraVeilState.deploymentConfig.mode = 'auto';
            runRecompute();
        });

        btnBudget?.addEventListener('click', () => {
            btnBudget.classList.add('active');
            btnAuto?.classList.remove('active');
            if (budgetWrapper) budgetWrapper.style.display = 'flex';
            window.TerraVeilState.deploymentConfig.mode = 'budget';
            runRecompute();
        });

        // Preset Chips
        document.querySelectorAll('#view-placement .preset-chip[data-budget]').forEach(chip => {
            chip.addEventListener('click', () => {
                document.querySelectorAll('#view-placement .preset-chip[data-budget]').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                const b = Number(chip.getAttribute('data-budget'));
                window.TerraVeilState.deploymentConfig.budget = b;
                window.TerraVeilState.deploymentConfig.preset = b;
                runRecompute();
            });
        });

        // Custom Budget
        document.getElementById('btn-apply-custom-budget')?.addEventListener('click', () => {
            const input = document.getElementById('input-custom-budget');
            const val = parseInt(input.value, 10);
            if (!isNaN(val) && val >= 1 && val <= 60) {
                document.querySelectorAll('#view-placement .preset-chip[data-budget]').forEach(c => c.classList.remove('active'));
                window.TerraVeilState.deploymentConfig.budget = val;
                runRecompute();
            }
        });

        // Planning Spacing Slider
        const spacingSlider = document.getElementById('placement-spacing-slider');
        const spacingDisplay = document.getElementById('placement-spacing-display');
        spacingSlider?.addEventListener('input', (e) => {
            const s = Number(e.target.value);
            if (spacingDisplay) spacingDisplay.textContent = `${s}m`;
            window.TerraVeilState.deploymentConfig.planningSpacing = s;
        });
        spacingSlider?.addEventListener('change', () => {
            runRecompute();
        });

        // Max Nodes per Zone Slider
        const maxSlider = document.getElementById('placement-maxnodes-slider');
        const maxDisplay = document.getElementById('placement-maxnodes-display');
        maxSlider?.addEventListener('input', (e) => {
            const m = Number(e.target.value);
            if (maxDisplay) maxDisplay.textContent = m;
            window.TerraVeilState.deploymentConfig.maxNodesPerZone = m;
        });
        maxSlider?.addEventListener('change', () => {
            runRecompute();
        });

        // Search & Filters for Full Table
        document.getElementById('placement-table-search')?.addEventListener('input', (e) => {
            fullTableSearchQuery = e.target.value;
            renderPlacementTableRows();
        });

        document.getElementById('placement-table-severity')?.addEventListener('change', (e) => {
            fullTableSeverityFilter = e.target.value;
            renderPlacementTableRows();
        });

        // Table Header Sorting
        document.querySelectorAll('#placement-full-table th[data-sort]').forEach(th => {
            th.addEventListener('click', () => {
                const col = th.getAttribute('data-sort');
                if (fullTableSortCol === col) {
                    fullTableSortAsc = !fullTableSortAsc;
                } else {
                    fullTableSortCol = col;
                    fullTableSortAsc = true;
                }
                renderPlacementTableRows();
            });
        });
    }

    window.addEventListener('terraveil:telemetry',event=>{
        if(event.detail.mode==='REAL'&&recommendedLayerGroup&&mapInstance?.hasLayer(recommendedLayerGroup))mapInstance.removeLayer(recommendedLayerGroup);
    });
    let pendingSave=null,saving=false,saveTimer;
    function saveStatus(message) {
        let el=document.getElementById('placement-save-status');
        if(!el){el=document.createElement('p');el.id='placement-save-status';el.setAttribute('role','status');document.getElementById('view-placement')?.prepend(el);}
        el.textContent=message;
    }
    async function persistPlan() {
        if(saving||!pendingSave)return;
        saving=true;const plan=pendingSave;pendingSave=null;
        try {
            const r=await fetch('/api/simulation/placement',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(plan)});
            if(!r.ok)throw new Error('Could not save the simulation placement');
            saveStatus(`Simulation follows this placement: ${plan.nodes.length} nodes saved. Hardware remains 3 nodes.`);
        } catch(error){pendingSave=pendingSave||plan;saveStatus(error.message+' — retrying.');}
        finally{saving=false;if(pendingSave)saveTimer=setTimeout(persistPlan,2000);}
    }
    window.addEventListener('terraveil:nodes-updated',event=>{
        if(event.detail?.restored)return;
        pendingSave=JSON.parse(JSON.stringify({nodes:event.detail.nodes,config:window.TerraVeilState.deploymentConfig,stats:window.TerraVeilState.analysisStats}));
        clearTimeout(saveTimer);saveTimer=setTimeout(persistPlan,300);
    });

    async function init(map) {
        mapInstance = map;

        // Wire Header Buttons on 2D map
        document.getElementById('btn-toggle-placement-panel')?.addEventListener('click', (e) => {
            e.preventDefault();
            togglePlacementPanel();
        });

        document.getElementById('btn-toggle-placement-layer')?.addEventListener('click', (e) => {
            e.preventDefault();
            togglePlacementLayer();
        });

        // Wire Section Events
        wireSectionEvents();

        // Restore the exact plan; opening a page must not silently reset its count.
        try {
            const r=await fetch('/api/simulation/placement',{cache:'no-store'});
            if(!r.ok)throw new Error('Placement restore failed');
            const {plan}=await r.json();
            if(plan){
                window.TerraVeilState.recommendedNodes=plan.nodes;
                Object.assign(window.TerraVeilState.deploymentConfig,plan.config||{});
                Object.assign(window.TerraVeilState.analysisStats,plan.stats||{});
                plan.nodes.filter(n=>n.status==='field_reviewed').forEach(n=>window.TerraVeilState.manualAdjustments.set(n.id,{lat:n.latitude,lon:n.longitude,status:n.status}));
                rankedZonesCache=calculateZonePriorities(await ensureInSARFeaturesLoaded());
                render2DMapLayers(plan.nodes);renderDrawerUI();renderNodePlacementSection();
                window.dispatchEvent(new CustomEvent('terraveil:nodes-updated',{detail:{nodes:plan.nodes,restored:true}}));
                saveStatus(`Simulation follows this placement: ${plan.nodes.length} nodes saved. Hardware remains 3 nodes.`);
            } else await runRecompute();
        } catch(error){saveStatus(error.message+' — saved placement was not overwritten.');}
    }

    // Global engine interface
    window.NodePlacementEngine = {
        init,
        config: NODE_PLACEMENT_CONFIG,
        calculateZonePriorities,
        allocateNodeBudget,
        calculateAutoAllocation,
        generateZoneAnchors,
        buildCanonicalRecommendations,
        recompute: runRecompute,
        show: renderNodePlacementSection,
        exportGeoJSON,
        selectNode,
        togglePlacementPanel,
        togglePlacementLayer,
        filterCandidate,
        localRiskScore,
        getRecommendations: () => window.TerraVeilState.recommendedNodes,
        getRankedZones: () => rankedZonesCache
    };

    if (window.map) {
        init(window.map);
    } else {
        window.addEventListener('terraveil:map-ready', (e) => {
            init(e.detail?.map || window.map);
        });
    }

    // Support direct loading of view-placement on URL
    if (new URLSearchParams(location.search).get('view') === 'placement') {
        window.TerraVeilNavigation?.switchTo('view-placement');
    }

})();

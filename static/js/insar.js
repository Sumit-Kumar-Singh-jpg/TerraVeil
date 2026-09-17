import * as THREE from 'three';
import { GroundMotionOverlay } from './ground-motion.js';
let groundMotion;
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const view = $('view-insar'), stage = $('insar-stage');
let manifest, samples, terrain, marker, scene, camera, renderer, controls;
let loading = false, ready = false, active = false, relief = 8, selected = null;
let span = 1, centerEast = 0, centerNorth = 0, floor = 0, pointerStart;
const ray = new THREE.Raycaster();
const palettes = {
    velocity: ['#246c9d', '#e7e8dc', '#c7503c'],
    height: ['#304f57', '#8eac83', '#eee0b5'],
    coherence: ['#34334f', '#489599', '#c9e4b2'],
};

// --- Recommended 3D Nodes Integration ---
let recommendedNodesGroup = null;
const node3DObjects = [];
const node3DLookup = new Map(); // nodeId -> 3D Object

function wgs84ToUtm45N(lat, lon) {
    const a = 6378137.0;
    const f = 1 / 298.257223563;
    const e2 = 2 * f - f * f;
    const k0 = 0.9996;
    const lon0 = 87.0;
    const latRad = lat * Math.PI / 180.0;
    const lonRad = (lon - lon0) * Math.PI / 180.0;
    const N = a / Math.sqrt(1 - e2 * Math.sin(latRad) * Math.sin(latRad));
    const T = Math.tan(latRad) * Math.tan(latRad);
    const C = (e2 / (1 - e2)) * Math.cos(latRad) * Math.cos(latRad);
    const A = Math.cos(latRad) * lonRad;
    const M = a * ((1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256) * latRad
        - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 * e2 * e2 / 1024) * Math.sin(2 * latRad)
        + (15 * e2 * e2 / 256 + 45 * e2 * e2 * e2 / 1024) * Math.sin(4 * latRad)
        - (35 * e2 * e2 * e2 / 3072) * Math.sin(6 * latRad));
    const easting = 500000.0 + k0 * N * (A + (1 - T + C) * Math.pow(A, 3) / 6 + (5 - 18 * T + T * T + 72 * C - 58 * (e2 / (1 - e2))) * Math.pow(A, 5) / 120);
    const northing = k0 * (M + N * Math.tan(latRad) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * Math.pow(A, 4) / 24 + (61 - 58 * T + T * T + 600 * C - 330 * (e2 / (1 - e2))) * Math.pow(A, 6) / 720));
    return { easting, northing };
}
const colors = Object.fromEntries(Object.entries(palettes).map(([k, v]) => [k, v.map(c => new THREE.Color(c))]));
const missing = new THREE.Color('#53616a');
const number = (n, digits = 2) => n.toLocaleString(undefined, {minimumFractionDigits:digits, maximumFractionDigits:digits});
const date = value => /^\d{8}$/.test(value || '') ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6)}` : 'Unavailable';

function activate() {
    document.querySelectorAll('.tab-view').forEach(el => el.classList.toggle('hidden', el !== view));
    document.querySelectorAll('.nav-link').forEach(el => el.classList.toggle('active', el.dataset.target === view.id));
    active = true;
    if (!ready && !loading) initialize();
    if (ready) resize();
}

function message(title, detail, retry = false) {
    const panel = $('insar-loading');
    panel.hidden = false;
    const strong = document.createElement('strong'), text = document.createElement('span');
    strong.textContent = title; text.textContent = detail; panel.replaceChildren(strong, text);
    if (retry) {
        const button = document.createElement('button'); button.textContent = 'Try again';
        button.onclick = initialize; panel.append(button);
    }
}

async function fetchAsset(url) {
    const response = await fetch(url, {signal:AbortSignal.timeout(30000)});
    if (!response.ok) throw new Error(`Dataset request failed (${response.status}).`);
    return response;
}

async function initialize() {
    if (loading || ready) return;
    loading = true;
    if (renderer) disposeScene();
    message('Preparing the terrain', 'Loading the local InSAR dataset…');
    try {
        manifest = await (await fetchAsset('/api/insar/manifest.json')).json();
        const g = manifest.grid;
        if (manifest.version !== 1 || !Number.isInteger(g.rows) || !Number.isInteger(g.columns) ||
            g.rows < 2 || g.columns < 2 || g.rows * g.columns > 2000000 ||
            !/^terrain-[a-f0-9]{16}\.bin$/.test(manifest.asset.filename)) throw new Error('Unsupported dataset manifest.');
        const buffer = await (await fetchAsset(`/api/insar/${manifest.asset.filename}`)).arrayBuffer();
        if (buffer.byteLength !== g.rows * g.columns * 7 * 4 || buffer.byteLength !== manifest.asset.bytes) throw new Error('Incomplete terrain asset.');
        if (crypto.subtle) {
            const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), b => b.toString(16).padStart(2,'0')).join('');
            if (hash !== manifest.asset.sha256) throw new Error('Terrain checksum mismatch. Please rebuild or refresh the dataset.');
        }
        samples = new Float32Array(buffer);
        createScene(); fillMetadata(); updateLayer(); setCamera(false); update3DRecommendedNodes();
        ready = true; stage.dataset.ready = 'true';
        groundMotion = new GroundMotionOverlay(scene, projectNode, surfaceHeight, position => {controls.target.copy(position); camera.position.copy(position).add(new THREE.Vector3(2,2,3)); controls.update();});
        update3DRecommendedNodes();
        inspect(Math.floor(g.rows / 2), Math.floor(g.columns / 2));
        $('insar-loading').hidden = true;
        resize();
    } catch (error) {
        disposeScene();
        message('The terrain could not be loaded', `${error.message} Check that the prepared InSAR assets are available and WebGL is enabled.`, true);
    } finally { loading = false; }
}

function createScene() {
    const g = manifest.grid, s = manifest.stats, count = g.rows*g.columns;
    centerEast = g.x_first + (g.columns-1)*g.x_step/2;
    centerNorth = g.y_first + (g.rows-1)*g.y_step/2;
    span = Math.max((g.columns-1)*g.x_step, -(g.rows-1)*g.y_step)/1000;
    floor = Math.floor(s.elevation_min/10)*10;
    scene = new THREE.Scene(); scene.background = new THREE.Color('#101923');
    camera = new THREE.PerspectiveCamera(42, 1, .01, span*20);
    renderer = new THREE.WebGLRenderer({antialias:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.setAttribute('aria-label', 'Jharia InSAR 3D terrain');
    stage.prepend(renderer.domElement);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.minDistance = .3; controls.maxDistance = span*4;
    controls.maxPolarAngle = Math.PI*.49;
    scene.add(new THREE.HemisphereLight('#e5f2fa', '#4c5962', .7));
    const sun = new THREE.DirectionalLight('#ffffff', .65); sun.position.set(-span, span*2, span); scene.add(sun);
    const position = new Float32Array(count*3), rgb = new Float32Array(count*3), indices = [];
    for (let r=0; r<g.rows; r++) for (let c=0; c<g.columns; c++) {
        const i = r*g.columns+c;
        position[i*3] = (g.x_first+c*g.x_step-centerEast)/1000;
        position[i*3+1] = (samples[i*7]-floor)/1000*relief;
        position[i*3+2] = -(g.y_first+r*g.y_step-centerNorth)/1000;
        if (r<g.rows-1 && c<g.columns-1) {
            const a=i, b=i+1, d=i+g.columns, e=d+1;
            // Keep holes in the DEM; do not bridge invalid terrain cells.
            if ([a,b,d].every(j => samples[j*7+6]&1)) indices.push(a,d,b);
            if ([b,d,e].every(j => samples[j*7+6]&1)) indices.push(b,d,e);
        }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(position,3));
    geometry.setAttribute('color', new THREE.BufferAttribute(rgb,3));
    geometry.setIndex(indices); geometry.computeVertexNormals(); geometry.computeBoundingSphere();
    terrain = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({vertexColors:true, side:THREE.DoubleSide}));
    scene.add(terrain);
    const grid = new THREE.GridHelper(Math.ceil(span/5)*5+10, Math.ceil(span/5)+2, '#385165', '#223544');
    grid.position.y = -.1; scene.add(grid);
    marker = new THREE.Mesh(new THREE.SphereGeometry(.16,16,10), new THREE.MeshBasicMaterial({color:'#ffffff',depthTest:false}));
    marker.renderOrder = 5; scene.add(marker);
    recommendedNodesGroup = new THREE.Group();
    recommendedNodesGroup.name = 'Recommended Monitoring Nodes 3D Surface Projections';
    scene.add(recommendedNodesGroup);
    const northZ = -(g.y_first-centerNorth)/1000;
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(0,0,-1), new THREE.Vector3(span*.54,0,northZ+5), 4, '#86b8cf', .6,.45); scene.add(arrow);
    label('N', span*.54, .5, northZ, 2.5);
    label('5 km grid', -span*.45, 0, -northZ+2, 4);
    renderer.domElement.addEventListener('pointerdown', event => {pointerStart = [event.clientX,event.clientY];});
    renderer.domElement.addEventListener('pointerup', event => {
        if (event.button !== 0 || !pointerStart || Math.hypot(event.clientX-pointerStart[0],event.clientY-pointerStart[1])>5) return;
        const rect = renderer.domElement.getBoundingClientRect();
        ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1, -(event.clientY-rect.top)/rect.height*2+1), camera);
        
        const liveId = groundMotion?.hit(ray);
        if (liveId) {groundMotion.select(liveId); return;}
        // 1. Raycast recommended 3D node markers first
        const nodeHits = ray.intersectObjects(node3DObjects, true);
        if (nodeHits.length > 0) {
            const hitObj = nodeHits.find(h => h.object.userData.nodeId);
            if (hitObj) {
                const nid = hitObj.object.userData.nodeId;
                window.NodePlacementEngine?.selectNode(nid, '3d_map');
                highlight3DNode(nid);
                return;
            }
        }

        // 2. Raycast terrain surface
        const hit = ray.intersectObject(terrain)[0];
        if (!hit) return;
        const c = Math.round((hit.point.x*1000+centerEast-g.x_first)/g.x_step);
        const r = Math.round((-hit.point.z*1000+centerNorth-g.y_first)/g.y_step);
        inspect(r,c);
    });
    renderer.domElement.addEventListener('webglcontextlost', event => {
        event.preventDefault(); ready=false; stage.dataset.ready='false';
        message('Graphics paused', 'The browser lost its graphics context. Reload this page to restore the map.');
    });
    renderer.setAnimationLoop(() => {
        if (!active || !ready || document.hidden) return;
        groundMotion?.tick(performance.now()/1000);
        controls.update(); renderer.render(scene,camera);
    });
    stage.dataset.triangles = String(indices.length/3);
}

// Historical DEM heights only; live effects are drawn in a separate mesh.
function surfaceHeight(x,z) {
    const g=manifest.grid;
    const col=Math.round((x*1000+centerEast-g.x_first)/g.x_step);
    const row=Math.round((-z*1000+centerNorth-g.y_first)/g.y_step);
    if(row<0||col<0||row>=g.rows||col>=g.columns)return null;
    const i=(row*g.columns+col)*7;
    return samples[i+6]&1 ? (samples[i]-floor)/1000*relief : null;
}
function projectNode(node) {
    if(!Number.isFinite(node.latitude)||!Number.isFinite(node.longitude))return null;
    const p=wgs84ToUtm45N(node.latitude,node.longitude);
    const x=(p.easting-centerEast)/1000,z=-(p.northing-centerNorth)/1000,y=surfaceHeight(x,z);
    return y===null?null:new THREE.Vector3(x,y,z);
}

function update3DRecommendedNodes() {
    if (!scene || !ready) return;
    if (!recommendedNodesGroup) {
        recommendedNodesGroup = new THREE.Group();
        scene.add(recommendedNodesGroup);
    }

    while (recommendedNodesGroup.children.length > 0) {
        const obj = recommendedNodesGroup.children[0];
        recommendedNodesGroup.remove(obj);
        obj.geometry?.dispose();
        obj.material?.dispose();
    }
    node3DObjects.length = 0;
    node3DLookup.clear();

    const live=window.TerraVeilTelemetry;
    const activeIds=new Set(live?.nodes.map(n=>n.node_id)||[]);
    const nodes = live?.mode==='REAL'?[]:(window.TerraVeilState?.recommendedNodes || []).filter(n=>!activeIds.has(n.id));
    const g = manifest.grid;

    nodes.forEach(node => {
        const { easting, northing } = wgs84ToUtm45N(node.latitude, node.longitude);
        const col = Math.round((easting - g.x_first) / g.x_step);
        const row = Math.round((northing - g.y_first) / g.y_step);

        let elev = floor;
        if (row >= 0 && row < g.rows && col >= 0 && col < g.columns) {
            const idx = (row * g.columns + col) * 7;
            if (samples[idx + 6] & 1) {
                elev = samples[idx];
            }
        }

        const posX = (easting - centerEast) / 1000;
        const posY = (elev - floor) / 1000 * relief + 0.20;
        const posZ = -(northing - centerNorth) / 1000;

        const isSelected = window.TerraVeilState?.selectedNodeId === node.id;
        const isReviewed = node.status === 'field_reviewed';
        const color = isSelected ? '#ffffff' : (isReviewed ? '#8b5cf6' : '#06b6d4');

        const geom = new THREE.OctahedronGeometry(0.25, 0);
        const mat = new THREE.MeshStandardMaterial({
            color: color,
            emissive: color,
            emissiveIntensity: isSelected ? 0.9 : 0.45,
            roughness: 0.25,
            metalness: 0.3
        });
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(posX, posY, posZ);
        mesh.userData.nodeId = node.id;
        mesh.userData.elev = elev;
        mesh.userData.node = node;

        recommendedNodesGroup.add(mesh);
        node3DObjects.push(mesh);
        node3DLookup.set(node.id, mesh);
    });
}

function highlight3DNode(nodeId) {
    node3DObjects.forEach(mesh => {
        const isMatch = mesh.userData.nodeId === nodeId;
        const isReviewed = mesh.userData.node?.status === 'field_reviewed';
        const color = isMatch ? '#ffffff' : (isReviewed ? '#8b5cf6' : '#06b6d4');
        mesh.material.color.set(color);
        mesh.material.emissive.set(color);
        mesh.material.emissiveIntensity = isMatch ? 1.0 : 0.45;
        mesh.scale.setScalar(isMatch ? 1.6 : 1.0);

        if (isMatch && controls) {
            controls.target.copy(mesh.position);
        }
    });
}

function label(text, x, y, z, width) {
    const canvas=document.createElement('canvas'); canvas.width=256; canvas.height=64;
    const ctx=canvas.getContext('2d');ctx.fillStyle='#a7c4d4';ctx.font='28px sans-serif';ctx.textAlign='center';ctx.fillText(text,128,42);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false}));sprite.position.set(x,y,z);sprite.scale.set(width,width/4,1);scene.add(sprite);
}

function fillMetadata() {
    const g=manifest.grid,s=manifest.stats;
    $('insar-period').textContent=`${date(manifest.period.start)} → ${date(manifest.period.end)}`;
    $('insar-resolution').textContent=`${number(g.x_step,0)} × ${number(-g.y_step,0)} m`;
    $('insar-coverage').textContent=`${number(s.velocity_samples,0)} / ${number(s.total_samples,0)}`;
    $('insar-median').textContent=`${number(s.velocity_median,1)} mm/year`;
    $('insar-crs').textContent=`EPSG:${g.epsg} · projected metres`;
    $('insar-grid-size').textContent=`${g.columns} × ${g.rows} samples`;
    $('insar-row').max=g.rows-1;$('insar-col').max=g.columns-1;
    $('insar-range').textContent=`Full observed range: ${number(s.velocity_min,2)} to ${number(s.velocity_max,2)} mm/year. Source rates are converted from ${manifest.units.velocity_source}; no temporal interpolation is applied.`;
    $('insar-warnings').replaceChildren(...manifest.warnings.slice(1).map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    $('insar-provenance-text').textContent=`HyP3 / MintPy products · reference date ${date(manifest.period.reference_date)}. Full source grid retained; no terrain resampling. ${g.coordinate_convention} Velocity NoData: ${manifest.masking.velocity_nodata ?? 'not specified'}. ${manifest.masking.velocity_rule}. Prepared ${manifest.generated_at.slice(0,10)}.`;
    $('insar-files').replaceChildren(...manifest.sources.map(source=>{const li=document.createElement('li');li.textContent=`${source.file} · ${source.dataset} · SHA-256 ${source.sha256.slice(0,16)}…`;return li;}));
}

function updateLayer() {
    if (!terrain) return;
    const mode=$('insar-layer').value,s=manifest.stats;
    const range=mode==='velocity'?[-s.color_limit_mm_year,s.color_limit_mm_year]:mode==='height'?[s.elevation_min,s.elevation_max]:[0,1];
    const channel=mode==='velocity'?1:mode==='height'?0:2, flag=mode==='velocity'?2:mode==='height'?1:4;
    const attribute=terrain.geometry.attributes.color, color=new THREE.Color();
    for(let i=0;i<attribute.count;i++){
        if(!(samples[i*7+6]&flag)) color.copy(missing);
        else {
            const t=THREE.MathUtils.clamp((samples[i*7+channel]-range[0])/(range[1]-range[0]||1),0,1)*2;
            color.copy(colors[mode][t<=1?0:1]).lerp(colors[mode][t<=1?1:2],t<=1?t:t-1);
        }
        attribute.setXYZ(i,color.r,color.g,color.b);
    }
    attribute.needsUpdate=true;
    $('insar-legend-title').textContent=mode==='velocity'?'LOS velocity · mm/year':mode==='height'?'Terrain elevation · m':'Temporal coherence · 0–1';
    $('insar-gradient').style.background=`linear-gradient(90deg,${palettes[mode].join(',')})`;
    $('insar-low').textContent=number(range[0],1);$('insar-high').textContent=number(range[1],1);$('insar-mid').textContent=number((range[0]+range[1])/2,1);
    $('insar-color-note').textContent=mode==='velocity'?'Colour saturates at the 98th percentile of |LOS|. Point values retain extremes.':mode==='coherence'?'Uniform positive values do not imply perfect reliability.':'DEM height; no velocity-driven displacement.';
    stage.dataset.layer=mode;
}

function inspect(row,col) {
    if(!ready || !Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0||row>=manifest.grid.rows||col>=manifest.grid.columns)return;
    selected=row*manifest.grid.columns+col;const i=selected*7,flags=samples[i+6],g=manifest.grid;
    $('insar-row').value=row;$('insar-col').value=col;
    $('insar-point-status').textContent=`Row ${row}, column ${col} · ${flags&2?'valid LOS sample':'no valid LOS value'}`;
    for(const [id,ch,flag,unit,digits] of [['velocity',1,2,' mm/year',2],['std',5,16,' mm/year',2],['height',0,1,' m',2],['coherence',2,4,'',3]]){
        $('insar-point-'+id).textContent=flags&flag?number(samples[i+ch],digits)+unit:'Unavailable';
    }
    $('insar-point-lat').textContent=number(samples[i+4],5)+'°';$('insar-point-lon').textContent=number(samples[i+3],5)+'°';
    $('insar-point-east').textContent=number(g.x_first+col*g.x_step,0)+' m';$('insar-point-north').textContent=number(g.y_first+row*g.y_step,0)+' m';
    marker.visible=Boolean(flags&1);
    marker.position.fromBufferAttribute(terrain.geometry.attributes.position,selected);marker.position.y+=.12;
    stage.dataset.selected=String(selected);
}

function updateRelief() {
    relief=Number($('insar-relief').value);$('insar-relief-value').value=`${relief}×`;$('insar-relief-caption').textContent=`Relief ${relief}×`;
    if(!terrain)return;
    const position=terrain.geometry.attributes.position;
    for(let i=0;i<position.count;i++)position.setY(i,(samples[i*7]-floor)/1000*relief);
    position.needsUpdate=true;terrain.geometry.computeVertexNormals();terrain.geometry.computeBoundingSphere();
    node3DObjects.forEach(m => { m.position.y = (m.userData.elev - floor) / 1000 * relief + 0.20; });
    groundMotion?.reproject();
    if(selected!==null)inspect(Math.floor(selected/manifest.grid.columns),selected%manifest.grid.columns);
}

function setCamera(top) {
    if(!camera)return;
    controls.target.set(0,((manifest.stats.elevation_min+manifest.stats.elevation_max)/2-floor)/1000*relief,0);
    // Portrait canvases need more distance to fit the terrain's horizontal extent.
    const fit=Math.max(1,1/(stage.clientWidth/stage.clientHeight));
    const distance=span*1.25*fit;
    camera.position.copy(controls.target).add(new THREE.Vector3(top?0:distance*.52,top?distance:distance*.72,top?.001:distance*.8));
    controls.update();
}

function resize() {
    if(!renderer||!active||!stage.clientWidth)return;
    renderer.setSize(stage.clientWidth,stage.clientHeight,false);camera.aspect=stage.clientWidth/stage.clientHeight;camera.updateProjectionMatrix();
}

function disposeScene() {
    groundMotion?.dispose(); groundMotion=null;
    controls?.dispose();renderer?.setAnimationLoop(null);
    scene?.traverse(object=>{object.geometry?.dispose();if(object.material){object.material.map?.dispose();object.material.dispose();}});
    renderer?.dispose();renderer?.domElement.remove();renderer=null;terrain=null;
}

document.querySelector('[data-target="view-insar"]').addEventListener('click',event=>{event.preventDefault();activate();});
new MutationObserver(()=>{active=!view.classList.contains('hidden');if(active&&ready)resize();}).observe(view,{attributes:true,attributeFilter:['class']});
new ResizeObserver(resize).observe(stage);
$('insar-layer').addEventListener('change',updateLayer);
$('insar-relief').addEventListener('input',updateRelief);
$('insar-shade').addEventListener('change',()=>{if(!terrain)return;terrain.material.dispose();terrain.material=$('insar-shade').checked?new THREE.MeshLambertMaterial({vertexColors:true,side:THREE.DoubleSide}):new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide});});
$('insar-oblique').onclick=()=>setCamera(false);$('insar-top').onclick=()=>setCamera(true);$('insar-reset').onclick=()=>setCamera(false);
$('insar-pixel-form').addEventListener('submit',event=>{event.preventDefault();inspect(Number($('insar-row').value),Number($('insar-col').value));});
if(new URLSearchParams(location.search).get('view')==='insar')activate();

// Global Node Selection & Update Synchronization
window.addEventListener('terraveil:nodes-updated', () => {
    if (ready) update3DRecommendedNodes();
});
window.addEventListener('terraveil:node-selected', (e) => {
    if (ready) highlight3DNode(e.detail?.nodeId);
});

let placementVisibilityKey='';
window.addEventListener('terraveil:telemetry',e=>{
    const key=e.detail.mode+'|'+e.detail.nodes.map(n=>n.node_id).join('|');
    if(key!==placementVisibilityKey){placementVisibilityKey=key;if(ready)update3DRecommendedNodes();}
});

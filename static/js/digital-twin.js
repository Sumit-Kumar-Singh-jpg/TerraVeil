import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const COLORS = { LOW: '#34b88c', MEDIUM: '#f6b34b', HIGH: '#ed665a', CRITICAL: '#ff493e', OFFLINE: '#73869b' };
const clamp = (x,a=0,b=1) => Math.max(a,Math.min(b,x));
const smooth = x => { x=clamp(x); return x*x*(3-2*x); };
const state = { ready:false, active:false, source:'host', snapshot:null, host:null, selected:null, demo:null, time:0, playing:false, gain:25, tour:null, lastPoll:0, pollBusy:false, failed:false };
let scene,camera,renderer,controls,manifest,forest,templates={},terrainMeshes=[],riskMeshes=[],cutMesh;
const nodeObjects=new Map(),sirenObjects=new Map(),raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();
const cutCanvas=document.createElement('canvas');cutCanvas.width=cutCanvas.height=2048;
const cutContext=cutCanvas.getContext('2d');const cutTexture=new THREE.CanvasTexture(cutCanvas);
cutTexture.minFilter=THREE.LinearFilter;cutTexture.magFilter=THREE.LinearFilter;cutTexture.generateMipmaps=false;
const riskCanvas=document.createElement('canvas');riskCanvas.width=riskCanvas.height=192;
const riskContext=riskCanvas.getContext('2d');const riskTexture=new THREE.CanvasTexture(riskCanvas);riskTexture.colorSpace=THREE.SRGBColorSpace;
const bounds={xmin:-34.5,xmax:109.5,zmin:23.333333,zmax:110};

function setLink(text,error=false){$('twin-link').textContent=text;$('twin-link').dataset.status=error?'error':'ok';}
async function json(url){const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(`Request failed (${response.status})`);return response.json();}

function activate(){
    document.querySelectorAll('.tab-view').forEach(el=>el.classList.toggle('hidden',el.id!=='view-twin'));
    document.querySelectorAll('.nav-link').forEach(el=>el.classList.toggle('active',el.dataset.target==='view-twin'));
    state.active=true;
    if(!state.ready&&!state.failed)initialize();
    else resize();
}
document.querySelector('[data-target="view-twin"]').addEventListener('click',event=>{event.preventDefault();activate();});
new MutationObserver(()=>{state.active=!$('view-twin').classList.contains('hidden');if(state.active&&state.ready)resize();}).observe($('view-twin'),{attributes:true,attributeFilter:['class']});
if(new URLSearchParams(location.search).get('view')==='twin')activate();

async function initialize(){
    if(state.loading)return;state.loading=true;
    try{
        scene=new THREE.Scene();scene.background=new THREE.Color('#192a32');scene.fog=new THREE.Fog('#192a32',550,1400);
        camera=new THREE.PerspectiveCamera(43,1,.1,2500);
        renderer=new THREE.WebGLRenderer({canvas:$('twin-canvas'),antialias:true,alpha:false,powerPreference:'high-performance'});
        renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.outputColorSpace=THREE.SRGBColorSpace;
        renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
        renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
        scene.add(new THREE.HemisphereLight('#e3f0ff','#5b5340',2.1));
        const sun=new THREE.DirectionalLight('#ffe1b2',3.2);sun.position.set(-160,260,140);sun.castShadow=true;
        sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-220,right:220,top:220,bottom:-220,near:1,far:700});sun.shadow.bias=-.0005;scene.add(sun);
        controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.07;controls.maxDistance=1000;controls.minDistance=2;controls.maxPolarAngle=Math.PI*.87;
        controls.addEventListener('start',()=>stopTour());cameraView('overview',false);
        new ResizeObserver(resize).observe($('twin-stage'));resize();
        const [data,asset]=await Promise.all([json('/api/twin/assets/scene.json'),new GLTFLoader().loadAsync('/api/twin/assets/mine.glb')]);
        manifest=data;scene.add(asset.scene);asset.scene.updateMatrixWorld(true);
        for(const child of asset.scene.children){
            if(child.userData.role==='tree_template'||child.userData.role==='hardware_template'){templates[child.name]=child;child.visible=false;}
        }
        forest=new THREE.Group();forest.name='Instanced forest';scene.add(forest);
        for(const group of manifest.trees){
            const template=templates[group.template];
            template.traverse(part=>{
                if(!part.isMesh)return;
                const instances=new THREE.InstancedMesh(part.geometry,part.material,group.matrices.length);
                const matrix=new THREE.Matrix4();for(let i=0;i<group.matrices.length;i++){matrix.fromArray(group.matrices[i]);instances.setMatrixAt(i,matrix);}
                instances.castShadow=true;instances.receiveShadow=true;instances.computeBoundingSphere();forest.add(instances);
            });
        }
        asset.scene.traverse(mesh=>{
            if(!mesh.isMesh)return;
            mesh.receiveShadow=true;mesh.castShadow=true;
            if(mesh.userData.role==='terrain'){
                mesh.geometry=mesh.geometry.clone();mesh.material=mesh.material.clone();mesh.userData.base=mesh.geometry.attributes.position.array.slice();terrainMeshes.push(mesh);
                addCutShader(mesh.material);
                const overlay=new THREE.Mesh(mesh.geometry.clone(),new THREE.MeshBasicMaterial({map:riskTexture,transparent:true,depthWrite:false,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2}));
                const uv=[];const base=mesh.userData.base;for(let i=0;i<base.length;i+=3)uv.push((base[i]+34.5)/144,1-(base[i+2]-23.333333)/86.666667);overlay.geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
                overlay.position.y=.018;overlay.renderOrder=3;addCutShader(overlay.material);mesh.parent.add(overlay);riskMeshes.push(overlay);
            }
        });
        cutMesh=new THREE.Mesh(new THREE.BufferGeometry(),new THREE.MeshStandardMaterial({color:'#302920',roughness:1,side:THREE.DoubleSide}));scene.add(cutMesh);
        renderer.domElement.addEventListener('click',pickNode);
        state.ready=true;$('twin-loading').classList.add('hidden');$('twin-stage').dataset.ready='true';
        await pollHost();requestAnimationFrame(animate);
    }catch(error){state.failed=true;setLink('Viewer unavailable',true);$('twin-load-detail').textContent=`${error.message}. Reload to retry. The monitoring dashboard remains available.`;$('twin-loading').querySelector('.twin-spinner').style.display='none';console.error('Digital twin:',error);}
}

function resize(){if(!renderer)return;const width=$('twin-stage').clientWidth,height=$('twin-stage').clientHeight;if(!width||!height)return;renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}
function cameraView(name,stop=true){
    if(!camera)return;if(stop)stopTour();
    const views={overview:[[190,250,440],[10,35,0]],surface:[[100,146,193],[34,54,68]],underground:[[105,74,257],[20,4,15]]};
    const [position,target]=views[name];camera.position.fromArray(position);controls.target.fromArray(target);controls.update();
    document.querySelectorAll('[data-twin-camera]').forEach(button=>button.classList.toggle('selected',button.dataset.twinCamera===name));
}
function stopTour(){state.tour=null;$('twin-tour').textContent='Play camera tour';}
function startTour(){state.tour=performance.now();$('twin-tour').textContent='Stop camera tour';}

function addCutShader(material){
    material.onBeforeCompile=shader=>{
        shader.uniforms.twinCut={value:cutTexture};
        shader.vertexShader='varying vec3 twinWorld;\n'+shader.vertexShader;
        shader.vertexShader=shader.vertexShader.replace('#include <project_vertex>','#include <project_vertex>\ntwinWorld=(modelMatrix*vec4(transformed,1.0)).xyz;');
        shader.fragmentShader='uniform sampler2D twinCut; varying vec3 twinWorld;\n'+shader.fragmentShader;
        shader.fragmentShader=shader.fragmentShader.replace('#include <alphatest_fragment>','#include <alphatest_fragment>\nvec2 cutUV=vec2((twinWorld.x+34.5)/144.0,1.0-(twinWorld.z-23.333333)/86.666667);\nif(all(greaterThanEqual(cutUV,vec2(0.0)))&&all(lessThanEqual(cutUV,vec2(1.0)))&&texture2D(twinCut,cutUV).r>.5)discard;');
    };material.customProgramCacheKey=()=> 'terraveil-cut-v1';
}

function ground(x,z){
    const grid=manifest.terrain,nx=grid.xs.length,nz=grid.zs.length;
    const u=clamp((x-grid.xs[0])/(grid.xs[nx-1]-grid.xs[0]))*(nx-1),v=clamp((z-grid.zs[0])/(grid.zs[nz-1]-grid.zs[0]))*(nz-1);
    const ix=Math.min(nx-2,Math.floor(u)),iz=Math.min(nz-2,Math.floor(v)),fx=u-ix,fz=v-iz;
    const a=grid.heights[iz*nx+ix],b=grid.heights[iz*nx+ix+1],c=grid.heights[(iz+1)*nx+ix],d=grid.heights[(iz+1)*nx+ix+1];return (a*(1-fx)+b*fx)*(1-fz)+(c*(1-fx)+d*fx)*fz;
}
function settlement(x,z,level){
    const phase=1+clamp((level-.15)/.35)+clamp((level-.5)/.2)+clamp((level-.7)/.15)+clamp((level-.85)/.15);
    const fade=clamp(Math.min((x+34.5)/8,(109.5-x)/8,(110-z)/8,(z-23.333333)/8));
    return [[.08,20,14],[.32,26,18],[.95,32,23],[1.8,40,28]].reduce((sum,[depth,rx,rz],i)=>sum+depth*Math.exp(-(((x-36)/rx)**2+((z-66)/rz)**2))*fade*(i===3?clamp(phase-4):Math.max(0,1-Math.abs(phase-(i+2)))),0);
}
function surface(x,z){return ground(x,z)-settlement(x,z,state.snapshot?.deformation.subsidence_level||0);}

function paintRisk(snapshot){
    const size=riskCanvas.width,image=riskContext.createImageData(size,size);const lookup=new Map(snapshot.nodes.map(n=>[n.node_id,n]));
    for(let row=0;row<size;row++)for(let col=0;col<size;col++){
        const x=bounds.xmin+(col+.5)/size*144,z=bounds.zmin+(row+.5)/size*86.666667;let field=0,high=false;
        for(const zone of snapshot.zones){
            let sum=0;for(const id of zone.node_ids){const n=lookup.get(id);if(!n)continue;const dx=(x-n.position[0])/9,dz=(z-n.position[2])/7;sum+=Math.exp(-(dx*dx+dz*dz))*(n.risk_score/100);}
            sum*=zone.correlation*(1+.22*Math.sin(x*.66+Math.sin(z*.42)*2)+.16*Math.cos(z*.54+x*.21));
            if(sum>field){field=sum;high=zone.risk_level==='HIGH'||zone.risk_level==='CRITICAL';}
        }
        const index=(row*size+col)*4;if(field>.28){image.data[index]=high?237:246;image.data[index+1]=high?102:179;image.data[index+2]=high?90:75;image.data[index+3]=Math.abs(field-.30)<.035?155:Math.round(55+clamp((field-.28)*.8)*55);}
    }
    riskContext.putImageData(image,0,0);riskTexture.needsUpdate=true;
}
function buildCracks(snapshot){
    cutContext.fillStyle='black';cutContext.fillRect(0,0,2048,2048);const positions=[],colors=[];
    const progress=snapshot.deformation.crack_progress||0,width=(snapshot.deformation.displacement_mm||0)*state.gain/1000;
    const uv=([x,z])=>[(x-bounds.xmin)/144*2048,(z-bounds.zmin)/86.666667*2048];
    function tri(a,b,c){positions.push(...a,...b,...c);}
    if(width>0)for(const path of manifest.fractures){
        const growth=smooth((progress-path.onset)/(path.end-path.onset));if(growth<=0)continue;
        const max=(path.points.length-1)*growth;
        for(let i=0;i<Math.ceil(max);i++){
            const a=path.points[i],raw=path.points[i+1];if(!raw)break;const f=Math.min(1,max-i),b=[a[0]+(raw[0]-a[0])*f,a[1]+(raw[1]-a[1])*f];
            const dx=b[0]-a[0],dz=b[1]-a[1],length=Math.hypot(dx,dz);if(length<1e-7)continue;
            const age=smooth((max-i)/Math.max(1,path.points.length*.08));const w=width*path.width_factor*age/2;
            const nx=-dz/length*w,nz=dx/length*w;
            const leftA=[a[0]+nx,surface(a[0]+nx,a[1]+nz)-.006,a[1]+nz],rightA=[a[0]-nx,surface(a[0]-nx,a[1]-nz)-.006,a[1]-nz];
            const leftB=[b[0]+nx,surface(b[0]+nx,b[1]+nz)-.006,b[1]+nz],rightB=[b[0]-nx,surface(b[0]-nx,b[1]-nz)-.006,b[1]-nz];
            const depth=.04+width*2.6,down=p=>[p[0],p[1]-depth,p[2]];
            tri(leftA,down(leftA),leftB);tri(leftB,down(leftA),down(leftB));tri(rightA,rightB,down(rightA));tri(rightB,down(rightB),down(rightA));tri(down(leftA),down(rightA),down(leftB));tri(down(leftB),down(rightA),down(rightB));
            const polygon=[leftA,rightA,rightB,leftB].map(p=>uv([p[0],p[2]]));cutContext.beginPath();cutContext.moveTo(...polygon[0]);polygon.slice(1).forEach(p=>cutContext.lineTo(...p));cutContext.closePath();cutContext.fillStyle='white';cutContext.fill();
        }
    }
    cutTexture.needsUpdate=true;const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.computeVertexNormals();cutMesh.geometry.dispose();cutMesh.geometry=geometry;
    $('twin-stage').dataset.crackVertices=String(positions.length/3);
}

function makeLabel(id,color){
    const canvas=document.createElement('canvas');canvas.width=256;canvas.height=88;const context=canvas.getContext('2d');
    context.fillStyle='#122029e8';context.beginPath();context.roundRect(4,4,248,64,12);context.fill();context.strokeStyle=color;context.lineWidth=3;context.stroke();
    context.fillStyle=color;context.font='600 30px monospace';context.textAlign='center';context.fillText(id,128,47);
    context.beginPath();context.moveTo(118,68);context.lineTo(128,85);context.lineTo(138,68);context.fill();
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
    return new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false,transparent:true}));
}
function hardware(templateName){
    const root=templates[templateName].clone(true);root.visible=true;
    root.traverse(part=>{if(part.isMesh){part.castShadow=true;part.receiveShadow=true;if(['led','beacon','battery','link'].includes(part.userData.role)){part.material=part.material.clone();part.material.vertexColors=false;}}});
    scene.add(root);return root;
}
function updateNodes(snapshot){
    const ids=new Set(snapshot.nodes.map(n=>n.node_id));
    for(const [id,item] of nodeObjects)if(!ids.has(id)){scene.remove(item.root,item.label);item.label.material.map.dispose();item.label.material.dispose();nodeObjects.delete(id);}
    for(const node of snapshot.nodes){
        const color=node.status==='ONLINE'?COLORS[node.risk_level]:COLORS.OFFLINE;let item=nodeObjects.get(node.node_id);
        if(!item){const root=hardware(node.node_type==='crack'?'CRACK_TEMPLATE':'GROUND_TEMPLATE');const label=makeLabel(node.node_id,color);label.scale.set(6,2.06,1);scene.add(label);item={root,label,color};nodeObjects.set(node.node_id,item);}
        if(item.color!==color){const label=makeLabel(node.node_id,color);item.label.material.map.dispose();item.label.material.map=label.material.map;label.material.dispose();item.color=color;}
        const [x,,z]=node.position;item.root.position.set(x,surface(x,z),z);item.root.rotation.set(THREE.MathUtils.degToRad(node.tilt_x||0),0,-THREE.MathUtils.degToRad(node.tilt_y||0));
        item.label.position.set(x,surface(x,z)+4.1,z);item.label.visible=$('twin-markers').checked;item.label.userData.nodeId=node.node_id;
        item.root.traverse(part=>{part.userData.nodeId=node.node_id;if(!part.isMesh)return;const role=part.userData.role;
            if(role==='led'){part.material.color.set(color);part.material.emissive.set(color);part.material.emissiveIntensity=.9;}
            if(role==='battery')part.material.color.setRGB(1-node.battery/100,node.battery/100,.03);
            if(role==='link')part.material.color.set(node.status==='ONLINE'?'#498eea':'#657689');
            if(role==='anchor_b')part.position.set(.564643*(node.displacement_mm||0)*state.gain/1000,0,.825335*(node.displacement_mm||0)*state.gain/1000);
        });
    }
    const select=$('twin-node-select');if(Array.from(select.options).map(o=>o.value).join('|')!==snapshot.nodes.map(n=>n.node_id).join('|')){
        select.replaceChildren(...snapshot.nodes.map(node=>new Option(node.node_id,node.node_id)));
    }
    if(!ids.has(state.selected))state.selected=snapshot.nodes.find(n=>n.node_type==='crack')?.node_id||snapshot.nodes[0]?.node_id||null;
    select.value=state.selected||'';
}
const sirenLocations=[[-95,81],[-6,-29],[76,32],[-55,-75],[83,-84],[-95,-22],[44,10],[15,-17]];
function updateSirens(snapshot){
    const ids=new Set(snapshot.sirens.map(n=>n.siren_id));for(const [id,item]of sirenObjects)if(!ids.has(id)){scene.remove(item.root);sirenObjects.delete(id);}
    snapshot.sirens.forEach((siren,index)=>{let item=sirenObjects.get(siren.siren_id);if(!item){const root=hardware('SIREN_TEMPLATE');const [x,y]=sirenLocations[index%sirenLocations.length];root.position.set(x,ground(x,-y),-y);item={root};sirenObjects.set(siren.siren_id,item);}item.active=siren.state==='ACTIVE';});
    const count=snapshot.sirens.filter(s=>s.state==='ACTIVE').length;$('twin-siren-status').textContent=count?`${count} / ${snapshot.sirens.length} active`:'All sirens in standby';$('twin-siren-status').style.color=count?COLORS.HIGH:'';
    $('twin-siren-list').replaceChildren(...snapshot.sirens.map(s=>{const chip=document.createElement('span');chip.className='twin-siren-chip'+(s.state==='ACTIVE'?' active':'');chip.textContent=s.siren_id;chip.title=s.name||s.siren_id;return chip;}));
}

function applySnapshot(snapshot){
    if(!state.ready)return;state.snapshot=snapshot;
    const level=snapshot.deformation.subsidence_level||0;
    terrainMeshes.forEach((mesh,index)=>{
        const position=mesh.geometry.attributes.position,base=mesh.userData.base;
        for(let i=0;i<position.count;i++)position.setY(i,base[i*3+1]-settlement(base[i*3],base[i*3+2],level));
        position.needsUpdate=true;mesh.geometry.computeVertexNormals();
        riskMeshes[index].geometry.attributes.position.array.set(position.array);riskMeshes[index].geometry.attributes.position.needsUpdate=true;
    });
    paintRisk(snapshot);buildCracks(snapshot);updateNodes(snapshot);updateSirens(snapshot);showSelected();
    $('twin-system-state').textContent=snapshot.system_state.replaceAll('_',' ');$('twin-stage').dataset.systemState=snapshot.system_state;
    $('twin-scene-metrics').textContent=`${snapshot.nodes.length} nodes · ${snapshot.zones.length} correlated regions`;$('twin-node-count').textContent=`${snapshot.nodes.length} nodes`;
}
function showSelected(){
    const node=state.snapshot?.nodes.find(n=>n.node_id===state.selected);if(!node)return;
    $('twin-node-id').textContent=node.node_id;$('twin-node-risk').textContent=node.status==='ONLINE'?node.risk_level:node.status;
    $('twin-node-risk').style.color=node.status==='ONLINE'?COLORS[node.risk_level]:COLORS.OFFLINE;
    $('twin-node-type').textContent=node.node_type==='crack'?'Crack / displacement monitor':'Tilt / vibration monitor';
    $('twin-reading-risk').textContent=`${node.risk_score.toFixed(1)} / 100`;
    $('twin-reading-tilt').textContent=node.node_type==='crack'?'—':`${node.tilt_x.toFixed(2)}° / ${node.tilt_y.toFixed(2)}°`;
    $('twin-reading-vibration').textContent=node.node_type==='crack'?'—':node.vibration.toFixed(3);
    $('twin-reading-crack').textContent=node.node_type==='crack'?`${node.displacement_mm.toFixed(2)} mm`:'—';
    $('twin-reading-battery').textContent=`${node.battery.toFixed(0)}%`;$('twin-reading-link').textContent=node.status;
    for(const[id,item]of nodeObjects)item.label.scale.setScalar(id===state.selected?1.2:1).multiply(new THREE.Vector3(6,2.06,1));
}
function pickNode(event){
    const rect=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);
    const targets=[...nodeObjects.values()].flatMap(item=>[item.root,...($('twin-markers').checked?[item.label]:[])]);
    const hit=raycaster.intersectObjects(targets,true).find(item=>item.object.userData.nodeId);if(hit){state.selected=hit.object.userData.nodeId;$('twin-node-select').value=state.selected;showSelected();}
}

async function pollHost(){
    if(state.pollBusy)return;state.pollBusy=true;state.lastPoll=performance.now();
    try{state.host=await json('/api/twin/state');if(state.source==='host'){applySnapshot(state.host);const online=state.host.nodes.filter(n=>n.status==='ONLINE').length;setLink(online?`Synced · ${online} online`:'Connected · readings stale',!online);}}
    catch(error){if(state.source==='host')setLink('Connection lost · holding last state',true);}
    finally{state.pollBusy=false;}
}
async function source(mode){
    if(!state.ready)return;state.source=mode;state.playing=false;stopTour();
    $('twin-host').classList.toggle('selected',mode==='host');$('twin-demo').classList.toggle('selected',mode==='demo');$('twin-replay').classList.toggle('hidden',mode!=='demo');
    $('twin-source-label').textContent=mode==='host'?'HOST TELEMETRY · SIMULATED INPUT':'RECORDED DEMO · HOST UNCHANGED';
    if(mode==='host'){if(state.host)applySnapshot(state.host);await pollHost();}
    else{try{state.demo=state.demo||await json('/static/models/terraveil/demo.json');state.time=0;applyDemo();setLink('Offline replay · 44 seconds');}catch(error){setLink('Demo unavailable · return to host',true);}}
}
function applyDemo(){if(!state.demo)return;const index=Math.min(state.demo.length-1,Math.round(state.time*4));applySnapshot(state.demo[index].state);$('twin-time').value=state.time;$('twin-time-label').textContent=`0:${String(Math.floor(state.time)).padStart(2,'0')} / 0:44`;$('twin-play').textContent=state.playing?'Pause':'Play demo';}

let previous=0,lastDemo=-1;
function animate(now){
    requestAnimationFrame(animate);const dt=previous?Math.min(.1,(now-previous)/1000):0;previous=now;
    if(!state.active||document.hidden)return;
    if(state.source==='host'&&now-state.lastPoll>2000)pollHost();
    if(state.source==='demo'&&state.playing){state.time=Math.min(44,state.time+dt);if(Math.round(state.time*4)!==lastDemo){lastDemo=Math.round(state.time*4);applyDemo();}if(state.time>=44){state.playing=false;$('twin-play').textContent='Play demo';}}
    if(state.tour!==null){const elapsed=(now-state.tour)/1000,t=smooth((elapsed-6)/6);camera.position.lerpVectors(new THREE.Vector3(93,286,558),new THREE.Vector3(80,125,168),t);controls.target.lerpVectors(new THREE.Vector3(0,40,0),new THREE.Vector3(36,55,66),t);if(elapsed>12)stopTour();}
    const flash=(Math.sin(now*.008)>0)?3:.2;
    for(const item of sirenObjects.values())item.root.traverse(mesh=>{if(mesh.isMesh&&mesh.userData.role==='beacon'){mesh.material.color.set(item.active?'#ff3d30':'#582b28');mesh.material.emissive.set(item.active?'#ff281c':'#000000');mesh.material.emissiveIntensity=item.active?flash:0;}});
    controls.update();renderer.render(scene,camera);
    $('twin-stage').dataset.drawCalls=String(renderer.info.render.calls);
}

$('twin-host').addEventListener('click',()=>source('host'));$('twin-demo').addEventListener('click',()=>source('demo'));
document.querySelectorAll('[data-twin-camera]').forEach(button=>button.addEventListener('click',()=>cameraView(button.dataset.twinCamera)));
$('twin-tour').addEventListener('click',()=>state.tour===null?startTour():stopTour());
$('twin-node-select').addEventListener('change',event=>{state.selected=event.target.value;showSelected();});
$('twin-focus-node').addEventListener('click',()=>{const item=nodeObjects.get(state.selected);if(!item)return;stopTour();controls.target.copy(item.root.position).add(new THREE.Vector3(0,1,0));camera.position.copy(item.root.position).add(new THREE.Vector3(7,5,10));controls.update();});
$('twin-forest').addEventListener('change',event=>{if(forest)forest.visible=event.target.checked;});
$('twin-risk').addEventListener('change',event=>riskMeshes.forEach(mesh=>mesh.visible=event.target.checked));
$('twin-markers').addEventListener('change',event=>nodeObjects.forEach(item=>item.label.visible=event.target.checked));
$('twin-gain').addEventListener('input',event=>{state.gain=Number(event.target.value);$('twin-gain-value').value=`${state.gain}×`;if(state.snapshot)applySnapshot(state.snapshot);});
$('twin-play').addEventListener('click',()=>{if(state.time>=44)state.time=0;state.playing=!state.playing;lastDemo=-1;applyDemo();});
$('twin-time').addEventListener('input',event=>{state.playing=false;state.time=Number(event.target.value);applyDemo();});
window.addEventListener('pagehide',()=>{controls?.dispose();renderer?.dispose();});

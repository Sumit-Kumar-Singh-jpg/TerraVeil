import * as THREE from 'three';
import {signal,movement,usable} from './motion-signal.js';
const COLORS={LOW:'#50bf8b',MEDIUM:'#edbb55',HIGH:'#fa914f',CRITICAL:'#fb5c65'};
const fmt=(n,suffix='')=>Number.isFinite(n)?n.toFixed(2)+suffix:'Unavailable';
export class GroundMotionOverlay {
    constructor(scene,project,height,focus) {
        Object.assign(this,{scene,project,height,focus,mode:null,selected:null,receivedAt:0,lastTime:0,gain:1,style:'settlement'});
        this.items=new Map();this.enabled=true;this.listeners=[];
        document.getElementById('ground-motion-panel')?.remove();
        this.panel=document.createElement('section');this.panel.id='ground-motion-panel';
        this.panel.innerHTML=`<h3>Live ground response</h3><p id="motion-feed" role="status">Waiting for telemetry</p>
            <label>Node <select id="motion-node" aria-label="Ground response node"></select></label>
            <button id="motion-focus" type="button">Focus node</button>
            <label class="insar-checkbox"><input id="motion-enabled" type="checkbox" checked> Show illustrative deformation</label>
            <label>Appearance <select id="motion-style"><option value="settlement">Settlement illustration</option><option value="slide">Downslope slide illustration</option></select></label>
            <label>Visual gain <input id="motion-gain" type="range" min="0.25" max="4" step="0.25" value="1"><output id="motion-gain-value">1×</output></label>
            <p class="motion-disclaimer">ILLUSTRATIVE ESTIMATE · The selected node’s orange wireframe responds to changes since this view opened. It does not measure depth, affected area or landslide travel. Radius: 700 m for display only. Original DEM and InSAR remain unchanged. Cyan diamonds are placement recommendations.</p>
            <button id="motion-baseline" type="button">Use current readings as reference</button><div id="motion-details"></div>`;
        document.querySelector('.insar-inspector').prepend(this.panel);
        this.badge=document.createElement('div');this.badge.className='motion-scene-badge';this.badge.textContent='ILLUSTRATIVE GROUND RESPONSE · waiting for node data';document.getElementById('insar-stage').append(this.badge);
        this.$=id=>this.panel.querySelector('#'+id);
        this.$('motion-node').onchange=e=>this.select(e.target.value);
        this.$('motion-focus').onclick=()=>{const p=this.items.get(this.selected)?.position;if(p)this.focus(p);};
        this.$('motion-enabled').onchange=e=>{this.enabled=e.target.checked;this.describe();};
        this.$('motion-style').onchange=e=>{this.style=e.target.value;this.describe();};
        this.$('motion-gain').oninput=e=>{this.gain=Number(e.target.value);this.$('motion-gain-value').textContent=this.gain+'×';};
        this.$('motion-baseline').onclick=()=>{for(const item of this.items.values())if(usable(item.reading,this.receivedAt)){item.baseline=signal(item.reading);item.response=movement(item.reading,item.baseline);item.amount=0;}this.describe();};
        this.listen('terraveil:telemetry',e=>this.update(e.detail));
        this.listen('terraveil:node-selected',e=>{if(this.items.has(e.detail.nodeId)){this.selected=e.detail.nodeId;this.describe();}});
        if(window.TerraVeilTelemetry)this.update(window.TerraVeilTelemetry);
    }
    listen(name,fn){window.addEventListener(name,fn);this.listeners.push([name,fn]);}
    create(node) {
        const root=new THREE.Group(),body=new THREE.Mesh(new THREE.BoxGeometry(.12,.3,.12),new THREE.MeshBasicMaterial({color:'#50bf8b'}));
        body.position.y=.2;root.add(body);
        const ring=new THREE.Mesh(new THREE.RingGeometry(.23,.26,32),new THREE.MeshBasicMaterial({color:'#ffba69',side:THREE.DoubleSide,depthTest:false,transparent:true,opacity:.8}));
        ring.rotation.x=-Math.PI/2;ring.position.y=.03;root.add(ring);root.userData.nodeId=node.node_id;this.scene.add(root);
        const geometry=new THREE.PlaneGeometry(1.4,1.4,20,20);geometry.rotateX(-Math.PI/2);
        const patch=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:'#ffa64f',wireframe:true,transparent:true,opacity:.75,depthTest:false}));patch.renderOrder=4;patch.frustumCulled=false;this.scene.add(patch);
        const item={node,root,body,ring,patch,base:null,position:null,baseline:null,amount:0,reading:{},response:movement({}),session:null};
        this.items.set(node.node_id,item);this.locate(item);return item;
    }
    locate(item) {
        item.position=this.project(item.node);item.root.visible=Boolean(item.position);
        if(!item.position){item.patch.visible=false;return;}
        item.root.position.copy(item.position);
        const a=item.patch.geometry.attributes.position,base=new Float32Array(a.count*3),valid=[],indices=[];
        for(let row=0;row<=20;row++)for(let col=0;col<=20;col++){
            const i=row*21+col,x=item.position.x+(col/20-.5)*1.4,z=item.position.z+(row/20-.5)*1.4,y=this.height(x,z);
            base.set([x,y===null?item.position.y:y,z],i*3);valid[i]=y!==null;
            if(row&&col){const t=i-22,b=i-21,c=i-1;if(valid[t]&&valid[b]&&valid[c])indices.push(t,b,c);if(valid[b]&&valid[c]&&valid[i])indices.push(b,i,c);}
        }
        item.base=base;item.patch.geometry.setIndex(indices);
        const dx=this.height(item.position.x+.08,item.position.z),dz=this.height(item.position.x,item.position.z+.08);
        item.downhill=new THREE.Vector2(dx===null?0:item.position.y-dx,dz===null?0:item.position.y-dz);
        if(item.downhill.length()>1e-6)item.downhill.normalize();
    }
    reproject(){for(const item of this.items.values())this.locate(item);}
    update(state) {
        if(this.mode!==state.mode){this.clear();this.mode=state.mode;}
        this.receivedAt=state.receivedAt;
        const readings=new Map(state.readings.map(r=>[r.node_id,r])),ids=new Set(state.nodes.map(n=>n.node_id));
        for(const id of this.items.keys())if(!ids.has(id))this.remove(id);
        for(const node of state.nodes){
            const item=this.items.get(node.node_id)||this.create(node),r=readings.get(node.node_id)||{};
            const moved=item.node.latitude!==node.latitude||item.node.longitude!==node.longitude;
            item.node=node;if(moved){this.locate(item);item.baseline=null;item.amount=0;}
            item.reading=r;
            if(item.session!==r.session_id){item.session=r.session_id;item.baseline=null;item.amount=0;}
            if(usable(r,this.receivedAt)){
                if(!item.baseline)item.baseline=signal(r);
                const s=signal(r);for(const key of Object.keys(s))if(!Number.isFinite(item.baseline[key])&&Number.isFinite(s[key]))item.baseline[key]=s[key];
                item.response=movement(r,item.baseline);
                item.body.rotation.set((s.roll||0)*Math.PI/180,0,-(s.pitch||0)*Math.PI/180);
            }
            item.body.material.color.set(r.online?(COLORS[r.risk_level]||'#50bf8b'):'#8c99a8');
        }
        const picker=this.$('motion-node'),key=[...ids].join('|');
        if(picker.dataset.ids!==key){picker.replaceChildren();for(const id of ids){const o=document.createElement('option');o.value=id;o.textContent=id;picker.append(o);}picker.dataset.ids=key;}
        if(!ids.has(this.selected))this.selected=[...this.items.values()].find(i=>i.position&&usable(i.reading,this.receivedAt))?.node.node_id||ids.values().next().value;
        this.describe();
    }
    select(id){this.selected=id;window.NodePlacementEngine?.selectNode(id,'insar_motion');this.describe();}
    describe(){
        const item=this.items.get(this.selected),fresh=item&&usable(item.reading,this.receivedAt);
        const active=[...this.items.values()].filter(i=>usable(i.reading,this.receivedAt)).length;
        this.$('motion-feed').textContent=`${this.mode||'CONNECTING'} · ${active}/${this.items.size} fresh nodes · updates every 2 s`;
        this.$('motion-node').value=this.selected||'';this.$('motion-focus').disabled=!item?.position;
        const mapping=[...this.items.values()].filter(i=>i.position).length;
        this.badge.textContent=`${this.enabled?'ILLUSTRATIVE GROUND RESPONSE':'SENSOR MARKERS ONLY'} · ${this.mode||'WAITING'} · ${mapping} mapped · ${this.selected||'no selection'} · ${active?'LIVE UPDATES':'STALE — ANIMATION FROZEN'}`;
        if(!item){this.$('motion-details').textContent='No registered nodes in this data source.';return;}
        const r=item.reading,s=signal(r),d=item.response;
        const rows=[['State',fresh?'Receiving': 'Stale / offline — last shape held'],['Location',item.position?(item.node.position_source==='planned_1km'?'Planned · 1 km spacing': 'Surface projection'): 'Unlocated or outside the DEM'],['Roll / pitch',fmt(s.roll,'°')+' / '+fmt(s.pitch,'°')],['Tilt change',fmt(d.tilt,'°')],['Gauge displacement',fmt(s.displacement,' mm')],['Gauge change',fmt(d.mm,' mm')],['Vibration',fmt(r.vibration)],['Live risk',r.risk_level||'Unavailable'],['Measured depth change','Unavailable'],['Measured slide distance','Unavailable'],['Last received',r.last_seen||'No reading']];
        const dl=document.createElement('dl');for(const [k,v]of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;dl.append(dt,dd);}this.$('motion-details').replaceChildren(dl);
    }
    tick(time){
        const dt=Math.min(.1,Math.max(0,time-(this.lastTime||time)));this.lastTime=time;
        if(Math.floor(time)!==this.lastSecond){this.lastSecond=Math.floor(time);this.describe();}
        for(const item of this.items.values()){
            const fresh=usable(item.reading,this.receivedAt);
            if(fresh)item.amount+=(item.response.strength-item.amount)*(1-Math.exp(-dt*4));
            const amount=item.amount*this.gain;
            item.ring.visible=fresh&&item.reading.vibration>0;item.ring.scale.setScalar(item.ring.visible?1+.15*Math.sin(time*8):1);
            item.body.material.color.set(fresh?(COLORS[item.reading.risk_level]||'#50bf8b'):'#8c99a8');
            item.body.scale.setScalar(item.node.node_id===this.selected?1.4:1);
            item.patch.visible=this.enabled&&item.node.node_id===this.selected&&Boolean(item.position)&&Boolean(item.base);item.patch.material.opacity=fresh?.75:.25;
            if(!item.patch.visible)continue;
            const p=item.patch.geometry.attributes.position,b=item.base;
            for(let i=0;i<p.count;i++){
                const x=b[i*3],z=b[i*3+2],r=Math.hypot(x-item.position.x,z-item.position.z)/.7;
                const w=r<1?(1-r*r)**2:0;
                // Arbitrary visual offsets in scene kilometres, never measured depth/travel.
                const shift=this.style==='slide'?.22*amount*w:0;
                const y=b[i*3+1]-.18*amount*w+.01;
                p.setXYZ(i,x+(item.downhill?.x||0)*shift,y,z+(item.downhill?.y||0)*shift);
            }
            p.needsUpdate=true;
        }
    }
    hit(ray){const hit=ray.intersectObjects([...this.items.values()].filter(i=>i.root.visible).map(i=>i.root),true)[0];let o=hit?.object;while(o&&!o.userData.nodeId)o=o.parent;return o?.userData.nodeId;}
    remove(id){const item=this.items.get(id);for(const o of [item.root,item.patch]){this.scene.remove(o);o.traverse(c=>{c.geometry?.dispose();c.material?.dispose();});}this.items.delete(id);}
    clear(){for(const id of [...this.items.keys()])this.remove(id);}
    dispose(){this.clear();for(const [n,f]of this.listeners)window.removeEventListener(n,f);this.panel.remove();this.badge.remove();}
}

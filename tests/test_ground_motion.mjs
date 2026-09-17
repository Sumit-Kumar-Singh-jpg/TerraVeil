import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as THREE from '../static/vendor/three/three.module.js';
import {movement,signal,usable} from '../static/js/motion-signal.js';
assert.equal(movement({roll:20,pitch:10},signal({roll:20,pitch:10})).strength,0);
assert.equal(movement({roll:20,pitch:10,vibration:1},signal({roll:20,pitch:10})).strength,0);
assert.equal(movement({roll:-179,pitch:0},signal({roll:179,pitch:0})).roll,2);
assert.equal(movement({displacement_mm:7},{displacement:2}).mm,5);
assert.equal(movement({roll:80,pitch:80},{roll:0,pitch:0}).strength,1);
assert.equal(movement({},{}).tilt,null);
assert.equal(usable({online:true},1000,7001),false);
class Element {
 constructor(){this.children=[];this.dataset={};this.value='';this.textContent='';}
 append(...e){this.children.push(...e);}
 prepend(e){this.children.unshift(e);}
 replaceChildren(...e){this.children=e;}
 remove(){}
 set innerHTML(s){}
 querySelector(q){return elements.get(q.slice(1));}
}
const elements=new Map(['insar-stage','motion-node','motion-feed','motion-focus','motion-enabled','motion-style','motion-gain','motion-gain-value','motion-baseline','motion-details'].map(id=>[id,new Element()]));
const inspector=new Element(),listeners=new Map();
globalThis.document={getElementById:id=>elements.get(id),createElement:()=>new Element(),querySelector:()=>inspector};
globalThis.window={addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n)};
let code=await readFile(new URL('../static/js/ground-motion.js',import.meta.url),'utf8');
code=code.replace("'three'",JSON.stringify(new URL('../static/vendor/three/three.module.js',import.meta.url).href)).replace("'./motion-signal.js'",JSON.stringify(new URL('../static/js/motion-signal.js',import.meta.url).href));
const {GroundMotionOverlay}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const scene=new THREE.Scene(),terrain=new THREE.Mesh(new THREE.PlaneGeometry(2,2),new THREE.MeshBasicMaterial());scene.add(terrain);
const original=terrain.geometry.attributes.position.array.slice();
const overlay=new GroundMotionOverlay(scene,n=>n.latitude===null?null:new THREE.Vector3(n.longitude,0,n.latitude),()=>0,()=>{});
const nodes=[{node_id:'UG-01',latitude:1,longitude:1},{node_id:'LD-01',latitude:2,longitude:2}];
const state=readings=>({mode:'REAL',nodes,readings,receivedAt:Date.now()});
const initial=[{node_id:'UG-01',roll:0,pitch:0,online:true,session_id:'a'},{node_id:'LD-01',displacement_mm:2,online:true,session_id:'a'}];
overlay.update(state(initial));overlay.tick(1);
const item=overlay.items.get('UG-01'),mesh=item.patch,anchor=item.root.position.clone();
assert.equal(item.response.strength,0);
overlay.update(state([{...initial[0],roll:5},{...initial[1],displacement_mm:6}]));
for(let t=1.1;t<3;t+=.1)overlay.tick(t);
assert.ok(item.patch.geometry.attributes.position.getY(220)<-.1,'fresh tilt animates ground patch');
assert.equal(item.patch,mesh);assert.ok(item.root.position.equals(anchor),'geographic marker never moves');
assert.equal(overlay.items.get('LD-01').response.mm,4);
assert.deepEqual(terrain.geometry.attributes.position.array,original,'source terrain is immutable');
const before=item.amount;overlay.update(state([{...initial[0],roll:80,online:false}]));overlay.tick(4);assert.equal(item.amount,before,'offline reading cannot animate');
overlay.enabled=false;overlay.tick(5);assert.equal(item.patch.visible,false);
overlay.update({...state(initial),mode:'SIMULATION'});assert.equal(overlay.items.get('UG-01').response.strength,0,'source change resets reference');
overlay.update({...state(initial),mode:'SIMULATION',nodes:[{...nodes[0],latitude:null}]});assert.equal(overlay.items.size,1);assert.equal(overlay.items.get('UG-01').root.visible,false);
overlay.dispose();assert.equal(scene.children.length,1);assert.equal(listeners.size,0);
console.log('PASS: baseline, units, animation, fixed coordinates, immutable DEM, stale freeze, source reset, removal, disposal');

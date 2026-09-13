"""Export the existing Blender mine as compact glTF geometry + reusable instances.
Run: blender -b TerraVeil_Digital_Twin.blend --python scripts/export_twin.py
The source .blend is never saved or overwritten. No third-party Python packages.
"""
import bpy,json,math,struct,array,hashlib,gzip
from pathlib import Path
from mathutils import Matrix,Vector
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'static/models/terraveil';OUT.mkdir(parents=True,exist_ok=True)
s=bpy.context.scene;s.frame_set(1);ctrl=bpy.data.objects['TERRAVEIL_SIMULATION_CONTROL']
ctrl['SUBSIDENCE_LEVEL']=0.;ctrl['CRACK_INPUT_MM']=0.;ctrl['CRACK_PROGRESS']=0.;bpy.context.view_layer.update()
convert=Matrix.Rotation(-math.pi/2,4,'X');inverse=convert.inverted()
gltf={'asset':{'version':'2.0','generator':'TerraVeil browser export'},'scene':0,'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':[],'buffers':[{}],'bufferViews':[],'accessors':[]}
binary=bytearray();material_ids={};stats={'source':bpy.data.filepath,'static_source_objects':0,'tree_instances':0,'triangles_in_asset':0}

def accessor(values,kind,components):
    while len(binary)%4:binary.append(0)
    data=array.array('f' if kind==5126 else 'I',values).tobytes();offset=len(binary);binary.extend(data)
    view=len(gltf['bufferViews']);gltf['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':len(data)})
    a={'bufferView':view,'componentType':kind,'count':len(values)//components,'type':{1:'SCALAR',3:'VEC3',4:'VEC4'}[components]}
    if components==3 and kind==5126:a.update(min=[min(values[i::3]) for i in range(3)],max=[max(values[i::3]) for i in range(3)])
    gltf['accessors'].append(a);return len(gltf['accessors'])-1

def color_of(mat):
    if not mat:return (.35,.28,.18),.9,0.
    color=tuple(mat.diffuse_color[:3]);rough=.85;metal=0
    if mat.use_nodes:
        bs=next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
        if bs:
            rough=bs.inputs['Roughness'].default_value;metal=bs.inputs['Metallic'].default_value
            if not bs.inputs['Base Color'].is_linked:color=tuple(bs.inputs['Base Color'].default_value[:3])
        ramps=[n for n in mat.node_tree.nodes if n.type=='VALTORGB']
        if ramps and (not bs or bs.inputs['Base Color'].is_linked):
            ramp=ramps[0].color_ramp;colors=[e.color for e in ramp.elements];color=tuple(sum(c[i] for c in colors)/len(colors) for i in range(3))
    return color,rough,metal

def material(mat):
    key=mat.name if mat else 'Soil'
    if key not in material_ids:
        color,rough,metal=color_of(mat);material_ids[key]=len(gltf['materials'])
        gltf['materials'].append({'name':key,'pbrMetallicRoughness':{'baseColorFactor':[1,1,1,1],'roughnessFactor':max(.2,rough),'metallicFactor':metal},'doubleSided':True})
    return material_ids[key]

def node(name,children=None,extras=None,parent=None,mesh=None):
    value={'name':name}
    if children is not None:value['children']=children
    if extras:value['extras']=extras
    if mesh is not None:value['mesh']=mesh
    index=len(gltf['nodes']);gltf['nodes'].append(value)
    if parent is None:gltf['scenes'][0]['nodes'].append(index)
    else:gltf['nodes'][parent].setdefault('children',[]).append(index)
    return index

def add_source(batches,ob,mesh,transform,role='body'):
    mesh.calc_loop_triangles();normal_matrix=transform.to_3x3().inverted_safe().transposed()
    vertex_cache={}
    for tri in mesh.loop_triangles:
        mat=mesh.materials[tri.material_index] if tri.material_index<len(mesh.materials) else None
        mid=material(mat);key=(mid,role)
        batch=batches.setdefault(key,{'p':[],'n':[],'c':[],'i':[]})
        color,_,_=color_of(mat)
        for vi,li in zip(tri.vertices,tri.loops):
            normal=normal_matrix@mesh.corner_normals[li].vector;normal.normalize()
            cache_key=(mid,vi,tuple(round(x,3) for x in normal))
            if cache_key not in vertex_cache:
                point=transform@mesh.vertices[vi].co;index=len(batch['p'])//3
                vertex_cache[cache_key]=index;batch['p'].extend(point);batch['n'].extend(normal)
                grain=.90+.10*math.sin(point.x*2.3+point.y*3.7+point.z*1.7)
                batch['c'].extend([min(1,max(.002,v*grain)) for v in color])
            batch['i'].append(vertex_cache[cache_key])

def emit(batches,name,parent=None):
    for (mat,role),b in batches.items():
        if not b['i']:continue
        primitive={'attributes':{'POSITION':accessor(b['p'],5126,3),'NORMAL':accessor(b['n'],5126,3),'COLOR_0':accessor(b['c'],5126,3)},'indices':accessor(b['i'],5125,1),'material':mat}
        gltf['meshes'].append({'name':name+'_'+role,'primitives':[primitive]});node(name+'_'+role+'_'+str(mat),parent=parent,mesh=len(gltf['meshes'])-1,extras={'role':role})
        stats['triangles_in_asset']+=len(b['i'])//3

environment=node('MINE_ENVIRONMENT',children=[],extras={'role':'environment'})
forest_instances={};tree_sources={};static={};dg=bpy.context.evaluated_depsgraph_get()
collections=['01_TERRAIN','02_FOREST','03_SURFACE_MINE','04_ROADS','05_GEOLOGY','06_UNDERGROUND','07_BUILDINGS','08_VEHICLES','10_ZONE_HOST','15_CONTROL_ROOM']
for colname in collections:
    print('EXPORTING',colname,flush=True)
    for ob in bpy.data.collections[colname].objects:
        if ob.type not in ('MESH','CURVE') or ob.hide_render or not ob.visible_get():continue
        if colname=='02_FOREST' and ob.type=='MESH' and 'tree variant' in ob.data.name:
            stem=ob.data.name.split('LOD')[0].rstrip(' _|');candidates=[m for m in bpy.data.meshes if m.name.startswith(stem) and 'LOD2' in m.name]
            mesh=candidates[0] if candidates else ob.data
            key=mesh.name;tree_sources[key]=(ob,mesh);matrix=convert@ob.matrix_world@inverse
            forest_instances.setdefault(key,[]).append([round(matrix[row][column],6) for column in range(4) for row in range(4)])
            stats['tree_instances']+=1;continue
        evaluated=ob.evaluated_get(dg);mesh=evaluated.to_mesh()
        try:add_source(static,ob,mesh,convert@ob.matrix_world)
        finally:evaluated.to_mesh_clear()
        stats['static_source_objects']+=1
emit(static,'Mine',environment)
trees=[]
for i,(key,(ob,mesh)) in enumerate(tree_sources.items()):
    root=node(f'TREE_TEMPLATE_{i}',children=[],extras={'role':'tree_template'});batch={};add_source(batch,ob,mesh,convert);emit(batch,f'Tree_{i}',root)
    trees.append({'template':f'TREE_TEMPLATE_{i}','matrices':forest_instances[key]})

for name,source in [('GROUND_TEMPLATE','NODE_GROUND_004'),('CRACK_TEMPLATE','NODE_CRACK_001'),('SIREN_TEMPLATE','SIREN_001')]:
    root=node(name,children=[],extras={'role':'hardware_template'});ob=bpy.data.objects[source];batches={};objects=list(ob.children_recursive)
    if name=='CRACK_TEMPLATE':objects+=list(bpy.data.objects['CRACK_GAUGE_RIG_001'].children_recursive)
    for part in objects:
        if part.type not in ('MESH','CURVE') or part.hide_render:continue
        ancestors=[part];p=part.parent
        while p:ancestors.append(p);p=p.parent
        role='anchor_b' if any(a.name=='ANCHOR_B_001' for a in ancestors) else 'led' if 'STATUS_LED' in part.name else 'beacon' if 'red_beacon' in part.name else 'battery' if 'BATTERY_INDICATOR' in part.name else 'link' if 'LINK_INDICATOR' in part.name else 'body'
        ev=part.evaluated_get(dg);mesh=ev.to_mesh()
        try:add_source(batches,part,mesh,convert@ob.matrix_world.inverted()@part.matrix_world,role)
        finally:ev.to_mesh_clear()
    emit(batches,name,root)

patch=bpy.data.objects['SUBSIDENCE_ZONE_A'];terrain=node('SUBSIDENCE_ZONE_A',children=[],extras={'role':'terrain'});batch={};add_source(batch,patch,patch.data,convert@patch.matrix_world,'terrain');emit(batch,'Surface',terrain)
xs=sorted({round(v.co.x,4) for v in patch.data.vertices});zs=sorted({round(-v.co.y,4) for v in patch.data.vertices})
heights={(round(v.co.x,4),round(-v.co.y,4)):round(v.co.z,5) for v in patch.data.vertices}
grid={'xs':xs,'zs':zs,'heights':[heights.get((x,z),57) for z in zs for x in xs]}
paths=[]
for ob in bpy.data.collections['FRACTURE_PATHS | edit these curves'].objects:
    points=ob.data.shape_keys.key_blocks[0].data
    paths.append({'id':ob.name,'onset':ob['onset_level'],'end':ob['end_level'],'width_factor':ob['width_factor'],'points':[[round(p.co.x,4),round(-p.co.y,4)] for p in points]})
gltf['buffers'][0]['byteLength']=len(binary)
document=json.dumps(gltf,separators=(',',':')).encode()
while len(document)%4:document+=b' '
while len(binary)%4:binary.append(0)
data=struct.pack('<4sII',b'glTF',2,12+8+len(document)+8+len(binary))+struct.pack('<I4s',len(document),b'JSON')+document+struct.pack('<I4s',len(binary),b'BIN\x00')+binary
(OUT/'mine.glb').write_bytes(data)
manifest={'version':1,'model':'mine.glb','source':'TerraVeil_Digital_Twin.blend','coordinate_system':'Y_UP_METRES','trees':trees,'terrain':grid,'fractures':paths,'stats':stats,'sha256':hashlib.sha256(data).hexdigest()}
(OUT/'scene.json').write_text(json.dumps(manifest,separators=(',',':')))
for file in ('mine.glb','scene.json'):
    with gzip.open(OUT/(file+'.gz'),'wb',compresslevel=6) as compressed:compressed.write((OUT/file).read_bytes())
stats.update(glb_bytes=len(data),glb_gzip_bytes=(OUT/'mine.glb.gz').stat().st_size)
(OUT/'export-report.json').write_text(json.dumps(stats,indent=2));print('EXPORTED',json.dumps(stats),flush=True)

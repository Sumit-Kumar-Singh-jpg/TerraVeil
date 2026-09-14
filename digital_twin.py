"""Read-only adapter from the host's telemetry/alert state to its 3D view."""
import math
from datetime import datetime, timezone

GEO_BOUNDS = {'lat_min':23.648,'lat_max':23.672,'lon_min':86.435,'lon_max':86.468}

def finite(value, default=0.0):
    try:
        number=float(value)
        return number if math.isfinite(number) else default
    except (TypeError,ValueError):return default

def age_seconds(timestamp, now):
    try:
        value=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
        return max(0,(now-value).total_seconds())
    except (ValueError,TypeError,AttributeError):return None

def distance(a,b):
    lat1,lat2=math.radians(a['latitude']),math.radians(b['latitude'])
    dlat=lat2-lat1;dlon=math.radians(b['longitude']-a['longitude'])
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)))

def clusters(nodes):
    """The same 500 m connected-component display rule as the existing GIS view."""
    remaining={n['node_id']:n for n in nodes if n.get('latitude') is not None and n['risk_level'] in ('MEDIUM','HIGH','CRITICAL') and n['status']=='ONLINE'}
    result=[]
    while remaining:
        _,first=remaining.popitem();group=[first];queue=[first]
        while queue:
            current=queue.pop()
            for key,other in list(remaining.items()):
                if distance(current,other)<=500:
                    group.append(other);queue.append(other);del remaining[key]
        if len(group)>=2:result.append(group)
    return sorted(result,key=lambda group:min(n['node_id'] for n in group))

def build_twin_state(nodes, readings, alert, now=None):
    now=now or datetime.now(timezone.utc)
    real = any(n.get('host_id') for n in nodes) or any(r.get('data_source') == 'REAL' for r in readings)
    # Collapse duplicate latest rows deterministically without changing host storage.
    lookup={}
    for reading in sorted(readings,key=lambda row:(str(row.get('timestamp','')),finite(row.get('id',0)))):lookup[reading['node_id']]=reading
    values=[]
    for node in nodes:
        rd=lookup.get(node['node_id'],{});age=age_seconds(rd.get('last_seen') or rd.get('timestamp'),now)
        status='ONLINE' if age is not None and age<=15 else 'STALE' if rd else 'NO_DATA'
        if real:
            from telemetry import timeout_seconds
            status = 'ONLINE' if age is not None and age <= timeout_seconds() else 'OFFLINE'
        risk=rd.get('risk_level','LOW');risk=risk if risk in ('LOW','MEDIUM','HIGH','CRITICAL') else 'LOW'
        lon=finite(node.get('longitude'),86.452);lat=finite(node.get('latitude'),23.657)
        position=[-22+115*(lon-GEO_BOUNDS['lon_min'])/(GEO_BOUNDS['lon_max']-GEO_BOUNDS['lon_min']),0,100-68*(lat-GEO_BOUNDS['lat_min'])/(GEO_BOUNDS['lat_max']-GEO_BOUNDS['lat_min'])]
        values.append(dict(node_id=node['node_id'],node_type=node['node_type'],latitude=lat,longitude=lon,position=position,
            tilt_x=finite(rd.get('tilt_x')),tilt_y=finite(rd.get('tilt_y')),vibration=finite(rd.get('vibration')),
            displacement_mm=max(0,finite(rd.get('displacement_mm'))),battery=max(0,min(100,finite(rd.get('battery')))),
            risk_score=max(0,min(100,finite(rd.get('risk_score')))),risk_level=risk,status=status,timestamp=rd.get('timestamp'),age_seconds=round(age,1) if age is not None else None))
    if real:
        for value, node in zip(values, nodes):
            rd = lookup.get(node['node_id'], {})
            for field in ('tilt_x','tilt_y','vibration','displacement_mm','battery','risk_score','soil','rssi','snr'):
                value[field] = rd.get(field)
            value['data_source'] = 'REAL'
            if not rd:
                value['risk_level'] = 'UNAVAILABLE'
            value['evidence'] = rd.get('evidence', 'No telemetry received')
            value['node_status'] = rd.get('status', 'OFFLINE')
            if node.get('latitude') is None or node.get('longitude') is None:
                value.update(latitude=None,longitude=None,position=None)
    groups=clusters([v for v in values if not real or lookup.get(v['node_id'],{}).get('persistent')]);zones=[]
    for index,group in enumerate(groups):
        score=sum(n['risk_score'] for n in group)/len(group)
        zones.append(dict(zone_id=f'ZONE_{index+1}',node_ids=[n['node_id'] for n in group],risk_score=round(score,2),risk_level='HIGH' if any(n['risk_level'] in ('HIGH','CRITICAL') for n in group) else 'MEDIUM',correlation=min(1,len(group)/3)))
    emergency=bool(alert.get('sirens_active')) or alert.get('status')=='RED_ALERT'
    state='EMERGENCY' if emergency else 'HIGH_RISK' if any(z['risk_level']=='HIGH' for z in zones) else 'CORRELATED_DEFORMATION' if zones else 'EARLY_ANOMALY' if any(n['status']=='ONLINE' and n['risk_level']!='LOW' for n in values) else 'NORMAL'
    if real and not emergency and not any(n['status']=='ONLINE' for n in values):
        state = 'NO_LIVE_DATA'
    correlated={key for zone in zones for key in zone['node_ids']}
    affected=[n for n in values if n['node_id'] in correlated]
    # Presentation-only deformation estimate. Never changes alert_state or the database.
    level=min(1,max([max(math.hypot(n['tilt_x'] or 0,n['tilt_y'] or 0)/3,(n['displacement_mm'] or 0)/15) for n in affected] or [0]))
    crack=max([n['displacement_mm'] for n in values if n['node_type']=='crack' and n['status']=='ONLINE'] or [0])
    return dict(version=1,source='HOST_DATABASE',input_mode='REAL' if real else 'SIMULATION',generated_at=now.isoformat(),system_state=state,
        nodes=values,zones=zones,deformation=dict(subsidence_level=level,displacement_mm=crack,crack_progress=min(1,crack/12),calibration='ILLUSTRATIVE'),
        alert=dict(status=alert.get('status','NORMAL'),sirens_active=emergency,message=alert.get('message',''),triggered_at=alert.get('triggered_at')),
        sirens=[dict(siren_id=n['id'],name=n['name'],location=n['location'],state='ACTIVE' if n.get('status')=='ON' else 'OFF') for n in alert.get('siren_zones',[])],
        registration=dict(kind='ILLUSTRATIVE',description='Sensor GPS extent fitted to the existing mine model; not a surveyed site registration.',geo_bounds=GEO_BOUNDS))

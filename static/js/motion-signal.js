// Dimensionless display response, not a measured depth or landslide distance.
export function signal(r) {
    return {roll:Number.isFinite(r.roll)?r.roll:r.tilt_x,
        pitch:Number.isFinite(r.pitch)?r.pitch:r.tilt_y, displacement:r.displacement_mm};
}
export function movement(reading,baseline) {
    const s=signal(reading),b=baseline||s,f=Number.isFinite;
    const angle=v=>((v+180)%360+360)%360-180;
    const roll=f(s.roll)&&f(b.roll)?angle(s.roll-b.roll):null;
    const pitch=f(s.pitch)&&f(b.pitch)?angle(s.pitch-b.pitch):null;
    const mm=f(s.displacement)&&f(b.displacement)?s.displacement-b.displacement:null;
    const tilt=roll!==null&&pitch!==null?Math.hypot(roll,pitch):null;
    const strength=Math.min(1,Math.max(tilt===null?0:Math.max(0,tilt-.05)/5,mm===null?0:Math.abs(mm)/10));
    return {roll,pitch,mm,tilt,strength};
}
export function usable(reading,receivedAt,now=Date.now()) {
    return reading?.online===true && Number.isFinite(receivedAt) && now-receivedAt<6000;
}

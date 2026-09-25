(() => {
  const host = document.getElementById('demo-session-conversations');
  if (!host) return;
  let selected = null, showPrevious = false, busy = false, lastHash = '';
  function safe(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function date(v) { return v ? new Date(v).toLocaleString() : '—'; }
  function button(d, current) {
    return `<button type="button" class="demo-chat-item ${selected === d.demo_id ? 'chosen' : ''}"
      data-demo-id="${safe(d.demo_id)}"><b>${current ? 'Current / latest' : 'Earlier demo'} · ${safe(date(d.started_at))}</b>
      <small>${safe(d.node_ids.join(' + '))} · ${d.ended_at ? 'ENDED' : 'ACTIVE'} · ${d.alarm_fired ? 'Physical alarm fired' : 'No physical alarm'}</small></button>`;
  }
  function paint(payload) {
    const sessions = payload.sessions || [];
    if (!sessions.length) {
      host.innerHTML = '<div class="demo-chat-empty">No demo sessions yet. Press SET ZERO when a physical node is online.</div>';
      return;
    }
    const chosen = sessions.find(d => d.demo_id === selected) || sessions[0];
    selected = chosen.demo_id;
    const older = sessions.filter(d => d.demo_id !== sessions[0].demo_id);
    const bubbles = (payload.events || []).map(ev => {
      const cls = (ev.kind === 'PHYSICAL_ALARM' || ev.kind === 'MANUAL_ALARM') ? 'alarm' : ev.kind === 'SEISMIC' ? 'seismic' : ev.kind.startsWith('RISK_') ? 'risk' : 'system';
      const title = ev.kind.startsWith('RISK_') ? `Risk observed · ${ev.risk_level}` :
        ev.kind === 'PHYSICAL_ALARM' ? 'Physical deformation · ALARM FIRED' :
        ev.kind === 'MANUAL_ALARM' ? 'Operator command · manual alarm' :
        ev.kind === 'SEISMIC' ? 'Simulated geophone precursor' : ev.kind === 'END' ? 'Session finished' : 'Session started';
      return `<article class="demo-chat-bubble ${cls}"><strong>${safe(title)}</strong>
        <p>${safe(ev.message)}</p><small>${safe(ev.node_id || '')} · ${safe(date(ev.timestamp))}</small></article>`;
    }).join('') || '<div class="demo-chat-empty">No risk or alarm events in this session.</div>';
    host.innerHTML = `<div class="demo-chat-layout"><aside class="demo-chat-sidebar">
      <h4>Demo conversations</h4>${button(sessions[0], true)}
      <button type="button" id="demo-show-previous" class="demo-previous">${showPrevious ? 'Hide' : 'Show'} earlier demos (${older.length})</button>
      ${showPrevious ? older.map(d => button(d, false)).join('') : ''}
      </aside><section class="demo-chat-thread"><div class="demo-chat-header">
      <strong>${safe(date(chosen.started_at))}</strong><small>${safe(chosen.node_ids.join(' · '))} · ${chosen.ended_at ? 'ENDED' : 'IN PROGRESS'}</small></div>
      <div class="demo-chat-messages">${bubbles}</div></section></div>`;
    host.querySelectorAll('[data-demo-id]').forEach(btn => btn.addEventListener('click', () => {
      selected = btn.dataset.demoId;
      if (selected === sessions[0].demo_id) host.dataset.latestViewed = selected;
      lastHash = ''; refresh();
    }));
    host.querySelector('#demo-show-previous')?.addEventListener('click', () => {
      showPrevious = !showPrevious; lastHash = ''; refresh();
    });
  }
  async function refresh() {
    if (busy || document.getElementById('view-alerts')?.classList.contains('hidden')) return;
    busy = true;
    try {
      const url = '/api/demo/history' + (selected ? '?demo_id=' + encodeURIComponent(selected) : '');
      const response = await fetch(url, {cache:'no-store'});
      if (!response.ok) throw new Error('Demo history unavailable');
      const data = await response.json();
      // When a new demo starts, automatically switch away from the old chat only
      // if the operator was looking at the previous latest demo.
      if (selected && data.sessions?.length && selected !== data.sessions[0].demo_id &&
          host.dataset.latestViewed === selected) {
        selected = data.sessions[0].demo_id;
        const next = await fetch('/api/demo/history?demo_id=' + encodeURIComponent(selected), {cache:'no-store'});
        if (next.ok) { const newData = await next.json(); host.dataset.latestViewed = selected;
          lastHash = JSON.stringify(newData); paint(newData); return; }
      }
      const hash = JSON.stringify(data);
      if (hash !== lastHash) { lastHash = hash; paint(data); }
      if (!host.dataset.latestViewed && data.sessions?.length) host.dataset.latestViewed = data.sessions[0].demo_id;
    } catch (error) { host.textContent = error.message; }
    finally { busy = false; }
  }
  setInterval(refresh, 2000);
  document.querySelector('[data-target="view-alerts"]')?.addEventListener('click', () => setTimeout(refresh, 0));
})();

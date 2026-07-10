(function () {
  const list = document.getElementById('events-list');

  function fmtTs(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function loadCameras() {
    const sel = document.getElementById('ev-filter-camera');
    try {
      const r = await fetch('/api/wall/cameras');
      const j = await r.json();
      (j.active || []).forEach(function (c) {
        const o = document.createElement('option');
        o.value = c.id;
        o.textContent = c.name || ('Camera ' + (c.id + 1));
        sel.appendChild(o);
      });
    } catch (e) { /* ignore */ }
  }

  async function loadEvents() {
    list.innerHTML = '<p class="events-loading">Loading…</p>';
    const params = new URLSearchParams();
    const cam = document.getElementById('ev-filter-camera').value;
    const cls = document.getElementById('ev-filter-class').value.trim();
    const hours = document.getElementById('ev-filter-hours').value;
    if (cam !== '') params.set('camera_id', cam);
    if (cls) params.set('class', cls);
    params.set('hours', hours);
    params.set('limit', '200');
    try {
      const r = await fetch('/api/events/sessions?' + params.toString());
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'failed');
      render(j.sessions || []);
    } catch (err) {
      list.innerHTML = '<p class="events-error">' + err.message + '</p>';
    }
  }

  function render(sessions) {
    if (!sessions.length) {
      list.innerHTML = '<p class="events-empty">No events in this period.</p>';
      return;
    }
    list.innerHTML = sessions.map(function (s) {
      const thumb = s.snapshot_url
        ? '<img class="ev-thumb" src="' + esc(s.snapshot_url) + '" alt="">'
        : '<div class="ev-thumb ev-thumb-empty"><span class="material-icons-outlined">image</span></div>';
      const clip = s.clip_url
        ? '<a class="btn btn-secondary btn-sm" href="' + esc(s.clip_url) + '" target="_blank" rel="noopener"><span class="material-icons-outlined">play_circle</span> Clip</a>'
        : '';
      const plate = s.plate_text ? '<span class="ev-plate">' + esc(s.plate_text) + '</span>' : '';
      return (
        '<article class="ev-row">' + thumb +
        '<div class="ev-body">' +
        '<div class="ev-title"><strong>' + esc(s.class_name) + '</strong> · Camera ' + (s.camera_id + 1) +
        (s.track_id != null ? ' · track ' + esc(s.track_id) : '') + plate + '</div>' +
        '<div class="ev-meta">' + fmtTs(s.started_at) +
        (s.ended_at ? ' → ' + fmtTs(s.ended_at) : ' · active') +
        ' · score ' + (s.max_confidence != null ? (s.max_confidence * 100).toFixed(0) + '%' : '—') + '</div>' +
        (s.region_labels ? '<div class="ev-regions">' + esc(s.region_labels) + '</div>' : '') +
        '</div><div class="ev-actions">' + clip + '</div></article>'
      );
    }).join('');
  }

  document.getElementById('ev-refresh').addEventListener('click', loadEvents);
  ['ev-filter-camera', 'ev-filter-hours'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', loadEvents);
  });
  document.getElementById('ev-filter-class').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') loadEvents();
  });

  loadCameras().then(loadEvents);
})();

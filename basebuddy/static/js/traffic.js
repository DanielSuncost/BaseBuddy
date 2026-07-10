// Traffic page: pick a camera / analytics region / object class and chart the
// recorded tracks. Traffic capture itself is driven by analytics regions
// (drawn on Camera Detail); the setup panel below can enable a full-frame
// region for any camera via the shared ROI API.
(function () {
  var sources = [];        // from /api/traffic/sources
  var hourlyChart = null;
  var dirChart = null;

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
  }

  function el(id) { return document.getElementById(id); }

  // Local date (YYYY-MM-DD); toISOString() would give the UTC date, which is
  // tomorrow during evening hours and made the charts query an empty day.
  function localDateStr() {
    var d = new Date();
    return d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  function currentCamera() {
    var v = el('traffic-cam-select').value;
    return v === '' ? null : parseInt(v, 10);
  }

  function selectedSource() {
    var camId = currentCamera();
    return sources.find(function (c) { return c.id === camId; }) || null;
  }

  // ---------- sources & filters ----------
  async function loadSources() {
    try {
      var data = await fetch('/api/traffic/sources').then(function (r) { return r.json(); });
      if (!data.ok) throw new Error(data.error || 'failed');
      sources = data.cameras || [];

      var camSel = el('traffic-cam-select');
      var withData = sources.filter(function (c) { return c.enabled || c.track_count > 0; });
      var pool = withData.length ? withData : sources;
      camSel.innerHTML = pool.map(function (c) {
        var suffix = c.track_count > 0 ? ' (' + c.track_count + ' tracks)' : (c.enabled ? '' : ' (not enabled)');
        return '<option value="' + c.id + '">' + esc(c.name) + suffix + '</option>';
      }).join('') || '<option value="">No cameras</option>';

      // Default: legacy TRAFFIC_CAM_ID, else the camera with the most tracks
      var def = data.default_cam;
      if (def === null || def === undefined || !pool.some(function (c) { return c.id === def; })) {
        var best = pool.slice().sort(function (a, b) { return b.track_count - a.track_count; })[0];
        def = best ? best.id : '';
      }
      camSel.value = String(def);

      updateFilterOptions();
      renderSetup();
      loadTraffic();
    } catch (e) {
      el('traffic-setup').innerHTML = '<p class="muted">Error loading cameras: ' + esc(e.message) + '</p>';
    }
  }

  function updateFilterOptions() {
    var src = selectedSource();
    var regionSel = el('traffic-region-select');
    var classSel = el('traffic-class-select');
    var regions = src ? src.region_labels : [];
    var classes = src ? src.classes : [];
    regionSel.innerHTML = '<option value="">All regions</option>' + regions.map(function (r) {
      return '<option value="' + esc(r) + '">' + esc(r) + '</option>';
    }).join('');
    classSel.innerHTML = '<option value="">All classes</option>' + classes.map(function (c) {
      return '<option value="' + esc(c) + '">' + esc(c) + '</option>';
    }).join('');
  }

  // ---------- data loading ----------
  async function loadTraffic() {
    var recentEl = el('traffic-recent');
    var camId = currentCamera();
    if (camId === null) {
      if (recentEl) recentEl.innerHTML = '<p class="muted">Select a camera.</p>';
      return;
    }
    var date = el('traffic-date-input').value || localDateStr();
    var region = el('traffic-region-select').value;
    var klass = el('traffic-class-select').value;

    var filters = '&cam=' + camId +
      (region ? '&region=' + encodeURIComponent(region) : '') +
      (klass ? '&class=' + encodeURIComponent(klass) : '');
    try {
      var results = await Promise.all([
        fetch('/api/traffic/hourly?date=' + date + filters).then(function (r) { return r.json(); }),
        fetch('/api/traffic/directions?date=' + date + filters + '&bin=45').then(function (r) { return r.json(); }),
        fetch('/api/traffic/recent?limit=30' + filters).then(function (r) { return r.json(); }),
      ]);
      renderHourly(results[0].data || []);
      renderDirections(results[1].data || []);
      renderRecent(results[2].data || [], recentEl);
      loadFlowMap();
    } catch (e) {
      if (recentEl) recentEl.innerHTML = '<p class="muted">Error loading traffic data.</p>';
    }
  }

  // ---------- charts ----------
  function renderHourly(data) {
    var canvas = el('traffic-hourly-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    var labels = data.map(function (d) { return (d.hour != null ? d.hour : d.h) + ':00'; });
    var counts = data.map(function (d) { return d.count != null ? d.count : d.n; });
    if (hourlyChart) hourlyChart.destroy();
    hourlyChart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels.length ? labels : ['—'],
        datasets: [{ label: 'Tracks', data: counts.length ? counts : [0], backgroundColor: 'rgba(26,115,232,0.7)' }],
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    });
  }

  function renderDirections(data) {
    var canvas = el('traffic-direction-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    var labels = data.map(function (d) {
      if (d.bin_label) return d.bin_label;
      if (d.start_deg != null) return d.start_deg + '–' + d.end_deg + '°';
      return String(d.direction || d.bin || '');
    });
    var counts = data.map(function (d) { return d.count != null ? d.count : d.n; });
    if (dirChart) dirChart.destroy();
    dirChart = new Chart(canvas, {
      type: 'polarArea',
      data: {
        labels: labels.length ? labels : ['N/A'],
        datasets: [{ data: counts.length ? counts : [0], backgroundColor: ['#1a73e8', '#34a853', '#fbbc04', '#ea4335', '#9334e6', '#12b5cb', '#ff6d01', '#46bdc6'] }],
      },
      options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
    });
  }

  function renderRecent(data, elRecent) {
    if (!elRecent) return;
    if (!data.length) {
      elRecent.innerHTML = '<p class="muted">No tracks for this selection yet. Ensure the camera is enabled below and detection is running.</p>';
      return;
    }
    elRecent.innerHTML = (
      '<table class="traffic-table"><thead><tr><th>Time</th><th>Class</th><th>Speed</th><th>Direction</th><th>Region</th></tr></thead><tbody>' +
      data.map(function (r) {
        var t = r.end_ts ? new Date(r.end_ts * 1000).toLocaleTimeString() : '—';
        return '<tr><td>' + esc(t) + '</td><td>' + esc(r.class_name || '—') +
          '</td><td>' + esc(r.speed_kph != null ? r.speed_kph + ' km/h' : '—') +
          '</td><td>' + esc(r.direction_deg != null ? Math.round(r.direction_deg) + '°' : '—') +
          '</td><td>' + esc(r.region_label || '—') + '</td></tr>';
      }).join('') +
      '</tbody></table>'
    );
  }

  // ---------- flow map ----------
  function loadFlowMap() {
    var img = el('traffic-flow-img');
    var camId = currentCamera();
    if (!img || camId === null) return;
    var minutes = el('traffic-flow-window').value || '60';
    var region = el('traffic-region-select').value;
    var klass = el('traffic-class-select').value;
    img.src = '/api/traffic/flow-map?cam=' + camId + '&minutes=' + minutes +
      (region ? '&region=' + encodeURIComponent(region) : '') +
      (klass ? '&class=' + encodeURIComponent(klass) : '') +
      '&_=' + Date.now();
  }

  // ---------- setup panel ----------
  function renderSetup() {
    var container = el('traffic-setup');
    if (!sources.length) {
      container.innerHTML = '<p class="muted">No cameras configured.</p>';
      return;
    }
    container.innerHTML = '<div class="traffic-setup-list">' + sources.map(function (c) {
      var chip = c.enabled
        ? '<span class="traffic-chip on">Capturing</span>'
        : '<span class="traffic-chip off">Not enabled</span>';
      var meta = [];
      if (c.region_labels.length) meta.push('Regions: ' + c.region_labels.map(esc).join(', '));
      if (c.classes.length) meta.push('Seen: ' + c.classes.map(esc).join(', '));
      if (c.track_count) meta.push(c.track_count + ' tracks');
      var enableBtn = c.enabled ? '' :
        '<button type="button" class="btn btn-success btn-sm" onclick="trafficEnableCamera(' + c.id + ')">Enable</button>';
      return (
        '<div class="traffic-setup-row">' +
          '<span class="traffic-setup-name">' + esc(c.name) + '</span>' +
          chip +
          '<span class="traffic-setup-meta">' + (meta.join(' · ') || 'No tracks recorded yet') + '</span>' +
          '<span class="traffic-setup-actions">' +
            enableBtn +
            '<a class="btn btn-secondary btn-sm" href="/camera/' + c.id + '">Edit regions</a>' +
          '</span>' +
        '</div>'
      );
    }).join('') + '</div>';
  }

  // Enable traffic capture by adding a full-frame analytics region via the
  // shared ROI API (same storage the Camera Detail region editor uses).
  window.trafficEnableCamera = async function (camId) {
    try {
      var current = await fetch('/api/rois/' + camId).then(function (r) { return r.json(); });
      var regions = (current.regions || []).slice();
      regions.push({
        label: 'traffic',
        shape: 'rect',
        x1: 0, y1: 0, x2: 1, y2: 1,
        filter: 'none',
        analytics: true,
        tag_detections: true,
      });
      var resp = await fetch('/api/rois/' + camId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions: regions }),
      }).then(function (r) { return r.json(); });
      if (!resp.ok) throw new Error(resp.error || 'save failed');
      if (typeof showToast === 'function') showToast('Traffic capture enabled', 'success');
      loadSources();
    } catch (e) {
      if (typeof showToast === 'function') showToast('Failed to enable: ' + e.message, 'error');
    }
  };

  // ---------- wiring ----------
  el('traffic-date-input').value = localDateStr();
  el('traffic-cam-select').addEventListener('change', function () {
    updateFilterOptions();
    loadTraffic();
  });
  el('traffic-region-select').addEventListener('change', loadTraffic);
  el('traffic-class-select').addEventListener('change', loadTraffic);
  el('traffic-date-input').addEventListener('change', loadTraffic);
  el('traffic-flow-window').addEventListener('change', loadFlowMap);
  el('traffic-flow-refresh').addEventListener('click', loadFlowMap);

  loadSources();
  setInterval(loadTraffic, 60000);
})();

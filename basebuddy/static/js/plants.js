(function () {
  var listEl = document.getElementById('plants-list');
  var toastEl = document.getElementById('plants-toast');
  var modal = document.getElementById('plants-modal');
  var form = document.getElementById('plants-form');
  var picker = document.getElementById('plant-camera-picker');
  var camInput = document.getElementById('plant-camera-input');
  var scheduleMode = document.getElementById('plant-schedule-mode');

  function toast(msg, err) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.style.background = err ? '#d93025' : '#202124';
    setTimeout(function () { toastEl.hidden = true; }, 4000);
  }

  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function healthClass(score) {
    if (score == null) return 'unknown';
    if (score >= 75) return 'good';
    if (score >= 50) return 'mid';
    return 'low';
  }

  function fmtTs(ts) {
    if (!ts) return 'Never';
    return new Date(ts * 1000).toLocaleString();
  }

  function scheduleLabel(m) {
    var s = m.schedule || {};
    if (s.mode === 'times' && s.times && s.times.length) {
      return 'Daily ' + s.times.join(', ');
    }
    var sec = s.interval_s || m.check_interval_s || 3600;
    if (sec >= 3600) return 'Every ' + Math.round(sec / 3600) + 'h';
    return 'Every ' + Math.round(sec / 60) + 'm';
  }

  scheduleMode.addEventListener('change', function () {
    var times = scheduleMode.value === 'times';
    document.getElementById('plant-times-wrap').hidden = !times;
    document.getElementById('plant-interval-wrap').hidden = times;
  });

  async function loadSettings() {
    var r = await fetch('/api/plants/settings');
    var j = await r.json();
    if (!j.ok) return;
    document.getElementById('pv-url').value = j.api_url || '';
    document.getElementById('pv-model').value = j.model || 'gpt-4o-mini';
    document.getElementById('pv-status').textContent = j.configured
      ? 'Vision API configured.'
      : 'Color tracking works without API — add key for LLM health analysis.';
  }

  document.getElementById('pv-save').addEventListener('click', async function () {
    try {
      var r = await fetch('/api/plants/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          PLANT_VISION_API_URL: document.getElementById('pv-url').value.trim(),
          PLANT_VISION_API_KEY: document.getElementById('pv-key').value.trim(),
          PLANT_VISION_MODEL: document.getElementById('pv-model').value.trim() || 'gpt-4o-mini',
        }),
      });
      var j = await r.json();
      toast(j.configured ? 'API settings saved' : 'Saved', !j.ok);
      document.getElementById('pv-key').value = '';
      loadSettings();
    } catch (e) { toast(e.message, true); }
  });

  async function loadCameras() {
    var r = await fetch('/api/plants/cameras');
    var j = await r.json();
    var cams = j.cameras || [];
    picker.innerHTML = cams.map(function (c) {
      return (
        '<button type="button" class="scenes-camera-option" data-id="' + c.id + '">' +
        '<span class="scenes-camera-thumb"><img src="/api/scenes/camera/' + c.id + '/preview?w=320" alt=""></span>' +
        '<span class="scenes-camera-name">' + escapeHtml(c.name || ('Camera ' + (c.id + 1))) + '</span></button>'
      );
    }).join('') || '<p class="scenes-muted">No cameras active.</p>';
    picker.querySelectorAll('.scenes-camera-option').forEach(function (btn) {
      btn.addEventListener('click', function () {
        picker.querySelectorAll('.scenes-camera-option').forEach(function (b) { b.classList.remove('selected'); });
        btn.classList.add('selected');
        camInput.value = btn.getAttribute('data-id');
      });
    });
    if (cams.length) picker.querySelector('.scenes-camera-option').click();
  }

  function renderMonitors(monitors) {
    if (!monitors.length) {
      listEl.innerHTML = '<p class="scenes-muted scenes-empty">No plants yet.</p>';
      return;
    }
    listEl.innerHTML = monitors.map(function (m) {
      var last = m.last_analysis;
      var res = last && last.result ? last.result : {};
      var score = last ? last.health_score : null;
      var color = m.last_color_sample;
      var summary = res.summary || (last && last.error) || (color ? 'Color sample recorded' : 'Awaiting first sample');
      return (
        '<article class="plants-card" data-id="' + m.id + '">' +
        '<div class="plants-card-head">' +
        '<div><h3>' + escapeHtml(m.name) + '</h3>' +
        '<p class="plants-meta">Cam ' + (m.camera_id + 1) +
        (m.species_hint ? ' · ' + escapeHtml(m.species_hint) : '') +
        ' · ' + escapeHtml(scheduleLabel(m)) +
        ' · ' + (m.sample_count || 0) + ' samples' +
        (m.segmentation && m.segmentation.mode === 'color_profile'
          ? ' · <span class="plants-seg-ok">Region set</span>' : '') +
        '</p></div>' +
        '<span class="plants-health ' + healthClass(score) + '">' + (score != null ? score + '/100' : '—') + '</span></div>' +
        '<p class="plants-summary">' + escapeHtml(summary) + '</p>' +
        (color && color.rgb ? '<p class="plants-meta">Last color RGB (' +
          Math.round(color.rgb.r) + ',' + Math.round(color.rgb.g) + ',' + Math.round(color.rgb.b) +
          ') · greenness ' + (color.greenness != null ? color.greenness.toFixed(3) : '—') + '</p>' : '') +
        '<div class="plants-frame-wrap"><img class="plants-frame" data-cam="' + m.camera_id + '" alt="Latest frame from camera ' + (m.camera_id + 1) + '" loading="lazy"></div>' +
        '<div class="plant-viz-wrap"><canvas class="plant-color-canvas" data-monitor="' + m.id + '" height="120"></canvas></div>' +
        '<div class="plants-actions">' +
        '<button type="button" class="btn btn-secondary btn-sm plant-region" data-id="' + m.id + '" data-cam="' + m.camera_id + '">' +
        '<span class="material-icons-outlined icon-sm">masks</span> Set plant region</button>' +
        '<button type="button" class="btn btn-primary btn-sm plant-analyze" data-id="' + m.id + '">Analyze now</button>' +
        '<button type="button" class="btn btn-secondary btn-sm plant-delete" data-id="' + m.id + '">Remove</button>' +
        '</div></article>'
      );
    }).join('');

    listEl.querySelectorAll('.plant-analyze').forEach(function (btn) {
      btn.addEventListener('click', function () { analyze(btn.getAttribute('data-id'), btn); });
    });
    listEl.querySelectorAll('.plant-region').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!window.PlantSegmentEditor) return;
        PlantSegmentEditor.open({
          monitorId: btn.getAttribute('data-id'),
          cameraId: btn.getAttribute('data-cam'),
          onSaved: function () {
            toast('Plant region saved', false);
            loadMonitors();
          },
        });
      });
    });
    listEl.querySelectorAll('.plant-delete').forEach(function (btn) {
      btn.addEventListener('click', function () { removeMonitor(btn.getAttribute('data-id')); });
    });

    listEl.querySelectorAll('.plant-color-canvas').forEach(function (canvas) {
      if (window.PlantColorViz) {
        PlantColorViz.loadAndRender(canvas.getAttribute('data-monitor'), canvas);
      }
    });

    listEl.querySelectorAll('.plants-frame').forEach(function (img) {
      img.addEventListener('error', function () {
        img.parentElement.hidden = true;
      });
      img.src = '/api/scenes/camera/' + img.getAttribute('data-cam') + '/preview?w=640&t=' + Date.now();
    });
  }

  async function loadMonitors() {
    var r = await fetch('/api/plants/monitors');
    var j = await r.json();
    renderMonitors(j.monitors || []);
  }

  async function analyze(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
    try {
      var r = await fetch('/api/plants/monitors/' + id + '/analyze', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || (j.result && j.result.error) || 'Failed');
      toast('Sample captured' + (j.result && j.result.ok ? ' + vision analysis' : ''), false);
      loadMonitors();
    } catch (e) { toast(e.message, true); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Analyze now'; }
    }
  }

  async function removeMonitor(id) {
    if (!confirm('Remove this plant monitor?')) return;
    await fetch('/api/plants/monitors/' + id, { method: 'DELETE' });
    loadMonitors();
  }

  document.getElementById('plants-add').addEventListener('click', function () {
    modal.hidden = false;
    loadCameras();
  });
  document.getElementById('plants-cancel').addEventListener('click', function () { modal.hidden = true; });

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var fd = new FormData(form);
    var mode = fd.get('schedule_mode') || 'interval';
    var body = {
      name: fd.get('name'),
      species_hint: fd.get('species_hint'),
      camera_id: parseInt(camInput.value, 10),
      check_interval_s: parseInt(fd.get('check_interval_s'), 10) || 3600,
      segmentation: {
        mode: 'auto',
        pattern_id: parseInt(fd.get('pattern_id'), 10),
        pattern_camera_id: parseInt(camInput.value, 10),
      },
    };
    if (mode === 'times') {
      body.schedule = {
        mode: 'times',
        times: String(fd.get('schedule_times') || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean),
        enabled: true,
      };
    } else {
      body.schedule = {
        mode: 'interval',
        interval_s: body.check_interval_s,
        enabled: true,
      };
    }
    try {
      var r = await fetch('/api/plants/monitors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Create failed');
      modal.hidden = true;
      form.reset();
      toast('Plant monitor created', false);
      loadMonitors();
    } catch (err) { toast(err.message, true); }
  });

  loadSettings();
  loadMonitors();
})();

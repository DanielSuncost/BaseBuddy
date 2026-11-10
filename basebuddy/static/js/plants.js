(function () {
  var listEl = document.getElementById('plants-list');
  var detailEl = document.getElementById('plants-detail');
  var gridSection = document.getElementById('plants-grid-section');
  var toastEl = document.getElementById('plants-toast');
  var modal = document.getElementById('plants-modal');
  var form = document.getElementById('plants-form');
  var picker = document.getElementById('plant-camera-picker');
  var camInput = document.getElementById('plant-camera-input');
  var scheduleMode = document.getElementById('plant-schedule-mode');

  var monitorsCache = [];
  var activeMonitorId = null;
  var activeRange = 'all'; // 24h | 7d | 30d | all | custom
  var customHours = 168;

  var RANGE_HOURS = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30, all: null };

  function toast(msg, err) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.style.background = err ? '#d93025' : '#202124';
    setTimeout(function () { toastEl.hidden = true; }, 4000);
  }
  window.plantsToast = toast;

  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function fmtCarve(n) {
    var x = parseFloat(n);
    if (!isFinite(x) || x <= 0) return '0';
    if (Math.abs(x - Math.round(x)) < 1e-6) return String(Math.round(x));
    return x.toFixed(1);
  }

  function greenVal(m) {
    var raw = (m && m.sam_growth && m.sam_growth.green_refine) || 0;
    var n = parseFloat(raw);
    if (!isFinite(n)) n = 0;
    return Math.max(0, Math.min(10, n));
  }

  function lumaVal(m) {
    var raw = (m && m.sam_growth && m.sam_growth.luma_carve) || 0;
    var n = parseFloat(raw);
    if (!isFinite(n)) n = 0;
    return Math.max(0, Math.min(100, n));
  }

  var _refineSaveTimer = null;
  function currentGreen() {
    var el = document.getElementById('plants-green-refine');
    return el ? parseFloat(el.value) || 0 : 0;
  }
  function currentLuma() {
    var el = document.getElementById('plants-luma-carve');
    return el ? parseFloat(el.value) || 0 : 0;
  }
  function currentRefine() { return currentGreen(); }

  function healthClass(score) {
    if (score == null) return 'unknown';
    if (score >= 75) return 'good';
    if (score >= 50) return 'mid';
    return 'low';
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

  function hasRegion(m) {
    return m.segmentation && m.segmentation.mode === 'color_profile';
  }

  function trendBadge(trends) {
    if (!trends) return '';
    if (trends.declining_greenness) {
      var metric = trends.trend_metric === 'G_norm' ? 'Normalized greenness' : 'Greenness';
      return '<span class="plants-trend plants-trend-down" title="' + metric + ' declining over window">Declining</span>';
    }
    if (trends.growing_coverage) {
      return '<span class="plants-trend plants-trend-up" title="Coverage increasing">Growing</span>';
    }
    var rate = trends.greenness_rate_per_day;
    if (rate != null && Math.abs(rate) < 0.002) {
      return '<span class="plants-trend plants-trend-stable">Stable</span>';
    }
    return '';
  }

  function hoursForRange() {
    if (activeRange === 'custom') return customHours > 0 ? customHours : null;
    return RANGE_HOURS[activeRange];
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
      : '';
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

  function findMonitor(id) {
    for (var i = 0; i < monitorsCache.length; i++) {
      if (monitorsCache[i].id === id) return monitorsCache[i];
    }
    return null;
  }

  function showGrid() {
    activeMonitorId = null;
    if (detailEl) detailEl.hidden = true;
    if (gridSection) gridSection.hidden = false;
    if (listEl) listEl.hidden = false;
  }

  function showDetail(id) {
    activeMonitorId = id;
    if (gridSection) gridSection.hidden = true;
    if (!detailEl) return;
    detailEl.hidden = false;
    var monitor = findMonitor(id);
    var group = cohortOf(monitor);
    if (group.length > 1) renderSubject(group, id);
    else renderDetail(monitor);
  }

  function subjectKey(m) {
    var sid = m && m.subject_id ? String(m.subject_id).trim() : '';
    return sid || ('solo:' + (m && m.id));
  }

  function cohortOf(m) {
    if (!m) return [];
    var key = subjectKey(m);
    if (key.indexOf('solo:') === 0) return [m];
    return monitorsCache.filter(function (x) { return subjectKey(x) === key; });
  }

  function cameraLabel(m) {
    var num = 'Cam ' + ((m && m.camera_id != null ? m.camera_id : 0) + 1);
    var named = m && m.camera_name ? String(m.camera_name).trim() : '';
    return named ? (named + ' · ' + num) : num;
  }

  function nameStem(name) {
    return String(name || '').toLowerCase().replace(/\s+\d+$/, '').replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function renderMonitors(monitors) {
    monitorsCache = monitors || [];
    if (!monitorsCache.length) {
      listEl.innerHTML = '<p class="scenes-muted scenes-empty">No plants yet. Add a plant to start tracking greenness.</p>';
      if (activeMonitorId) showGrid();
      return;
    }

    var seen = {};
    var groups = [];
    monitorsCache.forEach(function (m) {
      var key = subjectKey(m);
      if (!seen[key]) {
        seen[key] = [];
        groups.push(seen[key]);
      }
      seen[key].push(m);
    });

    listEl.innerHTML = groupSuggestionHtml() + groups.map(function (group) {
      if (group.length > 1) return groupCardHtml(group);
      var m = group[0];
      var last = m.last_analysis;
      var score = last ? last.health_score : null;
      var color = m.last_color_sample;
      var gVal = color && (color.G_norm != null ? color.G_norm
        : (color.G_rel != null ? color.G_rel : color.greenness));
      var g = gVal != null ? Number(gVal).toFixed(3) : '—';
      var gLabel = color && color.G_norm != null ? 'G_norm' : 'green';
      var cov = color && color.coverage != null ? (color.coverage * 100).toFixed(1) + '%' : '—';
      return (
        '<article class="plants-grid-card" data-id="' + m.id + '" tabindex="0" role="button" aria-label="Open ' + escapeHtml(m.name) + '">' +
        '<div class="plants-grid-thumb">' +
        '<img class="plants-grid-img" data-cam="' + m.camera_id + '" alt="" loading="lazy">' +
        (hasRegion(m) ? '<span class="plants-grid-mask-badge">Mask</span>' : '') +
        '</div>' +
        '<div class="plants-grid-body">' +
        '<div class="plants-grid-title-row">' +
        '<h3>' + escapeHtml(m.name) + '</h3>' +
        '<span class="plants-health ' + healthClass(score) + '">' + (score != null ? score : '—') + '</span>' +
        '</div>' +
        '<p class="plants-meta">' + escapeHtml(cameraLabel(m)) +
        (m.species_hint ? ' · ' + escapeHtml(m.species_hint) : '') +
        ' · ' + escapeHtml(scheduleLabel(m)) + '</p>' +
        '<div class="plants-grid-stats">' +
        '<span><strong>' + g + '</strong> ' + gLabel + '</span>' +
        '<span><strong>' + cov + '</strong> cover</span>' +
        '<span><strong>' + (m.sample_count || 0) + '</strong> samples</span>' +
        '</div>' +
        trendBadge(m.trends) +
        '</div></article>'
      );
    }).join('');

    listEl.querySelectorAll('.plants-grid-card').forEach(function (card) {
      function open() { showDetail(card.getAttribute('data-id')); }
      card.addEventListener('click', open);
      card.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
    });

    listEl.querySelectorAll('.plants-grid-img').forEach(function (img) {
      img.addEventListener('error', function () {
        img.classList.add('plants-img-missing');
      });
      img.src = '/api/scenes/camera/' + img.getAttribute('data-cam') + '/preview?w=480&t=' + Date.now();
    });

    var suggest = document.getElementById('plants-group-suggest');
    if (suggest) {
      suggest.addEventListener('click', function () {
        var ids = (suggest.getAttribute('data-ids') || '').split(',').filter(Boolean);
        if (ids.length >= 2) linkAngles(ids[0], ids);
      });
    }

    if (activeMonitorId) {
      var still = findMonitor(activeMonitorId);
      if (still) showDetail(activeMonitorId);
      else showGrid();
    }
  }

  function groupSuggestionHtml() {
    var buckets = {};
    monitorsCache.forEach(function (m) {
      if (m.subject_id) return;
      var stem = nameStem(m.name);
      if (!stem) return;
      if (!buckets[stem]) buckets[stem] = [];
      buckets[stem].push(m);
    });
    var hit = null;
    Object.keys(buckets).forEach(function (stem) {
      if (!hit && buckets[stem].length >= 2) hit = buckets[stem];
    });
    if (!hit) return '';
    var names = hit.map(function (m) { return m.name; }).join(' and ');
    return '<button type="button" class="plants-group-suggest" id="plants-group-suggest" data-ids="' +
      hit.map(function (m) { return m.id; }).join(',') + '">' +
      escapeHtml(names) + ' look like one plant. Group these cameras.</button>';
  }

  function groupCardHtml(group) {
    var title = (group[0].subject_name || group.slice().sort(function (a, b) {
      return String(a.name).length - String(b.name).length;
    })[0].name);
    var cams = group.map(cameraLabel).join(' · ');
    var thumbs = group.slice(0, 2).map(function (m) {
      return '<img class="plants-grid-img" data-cam="' + m.camera_id + '" alt="" loading="lazy">';
    }).join('');
    return '<article class="plants-grid-card plants-grid-card-group" data-id="' + group[0].id +
      '" tabindex="0" role="button" aria-label="Open ' + escapeHtml(title) + '">' +
      '<div class="plants-grid-thumb plants-grid-thumb-pair">' + thumbs + '</div>' +
      '<div class="plants-grid-body">' +
      '<div class="plants-grid-title-row"><h3>' + escapeHtml(title) + '</h3>' +
      '<span class="plants-angle-count">' + group.length + ' angles</span></div>' +
      '<p class="plants-meta">' + escapeHtml(cams) + ' · separate video and charts, one plant decision</p>' +
      '</div></article>';
  }

  function rangeButtonsHtml() {
    var keys = ['24h', '7d', '30d', 'all', 'custom'];
    return keys.map(function (k) {
      var label = k === 'custom' ? 'Custom' : (k === 'all' ? 'All' : k);
      return '<button type="button" class="plants-range-btn' + (activeRange === k ? ' active' : '') +
        '" data-range="' + k + '">' + label + '</button>';
    }).join('');
  }

  function formatRate(rate) {
    if (rate == null) return '—';
    var sign = rate > 0 ? '+' : '';
    return sign + rate.toFixed(4) + '/day';
  }

  function renderDetail(m, opts) {
    opts = opts || {};
    var host = opts.root || detailEl;
    if (!host || !m) return;
    var last = m.last_analysis;
    var res = last && last.result ? last.result : {};
    var score = last ? last.health_score : null;
    var color = m.last_color_sample;
    var trends = m.trends || {};
    var summary = res.summary || (last && last.error) || (color ? 'Color sample recorded' : 'Awaiting first sample');
    var gRel = color && (color.G_rel != null ? color.G_rel : color.greenness);
    var gRelStr = gRel != null ? Number(gRel).toFixed(3) : '—';
    var pixels = color && color.plant_pixels != null ? Number(color.plant_pixels).toLocaleString()
      : (trends.leaf_area_proxy != null ? Number(trends.leaf_area_proxy).toLocaleString() : '—');

    var bar = opts.embedded ? '' : (
      '<div class="plants-detail-bar">' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-detail-back">' +
      '<span class="material-icons-outlined icon-sm">arrow_back</span> All plants</button>' +
      '<div class="plants-detail-title">' +
      '<h2>' + escapeHtml(m.name) + '</h2>' +
      '<span class="plants-health ' + healthClass(score) + '">' + (score != null ? score + '/100' : '—') + '</span>' +
      trendBadge(trends) +
      '</div></div>'
    );

    var rename = opts.embedded ? '' : (
      '<div class="plant-rename">' +
      '<label>Plant<input id="plant-rename-plant" value="' + escapeHtml(m.name) + '"></label>' +
      '<label>Camera<input id="plant-rename-camera" value="' + escapeHtml(m.camera_name || '') +
      '" placeholder="' + escapeHtml(cameraLabel(m)) + '"></label>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plant-rename-save">Save names</button>' +
      '</div>'
    );

    host.innerHTML = bar + rename +

      '<p class="plants-meta plants-detail-meta">' + escapeHtml(cameraLabel(m)) +
      (m.species_hint ? ' · ' + escapeHtml(m.species_hint) : '') +
      ' · ' + escapeHtml(scheduleLabel(m)) +
      ' · ' + (m.sample_count || 0) + ' samples' +
      (hasRegion(m) ? ' · <span class="plants-seg-ok">Region set</span>' : ' · <span class="plants-seg-missing">No region yet</span>') +
      '</p>' +
      '<p class="plants-summary">' + escapeHtml(summary) + '</p>' +

      '<div class="plants-detail-layout">' +
      '<div class="plants-detail-media card">' +
      '<div class="plants-detail-media-head">' +
      '<span>Masked live frame</span>' +
      '<label class="plants-check"><input type="checkbox" id="plants-show-mask" checked> Show mask</label>' +
      '</div>' +
      '<div class="plants-frame-wrap plants-detail-frame">' +
      '<img id="plants-detail-frame" class="plants-frame" alt="Plant region overlay" loading="lazy">' +
      '</div>' +
      '<p class="plants-muted" id="plants-mask-hint">' +
      (hasRegion(m)
        ? 'Locked to the saved region' + (m.frame_crop ? ', cropped to your frame.' : '.')
        : 'No saved region yet. Edit mask to lock the plant.') +
      '</p>' +
      '<div class="plants-carve-grid">' +
      '<label class="plants-refine" title="0 keeps the full region. 0.2–0.8 trims pot and gaps; 1 drops a lot of foliage.">' +
      '<span>Drop non-green <strong id="plants-refine-val">' + fmtCarve(greenVal(m)) + '</strong></span>' +
      '<input type="range" id="plants-green-refine" min="0" max="10" step="0.1" value="' + greenVal(m) + '">' +
      '</label>' +
      '<label class="plants-refine" title="0 is off. 70–85 drops white shelf and blown highlights.">' +
      '<span>Drop brighter than <strong id="plants-luma-val">' + (lumaVal(m) ? lumaVal(m) : 'off') + '</strong></span>' +
      '<input type="range" id="plants-luma-carve" min="0" max="100" step="1" value="' + lumaVal(m) + '">' +
      '</label>' +
      '</div>' +
      '<div class="plants-toolbar">' +
      '<div class="plants-toolbar-main">' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-detail-region" data-id="' + m.id + '" data-cam="' + m.camera_id + '">' +
      '<span class="material-icons-outlined icon-sm">masks</span> ' + (hasRegion(m) ? 'Edit mask' : 'Set mask') + '</button>' +
      '<button type="button" class="btn btn-primary btn-sm" id="plants-detail-analyze" data-id="' + m.id + '">Analyze now</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-rebuild" title="Stamp this mask and these carves onto every still still on disk. Older series stay.">Rebuild history</button>' +
      '</div>' +
      '<div class="plants-toolbar-more">' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-detail-water" data-id="' + m.id + '" title="Log watering">' +
      '<span class="material-icons-outlined icon-sm">water_drop</span></button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-detail-refresh" title="Refresh">' +
      '<span class="material-icons-outlined icon-sm">refresh</span></button>' +
      '<button type="button" class="btn btn-secondary btn-sm plants-danger" id="plants-detail-delete" data-id="' + m.id + '" title="Remove">' +
      '<span class="material-icons-outlined icon-sm">delete</span></button>' +
      '</div></div></div>' +

      '<div class="plants-detail-side">' +
      '<div class="plants-stat-grid plants-stat-grid-compact">' +
      '<div class="plants-stat"><span class="plants-stat-label">G_rel</span><span class="plants-stat-value">' + gRelStr + '</span></div>' +
      '<div class="plants-stat"><span class="plants-stat-label">Leaf area</span><span class="plants-stat-value">' + pixels + ' px</span></div>' +
      '<div class="plants-stat"><span class="plants-stat-label">Δ green / day</span><span class="plants-stat-value">' + formatRate(trends.greenness_rate_per_day) + '</span></div>' +
      '<div class="plants-stat"><span class="plants-stat-label">Δ cover / day</span><span class="plants-stat-value">' + formatRate(trends.coverage_rate_per_day) + '</span></div>' +
      '</div>' +
      (trends.declining_greenness
        ? '<p class="plants-alert plants-alert-warn">Greenness is declining — check watering, light, or pests.</p>'
        : '') +
      '</div></div>' +

      '<div class="card plants-plot-card">' +
      '<div class="plants-plot-head">' +
      '<span>Health over time</span>' +
      '<div class="plants-range-group" id="plants-range-group">' + rangeButtonsHtml() + '</div>' +
      '</div>' +
      '<div class="plants-custom-range" id="plants-custom-range"' + (activeRange === 'custom' ? '' : ' hidden') + '>' +
      '<label>Hours <input type="number" id="plants-custom-hours" min="1" max="8760" value="' + customHours + '"></label>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-custom-apply">Apply</button>' +
      '</div>' +
      '<div class="plant-viz-wrap plant-viz-detail" id="plants-detail-plot"></div>' +
      '</div>' +

      '<div class="card sam-growth-section" id="plants-sam-growth"></div>';

    bindDetail(m);
    loadDetailFrame(m, true);
    loadDetailPlot(m);
    loadSamGrowth(m);
  }

  function bindDetail(m) {
    var back = document.getElementById('plants-detail-back');
    if (back) back.addEventListener('click', showGrid);
    var renameSave = document.getElementById('plant-rename-save');
    if (renameSave && !document.getElementById('plant-rename-angle')) {
      renameSave.addEventListener('click', function () {
        saveNames(m.id, {
          plant_name: document.getElementById('plant-rename-plant').value,
          camera_name: document.getElementById('plant-rename-camera').value,
        });
      });
    }

    document.getElementById('plants-show-mask').addEventListener('change', function (e) {
      loadDetailFrame(m, e.target.checked);
    });

    var refinePreviewTimer = null;
    function bindCarveSlider(inputId, valId, fmt) {
      var el = document.getElementById(inputId);
      if (!el) return;
      el.addEventListener('input', function () {
        var valEl = document.getElementById(valId);
        if (valEl) valEl.textContent = fmt(el.value);
        if (refinePreviewTimer) clearTimeout(refinePreviewTimer);
        refinePreviewTimer = setTimeout(function () {
          loadDetailFrame(m, document.getElementById('plants-show-mask').checked);
        }, 80);
        if (_refineSaveTimer) clearTimeout(_refineSaveTimer);
        _refineSaveTimer = setTimeout(function () {
          saveCarves(m.id, currentGreen(), currentLuma());
        }, 400);
      });
    }
    bindCarveSlider('plants-green-refine', 'plants-refine-val', fmtCarve);
    bindCarveSlider('plants-luma-carve', 'plants-luma-val', function (v) {
      var n = parseFloat(v) || 0;
      return n > 0 ? String(Math.round(n)) : 'off';
    });

    document.getElementById('plants-detail-refresh').addEventListener('click', function () {
      loadDetailFrame(m, document.getElementById('plants-show-mask').checked);
      loadDetailPlot(m);
      loadSamGrowth(m);
    });

    document.getElementById('plants-detail-region').addEventListener('click', function () {
      if (!window.PlantSegmentEditor) return;
      PlantSegmentEditor.open({
        monitorId: m.id,
        cameraId: m.camera_id,
        greenRefine: currentGreen(),
        lumaCarve: currentLuma(),
        frameCrop: m.frame_crop || null,
        onSaved: function (j) {
          toast((j && j.reanalyze) ? 'Region saved — rebuilding history…' : 'Plant region saved', false);
          loadMonitors().then(function () {
            if (activeMonitorId) showDetail(activeMonitorId);
          });
        },
      });
    });

    document.getElementById('plants-detail-analyze').addEventListener('click', function (e) {
      analyze(m.id, e.currentTarget);
    });

    var rebuildBtn = document.getElementById('plants-rebuild');
    if (rebuildBtn) {
      rebuildBtn.addEventListener('click', function () {
        rebuildHistory(m, rebuildBtn);
      });
    }

    document.getElementById('plants-detail-water').addEventListener('click', function () {
      logWatering(m);
    });

    document.getElementById('plants-detail-delete').addEventListener('click', function () {
      removeMonitor(m.id);
    });

    document.querySelectorAll('#plants-range-group .plants-range-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeRange = btn.getAttribute('data-range');
        var customWrap = document.getElementById('plants-custom-range');
        if (customWrap) customWrap.hidden = activeRange !== 'custom';
        document.querySelectorAll('#plants-range-group .plants-range-btn').forEach(function (b) {
          b.classList.toggle('active', b.getAttribute('data-range') === activeRange);
        });
        if (activeRange !== 'custom') loadDetailPlot(m);
      });
    });

    var applyBtn = document.getElementById('plants-custom-apply');
    if (applyBtn) {
      applyBtn.addEventListener('click', function () {
        var input = document.getElementById('plants-custom-hours');
        customHours = Math.max(1, parseInt(input.value, 10) || 168);
        loadDetailPlot(m);
      });
    }
  }

  async function saveCarves(monitorId, green, luma) {
    try {
      var r = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) +
        '/sam-growth/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ green_refine: green, luma_carve: luma }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Could not save carve');
      var mon = monitorsCache.filter(function (x) { return x.id === monitorId; })[0];
      if (mon) {
        mon.sam_growth = Object.assign({}, mon.sam_growth || {}, {
          green_refine: green,
          luma_carve: luma,
        });
      }
    } catch (err) {
      toast(err.message, true);
    }
  }

  function loadDetailFrame(m, showMask) {
    var img = document.getElementById('plants-detail-frame');
    if (!img) return;
    var t = Date.now();
    if (showMask) {
      img.src = '/api/plants/monitors/' + m.id + '/mask-overlay?w=960&green_refine=' +
        currentGreen() + '&luma_carve=' + currentLuma() + '&t=' + t;
    } else {
      img.src = '/api/plants/monitors/' + m.id + '/live-frame?w=960&t=' + t;
    }
    img.onerror = function () {
      img.src = '/api/scenes/camera/' + m.camera_id + '/preview?w=960&t=' + t;
      var hint = document.getElementById('plants-mask-hint');
      if (hint && showMask) hint.textContent = 'Mask overlay unavailable — showing raw preview.';
    };
  }

  async function loadDetailPlot(m, extra) {
    var root = document.getElementById('plants-detail-plot');
    if (!root || !window.PlantColorViz) return;
    var hours = hoursForRange();
    var opts = { limit: 2000 };
    if (hours != null) opts.hours = hours;
    extra = extra || {};
    if (extra.seriesId != null) opts.seriesId = extra.seriesId;
    var j = await PlantColorViz.loadAndRender(m.id, root, opts);
    // Refresh trend stats on detail without full list reload
    if (j.trends && activeMonitorId === m.id) {
      m.trends = j.trends;
    }
  }

  function loadSamGrowth(m) {
    var root = document.getElementById('plants-sam-growth');
    if (!root || !window.PlantSamGrowth) return;
    PlantSamGrowth.mount(root, m.id, {
      hasMask: hasRegion(m),
      plantName: m.name || 'plant',
      onEditMask: function () {
        var btn = document.getElementById('plants-detail-region');
        if (btn) btn.click();
      },
      onReanalyzeDone: function (seriesId) {
        loadDetailPlot(m, { seriesId: seriesId });
      },
    });
  }

  async function logWatering(m) {
    var note = window.prompt('Optional note for this watering (leave blank for none):', '');
    if (note === null) return; // cancelled
    try {
      var r = await fetch('/api/plants/monitors/' + m.id + '/watering', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: note }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Failed');
      toast('Watering logged', false);
      loadDetailPlot(m);
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function loadMonitors() {
    var r = await fetch('/api/plants/monitors');
    var j = await r.json();
    renderMonitors(j.monitors || []);
  }

  async function rebuildHistory(m, btn) {
    if (!window.confirm(
      'Build a new series from every still still on disk, using the current mask, crop, and carves? Existing series stay in the list.'
    )) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Rebuilding…';
    }
    try {
      await saveCarves(m.id, currentGreen(), currentLuma());
      var r = await fetch('/api/plants/monitors/' + encodeURIComponent(m.id) + '/reanalyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Could not start rebuild');
      toast('Rebuilding history…', false);
      loadSamGrowth(m);
    } catch (err) {
      toast(err.message, true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Rebuild history';
      }
    }
  }

  async function analyze(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
    try {
      var r = await fetch('/api/plants/monitors/' + id + '/analyze', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || (j.result && j.result.error) || 'Failed');
      var growth = j.sam_growth || {};
      toast(growth.ok && !growth.skipped ? 'Live sample recorded' : 'Sample captured', false);
      await loadMonitors();
      if (activeMonitorId === id) showDetail(id);
    } catch (e) { toast(e.message, true); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Analyze now'; }
    }
  }

  async function removeMonitor(id) {
    if (!confirm('Remove this plant monitor?')) return;
    await fetch('/api/plants/monitors/' + id, { method: 'DELETE' });
    showGrid();
    loadMonitors();
  }

  function mediaUrl(path) {
    if (!path) return '';
    if (String(path).indexOf('http') === 0 || String(path).charAt(0) === '/') return path;
    return '/' + String(path).replace(/\\/g, '/');
  }

  function subjectTitle(group) {
    var named = group.filter(function (m) { return m.subject_name; })[0];
    if (named) return named.subject_name;
    return group.slice().sort(function (a, b) {
      return String(a.name).length - String(b.name).length;
    })[0].name;
  }

  function renderSubject(group, selectedId) {
    var title = subjectTitle(group);
    var others = monitorsCache.filter(function (m) {
      return !group.some(function (g) { return g.id === m.id; });
    });
    detailEl.innerHTML =
      '<div class="plants-detail-bar">' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plants-subject-back">' +
      '<span class="material-icons-outlined icon-sm">arrow_back</span> All plants</button>' +
      '<div class="plants-detail-title"><h2>' + escapeHtml(title) + '</h2>' +
      '<span class="plants-angle-count">' + group.length + ' angles</span></div></div>' +
      '<div id="plant-cohort-stats" class="plant-cohort-stats"><p class="plants-muted">Reading both cameras…</p></div>' +
      '<div class="plant-angle-tabs" id="plant-angle-tabs">' +
      group.map(function (m) {
        return '<button type="button" class="plant-angle-tab' + (m.id === selectedId ? ' active' : '') +
          '" data-id="' + m.id + '">' + escapeHtml(m.name) +
          '<span>' + escapeHtml(cameraLabel(m)) + '</span></button>';
      }).join('') +
      '</div>' +
      '<div class="plant-rename">' +
      '<label>Plant<input id="plant-rename-plant" value="' + escapeHtml(title) + '"></label>' +
      '<label>This angle<input id="plant-rename-angle" value="' + escapeHtml((findMonitor(selectedId) || {}).name || '') + '"></label>' +
      '<label>Camera<input id="plant-rename-camera" value="' + escapeHtml((findMonitor(selectedId) || {}).camera_name || '') +
      '" placeholder="' + escapeHtml(cameraLabel(findMonitor(selectedId) || {})) + '"></label>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plant-rename-save">Save names</button>' +
      '</div>' +
      '<div class="plant-angle-tools">' +
      (others.length
        ? '<label>Add a camera <select id="plant-add-angle"><option value="">Choose…</option>' +
          others.map(function (m) {
            return '<option value="' + m.id + '">' + escapeHtml(m.name) + ' · ' + escapeHtml(cameraLabel(m)) + '</option>';
          }).join('') + '</select></label>'
        : '') +
      '<button type="button" class="btn btn-secondary btn-sm" id="plant-unlink">Unlink this camera</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="plant-judge">Judge this plant</button>' +
      '</div>' +
      '<div id="plants-angle-slot"></div>' +
      '<div class="card plant-sync" id="plant-sync"><p class="plants-muted">Loading synced playback…</p></div>' +
      '<div class="card plant-brief" id="plant-brief-card" hidden></div>';

    document.getElementById('plants-subject-back').addEventListener('click', showGrid);
    document.getElementById('plant-angle-tabs').addEventListener('click', function (e) {
      var btn = e.target.closest('.plant-angle-tab');
      if (!btn) return;
      showDetail(btn.getAttribute('data-id'));
    });
    var add = document.getElementById('plant-add-angle');
    if (add) {
      add.addEventListener('change', function () {
        if (!add.value) return;
        var ids = group.map(function (m) { return m.id; }).concat([add.value]);
        linkAngles(selectedId, ids, title);
      });
    }
    document.getElementById('plant-unlink').addEventListener('click', function () {
      unlinkAngle(selectedId);
    });
    document.getElementById('plant-judge').addEventListener('click', function (e) {
      judgePlant(selectedId, e.currentTarget);
    });
    document.getElementById('plant-rename-save').addEventListener('click', function () {
      saveNames(selectedId, {
        plant_name: document.getElementById('plant-rename-plant').value,
        angle_name: document.getElementById('plant-rename-angle').value,
        camera_name: document.getElementById('plant-rename-camera').value,
      });
    });
    renderDetail(findMonitor(selectedId), {
      root: document.getElementById('plants-angle-slot'),
      embedded: true,
    });
    loadCohort(selectedId);
    loadSync(selectedId);
  }

  async function loadCohort(id) {
    var box = document.getElementById('plant-cohort-stats');
    if (!box) return;
    try {
      var r = await fetch('/api/plants/monitors/' + encodeURIComponent(id) + '/cohort');
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Could not read angles');
      var c = (j.brief && j.brief.combined) || {};
      var g = c.mean_g_norm != null ? Number(c.mean_g_norm).toFixed(3) : '—';
      var cover = c.mean_coverage != null ? (c.mean_coverage * 100).toFixed(1) + '%' : '—';
      var rate = c.mean_rate_per_day != null ? formatRate(c.mean_rate_per_day) : '—';
      var tone = c.alert ? 'plants-alert-warn' : (c.split ? 'plants-alert-split' : '');
      box.innerHTML =
        '<div class="plants-stat"><span class="plants-stat-label">Mean G_norm</span><span class="plants-stat-value">' + g + '</span></div>' +
        '<div class="plants-stat"><span class="plants-stat-label">Mean cover</span><span class="plants-stat-value">' + cover + '</span></div>' +
        '<div class="plants-stat"><span class="plants-stat-label">Mean Δ / day</span><span class="plants-stat-value">' + rate + '</span></div>' +
        (c.reason ? '<p class="plants-alert ' + tone + '">' + escapeHtml(c.reason) + '</p>' : '<p class="plants-muted">Angles are steady. Each camera still keeps its own chart.</p>');
      box._brief = j.brief;
    } catch (err) {
      box.innerHTML = '<p class="plants-alert plants-alert-warn">' + escapeHtml(err.message) + '</p>';
    }
  }

  function loadSync(id) {
    var root = document.getElementById('plant-sync');
    if (!root) return;
    fetch('/api/plants/monitors/' + encodeURIComponent(id) + '/sync-playback')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!document.getElementById('plant-sync')) return;
        if (!j.ok) throw new Error(j.error || 'Playback failed');
        mountSync(j);
      })
      .catch(function (err) {
        if (root) root.innerHTML = '<p class="plants-alert plants-alert-warn">' + escapeHtml(err.message) + '</p>';
      });
  }

  function mountSync(data) {
    var root = document.getElementById('plant-sync');
    if (!root) return;
    var times = data.times || [];
    var angles = data.angles || [];
    if (!times.length) {
      root.innerHTML = '<p class="plants-muted">Synced playback starts once each camera has stored a still.</p>';
      return;
    }
    root.innerHTML =
      '<div class="plant-sync-head"><h3>Both angles, one clock</h3>' +
      '<span id="plant-sync-when"></span></div>' +
      '<div class="plant-sync-row" id="plant-sync-row"></div>' +
      '<input type="range" id="plant-sync-slider" min="0" max="' + (times.length - 1) + '" value="' + (times.length - 1) + '">';
    var slider = document.getElementById('plant-sync-slider');
    function show(i) {
      var t = times[i];
      var when = document.getElementById('plant-sync-when');
      if (when) when.textContent = new Date(t * 1000).toLocaleString();
      var row = document.getElementById('plant-sync-row');
      row.innerHTML = angles.map(function (angle) {
        var frame = (angle.frames || [])[i];
        var held = frame && frame.held_s > 120;
        var img = frame && frame.image_path
          ? '<img src="' + escapeHtml(mediaUrl(frame.image_path)) + '" alt="">'
          : '<div class="plant-sync-gap">No frame</div>';
        return '<figure class="plant-sync-shot">' + img +
          '<figcaption>' + escapeHtml(angle.name || 'Angle') +
          (held ? ' · held' : '') + '</figcaption></figure>';
      }).join('');
    }
    slider.addEventListener('input', function () { show(parseInt(slider.value, 10) || 0); });
    show(times.length - 1);
  }

  async function judgePlant(id, btn) {
    var card = document.getElementById('plant-brief-card');
    if (btn) { btn.disabled = true; btn.textContent = 'Judging…'; }
    try {
      var r = await fetch('/api/plants/monitors/' + encodeURIComponent(id) + '/judge', { method: 'POST' });
      var j = await r.json();
      if (!card) return;
      card.hidden = false;
      var result = j.result || {};
      if (!j.ok) throw new Error(result.error || j.error || 'Judge failed');
      var notes = (result.angle_notes || []).map(function (n) {
        return '<li><strong>' + escapeHtml(n.camera || '') + '</strong> ' + escapeHtml(n.observation || '') + '</li>';
      }).join('');
      card.innerHTML =
        '<h3>Plant judgement</h3>' +
        '<p>' + escapeHtml(result.summary || '') + '</p>' +
        '<p class="plants-meta">Score ' + (result.health_score != null ? result.health_score : '—') +
        ' · ' + escapeHtml(result.agreement || '') +
        (result.alert ? ' · alert' : '') + '</p>' +
        (notes ? '<ul>' + notes + '</ul>' : '');
      toast('Judgement ready', false);
    } catch (err) {
      toast(err.message, true);
      if (card) {
        card.hidden = false;
        card.innerHTML = '<p class="plants-alert plants-alert-warn">' + escapeHtml(err.message) + '</p>';
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Judge this plant'; }
    }
  }

  async function saveNames(id, fields) {
    try {
      var r = await fetch('/api/plants/monitors/' + encodeURIComponent(id) + '/names', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Could not rename');
      toast('Names saved', false);
      activeMonitorId = id;
      await loadMonitors();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function linkAngles(primaryId, ids, name) {
    var r = await fetch('/api/plants/monitors/' + encodeURIComponent(primaryId) + '/angles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ monitor_ids: ids, name: name || '' }),
    });
    var j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Could not group cameras');
    toast('Cameras grouped as one plant', false);
    activeMonitorId = primaryId;
    await loadMonitors();
  }

  async function unlinkAngle(id) {
    var r = await fetch('/api/plants/monitors/' + encodeURIComponent(id) + '/angles', { method: 'DELETE' });
    var j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Could not unlink');
    toast('Camera unlinked', false);
    activeMonitorId = id;
    await loadMonitors();
  }

  function fillSamePlantSelect() {
    var sel = document.getElementById('plant-same-as');
    if (!sel) return;
    var current = sel.value;
    sel.innerHTML = '<option value="">New plant</option>' + monitorsCache.map(function (m) {
      return '<option value="' + m.id + '">' + escapeHtml(m.name) + ' · ' + escapeHtml(cameraLabel(m)) + '</option>';
    }).join('');
    sel.value = current;
  }

  document.getElementById('plants-add').addEventListener('click', function () {
    modal.hidden = false;
    loadCameras();
    fillSamePlantSelect();
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
      var same = fd.get('same_plant');
      modal.hidden = true;
      form.reset();
      if (same && j.monitor && j.monitor.id) {
        var peer = findMonitor(same);
        var ids = [same, j.monitor.id];
        if (peer && peer.subject_id) {
          monitorsCache.forEach(function (m) {
            if (m.subject_id === peer.subject_id && ids.indexOf(m.id) < 0) ids.push(m.id);
          });
        }
        await linkAngles(j.monitor.id, ids, peer && (peer.subject_name || peer.name));
      } else {
        toast('Plant monitor created', false);
        await loadMonitors();
        if (j.monitor && j.monitor.id) showDetail(j.monitor.id);
      }
    } catch (err) { toast(err.message, true); }
  });

  loadSettings();
  loadMonitors();
})();

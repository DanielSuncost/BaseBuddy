/**
 * SAM growth tracking UI — compressed night-aware charts, series picker,
 * and a time-linked mask / raw / plot playback.
 */
window.PlantSamGrowth = (function () {
  var COLORS = {
    area: '#0f766e',
    areaFill: 'rgba(20, 184, 166, 0.22)',
    green: '#16a34a',
    greenFill: 'rgba(34, 197, 94, 0.18)',
    pct: '#0284c7',
    pctFill: 'rgba(14, 165, 233, 0.16)',
    grid: 'rgba(148, 163, 184, 0.22)',
    gridStrong: 'rgba(100, 116, 139, 0.28)',
    axis: '#64748b',
    label: '#134e4a',
    surface0: '#f8fffd',
    surface1: '#ecfdf5',
    night: 'rgba(15, 23, 42, 0.07)',
    playhead: '#f59e0b',
  };
  var FONT = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
  var NIGHT_WEIGHT = 0.07;
  var _playerTimer = null;
  var SPAN_DAYS = { '7d': 7, '14d': 14, '30d': 30, '90d': 90 };
  var growthSpan = 'all';
  var growthSpanDays = 14;

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtDate(ts) {
    if (ts == null) return '—';
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  }

  function fmtFull(ts) {
    if (ts == null) return '—';
    return new Date(ts * 1000).toLocaleString(undefined, {
      weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  }

  function fmtShort(ts) {
    if (ts == null) return '—';
    return new Date(ts * 1000).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric',
    });
  }

  function stillUrl(path) {
    if (!path) return '';
    if (path.indexOf('http') === 0 || path.charAt(0) === '/') return path;
    return '/' + path.replace(/\\/g, '/');
  }

  function silhouetteUrlFromOverlay(url) {
    if (!url) return '';
    return String(url).replace(/mask_([^/?#]+)/, 'sil_$1');
  }

  function normalizePlaybackFrame(f) {
    var overlay = stillUrl(f.overlay_url || f.mask_path || '');
    var mask = stillUrl(f.mask_url || f.silhouette_path || '');
    if (!mask || mask === overlay || /\/mask_/.test(mask)) {
      mask = silhouetteUrlFromOverlay(overlay);
    }
    return {
      sampled_at: f.sampled_at,
      overlay_url: overlay,
      mask_url: mask,
      raw_url: stillUrl(f.raw_url || ''),
      mask_area_px: f.mask_area_px,
      green_pixels_in_mask: f.green_pixels_in_mask,
      green_fraction_in_mask: f.green_fraction_in_mask,
    };
  }

  function isLightsOut(s) {
    if (!s) return false;
    if (s.lights_out) return true;
    var flags = s.quality_flags || [];
    if (flags.indexOf('plant_desaturated') >= 0) return true;
    var greenZero = s.green_pixels_in_mask === 0 || s.green_fraction_in_mask === 0;
    if (flags.indexOf('low_green_in_mask') >= 0 && greenZero) return true;
    if (s.L_plant != null && s.L_plant > 0.7 && greenZero) return true;
    return false;
  }

  function attachCompressedX(samples) {
    var x = 0;
    (samples || []).forEach(function (s, i) {
      if (i === 0) {
        s._cx = 0;
        return;
      }
      var dt = Math.max(0, (s.sampled_at || 0) - (samples[i - 1].sampled_at || 0));
      var night = isLightsOut(s) || isLightsOut(samples[i - 1]);
      x += night ? dt * NIGHT_WEIGHT : dt;
      s._cx = x;
    });
    var last = samples && samples.length ? samples[samples.length - 1]._cx : 0;
    return Math.max(last || 0, 1);
  }

  function rangeOf(vals) {
    var ok = vals.filter(function (v) { return v != null && isFinite(v); });
    if (!ok.length) return null;
    var mn = Math.min.apply(null, ok);
    var mx = Math.max.apply(null, ok);
    var pad = Math.max(Math.abs(mx - mn) * 0.14, Math.abs(mx) * 0.03, 1);
    if (mx - mn < 1e-9) return { min: mn - pad, max: mx + pad };
    return { min: mn - pad * 0.12, max: mx + pad * 0.18 };
  }

  function setupCanvas(canvas, cssH) {
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || canvas.parentElement.clientWidth || 320;
    var h = cssH || 168;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.max(1, Math.floor(h * dpr));
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }

  function fillChartSurface(ctx, w, h, dark) {
    if (dark) {
      var g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, '#0f172a');
      g.addColorStop(1, '#111827');
      ctx.fillStyle = g;
    } else {
      var lg = ctx.createLinearGradient(0, 0, w, h);
      lg.addColorStop(0, COLORS.surface0);
      lg.addColorStop(1, COLORS.surface1);
      ctx.fillStyle = lg;
    }
    ctx.fillRect(0, 0, w, h);
  }

  function drawLineChart(canvas, samples, key, opts) {
    opts = opts || {};
    var color = opts.color || COLORS.area;
    var fill = opts.fill || null;
    var formatY = opts.formatY || function (v) { return Math.round(v).toLocaleString(); };
    var title = opts.title || '';
    var height = opts.height || 178;
    var until = opts.until;
    var playhead = opts.playhead;
    var dark = !!opts.dark;
    var setup = setupCanvas(canvas, height);
    var ctx = setup.ctx;
    var w = setup.w;
    var h = setup.h;
    var padL = 54;
    var padR = 14;
    var padT = title ? 32 : 16;
    var padB = 28;
    var plotW = w - padL - padR;
    var plotH = h - padT - padB;

    fillChartSurface(ctx, w, h, dark);

    if (title) {
      ctx.fillStyle = dark ? '#e2e8f0' : COLORS.label;
      ctx.font = '650 12px ' + FONT;
      ctx.textAlign = 'left';
      ctx.fillText(title, padL, 18);
    }

    var usable = (samples || []).filter(function (s) {
      return s.ok !== false && s[key] != null && isFinite(s[key]);
    });
    if (until != null) {
      usable = usable.filter(function (s) { return s.sampled_at <= until; });
    }
    var axisSamples = usable.length ? usable : (samples || []);
    var cmax = attachCompressedX(axisSamples);
    var dayPts = usable.filter(function (s) { return !isLightsOut(s); });

    if (!dayPts.length) {
      ctx.fillStyle = dark ? '#94a3b8' : '#94a3b8';
      ctx.font = '500 12px ' + FONT;
      ctx.textAlign = 'center';
      ctx.fillText('No daytime samples yet', w / 2, h / 2);
      canvas._samHover = null;
      return;
    }

    var vals = dayPts.map(function (s) { return s[key]; });
    var rng = rangeOf(vals);

    function xFor(s) {
      return padL + ((s._cx || 0) / cmax) * plotW;
    }
    function yFor(v) {
      return padT + plotH - ((v - rng.min) / Math.max(1e-9, rng.max - rng.min)) * plotH;
    }

    // Compressed night bands
    var nightStart = null;
    axisSamples.forEach(function (s, i) {
      var night = isLightsOut(s);
      if (night && nightStart == null) nightStart = s;
      var endNight = (!night || i === axisSamples.length - 1) && nightStart;
      if (endNight) {
        var a = xFor(nightStart);
        var b = xFor(night ? s : axisSamples[Math.max(0, i - 1)]);
        ctx.fillStyle = dark ? 'rgba(15, 23, 42, 0.35)' : COLORS.night;
        ctx.fillRect(Math.min(a, b), padT, Math.max(2, Math.abs(b - a)), plotH);
        nightStart = night && i === axisSamples.length - 1 ? null : null;
      }
    });

    for (var i = 0; i <= 4; i++) {
      var gy = padT + (plotH * i) / 4;
      ctx.strokeStyle = i === 4 ? COLORS.gridStrong : COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(padL + plotW, gy);
      ctx.stroke();
    }
    ctx.fillStyle = dark ? '#94a3b8' : COLORS.axis;
    ctx.font = '500 10px ' + FONT;
    ctx.textAlign = 'right';
    [rng.max, (rng.min + rng.max) / 2, rng.min].forEach(function (v, yi) {
      ctx.fillText(formatY(v), padL - 6, padT + (plotH * yi) / 2 + 3);
    });
    ctx.textAlign = 'left';
    ctx.fillText(fmtShort(dayPts[0].sampled_at), padL, h - 8);
    ctx.textAlign = 'right';
    ctx.fillText(fmtShort(dayPts[dayPts.length - 1].sampled_at), padL + plotW, h - 8);
    ctx.textAlign = 'center';
    ctx.font = '500 9px ' + FONT;
    ctx.fillStyle = dark ? '#64748b' : '#94a3b8';
    ctx.fillText('nights compressed', padL + plotW / 2, h - 8);

    if (fill && dayPts.length >= 2) {
      var grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
      grad.addColorStop(0, fill);
      grad.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(xFor(dayPts[0]), padT + plotH);
      dayPts.forEach(function (s) { ctx.lineTo(xFor(s), yFor(s[key])); });
      ctx.lineTo(xFor(dayPts[dayPts.length - 1]), padT + plotH);
      ctx.closePath();
      ctx.fill();
    }

    ctx.strokeStyle = color;
    ctx.lineWidth = 2.4;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    dayPts.forEach(function (s, idx) {
      var x = xFor(s);
      var y = yFor(s[key]);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    dayPts.forEach(function (s) {
      var x = xFor(s);
      var y = yFor(s[key]);
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.arc(x, y, 3.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    });

    if (playhead != null) {
      var head = null;
      for (var p = dayPts.length - 1; p >= 0; p--) {
        if (dayPts[p].sampled_at <= playhead) { head = dayPts[p]; break; }
      }
      if (head) {
        ctx.strokeStyle = COLORS.playhead;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(xFor(head), padT);
        ctx.lineTo(xFor(head), padT + plotH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = COLORS.playhead;
        ctx.beginPath();
        ctx.arc(xFor(head), yFor(head[key]), 4.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    canvas._samHover = {
      points: dayPts,
      key: key,
      xFor: xFor,
      yFor: yFor,
      formatY: formatY,
      label: opts.valueLabel || key,
      padL: padL,
      padT: padT,
      plotW: plotW,
      plotH: plotH,
    };
  }

  function bindHover(canvas, tipEl) {
    if (!canvas || !tipEl || canvas._samHoverBound) return;
    canvas._samHoverBound = true;
    canvas.addEventListener('mousemove', function (e) {
      var meta = canvas._samHover;
      if (!meta || !meta.points.length) {
        tipEl.hidden = true;
        return;
      }
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var best = null;
      var bestD = 18;
      meta.points.forEach(function (s) {
        var px = meta.xFor(s);
        var d = Math.abs(px - x);
        if (d < bestD) {
          bestD = d;
          best = s;
        }
      });
      if (!best) {
        tipEl.hidden = true;
        return;
      }
      tipEl.hidden = false;
      tipEl.style.left = Math.min(rect.width - 140, Math.max(8, meta.xFor(best))) + 'px';
      tipEl.style.top = Math.max(8, meta.yFor(best[meta.key]) - 40) + 'px';
      tipEl.innerHTML =
        '<strong>' + escapeHtml(meta.formatY(best[meta.key])) + '</strong>' +
        '<span>' + escapeHtml(fmtDate(best.sampled_at)) + '</span>';
    });
    canvas.addEventListener('mouseleave', function () {
      tipEl.hidden = true;
    });
  }

  function renderEmpty(root, opts) {
    opts = opts || {};
    var hasMask = !!opts.hasSavedMask;
    var btnLabel = hasMask ? 'Edit mask' : 'Set plant region';
    var title = hasMask
      ? 'Growth series not linked yet'
      : 'Save a SAM region to start growth tracking';
    var blurb = hasMask
      ? 'This plant already has a saved mask. Click <em>Edit mask</em> to confirm the region — ' +
        'then Analyze now for one live sample, or Rebuild history to stamp that mask onto every still.'
      : 'Use <em>' + btnLabel + '</em> to draw the plant. That saved silhouette is locked so playback does not jump.';
    root.innerHTML =
      '<div class="sam-growth-empty">' +
      '<span class="material-icons-outlined">yard</span>' +
      '<p><strong>' + title + '</strong></p>' +
      '<p class="plants-muted">' + blurb + '</p>' +
      '<button type="button" class="btn btn-primary btn-sm" id="sam-growth-edit-mask">' +
      '<span class="material-icons-outlined icon-sm">masks</span> ' + btnLabel +
      '</button>' +
      '</div>';
    var btn = document.getElementById('sam-growth-edit-mask');
    if (btn && typeof opts.onEditMask === 'function') {
      btn.addEventListener('click', function () { opts.onEditMask(); });
    } else if (btn) {
      btn.addEventListener('click', function () {
        var detailBtn = document.getElementById('plants-detail-region');
        if (detailBtn) detailBtn.click();
      });
    }
  }

  function seriesSpan(s) {
    if (s && s.first_sampled_at && s.last_sampled_at) {
      return fmtShort(s.first_sampled_at) + ' – ' + fmtShort(s.last_sampled_at);
    }
    return s && s.created_at ? fmtShort(s.created_at) : '';
  }

  function renderSeriesOptions(seriesList, selectedId) {
    return seriesList.map(function (s) {
      var span = seriesSpan(s) || ('Series ' + s.id);
      var label = span +
        (s.sample_count ? ' · ' + s.sample_count : '') +
        (s.active ? ' · active' : '');
      return '<option value="' + s.id + '"' +
        (s.id === selectedId ? ' selected' : '') + '>' +
        escapeHtml(label) + '</option>';
    }).join('');
  }

  function renderExemplars(exemplars) {
    if (!exemplars || !exemplars.length) {
      return '<p class="plants-muted sam-growth-ex-empty">No exemplar stills yet — they appear on the first sample, daily, or after a large area change.</p>';
    }
    return '<div class="sam-growth-exemplars">' + exemplars.map(function (ex) {
      var url = stillUrl(ex.exemplar_path);
      return '<button type="button" class="sam-growth-thumb" data-url="' + escapeHtml(url) + '" title="' + escapeHtml(fmtDate(ex.sampled_at)) + '">' +
        '<img src="' + escapeHtml(url) + '" alt="Growth exemplar" loading="lazy">' +
        '<span>' + escapeHtml(fmtShort(ex.sampled_at)) + '</span>' +
        '</button>';
    }).join('') + '</div>';
  }

  function playbackFromDetail(detail) {
    var rows;
    if (detail.samples && detail.samples.length) {
      rows = detail.samples.filter(function (s) {
        return s.ok !== false && (s.mask_path || s.raw_path || s.exemplar_path);
      }).map(function (s) {
        return {
          sampled_at: s.sampled_at,
          overlay_url: stillUrl(s.mask_path || s.raw_path || s.exemplar_path),
          mask_url: stillUrl(s.silhouette_path || ''),
          raw_url: stillUrl(s.raw_path || s.exemplar_path || ''),
          mask_area_px: s.mask_area_px,
          green_pixels_in_mask: s.green_pixels_in_mask,
          green_fraction_in_mask: s.green_fraction_in_mask,
        };
      });
    } else {
      rows = detail.playback || [];
    }
    return rows.map(normalizePlaybackFrame).filter(function (f) {
      return f.overlay_url || f.mask_url;
    });
  }

  function pickDefaultSeries(seriesList, activeId) {
    var selectedId = activeId || (seriesList[0] && seriesList[0].id);
    var richest = seriesList.slice().sort(function (a, b) {
      return (b.sample_count || 0) - (a.sample_count || 0);
    })[0];
    var active = null;
    seriesList.forEach(function (s) { if (s.id === selectedId) active = s; });
    if (richest && (richest.sample_count || 0) > 8 && (!active || (active.sample_count || 0) < 3)) {
      return richest.id;
    }
    return selectedId;
  }

  function chartSpecs() {
    return {
      area: {
        key: 'mask_area_px', title: 'Mask area', color: COLORS.area, fill: COLORS.areaFill,
        formatY: function (v) {
          if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
          if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k';
          return String(Math.round(v));
        },
      },
      green: {
        key: 'green_pixels_in_mask', title: 'Green pixels in mask', color: COLORS.green, fill: COLORS.greenFill,
        formatY: function (v) {
          if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
          if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k';
          return String(Math.round(v));
        },
      },
      pct: {
        key: 'green_fraction_in_mask', title: '% green within mask', color: COLORS.pct, fill: COLORS.pctFill,
        formatY: function (v) { return (v * 100).toFixed(1) + '%'; },
      },
    };
  }

  function paintCharts(root, samples, until) {
    var specs = chartSpecs();
    Object.keys(specs).forEach(function (kind) {
      var canvas = root.querySelector('[data-chart="' + kind + '"]');
      var tip = root.querySelector('[data-tip="' + kind + '"]');
      if (!canvas) return;
      var spec = specs[kind];
      drawLineChart(canvas, samples, spec.key, {
        color: spec.color,
        fill: spec.fill,
        title: spec.title,
        valueLabel: spec.title,
        formatY: spec.formatY,
        until: until,
        playhead: until,
      });
      bindHover(canvas, tip);
    });
  }

  function renderCalendar(el, frames, idx) {
    if (!el || !frames.length) return;
    var cur = frames[Math.max(0, Math.min(idx, frames.length - 1))];
    var d = new Date(cur.sampled_at * 1000);
    var year = d.getFullYear();
    var month = d.getMonth();
    var first = new Date(year, month, 1);
    var startPad = first.getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var playedDays = {};
    var hasDays = {};
    frames.forEach(function (f, i) {
      var fd = new Date(f.sampled_at * 1000);
      if (fd.getFullYear() !== year || fd.getMonth() !== month) return;
      hasDays[fd.getDate()] = true;
      if (i <= idx) playedDays[fd.getDate()] = true;
    });
    var monthLabel = d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
    var dow = ['S', 'M', 'T', 'W', 'T', 'F', 'S'].map(function (x) {
      return '<span class="sam-evo-cal-dow">' + x + '</span>';
    }).join('');
    var cells = '';
    var i;
    for (i = 0; i < startPad; i++) cells += '<span class="sam-evo-cal-day empty"></span>';
    for (i = 1; i <= daysInMonth; i++) {
      var cls = 'sam-evo-cal-day';
      if (playedDays[i]) cls += ' played';
      else if (hasDays[i]) cls += ' has';
      if (i === d.getDate()) cls += ' current';
      cells += '<span class="' + cls + '">' + i + '</span>';
    }
    el.innerHTML =
      '<div class="sam-evo-cal-head">' + escapeHtml(monthLabel) + '</div>' +
      '<div class="sam-evo-cal-grid">' + dow + cells + '</div>';
  }

  function stopPlayer() {
    if (_playerTimer) {
      clearInterval(_playerTimer);
      _playerTimer = null;
    }
  }

  function bindPlayback(root, playback, samples, exportCtx) {
    if (!playback || !playback.length) return;
    var overlay = root.querySelector('#sam-evo-overlay');
    var mask = root.querySelector('#sam-evo-mask');
    var dateEl = root.querySelector('#sam-evo-date');
    var progEl = root.querySelector('#sam-evo-progress');
    var calEl = root.querySelector('#sam-evo-cal');
    var slider = root.querySelector('#sam-evo-slider');
    var playBtn = root.querySelector('#sam-evo-play');
    var speedSel = root.querySelector('#sam-evo-speed');
    var plots = root.querySelector('#sam-evo-plots');
    var statusEl = root.querySelector('#sam-evo-export-status');
    var idx = 0;
    var playing = false;
    var speed = 1;

    function intervalMs() {
      return Math.max(40, Math.round(420 / Math.max(0.25, speed)));
    }

    function setPlayIcon(on) {
      if (!playBtn) return;
      playBtn.innerHTML = '<span class="material-icons-outlined">' +
        (on ? 'pause' : 'play_arrow') + '</span>';
      playBtn.setAttribute('aria-label', on ? 'Pause' : 'Play');
    }

    function paintUnfolding(ts) {
      if (!plots) return;
      var specs = chartSpecs();
      var plotH = 148;
      ['area', 'green', 'pct'].forEach(function (kind) {
        var canvas = plots.querySelector('[data-chart="evo-' + kind + '"]');
        if (!canvas) return;
        var spec = specs[kind];
        drawLineChart(canvas, samples, spec.key, {
          color: spec.color,
          fill: spec.fill,
          title: spec.title,
          formatY: spec.formatY,
          height: plotH,
          until: ts,
          playhead: ts,
          dark: true,
        });
      });
    }

    function show(i) {
      idx = Math.max(0, Math.min(playback.length - 1, i));
      var f = playback[idx];
      if (overlay) {
        overlay.onerror = function () {
          overlay.onerror = null;
          if (idx < playback.length - 1) show(idx + 1);
        };
        if (f.overlay_url) overlay.src = stillUrl(f.overlay_url);
      }
      if (mask) {
        mask.onerror = function () {
          mask.onerror = null;
          mask.removeAttribute('src');
          mask.alt = 'Mask silhouette not ready';
        };
        if (f.mask_url) mask.src = stillUrl(f.mask_url);
      }
      if (dateEl) dateEl.textContent = fmtFull(f.sampled_at);
      if (progEl) {
        progEl.textContent = (idx + 1) + ' / ' + playback.length +
          ' · ' + Math.round((idx / Math.max(1, playback.length - 1)) * 100) + '%';
      }
      if (slider) slider.value = String(idx);
      renderCalendar(calEl, playback, idx);
      paintUnfolding(f.sampled_at);
    }

    function startTimer() {
      stopPlayer();
      _playerTimer = setInterval(function () {
        if (idx >= playback.length - 1) show(0);
        else show(idx + 1);
      }, intervalMs());
    }

    function toggle() {
      playing = !playing;
      setPlayIcon(playing);
      if (!playing) {
        stopPlayer();
        return;
      }
      startTimer();
    }

    if (slider) {
      slider.max = String(playback.length - 1);
      slider.addEventListener('input', function () {
        stopPlayer();
        playing = false;
        setPlayIcon(false);
        show(parseInt(slider.value, 10) || 0);
      });
    }
    if (speedSel) {
      speedSel.addEventListener('change', function () {
        speed = parseFloat(speedSel.value) || 1;
        if (playing) startTimer();
      });
    }
    if (playBtn) playBtn.addEventListener('click', toggle);

    function bindExport(btnId, format) {
      var btn = root.querySelector(btnId);
      if (!btn || !exportCtx) return;
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.textContent = 'Composing ' + format.toUpperCase() + ' — stills, mask, calendar, and plots…';
        }
        try {
          var r = await fetch('/api/plants/monitors/' + encodeURIComponent(exportCtx.monitorId) +
            '/sam-series/' + exportCtx.seriesId + '/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              format: format,
              speed: speed,
              hours: spanHours(),
              limit: 100000,
              max_frames: Math.min(playback.length, 2000),
            }),
          });
          if (!r.ok) {
            var errJ = {};
            try { errJ = await r.json(); } catch (e0) {}
            throw new Error(errJ.error || ('Export failed (' + r.status + ')'));
          }
          var blob = await r.blob();
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          var plant = (exportCtx.plantName || 'plant').replace(/[^\w\-]+/g, '_').slice(0, 40);
          a.href = url;
          a.download = plant + '_growth.' + format;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
          if (statusEl) statusEl.textContent = format.toUpperCase() + ' ready';
          if (window.plantsToast) window.plantsToast('Downloaded ' + format.toUpperCase(), false);
        } catch (err) {
          if (statusEl) statusEl.textContent = err.message;
          if (window.plantsToast) window.plantsToast(err.message, true);
        } finally {
          btn.disabled = false;
        }
      });
    }
    bindExport('#sam-evo-gif', 'gif');
    bindExport('#sam-evo-mov', 'mov');
    setPlayIcon(false);
    show(0);
  }

  function renderPlayback(playback, intervalH) {
    if (!playback || !playback.length) {
      return '<div class="sam-evo"><p class="plants-muted">Mask playback appears after growth samples are stored.</p></div>';
    }
    var first = playback[0];
    return '<div class="sam-evo" id="sam-evo">' +
      '<div class="sam-evo-chrome">' +
        '<div class="sam-evo-meta">' +
          '<div id="sam-evo-date" class="sam-evo-date">' + escapeHtml(fmtFull(first.sampled_at)) + '</div>' +
          '<div id="sam-evo-progress" class="sam-evo-progress">1 / ' + playback.length + '</div>' +
        '</div>' +
        '<div class="sam-evo-cal" id="sam-evo-cal"></div>' +
      '</div>' +
      '<div class="sam-evo-stage">' +
        '<div class="sam-evo-pair">' +
          '<figure class="sam-evo-shot">' +
            '<img id="sam-evo-overlay" alt="Mask on image" src="' + escapeHtml(stillUrl(first.overlay_url || first.raw_url)) + '">' +
            '<figcaption>Mask on image</figcaption>' +
          '</figure>' +
          '<figure class="sam-evo-shot">' +
            '<img id="sam-evo-mask" alt="Mask only" src="' + escapeHtml(stillUrl(first.mask_url)) + '">' +
            '<figcaption>Mask shape</figcaption>' +
          '</figure>' +
        '</div>' +
        '<div class="sam-evo-plots" id="sam-evo-plots">' +
          '<div class="sam-growth-chart-wrap"><canvas data-chart="evo-area"></canvas></div>' +
          '<div class="sam-growth-chart-wrap"><canvas data-chart="evo-green"></canvas></div>' +
          '<div class="sam-growth-chart-wrap"><canvas data-chart="evo-pct"></canvas></div>' +
        '</div>' +
      '</div>' +
      '<div class="sam-evo-transport">' +
        '<button type="button" class="sam-evo-play" id="sam-evo-play" aria-label="Play">' +
          '<span class="material-icons-outlined">play_arrow</span>' +
        '</button>' +
        '<input type="range" id="sam-evo-slider" min="0" max="' + (playback.length - 1) + '" value="0">' +
        '<label class="sam-evo-speed">Speed' +
          '<select id="sam-evo-speed">' +
            '<option value="0.5">0.5×</option>' +
            '<option value="1" selected>1×</option>' +
            '<option value="2">2×</option>' +
            '<option value="4">4×</option>' +
            '<option value="8">8×</option>' +
          '</select>' +
        '</label>' +
        '<div class="sam-evo-export">' +
          '<button type="button" class="btn btn-secondary btn-sm" id="sam-evo-gif">' +
            '<span class="material-icons-outlined icon-sm">gif</span> GIF</button>' +
          '<button type="button" class="btn btn-secondary btn-sm" id="sam-evo-mov">' +
            '<span class="material-icons-outlined icon-sm">movie</span> MOV</button>' +
        '</div>' +
      '</div>' +
      '<div class="sam-evo-export-status" id="sam-evo-export-status" hidden></div>' +
      '<p class="sam-evo-hint">Same locked mask on every frame. Export includes stills, shape, calendar, and plots.</p>' +
    '</div>';
  }

  function openExpand(title, samples, key, opts) {
    var existing = document.getElementById('sam-growth-expand-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'sam-growth-expand-modal';
    modal.className = 'scenes-modal sam-growth-expand';
    modal.innerHTML =
      '<div class="scenes-modal-inner card sam-growth-expand-inner">' +
      '<div class="sam-growth-expand-head">' +
      '<h3>' + escapeHtml(title) + '</h3>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="sam-growth-expand-close">Close</button>' +
      '</div>' +
      '<div class="sam-growth-chart-wrap sam-growth-chart-wrap-lg">' +
      '<canvas id="sam-growth-expand-canvas"></canvas>' +
      '<div class="sam-growth-tip" id="sam-growth-expand-tip" hidden></div>' +
      '</div></div>';
    document.body.appendChild(modal);
    modal.hidden = false;
    var canvas = document.getElementById('sam-growth-expand-canvas');
    var tip = document.getElementById('sam-growth-expand-tip');
    drawLineChart(canvas, samples, key, Object.assign({}, opts, { height: 360, title: '' }));
    bindHover(canvas, tip);
    document.getElementById('sam-growth-expand-close').onclick = function () { modal.remove(); };
    modal.addEventListener('click', function (e) {
      if (e.target === modal) modal.remove();
    });
  }

  function openLightbox(url) {
    var existing = document.getElementById('sam-growth-lightbox');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'sam-growth-lightbox';
    modal.className = 'scenes-modal sam-growth-lightbox';
    modal.innerHTML =
      '<div class="scenes-modal-inner card sam-growth-lightbox-inner">' +
      '<button type="button" class="btn btn-secondary btn-sm sam-growth-lightbox-close">Close</button>' +
      '<img src="' + escapeHtml(url) + '" alt="Growth exemplar">' +
      '</div>';
    document.body.appendChild(modal);
    modal.hidden = false;
    modal.querySelector('.sam-growth-lightbox-close').onclick = function () { modal.remove(); };
    modal.addEventListener('click', function (e) {
      if (e.target === modal) modal.remove();
    });
  }

  function spanHours() {
    if (growthSpan === 'all') return null;
    var days = growthSpan === 'custom' ? growthSpanDays : SPAN_DAYS[growthSpan];
    if (!days || days < 1) return null;
    return days * 24;
  }

  function withinSpan(ts) {
    var hours = spanHours();
    if (!hours) return true;
    var n = Number(ts);
    if (!isFinite(n)) return false;
    return n >= (Date.now() / 1000 - hours * 3600);
  }

  function spanQuery() {
    var hours = spanHours();
    var q = 'limit=100000';
    if (hours) q += '&hours=' + encodeURIComponent(hours);
    return q;
  }

  function spanButtonsHtml() {
    var keys = ['7d', '14d', '30d', '90d', 'all', 'custom'];
    return keys.map(function (k) {
      var label = k === 'all' ? 'All' : (k === 'custom' ? 'Custom' : k);
      return '<button type="button" class="plants-range-btn' + (growthSpan === k ? ' active' : '') +
        '" data-span="' + k + '">' + label + '</button>';
    }).join('');
  }

  async function loadSeriesDetail(monitorId, seriesId) {
    var r = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) +
      '/sam-series/' + seriesId + '?' + spanQuery());
    return r.json();
  }

  async function mount(root, monitorId, opts) {
    if (!root || !monitorId) return;
    opts = opts || {};
    stopPlayer();
    root.innerHTML = '<p class="plants-muted">Loading growth tracking…</p>';
    try {
      var listR = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) + '/sam-series');
      var listJ = await listR.json();
      if (!listJ.ok) throw new Error(listJ.error || 'Failed to load series');
      var seriesList = listJ.series || [];
      if (!seriesList.length) {
        renderEmpty(root, {
          hasSavedMask: !!(listJ.has_saved_mask || opts.hasMask),
          onEditMask: opts.onEditMask,
        });
        return;
      }
      var selectedId = pickDefaultSeries(seriesList, listJ.active_series_id);
      var growthMeta = Object.assign({}, listJ.sam_growth || {}, {
        has_anchor_points: listJ.has_anchor_points,
        bootstrapped: listJ.bootstrapped,
      });
      await renderSelected(root, monitorId, seriesList, selectedId, growthMeta, opts);
    } catch (e) {
      root.innerHTML = '<p class="plants-alert plants-alert-warn">' + escapeHtml(e.message) + '</p>';
    }
  }

  async function renderSelected(root, monitorId, seriesList, selectedId, growthCfg, opts) {
    opts = opts || {};
    stopPlayer();
    var detail = await loadSeriesDetail(monitorId, selectedId);
    if (!detail.ok) {
      root.innerHTML = '<p class="plants-alert plants-alert-warn">' + escapeHtml(detail.error || 'Failed') + '</p>';
      return;
    }
    var series = detail.series;
    var samples = (detail.samples || []).filter(function (s) {
      return withinSpan(s.sampled_at);
    });
    var exemplars = (detail.exemplars || []).filter(function (s) {
      return withinSpan(s.sampled_at);
    });
    var playback = playbackFromDetail(detail).filter(function (f) {
      return withinSpan(f.sampled_at);
    });
    var intervalH = Math.max(1, Math.round((growthCfg.interval_s || 3600) / 3600));
    var nightCount = samples.filter(isLightsOut).length;
    var listed = null;
    (seriesList || []).forEach(function (s) { if (s.id === selectedId) listed = s; });
    var span = seriesSpan(listed || series);

    root.innerHTML =
      '<div class="sam-growth-head">' +
      '<div class="sam-growth-title-row">' +
      '<h3 class="plants-section-title">Growth</h3>' +
      '<div class="sam-growth-controls">' +
      '<label class="sam-growth-series-label">' +
      '<select id="sam-growth-series" aria-label="Growth series">' + renderSeriesOptions(seriesList, selectedId) + '</select>' +
      '</label>' +
      (series.active
        ? '<span class="sam-growth-badge sam-growth-badge-active">Active</span>'
        : '<button type="button" class="btn btn-secondary btn-sm" id="sam-growth-activate">Make active</button>') +
      '<label class="sam-growth-interval">Every ' +
      '<input type="number" id="sam-growth-interval" min="1" max="168" value="' + intervalH + '" aria-label="Sample interval hours"> h</label>' +
      '</div></div>' +
      '<div class="sam-growth-span">' +
      '<span class="sam-growth-span-label">Playback</span>' +
      '<div class="plants-range-group" id="sam-growth-span">' + spanButtonsHtml() + '</div>' +
      (growthSpan === 'custom'
        ? '<label class="sam-growth-span-custom">Days ' +
          '<input type="number" id="sam-growth-span-days" min="1" step="1" value="' + growthSpanDays + '" aria-label="Playback length in days"></label>' +
          '<button type="button" class="plants-range-btn" id="sam-growth-span-apply">Apply</button>'
        : '') +
      '</div>' +
      '<p class="sam-growth-meta">' +
      (span ? escapeHtml(span) + ' · ' : '') +
      samples.length + ' in view' +
      (series.sample_count ? ' · ' + series.sample_count + ' stored' : '') +
      (nightCount ? ' · ' + nightCount + ' lights-out compressed' : '') +
      '</p>' +
      '<p class="sam-growth-reanalyze-status plants-muted" id="sam-growth-reanalyze-status" hidden></p></div>' +

      '<div class="sam-growth-grid">' +
      chartCard('area', 'Mask area') +
      chartCard('green', 'Green in mask') +
      chartCard('pct', '% green in mask') +
      '</div>' +

      renderPlayback(playback, intervalH) +

      '<div class="sam-growth-ex-section">' +
      '<div class="sam-growth-ex-head"><span>Exemplars</span></div>' +
      renderExemplars(exemplars) +
      '</div>';

    paintCharts(root, samples);
    bindPlayback(root, playback, samples, {
      monitorId: monitorId,
      seriesId: selectedId,
      plantName: opts.plantName || '',
    });

    root.querySelectorAll('.sam-growth-chart-card').forEach(function (card) {
      card.addEventListener('click', function (e) {
        if (e.target.closest('.sam-growth-expand-btn')) {
          var kind = card.getAttribute('data-kind');
          var spec = chartSpecs()[kind];
          if (spec) openExpand(spec.title, samples, spec.key, spec);
        }
      });
    });

    root.querySelectorAll('.sam-growth-thumb').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openLightbox(btn.getAttribute('data-url'));
      });
    });

    root.querySelectorAll('#sam-growth-span .plants-range-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        growthSpan = btn.getAttribute('data-span') || 'all';
        if (growthSpan !== 'custom') {
          renderSelected(root, monitorId, seriesList, selectedId, growthCfg, opts);
        } else {
          var daysInput = document.getElementById('sam-growth-span-days');
          if (!daysInput) {
            renderSelected(root, monitorId, seriesList, selectedId, growthCfg, opts);
          }
        }
      });
    });
    var spanApply = document.getElementById('sam-growth-span-apply');
    if (spanApply) {
      spanApply.addEventListener('click', function () {
        var daysInput = document.getElementById('sam-growth-span-days');
        growthSpanDays = Math.max(1, parseInt(daysInput && daysInput.value, 10) || 14);
        growthSpan = 'custom';
        renderSelected(root, monitorId, seriesList, selectedId, growthCfg, opts);
      });
    }

    var sel = document.getElementById('sam-growth-series');
    if (sel) {
      sel.addEventListener('change', function () {
        renderSelected(root, monitorId, seriesList, parseInt(sel.value, 10), growthCfg, opts);
      });
    }

    var intervalInput = document.getElementById('sam-growth-interval');
    if (intervalInput) {
      intervalInput.addEventListener('change', async function () {
        var hours = Math.max(1, parseInt(intervalInput.value, 10) || 1);
        try {
          var r = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) +
            '/sam-growth/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval_s: hours * 3600 }),
          });
          var j = await r.json();
          if (!j.ok) throw new Error(j.error || 'Could not save interval');
          growthCfg.interval_s = hours * 3600;
          if (window.plantsToast) window.plantsToast('Samples every ' + hours + 'h', false);
        } catch (err) {
          if (window.plantsToast) window.plantsToast(err.message, true);
        }
      });
    }

    var act = document.getElementById('sam-growth-activate');
    if (act) {
      act.addEventListener('click', async function () {
        act.disabled = true;
        try {
          var r = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) +
            '/sam-series/' + selectedId + '/activate', { method: 'POST' });
          var j = await r.json();
          if (!j.ok) throw new Error(j.error || 'Activate failed');
          mount(root, monitorId, opts);
        } catch (err) {
          act.disabled = false;
          if (window.plantsToast) window.plantsToast(err.message, true);
          else alert(err.message);
        }
      });
    }

    pollReanalyze(root, monitorId, opts);
  }

  var _rePoll = {};

  function setReanalyzeStatus(text, isErr) {
    var el = document.getElementById('sam-growth-reanalyze-status');
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.style.color = isErr ? 'var(--danger, #c5221f)' : '';
  }

  function pollReanalyze(root, monitorId, opts, seedJob) {
    if (_rePoll[monitorId]) {
      clearTimeout(_rePoll[monitorId]);
      _rePoll[monitorId] = null;
    }
    var watching = !!(seedJob && (seedJob.status === 'queued' || seedJob.status === 'running'));
    async function tick(job) {
      if (!job) {
        try {
          var r = await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) + '/reanalyze');
          var j = await r.json();
          job = (j && j.job) || null;
        } catch (e) {
          return;
        }
      }
      if (!job || !job.status) return;
      var btn = document.getElementById('plants-rebuild');
      if (job.status === 'queued' || job.status === 'running') {
        watching = true;
        if (btn) {
          btn.disabled = true;
          btn.textContent = 'Rebuilding…';
        }
        var done = job.done || 0;
        var total = job.total || 0;
        setReanalyzeStatus(total
          ? ('Rebuilding history… ' + done + ' / ' + total)
          : 'Collecting stills…');
        _rePoll[monitorId] = setTimeout(function () { tick(null); }, 1500);
        return;
      }
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Rebuild history';
      }
      if (job.status === 'error') {
        setReanalyzeStatus(job.error || 'Rebuild failed', true);
        return;
      }
      if (job.status === 'done') {
        setReanalyzeStatus('New series ready — ' + (job.ok_count || 0) + ' frames.');
        if (watching) {
          watching = false;
          if (window.plantsToast) window.plantsToast('History rebuilt', false);
          if (job.series_id && opts && typeof opts.onReanalyzeDone === 'function') {
            opts.onReanalyzeDone(job.series_id);
          }
          mount(root, monitorId, opts);
        }
      }
    }
    tick(seedJob || null);
  }

  function chartCard(kind, label) {
    return '<div class="sam-growth-chart-card" data-kind="' + kind + '">' +
      '<button type="button" class="sam-growth-expand-btn" title="Expand">' +
      '<span class="material-icons-outlined icon-sm">open_in_full</span></button>' +
      '<div class="sam-growth-chart-wrap">' +
      '<canvas data-chart="' + kind + '"></canvas>' +
      '<div class="sam-growth-tip" data-tip="' + kind + '" hidden></div>' +
      '</div>' +
      '<span class="sam-growth-chart-caption">' + escapeHtml(label) + '</span>' +
      '</div>';
  }

  return { mount: mount };
})();

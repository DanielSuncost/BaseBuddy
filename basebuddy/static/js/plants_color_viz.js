/**
 * Plant health small-multiples dashboard (canvas).
 * Panels: G_norm (+ band + watering), Coverage %, Yellow %, L_bg,
 * L_frame, L_plant, Daily Δ. Shared timeframe; toggleable panels; hover tips.
 */
window.PlantColorViz = (function () {
  var COLORS = {
    gNorm: '#0f766e',
    gNormFill: 'rgba(15, 118, 110, 0.14)',
    gNormBand: 'rgba(15, 118, 110, 0.16)',
    coverage: '#0d9488',
    coverageFill: 'rgba(13, 148, 136, 0.12)',
    yellow: '#b45309',
    yellowFill: 'rgba(180, 83, 9, 0.12)',
    lbg: '#64748b',
    lframe: '#475569',
    lplant: '#0f766e',
    deltaUp: '#137333',
    deltaDown: '#c5221f',
    watering: 'rgba(14, 116, 144, 0.75)',
    grid: 'rgba(148, 163, 184, 0.28)',
    gridStrong: 'rgba(100, 116, 139, 0.22)',
    axis: '#64748b',
    label: '#334155',
    poor: 'rgba(100, 116, 139, 0.4)',
    guide: 'rgba(15, 23, 42, 0.22)',
    surface: '#f8fafc',
  };

  var DEFAULT_VISIBLE = {
    gnorm: true,
    coverage: true,
    yellow: true,
    lbg: true,
    lframe: true,
    lplant: true,
    daily: true,
  };

  var L_BG_LOW = 0.08;
  var L_G_NORM_MIN = 0.42;
  var G_NORM_MAX = 5.0;
  var PLANT_S_MIN = 12;
  var G_NORM_EXCLUDE_FLAGS = [
    'L_bg_low', 'L_frame_low', 'L_plant_low', 'G_norm_clipped',
    'G_norm_dim_light', 'plant_desaturated',
  ];

  function rgb(c) {
    if (!c) return 'rgb(180,180,180)';
    return 'rgb(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ')';
  }

  function isLuminanceKey(key) {
    return key === 'L_bg' || key === 'L_frame' || key === 'L_plant';
  }

  function fmtTick(ts, spanHours) {
    var d = new Date(ts * 1000);
    if (spanHours <= 36) {
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
    if (spanHours <= 24 * 10) {
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric' });
    }
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function fmtDay(ts) {
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function isLowLight(s) {
    if (!s) return false;
    var flags = s.quality_flags || [];
    for (var i = 0; i < G_NORM_EXCLUDE_FLAGS.length; i++) {
      if (flags.indexOf(G_NORM_EXCLUDE_FLAGS[i]) >= 0) return true;
    }
    if (s.L_bg != null && s.L_bg < L_G_NORM_MIN) return true;
    if (s.L_frame != null && s.L_frame < L_G_NORM_MIN) return true;
    if (s.L_plant != null && s.L_plant < L_BG_LOW) return true;
    var sMean = s.hsv && s.hsv.s != null ? s.hsv.s : null;
    if (sMean != null && sMean < PLANT_S_MIN) return true;
    return false;
  }

  /** Hard darkness / garbage — exclude from coverage (not from L_* panels). */
  function isHardPoor(s) {
    if (!s) return false;
    if (s.sample_ok === false) {
      var flags = s.quality_flags || [];
      // Dim-light-only rows keep sample_ok true; desaturated / pitch-black are poor.
      if (flags.indexOf('plant_desaturated') >= 0) return true;
      if (flags.indexOf('L_bg_low') >= 0 || flags.indexOf('L_frame_low') >= 0 ||
          flags.indexOf('L_plant_low') >= 0) return true;
      if (s.L_bg != null && s.L_bg < L_BG_LOW) return true;
      if (s.L_frame != null && s.L_frame < L_BG_LOW) return true;
      return true;
    }
    if (s.L_bg != null && s.L_bg < L_BG_LOW) return true;
    if (s.L_frame != null && s.L_frame < L_BG_LOW) return true;
    var sMean = s.hsv && s.hsv.s != null ? s.hsv.s : null;
    if (sMean != null && sMean < PLANT_S_MIN) return true;
    return false;
  }

  /** Sample is usable for coverage / yellow panels (not L_* — those always plot). */
  function isSampleOk(s) {
    if (isHardPoor(s)) return false;
    if (isLowLight(s)) return false;
    if (s.sample_ok == null) return true;
    return !!s.sample_ok;
  }

  /**
   * Strict gate for G_norm / daily Δ / greenness trends.
   * Omits dark, dim, desaturated, poor, null, and spike values.
   */
  function isGreennessUsable(s) {
    if (!s) return false;
    if (s.sample_ok === false) return false;
    if (isLowLight(s)) return false;
    var g = s.G_norm;
    if (g == null || !isFinite(g)) return false;
    if (g > G_NORM_MAX || g < 0) return false;
    // Legacy rows with no lighting metadata but absurd ratios
    if (s.L_bg == null && s.L_frame == null && g > 3.5) return false;
    return true;
  }

  /** Raw G_norm for low-light toggle (reconstruct if pipeline nulled it). */
  function rawGNorm(s) {
    if (!s) return null;
    if (s.G_norm != null && isFinite(s.G_norm)) return s.G_norm;
    var gRel = s.G_rel != null ? s.G_rel : s.greenness;
    var lBg = s.L_bg;
    if (gRel == null || lBg == null || !(lBg > 0)) return null;
    var v = gRel / Math.max(lBg, L_BG_LOW);
    if (!isFinite(v) || v < 0 || v > G_NORM_MAX * 2) return null;
    return v;
  }

  function seriesVal(s, key) {
    if (key === 'G_norm') {
      // Never surface night-spike ratios on the primary health series
      if (!isGreennessUsable(s)) return null;
      return s.G_norm;
    }
    if (key === 'G_rel') {
      if (s.G_rel != null) return s.G_rel;
      if (s.greenness != null) return s.greenness;
      return null;
    }
    if (key === 'coverage') return s.coverage != null ? s.coverage : null;
    if (key === 'yellow_fraction') return s.yellow_fraction != null ? s.yellow_fraction : null;
    if (key === 'L_bg') return s.L_bg != null ? s.L_bg : null;
    if (key === 'L_frame') return s.L_frame != null ? s.L_frame : null;
    if (key === 'L_plant') return s.L_plant != null ? s.L_plant : null;
    return null;
  }

  function collect(samples, key, goodOnly) {
    return samples.map(function (s) {
      // Luminance panels always include every sample with a value.
      if (isLuminanceKey(key)) return seriesVal(s, key);
      if (key === 'G_norm') {
        if (goodOnly && !isGreennessUsable(s)) return null;
        return seriesVal(s, key);
      }
      if (goodOnly && !isSampleOk(s)) return null;
      return seriesVal(s, key);
    });
  }

  function rangeOf(vals) {
    var ok = vals.filter(function (v) { return v != null && isFinite(v); });
    if (!ok.length) return null;
    var mn = Math.min.apply(null, ok);
    var mx = Math.max.apply(null, ok);
    var pad = Math.max(0.01, (mx - mn) * 0.12);
    if (mx - mn < 1e-6) return { min: mn - pad, max: mx + pad };
    return { min: mn - pad * 0.2, max: mx + pad * 0.2 };
  }

  function yFor(v, rng, top, h) {
    return top + h - ((v - rng.min) / Math.max(1e-9, rng.max - rng.min)) * h;
  }

  function xForTime(ts, t0, t1, padL, plotW) {
    if (t1 <= t0) return padL + plotW / 2;
    return padL + ((ts - t0) / (t1 - t0)) * plotW;
  }

  /** Rolling p25–p75 band over a window of good samples (centered). */
  function rollingPercentileBand(samples, key, windowSize, pLo, pHi) {
    windowSize = windowSize || 7;
    pLo = pLo == null ? 0.25 : pLo;
    pHi = pHi == null ? 0.75 : pHi;
    var half = Math.floor(windowSize / 2);
    var out = samples.map(function () { return { lo: null, mid: null, hi: null }; });

    function percentile(sorted, p) {
      if (!sorted.length) return null;
      var idx = (sorted.length - 1) * p;
      var lo = Math.floor(idx);
      var hi = Math.ceil(idx);
      if (lo === hi) return sorted[lo];
      return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
    }

    for (var i = 0; i < samples.length; i++) {
      var vals = [];
      for (var j = Math.max(0, i - half); j <= Math.min(samples.length - 1, i + half); j++) {
        if (key === 'G_norm') {
          if (!isGreennessUsable(samples[j])) continue;
        } else if (!isSampleOk(samples[j])) {
          continue;
        }
        var v = seriesVal(samples[j], key);
        if (v != null && isFinite(v)) vals.push(v);
      }
      if (vals.length < 2) continue;
      vals.sort(function (a, b) { return a - b; });
      out[i] = {
        lo: percentile(vals, pLo),
        mid: percentile(vals, 0.5),
        hi: percentile(vals, pHi),
      };
    }
    return out;
  }

  /**
   * Last good value per UTC calendar day, then day-over-day delta.
   * Mirrors basebuddy.plugins.plant_health.plot_stats.daily_metric_deltas.
   */
  function dailyMetricDeltas(samples, key, goodOnly) {
    key = key || 'G_norm';
    if (goodOnly == null) goodOnly = true;
    var byDay = {};
    (samples || []).forEach(function (s) {
      if (s.sampled_at == null) return;
      if (goodOnly) {
        if (key === 'G_norm' || key === 'G_rel') {
          if (!isGreennessUsable(s) && key === 'G_norm') return;
          if (key === 'G_rel' && !isSampleOk(s)) return;
        } else if (!isSampleOk(s)) {
          return;
        }
      }
      var v = seriesVal(s, key);
      if (v == null || !isFinite(v)) return;
      var d = new Date(s.sampled_at * 1000);
      var mo = d.getUTCMonth() + 1;
      var da = d.getUTCDate();
      var day = d.getUTCFullYear() + '-' +
        (mo < 10 ? '0' : '') + mo + '-' +
        (da < 10 ? '0' : '') + da;
      var prev = byDay[day];
      if (!prev || s.sampled_at >= prev.ts) {
        byDay[day] = { day: day, ts: s.sampled_at, value: v, delta: null };
      }
    });
    var keys = Object.keys(byDay).sort();
    var ordered = keys.map(function (k) { return byDay[k]; });
    for (var i = 1; i < ordered.length; i++) {
      ordered[i].delta = Math.round((ordered[i].value - ordered[i - 1].value) * 1e5) / 1e5;
    }
    return ordered;
  }

  function heroStats(samples, trends, dailyDeltas) {
    trends = trends || {};
    var latest = null;
    var latestOk = null;
    for (var i = samples.length - 1; i >= 0; i--) {
      if (latest == null) latest = samples[i];
      if (latestOk == null && isGreennessUsable(samples[i]) && seriesVal(samples[i], 'G_norm') != null) {
        latestOk = samples[i];
      }
      if (latest && latestOk) break;
    }
    var ref = latestOk || latest;
    var gNorm = ref ? seriesVal(ref, 'G_norm') : (trends.G_norm_latest != null ? trends.G_norm_latest : null);
    var cov = ref && ref.coverage != null ? ref.coverage : null;
    var yel = ref && ref.yellow_fraction != null ? ref.yellow_fraction
      : (trends.yellow_fraction_latest != null ? trends.yellow_fraction_latest : null);
    var quality = ref
      ? (ref.sample_quality || (ref.sample_ok === false ? 'poor' : 'ok'))
      : '—';

    var delta7 = null;
    if (gNorm != null && samples.length) {
      var tCut = (ref ? ref.sampled_at : samples[samples.length - 1].sampled_at) - 7 * 86400;
      var past = null;
      for (var j = 0; j < samples.length; j++) {
        if (!isGreennessUsable(samples[j])) continue;
        var gv = seriesVal(samples[j], 'G_norm');
        if (gv == null) continue;
        if (samples[j].sampled_at <= tCut) past = gv;
        else break;
      }
      // If nothing before cutoff, use earliest good sample older than latest
      if (past == null) {
        for (var k = 0; k < samples.length; k++) {
          if (!isGreennessUsable(samples[k])) continue;
          var g0 = seriesVal(samples[k], 'G_norm');
          if (g0 != null && (!ref || samples[k].sampled_at < ref.sampled_at - 3600)) {
            past = g0;
            break;
          }
        }
      }
      if (past != null) delta7 = Math.round((gNorm - past) * 1e4) / 1e4;
    }

    // Prefer summing daily deltas over ~7 days when available
    if (dailyDeltas && dailyDeltas.length >= 2) {
      var sum = 0;
      var n = 0;
      var start = Math.max(1, dailyDeltas.length - 7);
      for (var d = start; d < dailyDeltas.length; d++) {
        if (dailyDeltas[d].delta != null) {
          sum += dailyDeltas[d].delta;
          n++;
        }
      }
      if (n) delta7 = Math.round(sum * 1e4) / 1e4;
    }

    return {
      gNorm: gNorm,
      delta7: delta7,
      coverage: cov,
      yellow: yel,
      quality: quality,
      rate: trends.greenness_rate_per_day,
    };
  }

  function setupCanvas(canvas, cssH) {
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
    var h = cssH || canvas.clientHeight || 140;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.max(1, Math.floor(h * dpr));
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }

  function fillSurface(ctx, w, h) {
    ctx.fillStyle = COLORS.surface;
    ctx.fillRect(0, 0, w, h);
  }

  function drawEmpty(ctx, w, h, msg) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '500 12px "IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(msg || 'No data in this timeframe', w / 2, h / 2);
  }

  function drawGrid(ctx, padL, padR, top, plotH, w, nLines) {
    nLines = nLines || 3;
    for (var i = 0; i <= nLines; i++) {
      var y = top + (plotH * i) / nLines;
      ctx.strokeStyle = i === nLines ? COLORS.gridStrong : COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
    }
  }

  function drawYLabels(ctx, rng, padL, top, plotH, format) {
    if (!rng) return;
    ctx.fillStyle = COLORS.axis;
    ctx.font = '500 10px "IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'right';
    var ticks = [rng.max, (rng.min + rng.max) / 2, rng.min];
    ticks.forEach(function (v, i) {
      var y = top + (plotH * i) / 2;
      ctx.fillText(format(v), padL - 6, y + 3);
    });
  }

  function drawXAxis(ctx, samples, t0, t1, padL, plotW, axisY, spanHours) {
    if (!samples.length) return;
    var ticks = [{ ts: t0 }];
    if (samples.length >= 3) {
      var mid = samples[Math.floor(samples.length / 2)].sampled_at;
      ticks.push({ ts: mid });
    }
    if (t1 !== t0) ticks.push({ ts: t1 });
    ctx.fillStyle = COLORS.axis;
    ctx.font = '500 10px "IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
    ticks.forEach(function (tick, ti) {
      var label = fmtTick(tick.ts, spanHours);
      var x = xForTime(tick.ts, t0, t1, padL, plotW);
      if (ti === 0) {
        ctx.textAlign = 'left';
        ctx.fillText(label, padL, axisY);
      } else if (ti === ticks.length - 1) {
        ctx.textAlign = 'right';
        ctx.fillText(label, padL + plotW, axisY);
      } else {
        ctx.textAlign = 'center';
        ctx.fillText(label, x, axisY);
      }
    });
  }

  function drawWateringMarkers(ctx, events, t0, t1, padL, plotW, top, bottom, labeled) {
    if (!events || !events.length || t1 <= t0) return;
    events.forEach(function (ev) {
      var ts = ev.watered_at;
      if (ts == null || ts < t0 || ts > t1) return;
      var x = xForTime(ts, t0, t1, padL, plotW);
      ctx.strokeStyle = COLORS.watering;
      ctx.lineWidth = 1.25;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = COLORS.watering;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x - 4, top - 7);
      ctx.lineTo(x + 4, top - 7);
      ctx.closePath();
      ctx.fill();
      if (labeled) {
        ctx.font = '9px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('water', x, top - 9);
      }
    });
  }

  function drawSeriesPoints(ctx, samples, key, rng, t0, t1, padL, plotW, top, plotH, color, showLowLight) {
    samples.forEach(function (s) {
      var v;
      var ok;
      if (isLuminanceKey(key)) {
        v = seriesVal(s, key);
        if (v == null) return;
        ok = !isHardPoor(s);
      } else if (key === 'G_norm') {
        if (!showLowLight && !isGreennessUsable(s)) return;
        if (showLowLight) {
          v = isGreennessUsable(s) ? s.G_norm : rawGNorm(s);
        } else {
          v = seriesVal(s, key);
        }
        ok = isGreennessUsable(s);
      } else {
        if (!showLowLight && !isSampleOk(s)) return;
        v = seriesVal(s, key);
        if (v == null) return;
        ok = isSampleOk(s);
      }
      if (v == null) return;
      var x = xForTime(s.sampled_at, t0, t1, padL, plotW);
      var y = yFor(v, rng, top, plotH);
      if (ok) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x, y, 2.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
      } else if (showLowLight || isLuminanceKey(key)) {
        ctx.strokeStyle = COLORS.poor;
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        ctx.arc(x, y, 2.6, 0, Math.PI * 2);
        ctx.stroke();
      }
    });
  }

  function annotateLowLight(ctx, samples, t0, t1, padL, plotW, top, plotH) {
    var any = false;
    for (var i = 0; i < samples.length; i++) {
      if (isLowLight(samples[i])) { any = true; break; }
    }
    if (!any) return;
    ctx.fillStyle = 'rgba(100, 116, 139, 0.75)';
    ctx.font = '10px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('gaps = low light excluded', padL + plotW, top - 4);
  }

  function strokeGoodSeries(ctx, samples, key, rng, t0, t1, padL, plotW, top, plotH, style, showLowLight) {
    var pts = [];
    samples.forEach(function (s) {
      var v;
      if (isLuminanceKey(key)) {
        v = seriesVal(s, key);
      } else if (key === 'G_norm') {
        if (!isGreennessUsable(s)) {
          if (!showLowLight) return;
          v = rawGNorm(s);
        } else {
          v = seriesVal(s, key);
        }
      } else if (!isSampleOk(s)) {
        if (!showLowLight) return;
        v = seriesVal(s, key);
      } else {
        v = seriesVal(s, key);
      }
      if (v == null) return;
      pts.push({
        x: xForTime(s.sampled_at, t0, t1, padL, plotW),
        y: yFor(v, rng, top, plotH),
      });
    });
    if (pts.length < 2) return false;
    if (style.fill) {
      ctx.fillStyle = style.fill;
      ctx.beginPath();
      ctx.moveTo(pts[0].x, top + plotH);
      pts.forEach(function (p) { ctx.lineTo(p.x, p.y); });
      ctx.lineTo(pts[pts.length - 1].x, top + plotH);
      ctx.closePath();
      ctx.fill();
    }
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.width || 2;
    ctx.setLineDash(style.dash || []);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    pts.forEach(function (p, i) {
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    return true;
  }

  function fillBand(ctx, samples, band, rng, t0, t1, padL, plotW, top, plotH) {
    var loPts = [];
    var hiPts = [];
    for (var i = 0; i < samples.length; i++) {
      var b = band[i];
      if (!b || b.lo == null || b.hi == null) continue;
      if (!isGreennessUsable(samples[i])) continue;
      var x = xForTime(samples[i].sampled_at, t0, t1, padL, plotW);
      loPts.push({ x: x, y: yFor(b.lo, rng, top, plotH) });
      hiPts.push({ x: x, y: yFor(b.hi, rng, top, plotH) });
    }
    if (loPts.length < 2) return;
    ctx.fillStyle = COLORS.gNormBand;
    ctx.beginPath();
    ctx.moveTo(hiPts[0].x, hiPts[0].y);
    for (var h = 1; h < hiPts.length; h++) ctx.lineTo(hiPts[h].x, hiPts[h].y);
    for (var l = loPts.length - 1; l >= 0; l--) ctx.lineTo(loPts[l].x, loPts[l].y);
    ctx.closePath();
    ctx.fill();

    // Median guide
    ctx.strokeStyle = 'rgba(15, 118, 110, 0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    var started = false;
    for (var m = 0; m < samples.length; m++) {
      var bm = band[m];
      if (!bm || bm.mid == null) continue;
      if (!isGreennessUsable(samples[m])) continue;
      var xm = xForTime(samples[m].sampled_at, t0, t1, padL, plotW);
      var ym = yFor(bm.mid, rng, top, plotH);
      if (!started) { ctx.moveTo(xm, ym); started = true; }
      else ctx.lineTo(xm, ym);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawLinePanel(canvas, samples, opts) {
    opts = opts || {};
    var height = opts.height || 150;
    var box = setupCanvas(canvas, height);
    var ctx = box.ctx;
    var w = box.w;
    var h = box.h;
    fillSurface(ctx, w, h);
    canvas._plantHover = null;

    if (!samples.length) {
      drawEmpty(ctx, w, h);
      return;
    }

    var padL = 48;
    var padR = 12;
    var padTop = 22;
    var padBot = 22;
    var plotW = w - padL - padR;
    var plotH = h - padTop - padBot;
    var t0 = samples[0].sampled_at;
    var t1 = samples[samples.length - 1].sampled_at;
    var spanHours = Math.max(0.01, (t1 - t0) / 3600);
    var showLowLight = !!opts.showLowLight;
    var isGNorm = opts.key === 'G_norm';
    var isLuma = isLuminanceKey(opts.key);

    var goodVals = collect(samples, opts.key, true);
    var allVals = collect(samples, opts.key, false);
    // Prefer good-only range so night spikes never dominate the Y axis.
    // Luminance panels always use all samples so dark periods are visible.
    var rng = isLuma ? rangeOf(allVals) : rangeOf(goodVals);
    if (!rng && isGNorm && showLowLight) {
      // Include reconstructed low-light G_norm when toggle is on
      var rawNight = samples.map(function (s) {
        var v = isGreennessUsable(s) ? s.G_norm : rawGNorm(s);
        return (v != null && isFinite(v) && v <= G_NORM_MAX) ? v : null;
      });
      rng = rangeOf(rawNight);
    }
    if (!rng && !isGNorm && showLowLight) rng = rangeOf(allVals);
    if (!rng && !isGNorm && !isLuma) rng = rangeOf(allVals);
    if (!rng) {
      var nightOnly = isGNorm && samples.some(isLowLight);
      drawEmpty(ctx, w, h, nightOnly
        ? 'No usable G_norm (low light excluded)'
        : ('No ' + (opts.unitLabel || opts.key) + ' yet'));
      return;
    }

    if (opts.fixedMin != null) rng.min = Math.min(rng.min, opts.fixedMin);
    if (opts.fixedMax != null) rng.max = Math.max(rng.max, opts.fixedMax);

    drawGrid(ctx, padL, padR, padTop, plotH, w, 4);
    drawYLabels(ctx, rng, padL, padTop, plotH, opts.formatY || function (v) {
      return v.toFixed(2);
    });

    if (opts.band && isGNorm) {
      var win = Math.max(5, Math.min(11, Math.floor(samples.length / 4) * 2 + 1));
      fillBand(ctx, samples, rollingPercentileBand(samples, opts.key, win), rng, t0, t1, padL, plotW, padTop, plotH);
    }

    strokeGoodSeries(ctx, samples, opts.key, rng, t0, t1, padL, plotW, padTop, plotH, {
      color: opts.color,
      width: opts.lineWidth || 2,
      dash: opts.dash,
      fill: opts.fill || null,
    }, showLowLight);
    drawSeriesPoints(ctx, samples, opts.key, rng, t0, t1, padL, plotW, padTop, plotH, opts.color, showLowLight);

    if (opts.watering) {
      drawWateringMarkers(ctx, opts.watering_events, t0, t1, padL, plotW, padTop, padTop + plotH, !!opts.wateringLabels);
    }

    if (isGNorm && !showLowLight) {
      annotateLowLight(ctx, samples, t0, t1, padL, plotW, padTop, plotH);
    }

    drawXAxis(ctx, samples, t0, t1, padL, plotW, h - 6, spanHours);

    ctx.fillStyle = COLORS.label;
    ctx.font = '600 11px "IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(opts.title || opts.key, padL, 14);

    var hoverPts = [];
    samples.forEach(function (s) {
      if (isLuma) {
        var lv = seriesVal(s, opts.key);
        if (lv == null || !isFinite(lv)) return;
        hoverPts.push({
          sampled_at: s.sampled_at,
          value: lv,
          ok: !isHardPoor(s),
          lowLight: isLowLight(s) || isHardPoor(s),
          sample: s,
        });
        return;
      }
      if (isGNorm) {
        if (!showLowLight && !isGreennessUsable(s)) return;
        var gv = showLowLight
          ? (isGreennessUsable(s) ? s.G_norm : rawGNorm(s))
          : seriesVal(s, opts.key);
        if (gv == null) return;
        hoverPts.push({
          sampled_at: s.sampled_at,
          value: gv,
          ok: isGreennessUsable(s),
          lowLight: isLowLight(s) || !isGreennessUsable(s),
          sample: s,
        });
        return;
      }
      if (!showLowLight && !isSampleOk(s)) return;
      var v = seriesVal(s, opts.key);
      if (v == null || !isFinite(v)) return;
      hoverPts.push({
        sampled_at: s.sampled_at,
        value: v,
        ok: isSampleOk(s),
        lowLight: isLowLight(s),
        sample: s,
      });
    });
    canvas._plantHover = {
      points: hoverPts,
      key: opts.key,
      formatY: opts.formatY || function (v) { return Number(v).toFixed(3); },
      label: opts.tipLabel || opts.title || opts.key,
      t0: t0,
      t1: t1,
      padL: padL,
      padTop: padTop,
      plotW: plotW,
      plotH: plotH,
      rng: rng,
      kind: 'line',
    };
  }

  function drawDailyDeltaPanel(canvas, daily, opts) {
    opts = opts || {};
    var height = opts.height || 140;
    var box = setupCanvas(canvas, height);
    var ctx = box.ctx;
    var w = box.w;
    var h = box.h;
    fillSurface(ctx, w, h);
    canvas._plantHover = null;

    var bars = (daily || []).filter(function (d) { return d.delta != null; });
    if (!bars.length) {
      drawEmpty(ctx, w, h, 'Need 2+ days for Δ');
      return;
    }

    var padL = 44;
    var padR = 10;
    var padTop = 18;
    var padBot = 22;
    var plotW = w - padL - padR;
    var plotH = h - padTop - padBot;
    var absMax = Math.max.apply(null, bars.map(function (d) { return Math.abs(d.delta); }));
    absMax = Math.max(absMax, 0.01);
    var rng = { min: -absMax, max: absMax };

    drawGrid(ctx, padL, padR, padTop, plotH, w, 2);
    drawYLabels(ctx, rng, padL, padTop, plotH, function (v) {
      var s = v >= 0 ? '+' : '';
      return s + v.toFixed(2);
    });

    var zeroY = yFor(0, rng, padTop, plotH);
    ctx.strokeStyle = 'rgba(100, 116, 139, 0.55)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, zeroY);
    ctx.lineTo(padL + plotW, zeroY);
    ctx.stroke();

    var gap = 2;
    var barW = Math.max(3, (plotW / bars.length) - gap);
    bars.forEach(function (d, i) {
      var x = padL + (i + 0.5) * (plotW / bars.length) - barW / 2;
      var yVal = yFor(d.delta, rng, padTop, plotH);
      var top = Math.min(yVal, zeroY);
      var bh = Math.max(1, Math.abs(yVal - zeroY));
      ctx.fillStyle = d.delta >= 0 ? COLORS.deltaUp : COLORS.deltaDown;
      ctx.fillRect(x, top, barW, bh);
    });

    // X labels: first / mid / last day
    ctx.fillStyle = COLORS.label;
    ctx.font = '10px system-ui, sans-serif';
    var labelIdx = [0];
    if (bars.length >= 3) labelIdx.push(Math.floor(bars.length / 2));
    if (bars.length > 1) labelIdx.push(bars.length - 1);
    labelIdx.forEach(function (idx, ti) {
      var d = bars[idx];
      var x = padL + (idx + 0.5) * (plotW / bars.length);
      var label = fmtDay(d.ts);
      if (ti === 0) ctx.textAlign = 'left';
      else if (ti === labelIdx.length - 1) ctx.textAlign = 'right';
      else ctx.textAlign = 'center';
      ctx.fillText(label, x, h - 6);
    });

    ctx.fillStyle = COLORS.label;
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(opts.title || 'Daily Δ G_norm', padL, 12);

    canvas._plantHover = {
      points: bars.map(function (d) {
        return { sampled_at: d.ts, value: d.delta, day: d.day, ok: true };
      }),
      key: 'delta',
      formatY: function (v) {
        var s = v >= 0 ? '+' : '';
        return s + Number(v).toFixed(4);
      },
      label: 'Daily Δ',
      t0: bars[0].ts,
      t1: bars[bars.length - 1].ts,
      padL: padL,
      padTop: padTop,
      plotW: plotW,
      plotH: plotH,
      rng: rng,
      kind: 'bars',
      barCount: bars.length,
    };
  }

  function drawColorBandStrip(canvas, samples, opts) {
    opts = opts || {};
    var height = opts.height || 36;
    var box = setupCanvas(canvas, height);
    var ctx = box.ctx;
    var w = box.w;
    var h = box.h;
    fillSurface(ctx, w, h);
    if (!samples.length) return;
    var padL = 8;
    var padR = 8;
    var n = samples.length;
    var colW = Math.max(2, (w - padL - padR) / n);
    samples.forEach(function (s, i) {
      var x = padL + i * colW;
      var c = s.rgb || {};
      var dom = (s.dominant_colors_rgb && s.dominant_colors_rgb[1]) || [c.r, c.g, c.b];
      var r = dom[0], g = dom[1], b = dom[2];
      ctx.fillStyle = rgb({ r: r, g: g, b: b });
      ctx.fillRect(x, 4, Math.ceil(colW) + 0.5, h - 8);
      if (s.sample_ok === false) {
        ctx.fillStyle = 'rgba(248,250,252,0.5)';
        ctx.fillRect(x, 4, Math.ceil(colW) + 0.5, h - 8);
      }
    });
  }

  /** Legacy single-canvas draw kept for compatibility (thin color strip + note). */
  function draw(canvas, samples, opts) {
    opts = opts || {};
    drawColorBandStrip(canvas, samples, { height: opts.height || 40 });
  }

  function ensureStructure(root) {
    var multiples = root.querySelector('[data-plants-multiples]');
    if (multiples) {
      // Upgrade older dashboards that lack luminance panels / hover wraps
      if (!root.querySelector('[data-panel-wrap="lframe"]')) {
        var luma = document.createElement('div');
        luma.className = 'plants-panel-row plants-panel-luma';
        luma.innerHTML =
          '<div class="plants-panel" data-panel-wrap="lframe">' +
          '<div class="plants-chart-wrap"><canvas data-panel="lframe" height="130"></canvas>' +
          '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
          '<div class="plants-panel" data-panel-wrap="lplant">' +
          '<div class="plants-chart-wrap"><canvas data-panel="lplant" height="130"></canvas>' +
          '<div class="plants-chart-tip" data-tip hidden></div></div></div>';
        multiples.appendChild(luma);
      }
      root.querySelectorAll('canvas[data-panel]').forEach(function (c) {
        if (c.getAttribute('data-panel') === 'colorstrip') return;
        if (c.closest('.plants-chart-wrap')) return;
        var wrap = document.createElement('div');
        wrap.className = 'plants-chart-wrap';
        c.parentNode.insertBefore(wrap, c);
        wrap.appendChild(c);
        var tip = document.createElement('div');
        tip.className = 'plants-chart-tip';
        tip.setAttribute('data-tip', '');
        tip.hidden = true;
        wrap.appendChild(tip);
      });
      // Re-bind toggles when new panel ids appear / low-light opt missing
      var tog = root.querySelector('[data-plants-toggles]');
      if (tog && (!tog.querySelector('[data-toggle="lframe"]') ||
          !tog.querySelector('[data-opt="show-low-light"]'))) {
        tog.removeAttribute('data-bound');
      }
      if (!root.querySelector('[data-plants-datasets]')) {
        var ds = document.createElement('div');
        ds.className = 'plants-dataset-row';
        ds.setAttribute('data-plants-datasets', '');
        ds.hidden = true;
        root.insertBefore(ds, root.firstChild);
      }
      return multiples;
    }
    root.innerHTML =
      '<div class="plants-dataset-row" data-plants-datasets hidden></div>' +
      '<div class="plants-hero-strip" data-plants-hero></div>' +
      '<div class="plants-panel-toggles" data-plants-toggles></div>' +
      '<div class="plants-color-strip-wrap"><canvas data-panel="colorstrip" class="plants-color-strip" height="36"></canvas></div>' +
      '<div class="plants-multiples" data-plants-multiples>' +
      '<div class="plants-panel plants-panel-full" data-panel-wrap="gnorm">' +
      '<div class="plants-chart-wrap"><canvas data-panel="gnorm" height="170"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '<div class="plants-panel-row">' +
      '<div class="plants-panel" data-panel-wrap="coverage">' +
      '<div class="plants-chart-wrap"><canvas data-panel="coverage" height="140"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '<div class="plants-panel" data-panel-wrap="yellow">' +
      '<div class="plants-chart-wrap"><canvas data-panel="yellow" height="140"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '</div>' +
      '<div class="plants-panel-row plants-panel-extras">' +
      '<div class="plants-panel" data-panel-wrap="lbg">' +
      '<div class="plants-chart-wrap"><canvas data-panel="lbg" height="130"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '<div class="plants-panel" data-panel-wrap="daily">' +
      '<div class="plants-chart-wrap"><canvas data-panel="daily" height="130"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '</div>' +
      '<div class="plants-panel-row plants-panel-luma">' +
      '<div class="plants-panel" data-panel-wrap="lframe">' +
      '<div class="plants-chart-wrap"><canvas data-panel="lframe" height="130"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '<div class="plants-panel" data-panel-wrap="lplant">' +
      '<div class="plants-chart-wrap"><canvas data-panel="lplant" height="130"></canvas>' +
      '<div class="plants-chart-tip" data-tip hidden></div></div></div>' +
      '</div></div>' +
      '<p class="plants-muted" data-plants-caption></p>';
    return root.querySelector('[data-plants-multiples]');
  }

  function syncToggleStyles(el, visible, showLowLight) {
    if (!el) return;
    el.querySelectorAll('label.plants-toggle').forEach(function (lab) {
      var input = lab.querySelector('input[data-toggle]');
      if (input) {
        var id = input.getAttribute('data-toggle');
        input.checked = !!visible[id];
        lab.classList.toggle('active', !!visible[id]);
        return;
      }
      var opt = lab.querySelector('input[data-opt="show-low-light"]');
      if (opt) {
        opt.checked = !!showLowLight;
        lab.classList.toggle('active', !!showLowLight);
      }
    });
  }

  function renderToggles(el, visible, onChange, extras) {
    if (!el) return;
    extras = extras || {};
    if (el.getAttribute('data-bound') === '1') {
      syncToggleStyles(el, visible, extras.showLowLight);
      // Keep latest callbacks (re-render replaces closure targets)
      el._plantsOnToggle = onChange;
      el._plantsOnShowLowLight = extras.onShowLowLight;
      return;
    }
    var items = [
      { id: 'gnorm', label: 'G_norm' },
      { id: 'coverage', label: 'Coverage' },
      { id: 'yellow', label: 'Yellow' },
      { id: 'lbg', label: 'L_bg' },
      { id: 'lframe', label: 'L_frame' },
      { id: 'lplant', label: 'L_plant' },
      { id: 'daily', label: 'Daily Δ' },
    ];
    el.innerHTML = items.map(function (it) {
      return '<label class="plants-toggle' + (visible[it.id] ? ' active' : '') + '">' +
        '<input type="checkbox" data-toggle="' + it.id + '"' + (visible[it.id] ? ' checked' : '') + '>' +
        it.label + '</label>';
    }).join('') +
      '<label class="plants-toggle plants-toggle-opt' + (extras.showLowLight ? ' active' : '') +
      '" title="Include dark / low-light points on charts (off by default)">' +
      '<input type="checkbox" data-opt="show-low-light"' + (extras.showLowLight ? ' checked' : '') + '>' +
      'Show low-light samples</label>';
    el.setAttribute('data-bound', '1');
    el._plantsOnToggle = onChange;
    el._plantsOnShowLowLight = extras.onShowLowLight;
    // Event delegation so re-renders / DOM upgrades never drop handlers
    el.addEventListener('change', function (ev) {
      var t = ev.target;
      if (!t || t.tagName !== 'INPUT') return;
      if (t.getAttribute('data-opt') === 'show-low-light') {
        if (typeof el._plantsOnShowLowLight === 'function') {
          el._plantsOnShowLowLight(!!t.checked);
        }
        return;
      }
      var id = t.getAttribute('data-toggle');
      if (id && typeof el._plantsOnToggle === 'function') {
        el._plantsOnToggle(id, !!t.checked);
      }
    });
  }

  function renderHero(el, stats) {
    if (!el) return;
    function fmtDelta(v) {
      if (v == null) return '—';
      var s = v > 0 ? '+' : '';
      return s + Number(v).toFixed(3);
    }
    function fmtPct(v) {
      if (v == null) return '—';
      return (v * 100).toFixed(1) + '%';
    }
    var dClass = stats.delta7 == null ? ''
      : (stats.delta7 > 0.002 ? ' plants-hero-up' : (stats.delta7 < -0.002 ? ' plants-hero-down' : ''));
    el.innerHTML =
      '<div class="plants-hero-item"><span class="plants-hero-label">G_norm</span>' +
      '<span class="plants-hero-value">' + (stats.gNorm != null ? Number(stats.gNorm).toFixed(3) : '—') + '</span></div>' +
      '<div class="plants-hero-item' + dClass + '"><span class="plants-hero-label">7d Δ</span>' +
      '<span class="plants-hero-value">' + fmtDelta(stats.delta7) + '</span></div>' +
      '<div class="plants-hero-item"><span class="plants-hero-label">Coverage</span>' +
      '<span class="plants-hero-value">' + fmtPct(stats.coverage) + '</span></div>' +
      '<div class="plants-hero-item"><span class="plants-hero-label">Yellow</span>' +
      '<span class="plants-hero-value">' + fmtPct(stats.yellow) + '</span></div>' +
      '<div class="plants-hero-item"><span class="plants-hero-label">Quality</span>' +
      '<span class="plants-hero-value">' + (stats.quality || '—') + '</span></div>';
  }

  function applyVisibility(root, visible) {
    Object.keys(visible).forEach(function (id) {
      var wrap = root.querySelector('[data-panel-wrap="' + id + '"]');
      if (wrap) wrap.hidden = !visible[id];
    });
    // Hide empty rows
    root.querySelectorAll('.plants-panel-row').forEach(function (row) {
      var panels = row.querySelectorAll('[data-panel-wrap]');
      var any = false;
      panels.forEach(function (p) { if (!p.hidden) any = true; });
      row.hidden = !any;
    });
  }

  function drawPanels(root, samples, opts, visible, daily, events) {
    var strip = root.querySelector('canvas[data-panel="colorstrip"]');
    if (strip) drawColorBandStrip(strip, samples, { height: 36 });
    var showLowLight = !!(opts && opts.showLowLight);

    var gCanvas = root.querySelector('canvas[data-panel="gnorm"]');
    if (gCanvas && visible.gnorm) {
      drawLinePanel(gCanvas, samples, {
        key: 'G_norm',
        title: 'G_norm (health) · daytime · median band p25–p75',
        tipLabel: 'G_norm',
        color: COLORS.gNorm,
        fill: COLORS.gNormFill,
        lineWidth: 2.4,
        band: true,
        watering: true,
        wateringLabels: true,
        watering_events: events,
        height: 176,
        showLowLight: showLowLight,
        formatY: function (v) { return v.toFixed(2); },
      });
    }

    var cCanvas = root.querySelector('canvas[data-panel="coverage"]');
    if (cCanvas && visible.coverage) {
      drawLinePanel(cCanvas, samples, {
        key: 'coverage',
        title: 'Foliage coverage % (green in mask / frame)',
        color: COLORS.coverage,
        fill: COLORS.coverageFill,
        lineWidth: 2,
        height: 144,
        fixedMin: 0,
        showLowLight: showLowLight,
        formatY: function (v) { return (v * 100).toFixed(0) + '%'; },
      });
    }

    var yCanvas = root.querySelector('canvas[data-panel="yellow"]');
    if (yCanvas && visible.yellow) {
      drawLinePanel(yCanvas, samples, {
        key: 'yellow_fraction',
        title: 'Yellow fraction %',
        color: COLORS.yellow,
        fill: COLORS.yellowFill,
        lineWidth: 2,
        height: 144,
        fixedMin: 0,
        showLowLight: showLowLight,
        formatY: function (v) { return (v * 100).toFixed(0) + '%'; },
      });
    }

    var lCanvas = root.querySelector('canvas[data-panel="lbg"]');
    if (lCanvas && visible.lbg) {
      drawLinePanel(lCanvas, samples, {
        key: 'L_bg',
        title: 'L_bg (background lighting)',
        tipLabel: 'L_bg',
        color: COLORS.lbg,
        lineWidth: 1.75,
        height: 130,
        formatY: function (v) { return v.toFixed(2); },
      });
    }

    var lfCanvas = root.querySelector('canvas[data-panel="lframe"]');
    if (lfCanvas && visible.lframe) {
      drawLinePanel(lfCanvas, samples, {
        key: 'L_frame',
        title: 'Frame luminance',
        tipLabel: 'L_frame',
        color: COLORS.lframe,
        lineWidth: 1.75,
        height: 130,
        formatY: function (v) { return v.toFixed(2); },
      });
    }

    var lpCanvas = root.querySelector('canvas[data-panel="lplant"]');
    if (lpCanvas && visible.lplant) {
      drawLinePanel(lpCanvas, samples, {
        key: 'L_plant',
        title: 'SAM / plant luminance',
        tipLabel: 'L_plant',
        color: COLORS.lplant,
        lineWidth: 1.75,
        height: 130,
        formatY: function (v) { return v.toFixed(2); },
      });
    }

    var dCanvas = root.querySelector('canvas[data-panel="daily"]');
    if (dCanvas && visible.daily) {
      drawDailyDeltaPanel(dCanvas, daily, {
        title: 'Daily Δ G_norm (daytime only)',
        height: 130,
      });
    }

    bindAllHovers(root);
  }

  function tipForCanvas(canvas) {
    if (!canvas) return null;
    var wrap = canvas.closest('.plants-chart-wrap');
    return wrap ? wrap.querySelector('[data-tip]') : null;
  }

  function bindHover(canvas, tipEl) {
    if (!canvas || !tipEl || canvas._plantHoverBound) return;
    canvas._plantHoverBound = true;
    canvas.style.cursor = 'crosshair';

    function hide() {
      tipEl.hidden = true;
      if (canvas._hoverGuide) {
        canvas._hoverGuide = null;
        // Redraw without guide is expensive; just clear overlay via re-draw flag
      }
    }

    canvas.addEventListener('mousemove', function (e) {
      var meta = canvas._plantHover;
      if (!meta || !meta.points || !meta.points.length) {
        tipEl.hidden = true;
        return;
      }
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var best = null;
      var bestD = 22;
      var bestX = 0;
      var bestY = 0;

      meta.points.forEach(function (p, i) {
        var px;
        if (meta.kind === 'bars') {
          px = meta.padL + (i + 0.5) * (meta.plotW / meta.barCount);
        } else {
          px = xForTime(p.sampled_at, meta.t0, meta.t1, meta.padL, meta.plotW);
        }
        var d = Math.abs(px - x);
        if (d < bestD) {
          bestD = d;
          best = p;
          bestX = px;
          bestY = yFor(p.value, meta.rng, meta.padTop, meta.plotH);
        }
      });

      if (!best) {
        tipEl.hidden = true;
        return;
      }

      // Vertical guide: redraw last frame with a light overlay stroke via a sibling
      // (cheap: draw on tip positioning only; guide drawn into tip's sibling canvas overlay)
      drawHoverGuide(canvas, meta, bestX);

      tipEl.hidden = false;
      var left = Math.min(rect.width - 150, Math.max(8, bestX - 20));
      var top = Math.max(8, bestY - 42);
      tipEl.style.left = left + 'px';
      tipEl.style.top = top + 'px';
      var note = best.lowLight ? '<em>low light</em>' : (best.ok === false ? '<em>poor quality</em>' : '');
      tipEl.innerHTML =
        '<strong>' + escapeHtml(meta.label) + ' ' + escapeHtml(meta.formatY(best.value)) + '</strong>' +
        '<span>' + escapeHtml(fmtTick(best.sampled_at, 48)) + '</span>' +
        (note ? '<span class="plants-chart-tip-note">' + note + '</span>' : '');
    });

    canvas.addEventListener('mouseleave', function () {
      tipEl.hidden = true;
      clearHoverGuide(canvas);
    });
  }

  function drawHoverGuide(canvas, meta, x) {
    // Store guide x; paint a thin line by briefly stroking over current bitmap
    // via an offscreen approach: use a dedicated overlay canvas if present.
    var wrap = canvas.closest('.plants-chart-wrap');
    if (!wrap) return;
    var overlay = wrap.querySelector('canvas[data-hover-overlay]');
    if (!overlay) {
      overlay = document.createElement('canvas');
      overlay.setAttribute('data-hover-overlay', '1');
      overlay.className = 'plants-chart-hover-overlay';
      wrap.appendChild(overlay);
    }
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || canvas.width;
    var h = canvas.clientHeight || parseInt(canvas.getAttribute('height'), 10) || 140;
    overlay.width = Math.max(1, Math.floor(w * dpr));
    overlay.height = Math.max(1, Math.floor(h * dpr));
    overlay.style.width = w + 'px';
    overlay.style.height = h + 'px';
    var ctx = overlay.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = COLORS.guide;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, meta.padTop);
    ctx.lineTo(x, meta.padTop + meta.plotH);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function clearHoverGuide(canvas) {
    var wrap = canvas.closest('.plants-chart-wrap');
    if (!wrap) return;
    var overlay = wrap.querySelector('canvas[data-hover-overlay]');
    if (!overlay) return;
    var ctx = overlay.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, overlay.width, overlay.height);
  }

  function bindAllHovers(root) {
    root.querySelectorAll('canvas[data-panel]').forEach(function (c) {
      if (c.getAttribute('data-panel') === 'colorstrip') return;
      bindHover(c, tipForCanvas(c));
    });
  }

  function renderDatasets(el, datasets, selectedId, onChange) {
    if (!el) return;
    datasets = datasets || [];
    if (datasets.length < 2) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    var cur = selectedId == null ? '' : String(selectedId);
    el.innerHTML = '<label class="plants-dataset-label">Dataset ' +
      '<select data-plants-dataset>' +
      datasets.map(function (d) {
        var val = d.series_id == null ? '' : String(d.series_id);
        var sel = val === cur ? ' selected' : '';
        var n = d.sample_count != null ? ' · ' + d.sample_count + ' samples' : '';
        return '<option value="' + escapeHtml(val) + '"' + sel + '>' +
          escapeHtml(d.label || 'Dataset') + n + '</option>';
      }).join('') +
      '</select></label>';
    var sel = el.querySelector('select');
    if (sel && !sel._bound) {
      sel._bound = true;
      sel.addEventListener('change', function () {
        var v = sel.value;
        if (typeof onChange === 'function') onChange(v === '' ? null : parseInt(v, 10));
      });
    }
  }

  function renderDashboard(root, samples, opts) {
    opts = opts || {};
    samples = samples || [];
    ensureStructure(root);

    var state = root._plantsVizState || {
      visible: Object.assign({}, DEFAULT_VISIBLE, opts.visible || {}),
      showLowLight: false,
    };
    root._plantsVizState = state;
    // Pick up newly added panel toggles (L_frame / L_plant) on older sessions
    Object.keys(DEFAULT_VISIBLE).forEach(function (k) {
      if (state.visible[k] == null) state.visible[k] = DEFAULT_VISIBLE[k];
    });
    if (opts.showLowLight != null) state.showLowLight = !!opts.showLowLight;
    state.samples = samples;
    state.opts = Object.assign({}, opts, { showLowLight: !!state.showLowLight });

    // Always recompute client-side so dark / spike samples cannot warp Daily Δ
    var daily = dailyMetricDeltas(samples, 'G_norm', true);
    state.daily = daily;
    var events = opts.watering_events || opts.wateringEvents || [];
    var stats = heroStats(samples, opts.trends, daily);
    renderHero(root.querySelector('[data-plants-hero]'), stats);

    function redraw() {
      state.opts = Object.assign({}, state.opts, { showLowLight: !!state.showLowLight });
      drawPanels(root, state.samples, state.opts, state.visible, state.daily,
        (state.opts && (state.opts.watering_events || state.opts.wateringEvents)) || events);
    }

    renderToggles(root.querySelector('[data-plants-toggles]'), state.visible, function (id, on) {
      state.visible[id] = on;
      syncToggleStyles(root.querySelector('[data-plants-toggles]'), state.visible, state.showLowLight);
      applyVisibility(root, state.visible);
      window.requestAnimationFrame(redraw);
    }, {
      showLowLight: !!state.showLowLight,
      onShowLowLight: function (on) {
        state.showLowLight = !!on;
        syncToggleStyles(root.querySelector('[data-plants-toggles]'), state.visible, state.showLowLight);
        window.requestAnimationFrame(redraw);
      },
    });

    applyVisibility(root, state.visible);
    redraw();

    if (!root._plantsVizResizeBound) {
      root._plantsVizResizeBound = true;
      var resizeTimer = null;
      window.addEventListener('resize', function () {
        if (!root.isConnected || !root._plantsVizState) return;
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          var st = root._plantsVizState;
          drawPanels(root, st.samples || [], st.opts || {}, st.visible,
            st.daily || [], (st.opts && (st.opts.watering_events || st.opts.wateringEvents)) || []);
        }, 120);
      });
    }

    var dsEl = root.querySelector('[data-plants-datasets]');
    if (dsEl) {
      renderDatasets(dsEl, opts.datasets || [], opts.seriesId, function (sid) {
        var next = Object.assign({}, opts, { seriesId: sid });
        loadAndRender(opts.monitorId || root.getAttribute('data-monitor-id'), root, next);
      });
    }

    var caption = root.querySelector('[data-plants-caption]');
    if (caption) {
      if (!samples.length) {
        caption.textContent = 'No samples in this timeframe.';
      } else {
        var first = new Date(samples[0].sampled_at * 1000).toLocaleString();
        var last = new Date(samples[samples.length - 1].sampled_at * 1000).toLocaleString();
        var trends = opts.trends || {};
        var metric = trends.trend_metric ? ' · trend on ' + trends.trend_metric : '';
        var usable = samples.filter(isGreennessUsable).length;
        caption.textContent = samples.length + ' samples (' + usable + ' daytime G_norm) · ' + first + ' → ' + last +
          metric +
          (events.length ? ' · ' + events.length + ' watering' : '') +
          (trends.declining_greenness ? ' · declining flagged' : '') +
          (state.showLowLight ? ' · showing low-light' : ' · low-light excluded from G_norm');
      }
    }

    return { samples: samples, daily_deltas: daily, hero: stats };
  }

  function buildQuery(options) {
    options = options || {};
    var q = [];
    var limit = options.limit || 200;
    q.push('limit=' + encodeURIComponent(limit));
    if (options.hours != null && options.hours > 0) {
      q.push('hours=' + encodeURIComponent(options.hours));
    } else if (options.since != null) {
      q.push('since=' + encodeURIComponent(options.since));
    }
    if (options.seriesId != null && options.seriesId !== '') {
      q.push('series_id=' + encodeURIComponent(options.seriesId));
    }
    return q.join('&');
  }

  async function loadAndRender(monitorId, target, options) {
    options = options || {};
    try {
      var r = await fetch('/api/plants/monitors/' + monitorId + '/color-timeline?' + buildQuery(options));
      var j = await r.json();
      var samples = j.samples || [];
      if (target && target.setAttribute) target.setAttribute('data-monitor-id', monitorId);
      var drawOpts = Object.assign({}, options, {
        watering_events: j.watering_events || [],
        trends: j.trends || null,
        // Prefer client recompute with strict low-light gate
        daily_deltas: dailyMetricDeltas(samples, 'G_norm', true),
        datasets: j.datasets || options.datasets || [],
        seriesId: j.series_id != null ? j.series_id : options.seriesId,
        monitorId: monitorId,
      });

      // target may be a canvas (legacy) or a container root
      if (target && target.tagName === 'CANVAS') {
        var wrap = target.parentElement;
        if (wrap) {
          renderDashboard(wrap, samples, drawOpts);
        } else {
          draw(target, samples, drawOpts);
        }
      } else if (target) {
        renderDashboard(target, samples, drawOpts);
      }
      return Object.assign({ ok: true }, j, { daily_deltas: drawOpts.daily_deltas || dailyMetricDeltas(samples, 'G_norm', true) });
    } catch (e) {
      if (target && target.tagName !== 'CANVAS') {
        renderDashboard(target, [], options);
      } else if (target) {
        draw(target, [], options);
      }
      return { ok: false, samples: [], trends: null, watering_events: [], daily_deltas: [] };
    }
  }

  return {
    loadAndRender: loadAndRender,
    draw: draw,
    renderDashboard: renderDashboard,
    fmtTick: fmtTick,
    dailyMetricDeltas: dailyMetricDeltas,
    rollingPercentileBand: rollingPercentileBand,
    heroStats: heroStats,
    isGreennessUsable: isGreennessUsable,
    isLowLight: isLowLight,
    isSampleOk: isSampleOk,
    rawGNorm: rawGNorm,
    COLORS: COLORS,
  };
})();

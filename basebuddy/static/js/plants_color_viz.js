/**
 * Color-band timeline — subtle green drift over time.
 */
window.PlantColorViz = (function () {
  function rgb(c) {
    if (!c) return 'rgb(180,180,180)';
    return 'rgb(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ')';
  }

  function draw(canvas, samples) {
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || 600;
    var h = 120;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--color-surface-alt') || '#f8fafc';
    ctx.fillRect(0, 0, w, h);

    if (!samples.length) {
      ctx.fillStyle = '#94a3b8';
      ctx.font = '13px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Run Analyze to build color history', w / 2, h / 2);
      return;
    }

    var bandTop = 8;
    var bandH = 72;
    var lineTop = bandTop + bandH + 10;
    var lineH = 28;
    var n = samples.length;
    var colW = Math.max(3, (w - 16) / n);

    samples.forEach(function (s, i) {
      var x = 8 + i * colW;
      var c = s.rgb || {};
      var dom = (s.dominant_colors_rgb && s.dominant_colors_rgb[1]) || [c.r, c.g, c.b];
      var r = dom[0], g = dom[1], b = dom[2];
      var grad = ctx.createLinearGradient(x, bandTop, x, bandTop + bandH);
      grad.addColorStop(0, 'rgb(' + Math.min(255, r + 40) + ',' + Math.min(255, g + 40) + ',' + Math.min(255, b + 30) + ')');
      grad.addColorStop(0.5, rgb(c));
      grad.addColorStop(1, 'rgb(' + Math.max(0, r - 25) + ',' + Math.max(0, g - 20) + ',' + Math.max(0, b - 15) + ')');
      ctx.fillStyle = grad;
      ctx.fillRect(x, bandTop, Math.ceil(colW) + 0.5, bandH);
    });

    var gvals = samples.map(function (s) { return s.greenness != null ? s.greenness : 0.5; });
    var gMin = Math.min.apply(null, gvals);
    var gMax = Math.max.apply(null, gvals);
    var gRange = Math.max(0.02, gMax - gMin);

    ctx.strokeStyle = 'rgba(26, 115, 232, 0.85)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    samples.forEach(function (s, i) {
      var x = 8 + i * colW + colW / 2;
      var g = s.greenness != null ? s.greenness : 0.5;
      var y = lineTop + lineH - ((g - gMin) / gRange) * (lineH - 4);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = '#64748b';
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('Color bands (median plant tone)', 8, bandTop - 2);
    ctx.fillText('Greenness trend', 8, lineTop - 2);

    var first = new Date(samples[0].sampled_at * 1000);
    var last = new Date(samples[n - 1].sampled_at * 1000);
    ctx.textAlign = 'right';
    ctx.fillText(last.toLocaleDateString(), w - 8, h - 4);
    ctx.textAlign = 'left';
    ctx.fillText(first.toLocaleDateString(), 8, h - 4);
  }

  async function loadAndRender(monitorId, canvas) {
    try {
      var r = await fetch('/api/plants/monitors/' + monitorId + '/color-timeline?limit=120');
      var j = await r.json();
      draw(canvas, j.samples || []);
    } catch (e) {
      draw(canvas, []);
    }
  }

  return { loadAndRender: loadAndRender, draw: draw };
})();

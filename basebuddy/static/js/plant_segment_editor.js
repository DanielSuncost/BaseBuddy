/**
 * SAM click-to-segment editor for plant monitors.
 * Left-click = foreground (plant), right-click = background.
 */
(function (global) {
  var modal = null;
  var canvas = null;
  var ctx = null;
  var statusEl = null;
  var loadingEl = null;
  var monitorId = null;
  var cameraId = null;
  var onSaved = null;
  var points = [];
  var labels = [];
  var previewTimer = null;
  var baseImage = null;

  function ensureModal() {
    if (modal) return;
    modal = document.getElementById('plant-segment-modal');
    canvas = document.getElementById('plant-segment-canvas');
    statusEl = document.getElementById('plant-segment-status');
    loadingEl = document.getElementById('plant-segment-loading');
    ctx = canvas.getContext('2d');

    document.getElementById('plant-segment-cancel').addEventListener('click', close);
    document.getElementById('plant-segment-reset').addEventListener('click', resetPoints);
    document.getElementById('plant-segment-save').addEventListener('click', save);

    canvas.addEventListener('click', function (e) {
      addPoint(e, 1);
    });
    canvas.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      addPoint(e, 0);
    });
  }

  function setStatus(msg, isErr) {
    if (!statusEl) return;
    statusEl.textContent = msg || '';
    statusEl.style.color = isErr ? 'var(--danger, #c5221f)' : 'var(--text-muted)';
  }

  function setLoading(on) {
    if (loadingEl) loadingEl.hidden = !on;
  }

  function canvasCoords(e) {
    var rect = canvas.getBoundingClientRect();
    return {
      x: Math.round((e.clientX - rect.left) * (canvas.width / rect.width)),
      y: Math.round((e.clientY - rect.top) * (canvas.height / rect.height)),
    };
  }

  function drawOfflineNotice() {
    canvas.width = 960;
    canvas.height = 540;
    ctx.fillStyle = '#1a1d21';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#aab2bd';
    ctx.font = '22px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Camera offline — no frame available', canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = '15px sans-serif';
    ctx.fillText('Reconnect the camera, then reopen this editor.', canvas.width / 2, canvas.height / 2 + 22);
    ctx.textAlign = 'start';
  }

  function drawBase() {
    if (!baseImage) return;
    canvas.width = baseImage.width;
    canvas.height = baseImage.height;
    ctx.drawImage(baseImage, 0, 0);
  }

  function drawPointMarkers() {
    for (var i = 0; i < points.length; i++) {
      var pt = points[i];
      var lab = labels[i];
      ctx.beginPath();
      ctx.arc(pt[0], pt[1], 7, 0, Math.PI * 2);
      ctx.fillStyle = lab === 1 ? 'rgba(40, 200, 80, 0.9)' : 'rgba(60, 60, 220, 0.9)';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  function schedulePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, 180);
  }

  function addPoint(e, label) {
    if (!baseImage) {
      setStatus('No camera frame — check that the camera is online, then reopen this editor.', true);
      return;
    }
    var c = canvasCoords(e);
    points.push([c.x, c.y]);
    labels.push(label);
    var fg = labels.filter(function (l) { return l === 1; }).length;
    var bg = labels.length - fg;
    setStatus('Plant: ' + fg + ' · Background: ' + bg + ' — updating mask…');
    schedulePreview();
  }

  function resetPoints() {
    points = [];
    labels = [];
    drawBase();
    setStatus('Click the plant (left) and background (right) to define the region.');
  }

  async function loadFrame() {
    setLoading(true);
    setStatus('Loading camera frame…');
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        baseImage = img;
        drawBase();
        setLoading(false);
        setStatus('Left-click plant · Right-click outside. Add a few points each.');
        resolve();
      };
      img.onerror = function () {
        setLoading(false);
        drawOfflineNotice();
        reject(new Error('Camera ' + (cameraId + 1) + ' has no frame available (offline or still connecting).'));
      };
      img.src = '/api/scenes/camera/' + cameraId + '/preview?w=1280&t=' + Date.now();
    });
  }

  async function refreshPreview() {
    if (!points.length) {
      drawBase();
      drawPointMarkers();
      return;
    }
    try {
      var r = await fetch('/api/plants/segment/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cameraId, points: points, labels: labels }),
      });
      if (!r.ok) {
        var err = await r.json().catch(function () { return {}; });
        throw new Error(err.error || 'Preview failed');
      }
      var blob = await r.blob();
      var url = URL.createObjectURL(blob);
      await new Promise(function (resolve, reject) {
        var img = new Image();
        img.onload = function () {
          canvas.width = img.width;
          canvas.height = img.height;
          ctx.drawImage(img, 0, 0);
          URL.revokeObjectURL(url);
          resolve();
        };
        img.onerror = reject;
        img.src = url;
      });
      var fg = labels.filter(function (l) { return l === 1; }).length;
      var bg = labels.length - fg;
      setStatus('Plant: ' + fg + ' · Background: ' + bg);
    } catch (e) {
      drawBase();
      drawPointMarkers();
      setStatus(e.message, true);
    }
  }

  async function save() {
    if (!labels.some(function (l) { return l === 1; })) {
      setStatus('Add at least one left-click on the plant.', true);
      return;
    }
    var btn = document.getElementById('plant-segment-save');
    btn.disabled = true;
    setStatus('Saving segmentation…');
    try {
      var r = await fetch('/api/plants/segment/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          monitor_id: monitorId,
          camera_id: cameraId,
          points: points,
          labels: labels,
        }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Save failed');
      setStatus('Saved pattern #' + j.pattern_id);
      if (typeof onSaved === 'function') onSaved(j);
      setTimeout(close, 400);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function close() {
    if (modal) modal.hidden = true;
    points = [];
    labels = [];
    monitorId = null;
    cameraId = null;
    onSaved = null;
    if (previewTimer) clearTimeout(previewTimer);
  }

  async function open(opts) {
    ensureModal();
    monitorId = opts.monitorId;
    cameraId = parseInt(opts.cameraId, 10);
    onSaved = opts.onSaved;
    points = [];
    labels = [];
    baseImage = null;
    modal.hidden = false;
    try {
      await loadFrame();
    } catch (e) {
      setStatus(e.message, true);
    }
  }

  global.PlantSegmentEditor = { open: open, close: close };
})(window);

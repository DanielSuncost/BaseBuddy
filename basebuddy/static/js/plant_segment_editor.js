/**
 * SAM click-to-segment editor for plant monitors.
 * Optional frame crop, then left-click = plant, right-click = background.
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
  var fullImage = null;
  var greenRefine = 0;
  var lumaCarve = 0;
  var frameCrop = null;
  var cropMode = false;
  var cropDrag = null;
  var ignoreClick = false;
  var suppressPrimaryClick = false;
  var previewGen = 0;
  var previewAbort = null;

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
    var setCrop = document.getElementById('plant-segment-set-crop');
    var clearCrop = document.getElementById('plant-segment-clear-crop');
    if (setCrop) setCrop.addEventListener('click', beginCrop);
    if (clearCrop) clearCrop.addEventListener('click', clearCropNow);

    var refine = document.getElementById('plant-segment-green-refine');
    if (refine && !refine._bound) {
      refine._bound = true;
      refine.addEventListener('input', function () {
        greenRefine = parseFloat(refine.value) || 0;
        var valEl = document.getElementById('plant-segment-refine-val');
        if (valEl) valEl.textContent = greenRefine ? greenRefine.toFixed(1) : '0';
        if (points.length) schedulePreview();
      });
    }
    var luma = document.getElementById('plant-segment-luma-carve');
    if (luma && !luma._bound) {
      luma._bound = true;
      luma.addEventListener('input', function () {
        lumaCarve = parseFloat(luma.value) || 0;
        var valEl = document.getElementById('plant-segment-luma-val');
        if (valEl) valEl.textContent = lumaCarve > 0 ? String(Math.round(lumaCarve)) : 'off';
        if (points.length) schedulePreview();
      });
    }

    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerCancel);
    canvas.addEventListener('click', function (e) {
      if (cropMode || ignoreClick || suppressPrimaryClick || e.button !== 0) {
        ignoreClick = false;
        return;
      }
      addPoint(e, 1);
    });
    canvas.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      suppressPrimaryClick = true;
      if (cropMode) return;
      addPoint(e, 0);
      setTimeout(function () { suppressPrimaryClick = false; }, 0);
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

  function updateCropBadge() {
    var badge = document.getElementById('plant-segment-crop-badge');
    if (!badge) return;
    badge.hidden = !frameCrop;
    badge.textContent = frameCrop ? 'Crop on' : '';
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

  function drawCropOverlay() {
    if (!fullImage) return;
    canvas.width = fullImage.width;
    canvas.height = fullImage.height;
    ctx.drawImage(fullImage, 0, 0);
    ctx.fillStyle = 'rgba(2, 6, 23, 0.45)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    var r = cropDrag;
    if (!r && frameCrop) {
      r = {
        x: Math.round(frameCrop.x * fullImage.width),
        y: Math.round(frameCrop.y * fullImage.height),
        w: Math.round(frameCrop.w * fullImage.width),
        h: Math.round(frameCrop.h * fullImage.height),
      };
    }
    if (!r) return;
    var x = Math.min(r.x, r.x + r.w);
    var y = Math.min(r.y, r.y + r.h);
    var w = Math.abs(r.w);
    var h = Math.abs(r.h);
    ctx.drawImage(fullImage, x, y, w, h, x, y, w, h);
    ctx.strokeStyle = '#14b8a6';
    ctx.lineWidth = 3;
    ctx.strokeRect(x + 1.5, y + 1.5, Math.max(0, w - 3), Math.max(0, h - 3));
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
    if (cropMode) drawCropOverlay();
    else {
      drawBase();
      setStatus('Click the plant (left) and background (right) to define the region.');
    }
  }

  function clampToCanvas(c) {
    return {
      x: Math.max(0, Math.min(canvas.width, c.x)),
      y: Math.max(0, Math.min(canvas.height, c.y)),
    };
  }

  function onPointerDown(e) {
    if (e.button === 2 || e.ctrlKey) suppressPrimaryClick = true;
    else suppressPrimaryClick = false;
    if (!cropMode || !fullImage || e.button !== 0 || e.ctrlKey) return;
    var c = clampToCanvas(canvasCoords(e));
    cropDrag = { x: c.x, y: c.y, w: 0, h: 0 };
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  }

  function onPointerMove(e) {
    if (!cropMode || !cropDrag) return;
    var c = clampToCanvas(canvasCoords(e));
    cropDrag.w = c.x - cropDrag.x;
    cropDrag.h = c.y - cropDrag.y;
    drawCropOverlay();
  }

  function onPointerCancel() {
    if (!cropDrag) return;
    cropDrag = null;
    ignoreClick = true;
    drawCropOverlay();
  }

  function onPointerUp() {
    if (!cropMode || !cropDrag || !fullImage) return;
    var x = Math.min(cropDrag.x, cropDrag.x + cropDrag.w);
    var y = Math.min(cropDrag.y, cropDrag.y + cropDrag.h);
    var w = Math.abs(cropDrag.w);
    var h = Math.abs(cropDrag.h);
    cropDrag = null;
    ignoreClick = true;
    if (w < 24 || h < 24) {
      drawCropOverlay();
      setStatus('Drag a larger box to crop — or click Clear crop.', true);
      return;
    }
    frameCrop = {
      x: x / fullImage.width,
      y: y / fullImage.height,
      w: w / fullImage.width,
      h: h / fullImage.height,
    };
    finishCrop();
  }

  function beginCrop() {
    cropMode = true;
    points = [];
    labels = [];
    cropDrag = null;
    if (!fullImage) {
      setStatus('Load a frame first.', true);
      return;
    }
    baseImage = fullImage;
    drawCropOverlay();
    setStatus('Drag a box around the area you want to keep. Analysis and playback will use only that crop.');
  }

  async function finishCrop() {
    cropMode = false;
    updateCropBadge();
    await persistCrop();
    try {
      await showWorkingFrame();
      setStatus('Crop set — left-click the plant, right-click background. Re-save the mask so SAM points match the crop.');
    } catch (e) {
      setStatus(e.message, true);
    }
  }

  async function clearCropNow() {
    frameCrop = null;
    cropMode = false;
    cropDrag = null;
    points = [];
    labels = [];
    updateCropBadge();
    await persistCrop();
    if (fullImage) {
      baseImage = fullImage;
      drawBase();
    }
    setStatus('Crop cleared — full frame. Place SAM points again if the mask was saved on a crop.');
  }

  function persistCrop() {
    if (!monitorId) return Promise.resolve();
    return fetch('/api/plants/monitors/' + encodeURIComponent(monitorId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frame_crop: frameCrop }),
    }).catch(function () {});
  }

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('Could not load camera frame')); };
      img.src = src;
    });
  }

  async function loadFullFrame() {
    fullImage = await loadImage('/api/scenes/camera/' + cameraId + '/preview?w=1280&t=' + Date.now());
  }

  async function showWorkingFrame() {
    if (frameCrop && monitorId) {
      var q = '&crop=' + encodeURIComponent(JSON.stringify(frameCrop));
      baseImage = await loadImage(
        '/api/plants/monitors/' + encodeURIComponent(monitorId) +
        '/live-frame?w=1280&t=' + Date.now() + q
      );
    } else if (fullImage) {
      baseImage = fullImage;
    }
    drawBase();
  }

  async function loadFrame() {
    setLoading(true);
    setStatus('Loading camera frame…');
    try {
      await loadFullFrame();
      await showWorkingFrame();
      setLoading(false);
      setStatus(frameCrop
        ? 'Crop is on. Left-click plant · Right-click outside. Use Set crop to redraw.'
        : 'Optional: Set crop first, then left-click plant · Right-click outside.');
    } catch (e) {
      setLoading(false);
      drawOfflineNotice();
      throw new Error('Camera ' + (cameraId + 1) + ' has no frame available (offline or still connecting).');
    }
  }

  async function refreshPreview() {
    var gen = ++previewGen;
    if (previewAbort) previewAbort.abort();
    previewAbort = new AbortController();
    if (!points.length) {
      drawBase();
      drawPointMarkers();
      return;
    }
    try {
      var r = await fetch('/api/plants/segment/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          camera_id: cameraId,
          points: points,
          labels: labels,
          green_refine: greenRefine,
          luma_carve: lumaCarve,
          frame_crop: frameCrop,
        }),
        signal: previewAbort.signal,
      });
      if (gen !== previewGen) return;
      if (!r.ok) {
        var err = await r.json().catch(function () { return {}; });
        throw new Error(err.error || 'Preview failed');
      }
      var blob = await r.blob();
      if (gen !== previewGen) return;
      var url = URL.createObjectURL(blob);
      await new Promise(function (resolve, reject) {
        var img = new Image();
        img.onload = function () {
          if (gen !== previewGen) {
            URL.revokeObjectURL(url);
            resolve();
            return;
          }
          canvas.width = img.width;
          canvas.height = img.height;
          ctx.drawImage(img, 0, 0);
          URL.revokeObjectURL(url);
          resolve();
        };
        img.onerror = reject;
        img.src = url;
      });
      if (gen !== previewGen) return;
      var fg = labels.filter(function (l) { return l === 1; }).length;
      var bg = labels.length - fg;
      setStatus('Plant: ' + fg + ' · Background: ' + bg);
    } catch (e) {
      if (e.name === 'AbortError' || gen !== previewGen) return;
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
          frame_crop: frameCrop,
          force_new: !!(document.getElementById('plant-segment-new-series') || {}).checked,
          reanalyze: !!(document.getElementById('plant-segment-reanalyze') || {}).checked,
        }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Save failed');
      try {
        await fetch('/api/plants/monitors/' + encodeURIComponent(monitorId) +
          '/sam-growth/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ green_refine: greenRefine, luma_carve: lumaCarve }),
        });
      } catch (e0) {}
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
    frameCrop = null;
    cropMode = false;
    cropDrag = null;
    fullImage = null;
    baseImage = null;
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
    fullImage = null;
    cropMode = false;
    cropDrag = null;
    frameCrop = opts.frameCrop || null;
    greenRefine = parseFloat(opts.greenRefine) || 0;
    lumaCarve = parseFloat(opts.lumaCarve) || 0;
    var refine = document.getElementById('plant-segment-green-refine');
    var valEl = document.getElementById('plant-segment-refine-val');
    if (refine) refine.value = String(greenRefine);
    if (valEl) valEl.textContent = greenRefine ? greenRefine.toFixed(1) : '0';
    var luma = document.getElementById('plant-segment-luma-carve');
    var lumaVal = document.getElementById('plant-segment-luma-val');
    if (luma) luma.value = String(lumaCarve);
    if (lumaVal) lumaVal.textContent = lumaCarve > 0 ? String(Math.round(lumaCarve)) : 'off';
    updateCropBadge();
    modal.hidden = false;
    var newSeries = document.getElementById('plant-segment-new-series');
    if (newSeries) newSeries.checked = false;
    var reanalyze = document.getElementById('plant-segment-reanalyze');
    if (reanalyze && !reanalyze._bound) {
      reanalyze._bound = true;
      reanalyze.addEventListener('change', function () {
        if (reanalyze.checked && newSeries) newSeries.checked = true;
      });
    }
    if (reanalyze) reanalyze.checked = false;
    try {
      await loadFrame();
    } catch (e) {
      setStatus(e.message, true);
    }
  }

  global.PlantSegmentEditor = { open: open, close: close };
})(window);

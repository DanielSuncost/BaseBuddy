// Camera wall: tile grid rendering, tile click handling, loading placeholders,
// and grid/birdseye view layout.

function renderCameras() {
  console.log('renderCameras called with', cameras.length, 'cameras');
  const grid = document.getElementById('camera-grid');
  if (!grid) {
    console.error('camera-grid element not found!');
    return;
  }
  
  grid.innerHTML = '';
  
  // Canvases are being recreated; drop cached 2D contexts of the old (detached) ones
  canvasContexts = {};
  
  // Filter cameras by selected group
  let visibleCameras = cameras;
  if (selectedGroup !== 'all') {
    const group = cameraGroups.find(g => g.id === selectedGroup);
    if (group) {
      visibleCameras = cameras.filter(c => group.camera_ids.includes(c.id));
    }
  }
  
  // Show filtered cameras
  console.log('Rendering cameras:', visibleCameras.length, 'of', cameras.length);
  visibleCameras.forEach((cam, index) => {
    // Use canvas for WebSocket streaming (unlimited cameras)
    grid.innerHTML += `
      <div class="camera-tile" id="tile-${cam.id}" data-cam-id="${cam.id}">
        <canvas id="canvas-${cam.id}" style="width:100%; height:100%; object-fit:contain;"></canvas>
        <div class="tile-loading" id="loading-${cam.id}">
          <span class="material-icons-outlined spinning">sync</span>
          <span>Connecting...</span>
        </div>
        <div class="tile-rec-overlay">
          <span class="tile-rec-dot"></span>
          <button class="tile-rec-btn tile-rec-start" onclick="toggleRecording(${cam.id}, event)" title="Start recording">
            <span class="material-icons-outlined">fiber_manual_record</span>
          </button>
          <button class="tile-rec-btn tile-rec-pause" onclick="toggleRecording(${cam.id}, event, true)" title="Pause recording">
            <span class="material-icons-outlined">pause</span>
          </button>
        </div>
        <div class="tile-rec-badge" id="rec-badge-${cam.id}">Recording stopped</div>
        <div class="tile-label">
          <span class="tile-name">${cam.name || 'Camera ' + (cam.id + 1)}</span>
          <span class="tile-status" id="status-${cam.id}"></span>
        </div>
        <button class="tile-audio-btn" id="audio-btn-${cam.id}" onclick="event.stopPropagation(); toggleTileAudio(${cam.id})" title="Toggle audio">
          <span class="material-icons-outlined">volume_off</span>
        </button>
        <button class="tile-settings-btn" onclick="event.stopPropagation(); openCameraProfile(${cam.id})" title="Camera settings">
          <span class="material-icons-outlined">settings</span>
        </button>
        <button class="tile-remove-btn" onclick="event.stopPropagation(); removeFromWall(${cam.id})" title="Remove from wall">
          <span class="material-icons-outlined">close</span>
        </button>
      </div>
    `;
    
    // Check camera status
    fetch(`/api/cameras/${cam.id}/profile`)
      .then(r => r.json())
      .then(data => {
        const tile = document.getElementById(`tile-${cam.id}`);
        const statusEl = document.getElementById(`status-${cam.id}`);
        if (tile && data.ok && data.profile) {
          const camEnabled = data.profile.camera_enabled !== false;
          const detEnabled = data.profile.detection_enabled !== false;
          if (!camEnabled) {
            tile.classList.add('disabled');
            if (statusEl) statusEl.textContent = 'OFF';
          } else if (detEnabled) {
            if (statusEl) statusEl.textContent = 'AI ON';
          } else {
            if (statusEl) statusEl.textContent = 'Video Only';
          }
        }
      })
      .catch(() => {});

    syncRecordingState(cam.id);
  });
  
  // Add button to add more cameras
  grid.innerHTML += `
    <div class="empty-slot" onclick="openAddCameraModal()">
      <div class="empty-slot-content">
        <span class="material-icons-outlined">add_circle_outline</span>
        <p style="margin: 8px 0 0; font-size: 13px;">Add Camera</p>
        ${inactiveCameras.length > 0 ? `<small style="opacity:0.6">${inactiveCameras.length} saved</small>` : ''}
      </div>
    </div>
  `;
  
  // Attach click handlers to tiles
  attachTileClickHandlers();
  applyCanvasLayout();
  
  // Start WebSocket video streaming
  setTimeout(initWebSocketStreaming, 100);
}

function openInferenceView(camId) {
  console.log('openInferenceView called for camera', camId);
  window.location.href = '/camera/' + camId;
}

// Attach click handlers to camera tiles after they're rendered
function attachTileClickHandlers() {
  document.querySelectorAll('.camera-tile').forEach(tile => {
    tile.style.cursor = 'pointer';
    tile.addEventListener('click', function(e) {
      if (this.dataset.suppressClick === '1') {
        this.dataset.suppressClick = '';
        return;
      }
      if (e.target.closest('.tile-remove-btn') || e.target.closest('.tile-rec-btn') || e.target.closest('.tile-settings-btn') || e.target.closest('.tile-audio-btn') || e.target.closest('.tile-resize')) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      const camId = parseInt(this.id.replace('tile-', ''));
      console.log('Camera tile clicked:', camId);
      if (!isNaN(camId)) {
        openInferenceView(camId);
      }
    });
  });
}

function addLoadingPlaceholder(id, name) {
  const grid = document.getElementById('camera-grid');
  if (!grid) return;
  
  // Insert before the "Add Camera" slot
  const addSlot = grid.querySelector('.empty-slot');
  const placeholder = document.createElement('div');
  placeholder.id = id;
  placeholder.className = 'camera-tile loading-placeholder';
  placeholder.innerHTML = `
    <div class="loading-content">
      <span class="material-icons-outlined spinning" style="font-size: 48px; opacity: 0.6;">sync</span>
      <p style="margin: 12px 0 0; font-size: 14px;">Adding ${name}...</p>
      <small style="opacity: 0.6;">Saving configuration</small>
    </div>
  `;
  
  if (addSlot) {
    grid.insertBefore(placeholder, addSlot);
  } else {
    grid.appendChild(placeholder);
  }
}

function removeLoadingPlaceholder(id) {
  const placeholder = document.getElementById(id);
  if (placeholder) {
    placeholder.remove();
  }
}

const WALL_CANVAS_KEY = 'wallCanvasLayout';

function loadCanvasLayouts() {
  try {
    const parsed = JSON.parse(localStorage.getItem(WALL_CANVAS_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (e) {
    return {};
  }
}

function canvasLayoutKey() {
  return selectedGroup || 'all';
}

function clampCanvasBox(box) {
  const w = Math.min(100, Math.max(8, Number(box.w) || 8));
  const h = Math.min(100, Math.max(8, Number(box.h) || 8));
  return {
    x: Math.min(100 - w, Math.max(0, Number(box.x) || 0)),
    y: Math.min(100 - h, Math.max(0, Number(box.y) || 0)),
    w: w,
    h: h,
    z: Math.max(1, Math.min(999, parseInt(box.z, 10) || 1)),
  };
}

function defaultCanvasBox(index, count) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(Math.max(count, 1))));
  const rows = Math.max(1, Math.ceil(count / cols));
  const gap = 0.8;
  const w = (100 - gap * (cols + 1)) / cols;
  const h = (100 - gap * (rows + 1)) / rows;
  const col = index % cols;
  const row = Math.floor(index / cols);
  return clampCanvasBox({
    x: gap + col * (w + gap),
    y: gap + row * (h + gap),
    w: w,
    h: h,
    z: index + 1,
  });
}

function placeCanvasTile(tile, box) {
  const placed = clampCanvasBox(box);
  tile.style.left = placed.x.toFixed(2) + '%';
  tile.style.top = placed.y.toFixed(2) + '%';
  tile.style.width = placed.w.toFixed(2) + '%';
  tile.style.height = placed.h.toFixed(2) + '%';
  tile.style.zIndex = String(placed.z);
  return placed;
}

function readCanvasBox(tile) {
  return clampCanvasBox({
    x: parseFloat(tile.style.left),
    y: parseFloat(tile.style.top),
    w: parseFloat(tile.style.width),
    h: parseFloat(tile.style.height),
    z: parseInt(tile.style.zIndex || '1', 10),
  });
}

function saveCanvasLayout() {
  const grid = document.getElementById('camera-grid');
  if (!grid) return;
  const layouts = loadCanvasLayouts();
  const group = {};
  grid.querySelectorAll('.camera-tile').forEach(function (tile) {
    const camId = tile.dataset.camId;
    if (!camId) return;
    group[camId] = readCanvasBox(tile);
  });
  layouts[canvasLayoutKey()] = group;
  localStorage.setItem(WALL_CANVAS_KEY, JSON.stringify(layouts));
}

function applyCanvasLayout() {
  const grid = document.getElementById('camera-grid');
  if (!grid) return;
  const canvasMode = wallViewMode === 'canvas';
  grid.classList.toggle('wall-canvas', canvasMode);
  if (!canvasMode) {
    grid.querySelectorAll('.camera-tile').forEach(function (tile) {
      tile.style.left = '';
      tile.style.top = '';
      tile.style.width = '';
      tile.style.height = '';
      tile.style.zIndex = '';
      tile.style.cursor = '';
      const handle = tile.querySelector('.tile-resize');
      if (handle) handle.remove();
    });
    return;
  }
  const tiles = Array.from(grid.querySelectorAll('.camera-tile'));
  const saved = (loadCanvasLayouts()[canvasLayoutKey()]) || {};
  tiles.forEach(function (tile, index) {
    const camId = tile.dataset.camId;
    const stored = saved[camId];
    const box = stored ? clampCanvasBox(stored) : defaultCanvasBox(index, tiles.length);
    placeCanvasTile(tile, box);
    tile.style.cursor = 'grab';
    if (!tile.querySelector('.tile-resize')) {
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'tile-resize';
      handle.title = 'Drag to resize';
      handle.setAttribute('aria-label', 'Resize camera');
      tile.appendChild(handle);
    }
  });
}

function onCanvasPointerDown(event) {
  if (wallViewMode !== 'canvas' || event.button !== 0) return;
  const tile = event.target.closest('.camera-tile');
  if (!tile) return;
  if (event.target.closest('.tile-remove-btn, .tile-rec-btn, .tile-settings-btn, .tile-audio-btn')) return;
  const resizing = Boolean(event.target.closest('.tile-resize'));
  if (!resizing && event.target.closest('button')) return;

  const grid = document.getElementById('camera-grid');
  const rect = grid.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  let topZ = 1;
  grid.querySelectorAll('.camera-tile').forEach(function (other) {
    topZ = Math.max(topZ, parseInt(other.style.zIndex || '1', 10));
  });
  const start = readCanvasBox(tile);
  start.z = Math.min(999, topZ + 1);
  placeCanvasTile(tile, start);
  tile.classList.add('is-arranging');

  const originX = event.clientX;
  const originY = event.clientY;
  let moved = false;

  function move(ev) {
    const dx = ((ev.clientX - originX) / rect.width) * 100;
    const dy = ((ev.clientY - originY) / rect.height) * 100;
    if (Math.abs(ev.clientX - originX) + Math.abs(ev.clientY - originY) > 4) moved = true;
    if (resizing) {
      placeCanvasTile(tile, {
        x: start.x,
        y: start.y,
        w: start.w + dx,
        h: start.h + dy,
        z: start.z,
      });
    } else {
      placeCanvasTile(tile, {
        x: start.x + dx,
        y: start.y + dy,
        w: start.w,
        h: start.h,
        z: start.z,
      });
    }
  }

  function up() {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    document.removeEventListener('pointercancel', up);
    tile.classList.remove('is-arranging');
    if (moved) {
      tile.dataset.suppressClick = '1';
      saveCanvasLayout();
    }
  }

  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
  document.addEventListener('pointercancel', up);
  event.preventDefault();
}

function setWallView(mode) {
  wallViewMode = mode;
  localStorage.setItem('wallViewMode', mode);
  const grid = document.getElementById('camera-grid');
  const bird = document.getElementById('birdseye-wrap');
  const gridBtn = document.getElementById('viewGridBtn');
  const birdBtn = document.getElementById('viewBirdseyeBtn');
  const canvasBtn = document.getElementById('viewCanvasBtn');
  if (grid) grid.hidden = mode === 'birdseye';
  if (bird) bird.hidden = mode !== 'birdseye';
  if (gridBtn) gridBtn.classList.toggle('active', mode === 'grid');
  if (birdBtn) birdBtn.classList.toggle('active', mode === 'birdseye');
  if (canvasBtn) canvasBtn.classList.toggle('active', mode === 'canvas');
  const hint = document.querySelector('.wall-hint');
  if (hint) {
    hint.textContent = mode === 'canvas'
      ? 'Drag a camera to move it. Drag the corner to resize.'
      : 'Click camera for detection view';
  }
  if (mode === 'birdseye') renderBirdseye();
  else applyCanvasLayout();
}

function renderBirdseye() {
  const canvas = document.getElementById('birdseye-canvas');
  const wrap = document.getElementById('birdseye-wrap');
  if (!canvas || !wrap || wrap.hidden) return;
  const ids = getVisibleCameraIds();
  if (!ids.length) return;
  const rect = wrap.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = Math.max(320, rect.height);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0f0f1a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const cols = Math.ceil(Math.sqrt(ids.length));
  const rows = Math.ceil(ids.length / cols);
  const pad = 8;
  const cellW = (canvas.width - pad * (cols + 1)) / cols;
  const cellH = (canvas.height - pad * (rows + 1)) / rows;
  ids.forEach(function (camId, i) {
    const b64 = birdseyeFrames[camId];
    if (!b64) return;
    const col = i % cols;
    const row = Math.floor(i / cols);
    const x = pad + col * (cellW + pad);
    const y = pad + row * (cellH + pad);
    const img = new Image();
    img.onload = function () {
      const scale = Math.min(cellW / img.width, cellH / img.height);
      const dw = img.width * scale;
      const dh = img.height * scale;
      const dx = x + (cellW - dw) / 2;
      const dy = y + (cellH - dh) / 2;
      ctx.fillStyle = '#1a1a2e';
      ctx.fillRect(x, y, cellW, cellH);
      ctx.drawImage(img, dx, dy, dw, dh);
      ctx.strokeStyle = 'rgba(99,102,241,0.6)';
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, cellW, cellH);
      const cam = cameras.find(function (c) { return c.id === camId; });
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(x, y + cellH - 24, cellW, 24);
      ctx.fillStyle = '#fff';
      ctx.font = '12px Inter, sans-serif';
      ctx.fillText(cam ? cam.name : ('Cam ' + (camId + 1)), x + 8, y + cellH - 8);
    };
    img.src = 'data:image/jpeg;base64,' + b64;
  });
}

function syncWallFullscreenButton() {
  const on = document.body.classList.contains('wall-fs');
  const btn = document.getElementById('wallFsBtn');
  if (btn) {
    const icon = btn.querySelector('.material-icons-outlined');
    if (icon) icon.textContent = on ? 'fullscreen_exit' : 'fullscreen';
    btn.title = on ? 'Exit fullscreen' : 'Fullscreen';
    btn.classList.toggle('active', on);
  }
  const menu = document.getElementById('wallFsMenu');
  if (menu) {
    menu.hidden = !on;
    menu.setAttribute('aria-expanded', document.body.classList.contains('wall-fs-chrome') ? 'true' : 'false');
    menu.setAttribute('aria-label', document.body.classList.contains('wall-fs-chrome') ? 'Hide camera wall controls' : 'Show camera wall controls');
  }
}

function enterWallFullscreen(requestBrowserFs) {
  document.body.classList.add('wall-fs');
  document.body.classList.remove('wall-fs-chrome');
  localStorage.setItem('wallFullscreen', '1');
  syncWallFullscreenButton();
  if (requestBrowserFs && document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(function () {});
  }
  if (wallViewMode === 'birdseye') requestAnimationFrame(renderBirdseye);
}

function exitWallFullscreen() {
  document.body.classList.remove('wall-fs', 'wall-fs-chrome');
  localStorage.setItem('wallFullscreen', '0');
  syncWallFullscreenButton();
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(function () {});
  }
  if (wallViewMode === 'birdseye') requestAnimationFrame(renderBirdseye);
}

function toggleWallFullscreen() {
  if (document.body.classList.contains('wall-fs')) exitWallFullscreen();
  else enterWallFullscreen(true);
}

function toggleWallChrome(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  if (!document.body.classList.contains('wall-fs')) return;
  document.body.classList.toggle('wall-fs-chrome');
  syncWallFullscreenButton();
}

document.addEventListener('fullscreenchange', function () {
  if (!document.fullscreenElement && document.body.classList.contains('wall-fs')) {
    exitWallFullscreen();
  }
});

document.addEventListener('keydown', function (event) {
  if (event.key !== 'Escape' || !document.body.classList.contains('wall-fs')) return;
  if (document.fullscreenElement) return;
  if (document.body.classList.contains('wall-fs-chrome')) {
    document.body.classList.remove('wall-fs-chrome');
    syncWallFullscreenButton();
    return;
  }
  exitWallFullscreen();
});

function ensureWallFullscreenControls() {
  const container = document.querySelector('.wall-container');
  if (!container) return;
  if (!document.getElementById('wallFsMenu')) {
    const menu = document.createElement('button');
    menu.type = 'button';
    menu.className = 'wall-fs-menu';
    menu.id = 'wallFsMenu';
    menu.hidden = true;
    menu.innerHTML = '<span class="material-icons-outlined">menu</span>';
    menu.addEventListener('click', toggleWallChrome);
    container.insertBefore(menu, container.firstChild);
  }
  if (!document.getElementById('viewCanvasBtn')) {
    const actions = document.querySelector('.wall-actions');
    const bird = document.getElementById('viewBirdseyeBtn');
    if (actions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'wall-action-btn';
      btn.id = 'viewCanvasBtn';
      btn.title = 'Canvas — drag and resize cameras';
      btn.innerHTML = '<span class="material-icons-outlined">dashboard_customize</span>';
      btn.addEventListener('click', function () { setWallView('canvas'); });
      if (bird && bird.parentNode === actions) bird.insertAdjacentElement('afterend', btn);
      else actions.appendChild(btn);
    }
  }
  if (!document.getElementById('wallFsBtn')) {
    const actions = document.querySelector('.wall-actions');
    if (!actions) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'wall-action-btn';
    btn.id = 'wallFsBtn';
    btn.title = 'Fullscreen';
    btn.innerHTML = '<span class="material-icons-outlined">fullscreen</span>';
    btn.addEventListener('click', toggleWallFullscreen);
    const bird = document.getElementById('viewBirdseyeBtn');
    if (bird && bird.parentNode === actions) bird.insertAdjacentElement('afterend', btn);
    else actions.appendChild(btn);
  }
}

document.addEventListener('DOMContentLoaded', function () {
  ensureWallFullscreenControls();
  const grid = document.getElementById('camera-grid');
  if (grid) grid.addEventListener('pointerdown', onCanvasPointerDown);
  setWallView(wallViewMode);
  if (localStorage.getItem('wallFullscreen') === '1') enterWallFullscreen(false);
});
window.addEventListener('resize', function () {
  if (wallViewMode === 'birdseye') renderBirdseye();
});

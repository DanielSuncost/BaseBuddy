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
      if (e.target.closest('.tile-remove-btn') || e.target.closest('.tile-rec-btn') || e.target.closest('.tile-settings-btn')) {
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

function setWallView(mode) {
  wallViewMode = mode;
  localStorage.setItem('wallViewMode', mode);
  const grid = document.getElementById('camera-grid');
  const bird = document.getElementById('birdseye-wrap');
  const gridBtn = document.getElementById('viewGridBtn');
  const birdBtn = document.getElementById('viewBirdseyeBtn');
  if (mode === 'birdseye') {
    if (grid) grid.hidden = true;
    if (bird) bird.hidden = false;
    if (gridBtn) gridBtn.classList.remove('active');
    if (birdBtn) birdBtn.classList.add('active');
    renderBirdseye();
  } else {
    if (grid) grid.hidden = false;
    if (bird) bird.hidden = true;
    if (gridBtn) gridBtn.classList.add('active');
    if (birdBtn) birdBtn.classList.remove('active');
  }
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

document.addEventListener('DOMContentLoaded', function () {
  setWallView(wallViewMode);
});
window.addEventListener('resize', function () {
  if (wallViewMode === 'birdseye') renderBirdseye();
});

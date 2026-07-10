// 3D Multiview page logic: camera selection, mask editor, reconstruction jobs,
// gallery and growth tracking. The Three.js viewer lives in multiview_viewer.js
// (ES module) and is reached via window.MultiviewViewer.

// ========== HELPERS ==========
function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatBytes(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) return 'N/A';
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB'];
    let v = bytes;
    let i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
    return v.toFixed(1) + ' ' + units[i];
}

function formatTimestamp(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
    });
}

function shortDate(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function formatLengthM(meters) {
    if (meters === null || meters === undefined) return 'N/A';
    return meters < 1 ? (meters * 100).toFixed(1) + ' cm' : meters.toFixed(2) + ' m';
}

function formatVolumeM3(m3) {
    if (m3 === null || m3 === undefined) return 'N/A';
    return (m3 * 1000).toFixed(2) + ' L';
}

function formatAreaM2(m2) {
    if (m2 === null || m2 === undefined) return 'N/A';
    return m2 < 0.1 ? (m2 * 10000).toFixed(1) + ' cm²' : m2.toFixed(3) + ' m²';
}

function formatRelative(v) {
    if (v === null || v === undefined || !isFinite(v)) return 'N/A';
    return Number(v).toPrecision(3);
}

// Short summary line for gallery cards, e.g. "Height 0.42 m · Volume 3.1 L".
function metricsSummary(metrics) {
    if (!metrics || metrics.units !== 'metric') return '';
    const parts = [];
    if (metrics.height !== null && metrics.height !== undefined) {
        parts.push('Height ' + formatLengthM(metrics.height));
    }
    if (metrics.hull_volume !== null && metrics.hull_volume !== undefined) {
        parts.push('Volume ' + formatVolumeM3(metrics.hull_volume));
    }
    return parts.join(' · ');
}

function showMessage(containerId, message, isError = false) {
    const container = document.getElementById(containerId);
    container.innerHTML = `<div class="mv-info-box ${isError ? 'danger' : 'info'}">${message}</div>`;
}

// ========== TAB SWITCHING ==========
document.querySelectorAll('.mv-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const tabName = tab.dataset.tab;
        document.querySelectorAll('.mv-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.mv-tab-content').forEach(c => c.classList.remove('active'));
        document.querySelector(`[data-tab-content="${tabName}"]`).classList.add('active');

        if (tabName === 'gallery') loadReconstructions();
        if (tabName === 'growth') loadGrowthSeries();
    });
});

// ========== STATE ==========
let selectedCameras = new Set();
let reconstructionsCache = [];
let pollTimer = null;

// Init on page load
loadAvailableCameras();
loadEngines();

// ========== CAMERAS ==========
async function loadAvailableCameras() {
    try {
        const response = await fetch('/api/multiview/cameras');
        const data = await response.json();

        if (data.ok && data.cameras.length > 0) {
            let html = '';
            for (const cam of data.cameras) {
                const isSelected = selectedCameras.has(cam.id);
                const hasLive = cam.has_live_feed;
                const hasMask = cam.has_mask;
                const hasRegions = cam.has_regions;
                const imageCount = cam.num_images || 0;

                // Use mask preview if mask exists, otherwise thumbnail
                const imgSrc = hasMask
                    ? `/api/multiview/mask-preview/${cam.id}?t=${Date.now()}`
                    : (cam.thumbnail_url || '');

                html += `
                    <div class="mv-camera-card ${isSelected ? 'selected' : ''}"
                         id="camCard_${cam.id}"
                         onclick="toggleCameraSelection(${cam.id})">
                        <div class="mv-camera-header">
                            <input type="checkbox" id="camCheck_${cam.id}"
                                   ${isSelected ? 'checked' : ''}
                                   onclick="event.stopPropagation(); toggleCameraSelection(${cam.id})">
                            <h3>${esc(cam.name) || 'Camera ' + cam.id}</h3>
                            ${hasLive ? '<span class="mv-badge mv-badge-live">LIVE</span>' : ''}
                            ${hasMask ? '<span class="mv-badge mv-badge-mask">MASK</span>' : ''}
                            ${hasRegions ? '<span class="mv-badge mv-badge-regions">REGIONS</span>' : ''}
                        </div>
                        <div class="mv-camera-preview">
                            <img id="camThumb_${cam.id}" src="${esc(imgSrc)}"
                                 onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22320%22 height=%22240%22><rect fill=%22%23e0e0e0%22 width=%22100%%22 height=%22100%%22/><text x=%2250%%22 y=%2250%%22 dominant-baseline=%22middle%22 text-anchor=%22middle%22 fill=%22%23888%22 font-size=%2214%22>No preview</text></svg>'">
                            <div class="mv-preview-actions">
                                ${hasLive ? `<button type="button" title="Refresh preview" onclick="event.stopPropagation(); refreshCameraPreview(${cam.id})"><span class="material-icons-outlined" style="font-size:14px">refresh</span></button>` : ''}
                                <button type="button" class="${hasMask ? 'mv-btn-mask-active' : ''}" onclick="event.stopPropagation(); openMaskEditor(${cam.id})">
                                    <span class="material-icons-outlined" style="font-size:14px">${hasMask ? 'edit' : 'add'}</span>
                                    ${hasMask ? 'Edit' : 'Mask'}
                                </button>
                                ${hasMask ? `<button type="button" class="mv-btn-delete" title="Delete mask" onclick="event.stopPropagation(); deleteMask(${cam.id})"><span class="material-icons-outlined" style="font-size:14px">delete</span></button>` : ''}
                                <button type="button" class="${hasRegions ? 'mv-btn-mask-active' : ''}" title="Exclude/include regions for 3D registration (e.g. timestamps)" onclick="event.stopPropagation(); openRegionEditor(${cam.id})">
                                    <span class="material-icons-outlined" style="font-size:14px">crop</span>
                                    Regions
                                </button>
                            </div>
                        </div>
                        <div class="mv-camera-info">
                            <strong>${imageCount}</strong> timelapse images
                            ${hasMask ? '<br><span style="color:var(--color-info);">Mask active</span>' : '<br><span class="text-muted">No mask</span>'}
                        </div>
                    </div>
                `;
            }
            document.getElementById('cameraSelectionGrid').innerHTML = html;
            updateSelectedCount();
        } else {
            document.getElementById('cameraSelectionGrid').innerHTML = `
                <div class="mv-info-box warning" style="grid-column: 1/-1;">
                    <h3><span class="material-icons-outlined">folder_off</span> No Timelapse Images Found</h3>
                    <p>Go to <a href="/timelapse">Timelapse</a> and enable capture for your cameras, then come back here.</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading cameras:', error);
        document.getElementById('cameraSelectionGrid').innerHTML = `
            <div class="mv-info-box danger" style="grid-column: 1/-1;">
                <h3><span class="material-icons-outlined">error</span> Error Loading Cameras</h3>
                <p>${esc(error.message)}</p>
            </div>
        `;
    }
}

function refreshCameraPreview(camId) {
    const img = document.getElementById(`camThumb_${camId}`);
    img.src = `/api/multiview/live-feed/${camId}?t=${Date.now()}`;
}

function toggleCameraSelection(camId) {
    if (selectedCameras.has(camId)) {
        selectedCameras.delete(camId);
    } else {
        selectedCameras.add(camId);
    }

    const card = document.getElementById(`camCard_${camId}`);
    const checkbox = document.getElementById(`camCheck_${camId}`);

    if (selectedCameras.has(camId)) {
        card.classList.add('selected');
        checkbox.checked = true;
    } else {
        card.classList.remove('selected');
        checkbox.checked = false;
    }

    updateSelectedCount();
}

function updateSelectedCount() {
    const count = selectedCameras.size;
    let msg = `<strong>${count}</strong> camera${count !== 1 ? 's' : ''} selected`;
    if (count < 2) {
        msg += ' <span style="color: var(--danger);">(need at least 2)</span>';
    } else {
        msg += ' <span style="color: var(--success);">Ready</span>';
    }
    document.getElementById('selectedCamerasCount').innerHTML = msg;
}

function getSelectedCameraIds() {
    return Array.from(selectedCameras);
}

// ========== MASK EDITOR ==========
let maskEditor = {
    camId: null,
    sessionId: null,
    frameImg: null,
    points: []  // {x, y, fg}
};

function openMaskEditor(camId) {
    maskEditor = { camId: camId, sessionId: null, frameImg: null, points: [] };

    const modal = document.createElement('div');
    modal.id = 'maskEditorModal';
    modal.innerHTML = `
        <div class="mv-modal-overlay">
            <div class="mv-modal-content">
                <div class="mv-modal-header">
                    <h2><span class="material-icons-outlined">masks</span> Edit Mask — Camera ${Number(camId)}</h2>
                    <button type="button" onclick="closeMaskEditor()" class="btn btn-secondary"><span class="material-icons-outlined">close</span> Close</button>
                </div>

                <div class="mv-info-box info" style="margin-bottom: 16px;">
                    <strong>Instructions:</strong>
                    <span style="color: var(--color-success);"><span class="material-icons-outlined icon-sm">radio_button_checked</span> Left-click</span> on plant = foreground |
                    <span style="color: var(--color-danger);"><span class="material-icons-outlined icon-sm">radio_button_checked</span> Right-click</span> on background = exclude
                </div>

                <div style="position: relative; border: 2px solid var(--border); border-radius: 8px; overflow: hidden;">
                    <canvas id="maskCanvas" style="width: 100%; display: block; cursor: crosshair;"></canvas>
                    <div id="maskLoading" class="mv-mask-loading">Loading...</div>
                </div>

                <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                    <button type="button" class="btn btn-secondary" onclick="resetMaskPoints()"><span class="material-icons-outlined">refresh</span> Reset Points</button>
                    <button type="button" class="btn btn-success" onclick="saveMask()"><span class="material-icons-outlined">save</span> Save Mask</button>
                    <button type="button" class="btn btn-secondary" onclick="closeMaskEditor()">Cancel</button>
                    <span id="maskStatus" style="margin-left: auto; color: var(--text-secondary);"></span>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    initMaskSession(camId);
}

async function initMaskSession(camId) {
    const loading = document.getElementById('maskLoading');
    loading.style.display = 'block';

    try {
        const response = await fetch('/api/multiview/segment/init', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camera_id: camId })
        });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'Failed to start segmentation session');

        maskEditor.sessionId = data.session_id;

        const canvas = document.getElementById('maskCanvas');
        const img = new Image();
        img.onload = function() {
            canvas.width = data.width || img.naturalWidth;
            canvas.height = data.height || img.naturalHeight;
            maskEditor.frameImg = img;
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
            loading.style.display = 'none';
            document.getElementById('maskStatus').textContent = `Image: ${canvas.width}x${canvas.height}`;
        };
        img.onerror = function() {
            loading.textContent = 'Error: failed to load camera frame';
        };
        img.src = data.frame_url + (data.frame_url.includes('?') ? '&' : '?') + 't=' + Date.now();

        canvas.onclick = function(e) {
            const pos = canvasCoords(canvas, e);
            handleMaskClick(pos.x, pos.y, true);
        };
        canvas.oncontextmenu = function(e) {
            e.preventDefault();
            const pos = canvasCoords(canvas, e);
            handleMaskClick(pos.x, pos.y, false);
        };
    } catch (error) {
        console.error('Error initializing mask editor:', error);
        loading.textContent = 'Error: ' + error.message;
    }
}

function canvasCoords(canvas, e) {
    const rect = canvas.getBoundingClientRect();
    return {
        x: Math.round((e.clientX - rect.left) * (canvas.width / rect.width)),
        y: Math.round((e.clientY - rect.top) * (canvas.height / rect.height))
    };
}

function pointCount(v) {
    return Array.isArray(v) ? v.length : (v || 0);
}

async function handleMaskClick(x, y, isForeground) {
    if (!maskEditor.sessionId) return;
    document.getElementById('maskStatus').textContent = 'Updating mask...';

    try {
        const response = await fetch('/api/multiview/segment/click', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: maskEditor.sessionId,
                x: x, y: y,
                is_foreground: isForeground
            })
        });
        const data = await response.json();

        if (data.ok) {
            maskEditor.points.push({ x: x, y: y, fg: isForeground });
            const coverage = data.coverage !== undefined ? ` — Coverage: ${(data.coverage * 100).toFixed(1)}%` : '';
            document.getElementById('maskStatus').textContent =
                `FG: ${pointCount(data.fg_points)}, BG: ${pointCount(data.bg_points)}${coverage}`;
            await redrawMaskCanvas(data.mask_url);
        } else {
            document.getElementById('maskStatus').textContent = 'Error: ' + (data.error || 'click failed');
        }
    } catch (error) {
        document.getElementById('maskStatus').textContent = 'Error: ' + error.message;
    }
}

// Draw frame, then mask (white regions) as translucent green, then click points.
async function redrawMaskCanvas(maskUrl) {
    const canvas = document.getElementById('maskCanvas');
    if (!canvas || !maskEditor.frameImg) return;
    const ctx = canvas.getContext('2d');

    ctx.drawImage(maskEditor.frameImg, 0, 0, canvas.width, canvas.height);

    if (maskUrl) {
        try {
            const maskImg = await loadImage(maskUrl + (maskUrl.includes('?') ? '&' : '?') + 't=' + Date.now());
            const off = document.createElement('canvas');
            off.width = canvas.width;
            off.height = canvas.height;
            const octx = off.getContext('2d');
            octx.drawImage(maskImg, 0, 0, off.width, off.height);
            const imgData = octx.getImageData(0, 0, off.width, off.height);
            const px = imgData.data;
            for (let i = 0; i < px.length; i += 4) {
                if (px[i] > 127) {
                    px[i] = 34; px[i + 1] = 197; px[i + 2] = 94; px[i + 3] = 110;
                } else {
                    px[i + 3] = 0;
                }
            }
            octx.putImageData(imgData, 0, 0);
            ctx.drawImage(off, 0, 0);
        } catch (error) {
            console.error('Error loading mask overlay:', error);
        }
    }

    for (const pt of maskEditor.points) {
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 8, 0, 2 * Math.PI);
        ctx.fillStyle = pt.fg ? 'rgba(40, 167, 69, 0.8)' : 'rgba(220, 53, 69, 0.8)';
        ctx.fill();
        ctx.strokeStyle = 'white';
        ctx.lineWidth = 2;
        ctx.stroke();
    }
}

function loadImage(src) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('Failed to load image'));
        img.src = src;
    });
}

async function resetMaskPoints() {
    if (!maskEditor.sessionId) return;
    try {
        await fetch('/api/multiview/segment/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: maskEditor.sessionId })
        });
        maskEditor.points = [];
        await redrawMaskCanvas(null);
        document.getElementById('maskStatus').textContent = 'Points reset';
    } catch (error) {
        console.error('Error resetting:', error);
    }
}

async function saveMask() {
    if (!maskEditor.sessionId) return;
    try {
        const response = await fetch('/api/multiview/segment/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: maskEditor.sessionId })
        });
        const data = await response.json();

        if (data.ok) {
            showToast('Mask saved!', 'success');
            closeMaskEditor();
            loadAvailableCameras();
        } else {
            showToast(data.error || 'Failed to save mask', 'error');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function closeMaskEditor() {
    const modal = document.getElementById('maskEditorModal');
    if (modal) modal.remove();
    maskEditor = { camId: null, sessionId: null, frameImg: null, points: [] };
}

async function deleteMask(camId) {
    if (!confirm(`Delete mask for Camera ${camId}?`)) return;

    try {
        const response = await fetch(`/api/multiview/delete-mask/${camId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.ok) loadAvailableCameras();
        else showToast(data.error || 'Failed to delete mask', 'error');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ========== REGISTRATION REGION EDITOR ==========
// Draw exclude boxes (red - never used for registration, e.g. burned-in
// timestamps) and include boxes (green - if any exist, ONLY those areas are
// used). Boxes are stored normalized (0-1) so they survive resolution changes.
let regionEditor = {
    camId: null,
    frameImg: null,
    mode: 'exclude',       // 'exclude' | 'include'
    exclude: [],           // [[x1,y1,x2,y2] normalized]
    include: [],
    useSegMask: false,
    drag: null             // {x, y} canvas px, while dragging
};

function openRegionEditor(camId) {
    regionEditor = { camId, frameImg: null, mode: 'exclude',
                     exclude: [], include: [], useSegMask: false, drag: null };

    const modal = document.createElement('div');
    modal.id = 'regionEditorModal';
    modal.innerHTML = `
        <div class="mv-modal-overlay">
            <div class="mv-modal-content">
                <div class="mv-modal-header">
                    <h2><span class="material-icons-outlined">crop</span> Registration Regions — Camera ${Number(camId)}</h2>
                    <button type="button" onclick="closeRegionEditor()" class="btn btn-secondary"><span class="material-icons-outlined">close</span> Close</button>
                </div>

                <div class="mv-info-box info" style="margin-bottom: 12px;">
                    Drag boxes over areas to control which pixels can produce 3D registration points.
                    <span style="color: var(--color-danger);">Exclude</span> boxes (e.g. timestamps, logos, banners) are never used.
                    If any <span style="color: var(--color-success);">Include</span> boxes exist, <em>only</em> those areas are used.
                </div>

                <div style="margin-bottom: 12px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                    <button type="button" id="regionModeExclude" class="btn btn-danger" onclick="setRegionMode('exclude')">
                        <span class="material-icons-outlined">block</span> Exclude
                    </button>
                    <button type="button" id="regionModeInclude" class="btn btn-secondary" onclick="setRegionMode('include')">
                        <span class="material-icons-outlined">check_box</span> Include only
                    </button>
                    <label style="display: flex; align-items: center; gap: 6px; margin-left: 8px; cursor: pointer;">
                        <input type="checkbox" id="regionUseSegMask" onchange="regionEditor.useSegMask = this.checked">
                        Also restrict to saved segmentation mask
                    </label>
                </div>

                <div style="position: relative; border: 2px solid var(--border); border-radius: 8px; overflow: hidden;">
                    <canvas id="regionCanvas" style="width: 100%; display: block; cursor: crosshair;"></canvas>
                    <div id="regionLoading" class="mv-mask-loading">Loading...</div>
                </div>

                <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                    <button type="button" class="btn btn-secondary" onclick="undoRegionBox()"><span class="material-icons-outlined">undo</span> Undo Box</button>
                    <button type="button" class="btn btn-secondary" onclick="clearRegionBoxes()"><span class="material-icons-outlined">delete_sweep</span> Clear All</button>
                    <button type="button" class="btn btn-success" onclick="saveRegions()"><span class="material-icons-outlined">save</span> Save Regions</button>
                    <button type="button" class="btn btn-secondary" onclick="closeRegionEditor()">Cancel</button>
                    <span id="regionStatus" style="margin-left: auto; color: var(--text-secondary);"></span>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    initRegionEditor(camId);
}

function setRegionMode(mode) {
    regionEditor.mode = mode;
    const ex = document.getElementById('regionModeExclude');
    const inc = document.getElementById('regionModeInclude');
    ex.className = mode === 'exclude' ? 'btn btn-danger' : 'btn btn-secondary';
    inc.className = mode === 'include' ? 'btn btn-success' : 'btn btn-secondary';
}

async function initRegionEditor(camId) {
    const loading = document.getElementById('regionLoading');
    loading.style.display = 'block';
    const canvas = document.getElementById('regionCanvas');

    try {
        // Load existing regions
        const regResponse = await fetch(`/api/multiview/regions/${camId}`);
        const regData = await regResponse.json();
        if (regData.ok && regData.regions) {
            regionEditor.exclude = regData.regions.exclude || [];
            regionEditor.include = regData.regions.include || [];
            regionEditor.useSegMask = !!regData.regions.use_seg_mask;
            document.getElementById('regionUseSegMask').checked = regionEditor.useSegMask;
        }

        // Load a live frame for the backdrop
        const img = await loadImage(`/api/multiview/live-feed/${camId}?t=${Date.now()}`);
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        regionEditor.frameImg = img;
        loading.style.display = 'none';
        redrawRegionCanvas();
        updateRegionStatus();

        canvas.onmousedown = (e) => {
            if (e.button !== 0) return;
            regionEditor.drag = canvasCoords(canvas, e);
        };
        canvas.onmousemove = (e) => {
            if (!regionEditor.drag) return;
            redrawRegionCanvas(canvasCoords(canvas, e));
        };
        canvas.onmouseup = (e) => {
            if (!regionEditor.drag) return;
            const start = regionEditor.drag;
            const end = canvasCoords(canvas, e);
            regionEditor.drag = null;
            const box = [
                Math.min(start.x, end.x) / canvas.width,
                Math.min(start.y, end.y) / canvas.height,
                Math.max(start.x, end.x) / canvas.width,
                Math.max(start.y, end.y) / canvas.height
            ];
            if ((box[2] - box[0]) >= 0.005 && (box[3] - box[1]) >= 0.005) {
                regionEditor[regionEditor.mode].push(box);
            }
            redrawRegionCanvas();
            updateRegionStatus();
        };
        canvas.onmouseleave = () => {
            regionEditor.drag = null;
            redrawRegionCanvas();
        };
    } catch (error) {
        console.error('Error initializing region editor:', error);
        loading.textContent = 'Error: ' + error.message;
    }
}

function drawRegionBox(ctx, box, w, h, color) {
    const x = box[0] * w, y = box[1] * h;
    const bw = (box[2] - box[0]) * w, bh = (box[3] - box[1]) * h;
    ctx.fillStyle = color + '33';
    ctx.fillRect(x, y, bw, bh);
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(2, w / 400);
    ctx.strokeRect(x, y, bw, bh);
}

function redrawRegionCanvas(dragPos) {
    const canvas = document.getElementById('regionCanvas');
    if (!canvas || !regionEditor.frameImg) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;

    ctx.drawImage(regionEditor.frameImg, 0, 0, w, h);
    for (const box of regionEditor.exclude) drawRegionBox(ctx, box, w, h, '#dc3545');
    for (const box of regionEditor.include) drawRegionBox(ctx, box, w, h, '#28a745');

    if (regionEditor.drag && dragPos) {
        const color = regionEditor.mode === 'exclude' ? '#dc3545' : '#28a745';
        const box = [
            Math.min(regionEditor.drag.x, dragPos.x) / w,
            Math.min(regionEditor.drag.y, dragPos.y) / h,
            Math.max(regionEditor.drag.x, dragPos.x) / w,
            Math.max(regionEditor.drag.y, dragPos.y) / h
        ];
        drawRegionBox(ctx, box, w, h, color);
    }
}

function updateRegionStatus() {
    const el = document.getElementById('regionStatus');
    if (el) {
        el.textContent = `${regionEditor.exclude.length} exclude, ${regionEditor.include.length} include`;
    }
}

function undoRegionBox() {
    // Remove the most recently added box in the current mode; fall back to the other list.
    if (regionEditor[regionEditor.mode].length > 0) {
        regionEditor[regionEditor.mode].pop();
    } else {
        const other = regionEditor.mode === 'exclude' ? 'include' : 'exclude';
        regionEditor[other].pop();
    }
    redrawRegionCanvas();
    updateRegionStatus();
}

function clearRegionBoxes() {
    regionEditor.exclude = [];
    regionEditor.include = [];
    redrawRegionCanvas();
    updateRegionStatus();
}

async function saveRegions() {
    try {
        const response = await fetch(`/api/multiview/regions/${regionEditor.camId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                exclude: regionEditor.exclude,
                include: regionEditor.include,
                use_seg_mask: regionEditor.useSegMask
            })
        });
        const data = await response.json();
        if (data.ok) {
            showToast('Registration regions saved', 'success');
            closeRegionEditor();
            loadAvailableCameras();
        } else {
            showToast(data.error || 'Failed to save regions', 'error');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function closeRegionEditor() {
    const modal = document.getElementById('regionEditorModal');
    if (modal) modal.remove();
    regionEditor = { camId: null, frameImg: null, mode: 'exclude',
                     exclude: [], include: [], useSegMask: false, drag: null };
}

// ========== TEST MATCHING ==========
async function testMatchQuality() {
    const cameraIds = getSelectedCameraIds();

    if (cameraIds.length < 2) {
        showMessage('matchTestResult', 'Please select at least 2 cameras first.', true);
        return;
    }

    showMessage('matchTestResult', 'Testing feature matching quality…', false);

    try {
        const useMasks = document.getElementById('useMasksCheckbox');
        const response = await fetch('/api/multiview/test-matching', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                camera_ids: cameraIds,
                use_masks: useMasks ? useMasks.checked : true
            })
        });

        const data = await response.json();

        if (data.ok) {
            const qualityColor = {
                'excellent': 'var(--success)',
                'good': 'var(--success)',
                'acceptable': 'var(--warning)',
                'poor': 'var(--danger)'
            }[data.quality] || 'var(--text-secondary)';

            let html = `
                <h4 style="margin-bottom: 12px;">Match Quality:
                    <span style="color: ${qualityColor}; text-transform: uppercase;">${esc(data.quality)}</span>
                </h4>
                <div class="mv-stat-grid">
                    <div class="mv-stat-cell">
                        <div class="mv-stat-value">${Number(data.num_matches)}</div>
                        <div class="mv-stat-label">Matches</div>
                    </div>
                    <div class="mv-stat-cell">
                        <div class="mv-stat-value" style="color: var(--success);">${Number(data.num_inliers)}</div>
                        <div class="mv-stat-label">Inliers</div>
                    </div>
                    <div class="mv-stat-cell">
                        <div class="mv-stat-value" style="color: var(--color-text);">${(data.inlier_ratio * 100).toFixed(1)}%</div>
                        <div class="mv-stat-label">Ratio</div>
                    </div>
                </div>
            `;

            if (data.masks_used && data.masks_used.length > 0) {
                html += `<div class="mv-info-box info" style="margin-bottom: 12px;">
                    <strong>Masks applied:</strong> Cameras ${data.masks_used.map(Number).join(', ')}
                </div>`;
            }

            if (data.visualization_url) {
                html += `<img src="${esc(data.visualization_url)}?t=${Date.now()}" style="width: 100%; border-radius: 8px; margin-top: 12px;">`;
            }

            document.getElementById('matchTestResult').innerHTML = html;
        } else {
            showMessage('matchTestResult', esc(data.error), true);
        }
    } catch (error) {
        showMessage('matchTestResult', esc(error.message), true);
    }
}

// ========== ENGINES ==========
async function loadEngines() {
    const select = document.getElementById('engineSelect');
    if (!select) return;

    try {
        const response = await fetch('/api/multiview/engines');
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'Failed to load engines');

        select.innerHTML = '';
        for (const engine of data.engines) {
            const opt = document.createElement('option');
            opt.value = engine.id;
            let label = engine.label;
            if (engine.recommended) label += ' (recommended)';
            if (!engine.available) {
                label += ' (not installed)';
                opt.disabled = true;
            }
            opt.textContent = label;
            if (engine.description) opt.title = engine.description;
            select.appendChild(opt);
        }
        select.value = 'auto';

        const cudaNote = document.getElementById('cudaNote');
        if (cudaNote) {
            cudaNote.textContent = data.cuda_available
                ? 'CUDA GPU detected — deep-learning engines will run fast.'
                : 'No CUDA GPU detected — deep-learning engines will run on CPU (slower).';
        }
    } catch (error) {
        console.error('Error loading engines:', error);
        select.innerHTML = '<option value="auto">Auto (best available)</option>';
    }
}

// ========== RECONSTRUCTION ==========
async function startReconstruction() {
    const cameraIds = getSelectedCameraIds();
    if (cameraIds.length < 2) {
        showMessage('reconstructionMessage', 'Please select at least 2 cameras first.', true);
        return;
    }

    const engineSelect = document.getElementById('engineSelect');
    const useMasks = document.getElementById('useMasksCheckbox');
    const btn = document.getElementById('reconstructBtn');
    if (btn) btn.disabled = true;

    renderJobProgress(0, 'Starting reconstruction…');

    try {
        const response = await fetch('/api/multiview/reconstruct/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                camera_ids: cameraIds,
                engine: engineSelect ? engineSelect.value : 'auto',
                use_masks: useMasks ? useMasks.checked : true,
                source: 'live'
            })
        });
        const data = await response.json();

        if (!data.ok) {
            throw new Error(data.error || 'Failed to start reconstruction');
        }
        pollJob(data.job_id);
    } catch (error) {
        if (btn) btn.disabled = false;
        showMessage('reconstructionMessage', esc(error.message), true);
    }
}

function renderJobProgress(progress, message) {
    const pct = Math.max(0, Math.min(100, Math.round(progress || 0)));
    document.getElementById('reconstructionMessage').innerHTML = `
        <div class="mv-progress">
            <div class="mv-progress-bar">
                <div class="mv-progress-fill" style="width: ${pct}%"></div>
            </div>
            <div class="mv-progress-text">${esc(message || 'Working…')} (${pct}%)</div>
        </div>
    `;
}

function stopPolling() {
    if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
    }
}

function pollJob(jobId) {
    stopPolling();
    let consecutiveFailures = 0;
    const btn = document.getElementById('reconstructBtn');

    const finish = () => {
        stopPolling();
        if (btn) btn.disabled = false;
    };

    const tick = async () => {
        try {
            const response = await fetch(`/api/multiview/jobs/${encodeURIComponent(jobId)}`);
            if (!response.ok) throw new Error(`Job status request failed (${response.status})`);
            const data = await response.json();
            if (!data.ok) throw new Error(data.error || 'Job status request failed');

            consecutiveFailures = 0;
            const job = data.job;

            if (job.status === 'done') {
                finish();
                const recon = job.result && job.result.reconstruction;
                renderReconstructionDone(recon);
                if (recon) {
                    reconstructionsCache = [];
                    loadReconstructions();
                }
                return;
            }
            if (job.status === 'error') {
                finish();
                showMessage('reconstructionMessage',
                    '<strong>Reconstruction failed:</strong> ' + esc(job.error || job.message || 'Unknown error'), true);
                return;
            }

            renderJobProgress(job.progress, job.message || (job.status === 'queued' ? 'Queued…' : 'Running…'));
            pollTimer = setTimeout(tick, 1500);
        } catch (error) {
            consecutiveFailures++;
            if (consecutiveFailures >= 3) {
                finish();
                showMessage('reconstructionMessage',
                    '<strong>Lost contact with the reconstruction job:</strong> ' + esc(error.message), true);
                return;
            }
            pollTimer = setTimeout(tick, 1500);
        }
    };

    tick();
}

function renderReconstructionDone(recon) {
    if (!recon) {
        showMessage('reconstructionMessage', 'Reconstruction finished but returned no result.', true);
        return;
    }
    reconstructionsCache = reconstructionsCache.filter(r => r.id !== recon.id).concat([recon]);

    document.getElementById('reconstructionMessage').innerHTML = `
        <div class="mv-info-box success">
            <h3><span class="material-icons-outlined">check_circle</span> 3D Reconstruction Complete</h3>
            <p>Engine: <strong>${esc(recon.engine)}</strong> · Points: <strong>${Number(recon.num_points).toLocaleString()}</strong></p>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px;">
                <button type="button" class="btn btn-primary" onclick='viewReconstructionById(${JSON.stringify(recon.id)})'>
                    <span class="material-icons-outlined">3d_rotation</span> View in 3D
                </button>
                <a href="${esc(recon.download_url)}" download class="btn btn-secondary">
                    <span class="material-icons-outlined">download</span> Download .PLY
                </a>
            </div>
        </div>
    `;
}

function openViewer(meta) {
    if (window.MultiviewViewer && typeof window.MultiviewViewer.open === 'function') {
        window.MultiviewViewer.open(meta);
    } else {
        showToast('3D viewer is still loading — try again in a moment', 'error');
    }
}

async function viewReconstructionById(id) {
    let meta = reconstructionsCache.find(r => String(r.id) === String(id));
    if (!meta) {
        try {
            const response = await fetch('/api/multiview/reconstructions');
            const data = await response.json();
            if (data.ok) {
                reconstructionsCache = data.reconstructions;
                meta = reconstructionsCache.find(r => String(r.id) === String(id));
            }
        } catch (error) {
            console.error('Error fetching reconstructions:', error);
        }
    }
    if (meta) {
        openViewer(meta);
    } else {
        showToast('Reconstruction not found', 'error');
    }
}

// ========== GALLERY ==========
async function loadReconstructions() {
    try {
        const response = await fetch('/api/multiview/reconstructions');
        const data = await response.json();

        if (data.ok && data.reconstructions.length > 0) {
            reconstructionsCache = data.reconstructions;
            let html = '';
            for (const recon of data.reconstructions) {
                const summary = metricsSummary(recon.metrics);
                html += `
                    <div class="mv-reconstruction-item">
                        <div class="mv-recon-details">
                            <h3 style="margin: 0 0 4px 0;">${esc(formatTimestamp(recon.timestamp))}
                                <span class="mv-badge mv-badge-engine">${esc(recon.engine)}</span>
                            </h3>
                            <p style="margin: 0; color: var(--text-secondary); font-size: 0.9rem;">
                                Cameras: ${(recon.camera_ids || []).map(Number).join(', ') || 'N/A'} ·
                                Points: ${recon.num_points != null ? Number(recon.num_points).toLocaleString() : 'N/A'} ·
                                ${esc(formatBytes(recon.size_bytes))}
                            </p>
                            ${summary ? `<p style="margin: 4px 0 0 0; color: var(--color-success); font-size: 0.9rem;">${esc(summary)}</p>` : ''}
                        </div>
                        <div class="mv-recon-actions">
                            <button type="button" class="btn btn-primary" onclick='viewReconstructionById(${JSON.stringify(recon.id)})'>
                                <span class="material-icons-outlined">3d_rotation</span> View in 3D
                            </button>
                            <a href="${esc(recon.download_url)}" download class="btn btn-secondary">
                                <span class="material-icons-outlined">download</span> Download
                            </a>
                            <button type="button" class="btn btn-secondary mv-btn-danger" onclick='deleteReconstruction(${JSON.stringify(recon.id)})'>
                                <span class="material-icons-outlined">delete</span> Delete
                            </button>
                        </div>
                    </div>
                `;
            }
            document.getElementById('reconstructionsList').innerHTML = html;
        } else {
            reconstructionsCache = [];
            document.getElementById('reconstructionsList').innerHTML = `
                <div style="text-align: center; padding: 40px; color: var(--text-secondary);">
                    <span class="material-icons-outlined" style="font-size: 48px; opacity: 0.5;">view_in_ar</span>
                    <p style="margin-top: 16px;">No reconstructions yet.</p>
                    <p>Create your first 3D model using the Cameras tab!</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading reconstructions:', error);
        document.getElementById('reconstructionsList').innerHTML = '<p style="color: var(--danger);">Error loading reconstructions</p>';
    }
}

async function deleteReconstruction(id) {
    if (!confirm('Delete this reconstruction? This cannot be undone.')) return;

    try {
        const response = await fetch(`/api/multiview/reconstruction/${encodeURIComponent(id)}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.ok) {
            showToast('Reconstruction deleted', 'success');
            loadReconstructions();
        } else {
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ========== GROWTH ==========
async function loadGrowthSeries() {
    const container = document.getElementById('growthContent');
    container.innerHTML = '<div class="mv-loading-placeholder">Loading growth data...</div>';

    try {
        const response = await fetch('/api/multiview/growth-series');
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'Failed to load growth series');

        const series = data.series || [];
        if (series.length < 2) {
            container.innerHTML = `
                <div class="mv-info-box info">
                    <h3><span class="material-icons-outlined">trending_up</span> Not enough data yet</h3>
                    <p>Create reconstructions over time with a scale set to track growth.
                       You need at least 2 reconstructions with computed metrics.
                       Workflow: mask your cameras, reconstruct, open the 3D viewer, set a real-world
                       scale, and repeat over the following days.</p>
                </div>
            `;
            return;
        }

        const allMetric = series.every(e => e.metrics && e.metrics.units === 'metric');
        const unitNote = allMetric ? '' : `
            <div class="mv-info-box warning">
                Some reconstructions have no real-world scale — values shown in relative (unitless) units.
                Set a scale in the 3D viewer for metric measurements.
            </div>
        `;

        const heightChart = renderGrowthChart(
            'Height over time',
            allMetric ? 'm' : 'relative units',
            series,
            e => e.metrics ? e.metrics.height : null
        );
        const volumeChart = renderGrowthChart(
            'Hull volume over time',
            allMetric ? 'm³' : 'relative units',
            series,
            e => e.metrics ? e.metrics.hull_volume : null
        );

        let tableRows = '';
        for (const entry of series) {
            const m = entry.metrics || {};
            const metric = m.units === 'metric';
            tableRows += `
                <tr>
                    <td>${esc(formatTimestamp(entry.timestamp))}</td>
                    <td>${metric ? esc(formatLengthM(m.height)) : esc(formatRelative(m.height))}</td>
                    <td>${metric ? esc(formatAreaM2(m.canopy_area)) : esc(formatRelative(m.canopy_area))}</td>
                    <td>${metric ? esc(formatVolumeM3(m.hull_volume)) : esc(formatRelative(m.hull_volume))}</td>
                    <td><button type="button" class="btn btn-secondary btn-sm" onclick='viewReconstructionById(${JSON.stringify(entry.id)})'>View</button></td>
                </tr>
            `;
        }

        container.innerHTML = `
            ${unitNote}
            <div class="mv-growth-charts">
                ${heightChart}
                ${volumeChart}
            </div>
            <table class="mv-growth-table">
                <thead>
                    <tr><th>Date</th><th>Height</th><th>Canopy area</th><th>Hull volume</th><th></th></tr>
                </thead>
                <tbody>${tableRows}</tbody>
            </table>
        `;
    } catch (error) {
        console.error('Error loading growth series:', error);
        container.innerHTML = `<div class="mv-info-box danger">${esc(error.message)}</div>`;
    }
}

// Pure-SVG line chart. entries: growth-series entries; valueFn extracts the y value.
function renderGrowthChart(title, unitLabel, entries, valueFn) {
    const pts = entries
        .map(e => ({ t: new Date(e.timestamp).getTime(), v: valueFn(e), label: shortDate(e.timestamp) }))
        .filter(p => p.v !== null && p.v !== undefined && isFinite(p.v) && isFinite(p.t));

    if (pts.length < 2) {
        return `<div class="mv-chart-card"><h3>${esc(title)}</h3><p class="text-muted">Not enough data.</p></div>`;
    }

    const W = 520, H = 240;
    const padL = 62, padR = 16, padT = 16, padB = 34;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    let vMin = Math.min(...pts.map(p => p.v));
    let vMax = Math.max(...pts.map(p => p.v));
    if (vMin === vMax) { vMin -= 0.5; vMax += 0.5; }
    const vPad = (vMax - vMin) * 0.08;
    vMin -= vPad;
    vMax += vPad;

    const tMin = Math.min(...pts.map(p => p.t));
    const tMax = Math.max(...pts.map(p => p.t));
    const tSpan = (tMax - tMin) || 1;

    const xOf = t => padL + ((t - tMin) / tSpan) * plotW;
    const yOf = v => padT + (1 - (v - vMin) / (vMax - vMin)) * plotH;

    const fmtVal = v => Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString();

    // 4 gridlines with value labels
    let grid = '';
    for (let i = 0; i < 4; i++) {
        const v = vMin + ((vMax - vMin) * i) / 3;
        const y = yOf(v);
        grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="mv-chart-grid"/>`;
        grid += `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" class="mv-chart-ylabel" text-anchor="end">${esc(fmtVal(v))}</text>`;
    }

    // X date labels — at most 6, evenly picked
    let xLabels = '';
    const maxLabels = Math.min(6, pts.length);
    const step = (pts.length - 1) / (maxLabels - 1);
    const used = new Set();
    for (let i = 0; i < maxLabels; i++) {
        const idx = Math.round(i * step);
        if (used.has(idx)) continue;
        used.add(idx);
        xLabels += `<text x="${xOf(pts[idx].t).toFixed(1)}" y="${H - 12}" class="mv-chart-xlabel" text-anchor="middle">${esc(pts[idx].label)}</text>`;
    }

    const polyPoints = pts.map(p => `${xOf(p.t).toFixed(1)},${yOf(p.v).toFixed(1)}`).join(' ');
    const dots = pts.map(p =>
        `<circle cx="${xOf(p.t).toFixed(1)}" cy="${yOf(p.v).toFixed(1)}" r="3.5" class="mv-chart-dot"><title>${esc(p.label)}: ${esc(fmtVal(p.v))} ${esc(unitLabel)}</title></circle>`
    ).join('');

    return `
        <div class="mv-chart-card">
            <h3>${esc(title)} <span class="mv-chart-unit">(${esc(unitLabel)})</span></h3>
            <svg viewBox="0 0 ${W} ${H}" class="mv-chart-svg" role="img" aria-label="${esc(title)}">
                ${grid}
                <polyline points="${polyPoints}" class="mv-chart-line" fill="none"/>
                ${dots}
                ${xLabels}
            </svg>
        </div>
    `;
}

(function () {
    const listEl = document.getElementById('scenes-list');
    const toastEl = document.getElementById('scenes-toast');
    const modal = document.getElementById('scenes-modal');
    const form = document.getElementById('scenes-form');
    const roiModal = document.getElementById('roi-modal');
    const roiCanvas = document.getElementById('roi-canvas');
    const roiCoords = document.getElementById('roi-coords');
    const roiLabelInput = document.getElementById('roi-label');
    const roiModalTitle = document.getElementById('roi-modal-title');
    const cameraPicker = document.getElementById('scene-camera-picker');
    const cameraInput = document.getElementById('scene-camera-input');
    const cameraLive = document.getElementById('scene-camera-live');
    const cameraLiveImg = document.getElementById('scene-camera-live-img');
    const cameraLiveLabel = document.getElementById('scene-camera-live-label');

    let roiEdit = null;
    let roiDrag = null;
    let roiRect = null;
    let roiImg = null;
    let camerasCache = [];
    let previewTimer = null;
    let selectedCameraId = null;

    function toast(msg, isError) {
        toastEl.textContent = msg;
        toastEl.hidden = false;
        toastEl.style.background = isError ? '#d93025' : '#202124';
        setTimeout(function () { toastEl.hidden = true; }, 3500);
    }

    function showEl(el) {
        if (el) el.classList.remove('hidden');
    }

    function hideEl(el) {
        if (el) el.classList.add('hidden');
    }

    function cameraName(id) {
        const cam = camerasCache.find(function (c) { return c.id === id; });
        return cam ? (cam.name || ('Camera ' + (id + 1))) : ('Camera ' + (id + 1));
    }

    function previewUrl(cameraId, width) {
        const w = width ? ('&w=' + width) : '';
        return '/api/scenes/camera/' + cameraId + '/preview?t=' + Date.now() + w;
    }

    function stateClass(state) {
        if (state === 'present') return 'state-present';
        if (state === 'empty') return 'state-empty';
        return 'state-unknown';
    }

    function nextSlotId(scene) {
        const slots = scene.slots || [];
        let n = 1;
        while (slots.some(function (s) { return s.id === 'slot-' + n; })) n++;
        return 'slot-' + n;
    }

    function renderScenes(scenes) {
        if (!scenes.length) {
            listEl.innerHTML =
                '<div class="scenes-empty">' +
                '<span class="material-icons-outlined scenes-empty-icon">kitchen</span>' +
                '<p>No scenes yet</p>' +
                '<p class="scenes-muted">Add a scene to monitor a pantry, fridge, or shelf camera.</p>' +
                '<button type="button" class="btn btn-primary" id="scenes-add-empty">' +
                '<span class="material-icons-outlined">add</span> Add scene</button></div>';
            document.getElementById('scenes-add-empty')?.addEventListener('click', openAddModal);
            return;
        }

        listEl.innerHTML = scenes.map(function (scene) {
            const slots = (scene.slots || []).map(function (slot) {
                const r = slot.roi || {};
                const roiTxt = '[' + (r.x1 || 0).toFixed(2) + ', ' + (r.y1 || 0).toFixed(2) +
                    ' – ' + (r.x2 || 0).toFixed(2) + ', ' + (r.y2 || 0).toFixed(2) + ']';
                return (
                    '<div class="scenes-slot ' + stateClass(slot.state) + '">' +
                    '<strong>' + (slot.label || slot.id) + '</strong><br>' +
                    (slot.state || 'unknown') +
                    (slot.confidence != null ? ' (' + Math.round(slot.confidence * 100) + '%)' : '') +
                    '<div class="scenes-slot-roi">' + roiTxt + '</div>' +
                    '<div class="scenes-slot-actions">' +
                    '<button type="button" class="btn btn-secondary btn-sm" data-roi="' + scene.id + ':' + slot.id + '">Edit region</button>' +
                    '<button type="button" class="btn btn-secondary btn-sm" data-baseline="' + scene.id + ':' + slot.id + '">Set baseline</button>' +
                    '</div></div>'
                );
            }).join('');

            const slotsHtml = slots ||
                '<div class="scenes-no-slots">' +
                '<p class="scenes-muted">No regions yet — draw labeled regions on the camera view.</p>' +
                '<button type="button" class="btn btn-primary btn-sm" data-add-region="' + scene.id + '">' +
                '<span class="material-icons-outlined">crop_free</span> Add region</button></div>';

            return (
                '<div class="scenes-card" data-scene-id="' + scene.id + '">' +
                '<div class="scenes-card-header">' +
                '<div><strong>' + scene.name + '</strong>' +
                '<span class="scenes-muted"> · ' + scene.scene_type + ' · ' + cameraName(scene.camera_id) + '</span></div>' +
                '<div class="scenes-card-actions">' +
                '<button type="button" class="btn btn-secondary btn-sm" data-add-region="' + scene.id + '">' +
                '<span class="material-icons-outlined">add</span> Add region</button>' +
                '<button type="button" class="btn btn-secondary btn-sm" data-check="' + scene.id + '">Check now</button>' +
                '<button type="button" class="btn btn-secondary btn-sm" data-delete="' + scene.id + '">Delete</button>' +
                '</div></div>' +
                '<div class="scenes-slots">' + slotsHtml + '</div></div>'
            );
        }).join('');

        listEl.querySelectorAll('[data-check]').forEach(function (btn) {
            btn.addEventListener('click', function () { checkScene(btn.dataset.check); });
        });
        listEl.querySelectorAll('[data-delete]').forEach(function (btn) {
            btn.addEventListener('click', function () { deleteScene(btn.dataset.delete); });
        });
        listEl.querySelectorAll('[data-baseline]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const parts = btn.dataset.baseline.split(':');
                setBaseline(parts[0], parts[1]);
            });
        });
        listEl.querySelectorAll('[data-roi]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const parts = btn.dataset.roi.split(':');
                openRegionDesigner(parts[0], parts[1]);
            });
        });
        listEl.querySelectorAll('[data-add-region]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                openRegionDesigner(btn.dataset.addRegion, null);
            });
        });
    }

    function stopPreviewTimer() {
        if (previewTimer) {
            clearInterval(previewTimer);
            previewTimer = null;
        }
    }

    function startPreviewTimer(cameraId) {
        stopPreviewTimer();
        if (cameraId == null) return;
        previewTimer = setInterval(function () {
            if (selectedCameraId === cameraId && cameraLiveImg) {
                cameraLiveImg.src = previewUrl(cameraId);
            }
        }, 4000);
    }

    function selectCamera(cameraId) {
        selectedCameraId = cameraId;
        if (cameraInput) cameraInput.value = String(cameraId);
        document.getElementById('scenes-submit').disabled = false;

        cameraPicker.querySelectorAll('.scenes-camera-option').forEach(function (btn) {
            btn.classList.toggle('selected', parseInt(btn.dataset.cameraId, 10) === cameraId);
        });

        showEl(cameraLive);
        if (cameraLiveLabel) cameraLiveLabel.textContent = cameraName(cameraId);
        if (cameraLiveImg) {
            cameraLiveImg.classList.remove('preview-failed');
            cameraLiveImg.src = previewUrl(cameraId);
            cameraLiveImg.onerror = function () {
                cameraLiveImg.classList.add('preview-failed');
            };
        }
        startPreviewTimer(cameraId);
    }

    function renderCameraPicker(cameras) {
        camerasCache = cameras;
        if (!cameras.length) {
            cameraPicker.innerHTML =
                '<p class="scenes-muted">No cameras configured. Add cameras on the Camera Wall first.</p>';
            hideEl(cameraLive);
            if (cameraInput) cameraInput.value = '';
            document.getElementById('scenes-submit').disabled = true;
            return;
        }

        cameraPicker.innerHTML = cameras.map(function (c) {
            return (
                '<button type="button" class="scenes-camera-option" data-camera-id="' + c.id + '">' +
                '<span class="scenes-camera-thumb">' +
                '<img src="' + previewUrl(c.id, 240) + '" alt="" loading="lazy">' +
                '<span class="scenes-camera-thumb-fallback">No live frame</span></span>' +
                '<span class="scenes-camera-name">' + (c.name || ('Camera ' + (c.id + 1))) + '</span>' +
                '</button>'
            );
        }).join('');

        cameraPicker.querySelectorAll('.scenes-camera-option').forEach(function (btn) {
            btn.addEventListener('click', function () {
                selectCamera(parseInt(btn.dataset.cameraId, 10));
            });
            const img = btn.querySelector('img');
            if (img) {
                img.onerror = function () {
                    btn.classList.add('preview-unavailable');
                };
            }
        });

        selectCamera(cameras[0].id);
    }

    async function loadCameras() {
        try {
            const res = await fetch('/api/cameras');
            const json = await res.json();
            const cameras = (json.cameras || []).filter(function (c) {
                return c.url && String(c.url).trim();
            });
            renderCameraPicker(cameras);
            return cameras;
        } catch (e) {
            cameraPicker.innerHTML = '<p class="scenes-muted">Could not load cameras.</p>';
            document.getElementById('scenes-submit').disabled = true;
            return [];
        }
    }

    function openAddModal() {
        form.reset();
        loadCameras().then(function () {
            modal.hidden = false;
        });
    }

    function closeModal(el) {
        if (el) el.hidden = true;
        if (el === modal) {
            stopPreviewTimer();
            selectedCameraId = null;
        }
    }

    async function loadScenes() {
        const res = await fetch('/api/scenes');
        const json = await res.json();
        window._scenesCache = (json.data && json.data.scenes) || [];
        if (!camerasCache.length) {
            try {
                const camRes = await fetch('/api/cameras');
                const camJson = await camRes.json();
                camerasCache = (camJson.cameras || []).filter(function (c) {
                    return c.url && String(c.url).trim();
                });
            } catch (e) { /* ignore */ }
        }
        renderScenes(window._scenesCache);
    }

    async function checkScene(id) {
        const res = await fetch('/api/scenes/' + encodeURIComponent(id) + '/check', { method: 'POST' });
        const json = await res.json();
        toast(json.ok ? 'Scene checked' : (json.error || 'Check failed'), !json.ok);
        loadScenes();
    }

    async function deleteScene(id) {
        if (!confirm('Delete this scene?')) return;
        await fetch('/api/scenes/' + encodeURIComponent(id), { method: 'DELETE' });
        loadScenes();
    }

    async function setBaseline(sceneId, slotId) {
        const res = await fetch(
            '/api/scenes/' + encodeURIComponent(sceneId) + '/slots/' + encodeURIComponent(slotId) + '/baseline',
            { method: 'POST' }
        );
        const json = await res.json();
        toast(json.ok ? 'Baseline saved' : (json.error || 'Failed'), !json.ok);
        loadScenes();
    }

    function canvasPoint(evt) {
        const rect = roiCanvas.getBoundingClientRect();
        const x = (evt.clientX - rect.left) / rect.width;
        const y = (evt.clientY - rect.top) / rect.height;
        return { x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)) };
    }

    function drawRoiCanvas() {
        if (!roiCanvas || !roiImg) return;
        const ctx = roiCanvas.getContext('2d');
        const w = roiCanvas.width;
        const h = roiCanvas.height;
        ctx.clearRect(0, 0, w, h);
        ctx.drawImage(roiImg, 0, 0, w, h);
        if (roiRect) {
            ctx.strokeStyle = '#1a73e8';
            ctx.lineWidth = 2;
            ctx.fillStyle = 'rgba(26, 115, 232, 0.2)';
            const x = roiRect.x1 * w;
            const y = roiRect.y1 * h;
            const rw = (roiRect.x2 - roiRect.x1) * w;
            const rh = (roiRect.y2 - roiRect.y1) * h;
            ctx.fillRect(x, y, rw, rh);
            ctx.strokeRect(x, y, rw, rh);
            roiCoords.textContent =
                'x1=' + roiRect.x1.toFixed(3) + ' y1=' + roiRect.y1.toFixed(3) +
                ' x2=' + roiRect.x2.toFixed(3) + ' y2=' + roiRect.y2.toFixed(3);
        }
    }

    function loadRoiPreview(cameraId) {
        roiImg = new Image();
        roiImg.crossOrigin = 'anonymous';
        roiImg.onload = function () {
            roiCanvas.width = Math.min(640, roiImg.naturalWidth);
            roiCanvas.height = Math.round(roiCanvas.width * (roiImg.naturalHeight / roiImg.naturalWidth));
            drawRoiCanvas();
        };
        roiImg.onerror = function () { toast('Could not load camera preview — is the camera running?', true); };
        roiImg.src = previewUrl(cameraId);
    }

    function openRegionDesigner(sceneId, slotId) {
        const scene = (window._scenesCache || []).find(function (s) { return s.id === sceneId; });
        if (!scene) return;

        const isNew = !slotId;
        const slot = isNew ? null : (scene.slots || []).find(function (s) { return s.id === slotId; });
        if (!isNew && !slot) return;

        roiEdit = { sceneId: sceneId, slotId: slotId, scene: scene, isNew: isNew };
        roiRect = isNew
            ? { x1: 0.1, y1: 0.1, x2: 0.4, y2: 0.4 }
            : Object.assign({ x1: 0.1, y1: 0.1, x2: 0.4, y2: 0.4 }, slot.roi || {});

        if (roiModalTitle) {
            roiModalTitle.textContent = isNew ? 'Add region' : 'Edit region';
        }
        if (roiLabelInput) {
            roiLabelInput.value = isNew ? '' : (slot.label || '');
        }
        if (roiCoords) roiCoords.textContent = '';

        roiModal.hidden = false;
        loadRoiPreview(scene.camera_id);
    }

    if (roiCanvas) {
        roiCanvas.addEventListener('mousedown', function (evt) {
            roiDrag = canvasPoint(evt);
            roiRect = { x1: roiDrag.x, y1: roiDrag.y, x2: roiDrag.x, y2: roiDrag.y };
        });
        roiCanvas.addEventListener('mousemove', function (evt) {
            if (!roiDrag) return;
            const p = canvasPoint(evt);
            roiRect.x2 = p.x;
            roiRect.y2 = p.y;
            if (roiRect.x2 < roiRect.x1) { var t = roiRect.x1; roiRect.x1 = roiRect.x2; roiRect.x2 = t; }
            if (roiRect.y2 < roiRect.y1) { var t2 = roiRect.y1; roiRect.y1 = roiRect.y2; roiRect.y2 = t2; }
            drawRoiCanvas();
        });
        roiCanvas.addEventListener('mouseup', function () { roiDrag = null; });
        roiCanvas.addEventListener('mouseleave', function () { roiDrag = null; });
    }

    document.getElementById('roi-cancel').addEventListener('click', function () {
        roiModal.hidden = true;
        roiEdit = null;
    });

    document.getElementById('roi-save').addEventListener('click', async function () {
        if (!roiEdit || !roiRect) return;
        const label = (roiLabelInput && roiLabelInput.value.trim()) || '';
        if (!label) {
            toast('Enter a label for this region', true);
            roiLabelInput?.focus();
            return;
        }

        const scene = roiEdit.scene;
        let slots;
        if (roiEdit.isNew) {
            const newId = nextSlotId(scene);
            slots = (scene.slots || []).concat([{
                id: newId,
                label: label,
                roi: Object.assign({}, roiRect),
                rules: { empty_confirm_checks: 2, notify: true },
            }]);
        } else {
            slots = (scene.slots || []).map(function (s) {
                if (s.id !== roiEdit.slotId) return s;
                return Object.assign({}, s, {
                    label: label,
                    roi: Object.assign({}, roiRect),
                });
            });
        }

        const res = await fetch('/api/scenes/' + encodeURIComponent(roiEdit.sceneId), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slots: slots }),
        });
        const json = await res.json();
        toast(json.ok ? 'Region saved' : (json.error || 'Save failed'), !json.ok);
        roiModal.hidden = true;
        roiEdit = null;
        loadScenes();
    });

    document.getElementById('scenes-add').addEventListener('click', openAddModal);
    document.getElementById('scenes-cancel').addEventListener('click', function () { closeModal(modal); });
    modal.addEventListener('click', function (evt) {
        if (evt.target === modal) closeModal(modal);
    });
    roiModal.addEventListener('click', function (evt) {
        if (evt.target === roiModal) {
            roiModal.hidden = true;
            roiEdit = null;
        }
    });

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        const fd = new FormData(form);
        const cameraId = fd.get('camera_id');
        if (cameraId === '' || cameraId === null) {
            toast('Select a camera first (Camera Wall → add & start cameras)', true);
            return;
        }
        const payload = {
            name: fd.get('name'),
            scene_type: fd.get('scene_type'),
            camera_id: parseInt(cameraId, 10),
            check_interval_s: parseInt(fd.get('check_interval_s'), 10),
            slots: [],
        };
        const res = await fetch('/api/scenes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const json = await res.json();
        if (json.ok) {
            closeModal(modal);
            form.reset();
            await loadScenes();
            const sceneId = json.data && json.data.id;
            toast('Scene created — add labeled regions on the camera view');
            if (sceneId) {
                openRegionDesigner(sceneId, null);
            }
        } else {
            toast(json.error || 'Create failed', true);
        }
    });

    loadScenes();
})();

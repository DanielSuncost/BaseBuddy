/**
 * Gallery page: modal, track/group grid, selection, delete, GIF.
 */

var currentDetectionId = null;
var currentDetectionClass = null;
var selectedIds = new Set();
var knownPeopleCache = null;
var currentGalleryItem = null;

function formatClassLabel(cls) {
    if (!cls) return 'Unknown';
    return String(cls)
        .replace(/_/g, ' ')
        .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

function showEl(el) {
    if (el) el.classList.remove('hidden');
}

function hideEl(el) {
    if (el) el.classList.add('hidden');
}

function groupFetchUrl(groupKey, itemEl) {
    const base = '/api/gallery/group/' + encodeURIComponent(groupKey);
    const urlParams = new URLSearchParams(window.location.search);
    const qs = new URLSearchParams();
    const view = urlParams.get('view')
        || (itemEl && itemEl.getAttribute('data-gallery-view'))
        || 'recent';

    if (view === 'date') {
        const date = urlParams.get('date')
            || (itemEl && itemEl.getAttribute('data-gallery-date'))
            || '';
        if (date) qs.set('date', date);
    } else {
        const hours = urlParams.get('hours')
            || (itemEl && itemEl.getAttribute('data-gallery-hours'))
            || '1';
        qs.set('hours', hours);
    }

    const cam = urlParams.get('cam')
        || (itemEl && itemEl.getAttribute('data-gallery-cam'))
        || '';
    if (cam) qs.set('cam', cam);

    const q = qs.toString();
    return q ? base + '?' + q : base;
}

function parseGroupResponse(data) {
    if (data.ok && Array.isArray(data.detections)) {
        return data.detections;
    }
    if (data.ok && Array.isArray(data.data)) {
        return data.data;
    }
    return [];
}

function openModalFromItem(item, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    currentGalleryItem = item;
    const imgPath = item.getAttribute('data-img-path');
    const cls = item.getAttribute('data-class');
    const cam = item.getAttribute('data-camera');
    const time = item.getAttribute('data-timestamp');
    const rel = item.getAttribute('data-relative');
    const conf = item.getAttribute('data-confidence');
    const id = parseInt(item.getAttribute('data-id'), 10);
    const groupKey = item.getAttribute('data-group-key') || '';
    const groupCount = parseInt(item.getAttribute('data-group-count') || '1', 10);
    const groupType = item.getAttribute('data-group-type') || 'similar';
    openModal(event, imgPath, cls, cam, time, rel, conf, id, groupKey, groupCount, groupType);
}

function openModal(event, src, cls, cam, time, rel, conf, id, groupKey, groupCount, groupType) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }

    const modal = document.getElementById('imageModal');
    const modalImg = document.getElementById('modalImg');
    const singleView = document.getElementById('singleImageView');
    const groupView = document.getElementById('groupView');

    if (!modal || !modalImg) {
        console.error('Modal elements not found');
        return;
    }

    modalImg.src = src || '';
    modalImg.alt = formatClassLabel(cls) + ' detection';
    document.getElementById('modalTitle').textContent = formatClassLabel(cls);
    const classBadge = document.getElementById('modalClass');
    if (classBadge) {
        classBadge.textContent = cls ? formatClassLabel(cls) : '';
        classBadge.style.display = cls ? '' : 'none';
    }
    document.getElementById('modalCam').textContent = cam || '';
    document.getElementById('modalTime').textContent = time || '';
    document.getElementById('modalRelative').textContent = rel || '';
    document.getElementById('modalConf').textContent = (conf || '0') + '%';
    currentDetectionId = id;
    currentDetectionClass = cls || null;
    updatePersonLabelRow(currentDetectionClass);
    loadKnownPeople();
    loadEventLabels(id);

    modal.style.display = 'flex';
    modal.classList.add('show');
    document.body.style.overflow = 'hidden';

    const gk = (groupKey && String(groupKey).trim()) || '';
    const gc = parseInt(String(groupCount || '1'), 10) || 1;
    const hasGroup =
        gk &&
        gk !== 'null' &&
        gk !== 'undefined' &&
        gc > 1;

    const groupHeader = document.getElementById('groupViewHeader');
    if (groupHeader) groupHeader.textContent = '';

    if (hasGroup) {
        hideEl(singleView);
        showEl(groupView);
        if (groupHeader) {
            const kind = groupType === 'track' ? 'Track sequence' : 'Similar group';
            groupHeader.textContent = kind + ' · ' + gc + ' frames';
        }
        loadGroupImages(gk, id, gc, groupType);
    } else {
        hideEl(groupView);
        showEl(singleView);
    }
}

function renderGroupGrid(detections) {
    const grid = document.getElementById('groupImagesGrid');
    if (!grid) return;
    grid.innerHTML = '';
    detections.forEach(function (det, idx) {
        const thumb = det.thumbnail_path || det.full_image_path || '';
        const full = det.full_image_path || det.thumbnail_path || '';
        const wrap = document.createElement('div');
        wrap.className = 'group-image-item' + (det.id === currentDetectionId ? ' group-image-focused' : '');
        wrap.dataset.id = String(det.id);

        wrap.addEventListener('click', function (e) {
            if (e.target.closest('.group-tile-btn')) return;
            focusDetectionDet(det);
        });

        const img = document.createElement('img');
        img.src = thumb || full;
        img.alt = 'Detection ' + (idx + 1);
        img.loading = 'lazy';

        const actions = document.createElement('div');
        actions.className = 'group-tile-actions';

        const zoomBtn = document.createElement('button');
        zoomBtn.type = 'button';
        zoomBtn.className = 'group-tile-btn';
        zoomBtn.title = 'Enlarge';
        zoomBtn.innerHTML = '<span class="material-icons-outlined">zoom_in</span>';
        zoomBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            enlargeGroupImage(full || thumb, idx, det.id);
        });

        const fpBtn = document.createElement('button');
        fpBtn.type = 'button';
        fpBtn.className = 'group-tile-btn group-tile-fp';
        fpBtn.title = 'False positive';
        fpBtn.innerHTML = '<span class="material-icons-outlined">thumb_down</span>';
        fpBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            markFalsePositiveById(det.id, { skipConfirm: false });
        });

        actions.appendChild(zoomBtn);
        actions.appendChild(fpBtn);
        wrap.appendChild(img);
        wrap.appendChild(actions);
        grid.appendChild(wrap);
    });
}

function focusDetectionDet(det) {
    if (!det) return;
    currentDetectionId = det.id;
    currentDetectionClass = det.class_name || null;
    updatePersonLabelRow(currentDetectionClass);
    loadEventLabels(det.id);

    document.getElementById('modalTitle').textContent = formatClassLabel(det.class_name);
    const classBadge = document.getElementById('modalClass');
    if (classBadge) {
        classBadge.textContent = det.class_name ? formatClassLabel(det.class_name) : '';
    }
    if (det.confidence != null) {
        document.getElementById('modalConf').textContent = Math.round(det.confidence * 100) + '%';
    }
    if (det.timestamp) {
        document.getElementById('modalTime').textContent = det.timestamp;
    }

    document.querySelectorAll('.group-image-item').forEach(function (el) {
        el.classList.toggle('group-image-focused', parseInt(el.dataset.id, 10) === det.id);
    });
}

async function loadKnownPeople() {
    if (knownPeopleCache) {
        populatePersonSelect(knownPeopleCache);
        return;
    }
    try {
        const res = await fetch('/api/people');
        knownPeopleCache = await res.json();
        populatePersonSelect(knownPeopleCache);
    } catch (e) {
        knownPeopleCache = [];
    }
}

function populatePersonSelect(people) {
    const sel = document.getElementById('labelPersonSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Select known person —</option>';
    (people || []).forEach(function (p) {
        const opt = document.createElement('option');
        opt.value = String(p.id);
        opt.textContent = p.name || ('Person ' + p.id);
        sel.appendChild(opt);
    });
}

function updatePersonLabelRow(cls) {
    const row = document.getElementById('personLabelRow');
    if (!row) return;
    if (cls === 'person') {
        row.classList.add('person-active');
        row.classList.remove('person-inactive');
    } else {
        row.classList.add('person-inactive');
        row.classList.remove('person-active');
    }
}

async function loadEventLabels(eventId) {
    const badge = document.getElementById('labelSavedBadge');
    const identity = document.getElementById('labelIdentity');
    const notes = document.getElementById('labelNotes');
    const corrected = document.getElementById('labelCorrectClass');
    const personName = document.getElementById('labelPersonName');
    const personSelect = document.getElementById('labelPersonSelect');

    if (!eventId) return;
    try {
        const res = await fetch('/api/gallery/events/' + eventId);
        const data = await res.json();
        if (!data.ok || !data.event) return;
        const ev = data.event;
        if (identity) identity.value = ev.identity_label || data.person_name || '';
        if (notes) notes.value = ev.user_label || '';
        if (corrected) corrected.value = ev.corrected_class || '';
        if (personSelect && ev.labeled_person_id) {
            personSelect.value = String(ev.labeled_person_id);
        }
        if (personName) personName.value = '';
        if (badge) {
            if (ev.identity_label || ev.labeled_person_id || ev.training_label === 'verified') {
                badge.textContent = 'Saved: ' + (ev.identity_label || data.person_name || ev.corrected_class || 'verified');
                showEl(badge);
            } else {
                hideEl(badge);
            }
        }
    } catch (e) {
        if (badge) hideEl(badge);
    }
}

async function loadGroupImages(groupKey, detectionId, expectedCount, groupType) {
    const loadingEl = document.getElementById('groupLoading');
    const grid = document.getElementById('groupImagesGrid');
    const singleView = document.getElementById('singleImageView');
    const groupView = document.getElementById('groupView');
    const groupHeader = document.getElementById('groupViewHeader');

    showEl(loadingEl);
    if (grid) grid.innerHTML = '';
    try {
        const url = groupFetchUrl(groupKey, currentGalleryItem);
        const response = await fetch(url);
        const data = await response.json();
        const detections = parseGroupResponse(data);

        if (detections.length > 0) {
            hideEl(singleView);
            showEl(groupView);
            window.groupDetections = detections;
            renderGroupGrid(detections);
            if (groupHeader) {
                const kind = groupType === 'track' ? 'Track sequence' : 'Similar group';
                const label = detections.length === 1 ? 'frame' : 'frames';
                groupHeader.textContent = kind + ' · ' + detections.length + ' ' + label;
                if (expectedCount > 1 && detections.length < expectedCount) {
                    groupHeader.textContent += ' (badge showed ' + expectedCount + ')';
                }
            }

            const link = document.getElementById('recordingLink');
            if (data.recording_path && link) {
                link.href = data.recording_path;
                showEl(link);
            } else if (link) {
                hideEl(link);
            }
        } else {
            hideEl(groupView);
            showEl(singleView);
            if (groupHeader) groupHeader.textContent = '';
            console.warn('Group API returned no detections for key:', groupKey);
        }
    } catch (error) {
        console.error('Error loading group images:', error);
        hideEl(groupView);
        showEl(singleView);
    } finally {
        hideEl(loadingEl);
    }
}

function closeGifViewer() {
    const gifControls = document.getElementById('gifControls');
    const gifPlayer = document.getElementById('gifPlayer');
    hideEl(gifControls);
    if (gifPlayer) {
        hideEl(gifPlayer);
        gifPlayer.src = '';
    }
}

async function generateGIF() {
    if (!window.groupDetections || window.groupDetections.length === 0) {
        alert('No images to generate GIF');
        return;
    }

    const gifBtnDefaultHtml =
        '<span class="material-icons-outlined">animation</span> Generate GIF';
    const gifBtn = document.getElementById('generateGifBtn');
    if (gifBtn) {
        gifBtn.disabled = true;
        gifBtn.textContent = 'Generating GIF...';
    }

    try {
        const imagePaths = window.groupDetections
            .map(function (d) {
                return d.full_image_path || d.thumbnail_path;
            })
            .filter(function (path) {
                return path && String(path).trim() !== '';
            });

        if (imagePaths.length === 0) {
            alert('No valid image paths found');
        } else {
            const response = await fetch('/api/gallery/generate_gif', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_paths: imagePaths }),
            });

            const data = await response.json();
            const gifUrl = data.gif_path || (data.data && data.data.gif_url) || null;

            if ((data.ok) && gifUrl) {
                const player = document.getElementById('gifPlayer');
                const controls = document.getElementById('gifControls');
                if (player) {
                    player.src = gifUrl;
                    showEl(player);
                }
                if (controls) showEl(controls);
            } else {
                alert('Error generating GIF: ' + (data.error || 'Unknown error'));
            }
        }
    } catch (error) {
        alert('Error generating GIF: ' + error.message);
    } finally {
        if (gifBtn) {
            gifBtn.disabled = false;
            gifBtn.innerHTML = gifBtnDefaultHtml;
        }
    }
}

function enlargeGroupImage(imagePath, index, detectionId) {
    if (!window.groupDetections || !window.groupDetections.length) return;
    if (detectionId != null) {
        const det = window.groupDetections.find(function (d) { return d.id === detectionId; });
        if (det) focusDetectionDet(det);
    }

    const viewer = document.createElement('div');
    viewer.className = 'image-viewer';

    const content = document.createElement('div');
    content.className = 'image-viewer-content';

    const closeBtn = document.createElement('span');
    closeBtn.className = 'image-viewer-close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = function () {
        viewer.remove();
    };

    const img = document.createElement('img');
    img.className = 'image-viewer-img';
    img.src = imagePath;

    const actions = document.createElement('div');
    actions.className = 'image-viewer-actions';

    const fpBtn = document.createElement('button');
    fpBtn.type = 'button';
    fpBtn.className = 'btn btn-fp btn-sm';
    fpBtn.textContent = 'False positive';
    fpBtn.onclick = function () {
        const det = window.groupDetections[index];
        if (det) {
            markFalsePositiveById(det.id, { skipConfirm: false }).then(function (ok) {
                if (ok) viewer.remove();
            });
        }
    };

    const labelBtn = document.createElement('button');
    labelBtn.type = 'button';
    labelBtn.className = 'btn btn-primary btn-sm';
    labelBtn.textContent = 'Label';
    labelBtn.onclick = function () {
        const det = window.groupDetections[index];
        if (det) {
            focusDetectionDet(det);
            viewer.remove();
        }
    };

    actions.appendChild(fpBtn);
    actions.appendChild(labelBtn);

    const nav = document.createElement('div');
    nav.className = 'image-viewer-nav';

    const prev = document.createElement('button');
    prev.type = 'button';
    prev.textContent = '\u2039 Prev';
    prev.disabled = index === 0;
    prev.onclick = function () {
        navigateGroupImage(index - 1);
    };

    const label = document.createElement('span');
    label.textContent = index + 1 + ' / ' + window.groupDetections.length;

    const next = document.createElement('button');
    next.type = 'button';
    next.textContent = 'Next \u203a';
    next.disabled = index >= window.groupDetections.length - 1;
    next.onclick = function () {
        navigateGroupImage(index + 1);
    };

    nav.appendChild(prev);
    nav.appendChild(label);
    nav.appendChild(next);

    content.appendChild(closeBtn);
    content.appendChild(img);
    content.appendChild(actions);
    content.appendChild(nav);
    viewer.appendChild(content);
    viewer.onclick = function (e) {
        if (e.target === viewer) viewer.remove();
    };
    document.body.appendChild(viewer);
    window.currentGroupImageIndex = index;
}

function navigateGroupImage(newIndex) {
    if (!window.groupDetections || newIndex < 0 || newIndex >= window.groupDetections.length) {
        return;
    }
    const det = window.groupDetections[newIndex];
    const path = det.full_image_path || det.thumbnail_path;
    const img = document.querySelector('.image-viewer-img');
    const navSpan = document.querySelector('.image-viewer-nav span');
    if (img) img.src = path;
    if (navSpan) {
        navSpan.textContent = newIndex + 1 + ' / ' + window.groupDetections.length;
    }

    const nav = document.querySelector('.image-viewer-nav');
    if (nav && nav.children.length >= 3) {
        const prevBtn = nav.children[0];
        const nextBtn = nav.children[2];
        prevBtn.disabled = newIndex === 0;
        nextBtn.disabled = newIndex >= window.groupDetections.length - 1;
        prevBtn.onclick = function () {
            navigateGroupImage(newIndex - 1);
        };
        nextBtn.onclick = function () {
            navigateGroupImage(newIndex + 1);
        };
    }
    window.currentGroupImageIndex = newIndex;
}

function closeModal() {
    const modal = document.getElementById('imageModal');
    if (modal) {
        modal.classList.remove('show');
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }
    hideEl(document.getElementById('groupView'));
    const grid = document.getElementById('groupImagesGrid');
    if (grid) grid.innerHTML = '';
    window.groupDetections = [];
    closeGifViewer();
}

window.onclick = function (event) {
    const modal = document.getElementById('imageModal');
    if (modal && event.target === modal) {
        closeModal();
    }
    const tModal = document.getElementById('timelineModal');
    if (tModal && event.target === tModal) {
        tModal.style.display = 'none';
        document.body.style.overflow = '';
    }
};

function toggleSelection(id, event) {
    event.stopPropagation();
    const item = document.querySelector('.gallery-item[data-id="' + id + '"]');
    if (!item) return;
    if (selectedIds.has(id)) {
        selectedIds.delete(id);
        item.classList.remove('selected');
    } else {
        selectedIds.add(id);
        item.classList.add('selected');
    }
    updateSelectionUI();
}

function toggleSelectAll() {
    const allItems = document.querySelectorAll('.gallery-item');
    if (selectedIds.size === allItems.length && allItems.length > 0) {
        selectedIds.clear();
        allItems.forEach(function (el) {
            el.classList.remove('selected');
        });
    } else {
        allItems.forEach(function (el) {
            const id = parseInt(el.dataset.id, 10);
            selectedIds.add(id);
            el.classList.add('selected');
        });
    }
    updateSelectionUI();
}

function updateSelectionUI() {
    const countEl = document.getElementById('selectionCount');
    const delBtn = document.getElementById('deleteBtn');
    if (countEl) countEl.textContent = selectedIds.size + ' selected';
    if (delBtn) delBtn.classList.toggle('hidden', selectedIds.size === 0);
}

async function deleteSelected() {
    if (!confirm('Delete ' + selectedIds.size + ' items? This cannot be undone.')) return;

    const ids = Array.from(selectedIds);
    try {
        const res = await fetch('/api/gallery/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: ids }),
        });
        if (res.ok) {
            location.reload();
        } else {
            alert('Failed to delete items');
        }
    } catch (e) {
        alert('Error deleting items: ' + e.message);
    }
}

async function markFalsePositiveById(id, opts) {
    opts = opts || {};
    if (!id) return false;
    if (
        !opts.skipConfirm &&
        !confirm(
            'Mark this detection as a false positive?\n\n' +
                'It will be removed from the gallery and included in training export as a negative example.'
        )
    ) {
        return false;
    }
    try {
        const res = await fetch('/api/gallery/mark_false_positive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event_id: id }),
        });
        const data = await res.json();
        if (data.ok) {
            removeGalleryItem(id);
            if (window.groupDetections && window.groupDetections.length) {
                window.groupDetections = window.groupDetections.filter(function (d) {
                    return d.id !== id;
                });
                renderGroupGrid(window.groupDetections);
                const groupHeader = document.getElementById('groupViewHeader');
                if (groupHeader && window.groupDetections.length) {
                    groupHeader.textContent =
                        groupHeader.textContent.replace(/\d+ frames?/, window.groupDetections.length + ' frames');
                }
                if (currentDetectionId === id) {
                    currentDetectionId = window.groupDetections[0]?.id || null;
                    if (window.groupDetections[0]) focusDetectionDet(window.groupDetections[0]);
                }
            }
            if (opts.closeModal) closeModal();
            showGalleryStatus('Marked as false positive — saved for training export.');
            return true;
        }
        alert(data.error || 'Failed to mark false positive');
    } catch (e) {
        alert('Error: ' + e.message);
    }
    return false;
}

async function markCurrentFalsePositive() {
    if (!currentDetectionId) {
        alert('No detection selected.');
        return;
    }
    await markFalsePositiveById(currentDetectionId, { closeModal: true });
}

async function saveDetectionLabel() {
    if (!currentDetectionId) {
        alert('No detection selected.');
        return;
    }
    const personSelect = document.getElementById('labelPersonSelect');
    const personName = document.getElementById('labelPersonName');
    const notes = document.getElementById('labelNotes');
    const corrected = document.getElementById('labelCorrectClass');
    const addFace = document.getElementById('labelAddToFaceLibrary');

    const payload = {
        event_id: currentDetectionId,
        training_label: 'verified',
        identity_label: document.getElementById('labelIdentity')?.value?.trim() || '',
        notes: notes?.value?.trim() || '',
        corrected_class: corrected?.value || '',
        add_to_face_library: currentDetectionClass === 'person' && addFace?.checked,
    };
    if (personSelect?.value) {
        payload.person_id = parseInt(personSelect.value, 10);
    } else if (personName?.value?.trim()) {
        payload.person_name = personName.value.trim();
    }

    try {
        const res = await fetch('/api/gallery/label', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.ok) {
            let msg = 'Teaching label saved.';
            if (data.face_added) msg += ' Face added to Identities library.';
            showGalleryStatus(msg);
            loadEventLabels(currentDetectionId);
            if (personName) personName.value = '';
        } else {
            alert(data.error || 'Failed to save label');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function ignoreCurrentRegion() {
    if (!currentDetectionId) {
        alert('No detection selected.');
        return;
    }
    if (
        !confirm(
            'Ignore this class in this region of the frame?\n\n' +
                'Future detections that overlap this area (same class, same camera) will not be saved to the gallery.'
        )
    ) {
        return;
    }
    try {
        const res = await fetch('/api/gallery/false_positive_zones', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event_id: currentDetectionId }),
        });
        const data = await res.json();
        if (data.ok) {
            showGalleryStatus(
                'Ignore zone saved. Future overlapping detections will be skipped.'
            );
        } else {
            alert(data.error || 'Failed to save ignore zone');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function showGalleryStatus(message) {
    const el = document.getElementById('statusMessage');
    if (!el) return;
    el.innerHTML =
        '<div class="gallery-status-toast">' +
        '<span class="material-icons-outlined">check_circle</span>' +
        '<span>' + message + '</span></div>';
    el.classList.remove('hidden');
    window.clearTimeout(window._galleryStatusTimer);
    window._galleryStatusTimer = window.setTimeout(function () {
        el.innerHTML = '';
    }, 5000);
}

function removeGalleryItem(id) {
    const item = document.querySelector('.gallery-item[data-id="' + id + '"]');
    if (item) item.remove();
    selectedIds.delete(id);
    updateSelectionUI();
}

async function deleteCurrent() {
    if (!currentDetectionId || !confirm('Delete this detection?')) return;
    try {
        const res = await fetch('/api/gallery/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: [currentDetectionId] }),
        });
        if (res.ok) {
            location.reload();
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        closeModal();
    }
});

/** Browse-by-date: keep filters when changing day/month */
function selectGalleryDate(dateStr) {
    var p = new URLSearchParams(window.location.search);
    p.set('view', 'date');
    p.set('date', dateStr);
    p.set('page', '1');
    window.location.href = '/gallery?' + p.toString();
}

function changeGalleryMonth(delta) {
    var p = new URLSearchParams(window.location.search);
    var d = p.get('date');
    if (!d) {
        d = new Date().toISOString().split('T')[0];
    }
    var parts = d.split('-');
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10);
    if (isNaN(y) || isNaN(m)) {
        var now = new Date();
        y = now.getFullYear();
        m = now.getMonth() + 1;
    }
    m += delta;
    if (m < 1) {
        m = 12;
        y -= 1;
    }
    if (m > 12) {
        m = 1;
        y += 1;
    }
    var newDate =
        y +
        '-' +
        String(m).padStart(2, '0') +
        '-01';
    p.set('date', newDate);
    p.set('view', 'date');
    p.set('page', '1');
    window.location.href = '/gallery?' + p.toString();
}

function exportTrainingData() {
    const hours = new URLSearchParams(window.location.search).get('hours') || '168';
    window.location.href = '/api/gallery/export?format=yolo&hours=' + encodeURIComponent(hours);
}

/** Navigate when a compact toolbar filter changes */
function applyGalleryFilters(changed) {
    const p = new URLSearchParams(window.location.search);
    const timeVal = document.getElementById('galleryTimeFilter')?.value || 'recent:1';
    const cls = document.getElementById('galleryClassFilter')?.value || '';
    const cam = document.getElementById('galleryCameraFilter')?.value || '';
    const perPage = document.getElementById('galleryPerPage')?.value || '50';

    if (timeVal === 'date') {
        p.set('view', 'date');
        if (!p.get('date')) {
            p.set('date', new Date().toISOString().split('T')[0]);
        }
    } else {
        const parts = timeVal.split(':');
        p.set('view', 'recent');
        p.set('hours', parts[1] || '1');
    }

    if (cls) {
        p.set('class', cls);
    } else {
        p.delete('class');
    }

    if (cam) {
        p.set('cam', cam);
    } else {
        p.delete('cam');
    }

    p.set('per_page', perPage);
    if (changed !== 'page') {
        p.set('page', '1');
    }

    window.location.href = '/gallery?' + p.toString();
}

document.addEventListener('DOMContentLoaded', function () {
    const grid = document.querySelector('.gallery-grid');
    if (!grid || !('IntersectionObserver' in window)) return;
    grid.querySelectorAll('img[loading="lazy"]').forEach(function (img) {
        const io = new IntersectionObserver(function (entries, obs) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) obs.unobserve(entry.target);
            });
        }, { rootMargin: '200px' });
        io.observe(img);
    });
});

/**
 * Labeled polygon region editor for camera detail view.
 */
(function (global) {
    'use strict';

    const FILTER_MODES = ['include', 'exclude', 'none'];
    const FILTER_HINTS = {
        include: 'Include: only detect inside this region.',
        exclude: 'Exclude: ignore detections inside this region.',
        none: 'None: no filtering — use for tagging, analytics, or notifications only.',
    };

    const editors = {};

    function uid() {
        return 'r_' + Math.random().toString(36).slice(2, 12);
    }

    function clamp01(v) {
        return Math.max(0, Math.min(1, Number(v) || 0));
    }

    function regionColor(label) {
        if (!label) return '#2563eb';
        let h = 0;
        for (let i = 0; i < label.length; i++) h += label.charCodeAt(i);
        return `hsl(${h % 360}, 65%, 45%)`;
    }

    function filterStroke(filter) {
        if (filter === 'include') return 'var(--color-success)';
        if (filter === 'exclude') return 'var(--color-danger)';
        return 'var(--color-primary)';
    }

    function parseRegionsResponse(json) {
        if (Array.isArray(json.regions)) return json.regions;
        if (Array.isArray(json.rois)) return json.rois;
        if (json.data && Array.isArray(json.data.regions)) return json.data.regions;
        if (json.data && Array.isArray(json.data.rois)) return json.data.rois;
        return [];
    }

    function migrateLegacy(raw) {
        const filter = FILTER_MODES.includes(raw.filter) ? raw.filter
            : (raw.mode === 'include' ? 'include' : (raw.mode === 'exclude' ? 'exclude' : 'exclude'));
        let points = [];
        if (Array.isArray(raw.points) && raw.points.length >= 3) {
            points = raw.points.map((p) => [clamp01(p[0]), clamp01(p[1])]);
        } else if (raw.x1 != null && raw.y1 != null && raw.x2 != null && raw.y2 != null) {
            let x1 = clamp01(raw.x1);
            let y1 = clamp01(raw.y1);
            let x2 = clamp01(raw.x2);
            let y2 = clamp01(raw.y2);
            if (x2 < x1) [x1, x2] = [x2, x1];
            if (y2 < y1) [y1, y2] = [y2, y1];
            points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]];
        }
        const notify = raw.notify && typeof raw.notify === 'object' ? raw.notify : {};
        const notifyClasses = notify.classes || raw.notify_classes || [];
        return {
            id: raw.id || uid(),
            label: (raw.label || '').trim(),
            shape: raw.shape || (points.length === 4 ? 'rect' : 'polygon'),
            points,
            filter,
            tag_detections: raw.tag_detections != null ? !!raw.tag_detections : !!raw.label,
            analytics: raw.analytics != null ? !!raw.analytics : !!raw.label,
            notify: {
                enabled: notify.enabled != null ? !!notify.enabled : (notifyClasses.length > 0),
                classes: Array.isArray(notifyClasses) ? notifyClasses.slice() : [],
                cooldown_s: Number(notify.cooldown_s || raw.notify_cooldown_s || 60),
            },
        };
    }

    function normalizeRegion(raw) {
        const r = migrateLegacy(raw);
        if (r.points.length < 3) {
            r.points = [[0, 0], [1, 0], [1, 1], [0, 1]];
        }
        return r;
    }

    function ptsToSvg(points) {
        return points.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ');
    }

    function dist(a, b) {
        const dx = a[0] - b[0];
        const dy = a[1] - b[1];
        return Math.sqrt(dx * dx + dy * dy);
    }

    function createEditor(camId) {
        return {
            camId,
            active: false,
            regions: [],
            selectedId: null,
            draftPoints: [],
            drawFilter: 'include',
            availableClasses: [],
            els: {},
        };
    }

    function getContainerRect(ed) {
        const el = ed.els.container;
        return el ? el.getBoundingClientRect() : null;
    }

    function pointerNorm(ed, clientX, clientY) {
        const rect = getContainerRect(ed);
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        return {
            x: clamp01((clientX - rect.left) / rect.width),
            y: clamp01((clientY - rect.top) / rect.height),
        };
    }

    function selectedRegion(ed) {
        return ed.regions.find((r) => r.id === ed.selectedId) || null;
    }

    function setDrawFilter(ed, mode) {
        ed.drawFilter = FILTER_MODES.includes(mode) ? mode : 'include';
        FILTER_MODES.forEach((m) => {
            const btn = ed.els.filterBtns && ed.els.filterBtns[m];
            if (btn) btn.classList.toggle('active', ed.drawFilter === m);
        });
        if (ed.els.filterHint) {
            ed.els.filterHint.textContent = FILTER_HINTS[ed.drawFilter];
        }
    }

    function renderOverlay(ed) {
        const svg = ed.els.svg;
        if (!svg) return;
        const parts = [];

        ed.regions.forEach((region) => {
            if (!region.points || region.points.length < 3) return;
            const color = regionColor(region.label);
            const stroke = filterStroke(region.filter);
            const selected = region.id === ed.selectedId;
            const opacity = region.filter === 'none' ? 0.12 : 0.2;
            parts.push(`
                <polygon class="region-shape ${selected ? 'selected' : ''}" data-id="${region.id}"
                    points="${ptsToSvg(region.points)}"
                    fill="${color}" fill-opacity="${opacity}"
                    stroke="${stroke}" stroke-width="${selected ? 2.5 : 1.5}"
                    vector-effect="non-scaling-stroke"/>
            `);
            const cx = region.points.reduce((s, p) => s + p[0], 0) / region.points.length;
            const cy = region.points.reduce((s, p) => s + p[1], 0) / region.points.length;
            const label = region.label || region.filter;
            parts.push(`
                <text class="region-label" x="${cx * 100}%" y="${cy * 100}%"
                    text-anchor="middle" dominant-baseline="middle">${escapeHtml(label)}</text>
            `);
        });

        if (ed.draftPoints.length) {
            const draftPts = ed.draftPoints.slice();
            if (draftPts.length >= 2) {
                parts.push(`
                    <polyline class="region-draft-line" points="${ptsToSvg(draftPts)}"
                        fill="none" stroke="var(--color-primary)" stroke-width="2"
                        stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>
                `);
            }
            draftPts.forEach((p, i) => {
                parts.push(`
                    <circle class="region-vertex" cx="${p[0] * 100}%" cy="${p[1] * 100}%"
                        r="4" fill="${i === 0 ? 'var(--color-success)' : 'var(--color-primary)'}"/>
                `);
            });
        }

        svg.innerHTML = parts.join('');
        svg.querySelectorAll('.region-shape').forEach((poly) => {
            poly.addEventListener('click', (e) => {
                e.stopPropagation();
                selectRegion(ed, poly.dataset.id);
            });
        });
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function renderRegionList(ed) {
        const list = ed.els.list;
        if (!list) return;
        if (!ed.regions.length) {
            list.innerHTML = '<li class="region-list-empty">Click on the video to place polygon vertices. Finish with Enter or double-click the first point.</li>';
            return;
        }
        list.innerHTML = ed.regions.map((r) => {
            const active = r.id === ed.selectedId ? ' active' : '';
            const badges = [];
            if (r.filter !== 'none') badges.push(r.filter);
            if (r.tag_detections && r.label) badges.push('tag');
            if (r.analytics && r.label) badges.push('analytics');
            if (r.notify && r.notify.enabled) badges.push('notify');
            return `
                <li class="region-list-item${active}" data-id="${r.id}">
                    <button type="button" class="region-list-select" data-id="${r.id}">
                        <span class="region-swatch" style="background:${regionColor(r.label)}"></span>
                        <span class="region-list-text">
                            <strong>${escapeHtml(r.label || 'Unlabeled')}</strong>
                            <span class="region-badges">${badges.map((b) => `<span class="region-badge">${b}</span>`).join('')}</span>
                        </span>
                    </button>
                    <button type="button" class="btn btn-ghost btn-icon btn-sm region-delete" data-id="${r.id}" title="Remove">
                        <span class="material-icons-outlined">delete</span>
                    </button>
                </li>`;
        }).join('');

        list.querySelectorAll('.region-list-select, .region-list-item').forEach((el) => {
            el.addEventListener('click', (e) => {
                const id = el.dataset.id || el.closest('[data-id]')?.dataset.id;
                if (id) selectRegion(ed, id);
            });
        });
        list.querySelectorAll('.region-delete').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteRegion(ed, btn.dataset.id);
            });
        });
    }

    function renderProperties(ed) {
        const panel = ed.els.props;
        if (!panel) return;
        const region = selectedRegion(ed);
        if (!region) {
            panel.hidden = true;
            panel.innerHTML = '';
            return;
        }
        panel.hidden = false;
        const notify = region.notify || { enabled: false, classes: [], cooldown_s: 60 };
        const classOptions = ed.availableClasses.map((c) => {
            const sel = notify.classes.includes(c) ? ' selected' : '';
            return `<option value="${escapeHtml(c)}"${sel}>${escapeHtml(c)}</option>`;
        }).join('');

        panel.innerHTML = `
            <h4 class="region-props-title">Region properties</h4>
            <label class="form-label">Label <span class="form-hint">(used for analytics &amp; notifications)</span></label>
            <input type="text" class="form-input region-prop-label" value="${escapeHtml(region.label)}" placeholder="e.g. main_street">

            <label class="form-label">Filter mode</label>
            <select class="form-select region-prop-filter">
                ${FILTER_MODES.map((m) => `<option value="${m}"${region.filter === m ? ' selected' : ''}>${m}</option>`).join('')}
            </select>

            <div class="region-prop-checks">
                <label class="form-check"><input type="checkbox" class="region-prop-tag" ${region.tag_detections ? 'checked' : ''}> Tag detections with label</label>
                <label class="form-check"><input type="checkbox" class="region-prop-analytics" ${region.analytics ? 'checked' : ''}> Include in traffic analytics</label>
                <label class="form-check"><input type="checkbox" class="region-prop-notify-en" ${notify.enabled ? 'checked' : ''}> Enable notifications</label>
            </div>

            <label class="form-label">Notify on classes</label>
            <select class="form-select region-prop-notify-classes" multiple size="5">${classOptions}</select>
            <p class="form-hint">Hold Ctrl/Cmd to select multiple classes.</p>

            <label class="form-label">Cooldown (seconds)</label>
            <input type="number" class="form-input region-prop-cooldown" min="5" max="3600" value="${notify.cooldown_s || 60}">
        `;

        const sync = () => {
            region.label = panel.querySelector('.region-prop-label').value.trim();
            region.filter = panel.querySelector('.region-prop-filter').value;
            region.tag_detections = panel.querySelector('.region-prop-tag').checked;
            region.analytics = panel.querySelector('.region-prop-analytics').checked;
            region.notify = region.notify || {};
            region.notify.enabled = panel.querySelector('.region-prop-notify-en').checked;
            region.notify.classes = Array.from(panel.querySelector('.region-prop-notify-classes').selectedOptions).map((o) => o.value);
            region.notify.cooldown_s = Number(panel.querySelector('.region-prop-cooldown').value) || 60;
            renderOverlay(ed);
            renderRegionList(ed);
        };

        panel.querySelectorAll('input, select').forEach((el) => {
            el.addEventListener('change', sync);
            el.addEventListener('input', sync);
        });
    }

    function selectRegion(ed, id) {
        ed.selectedId = id;
        renderOverlay(ed);
        renderRegionList(ed);
        renderProperties(ed);
    }

    function deleteRegion(ed, id) {
        ed.regions = ed.regions.filter((r) => r.id !== id);
        if (ed.selectedId === id) ed.selectedId = null;
        renderOverlay(ed);
        renderRegionList(ed);
        renderProperties(ed);
        saveRegions(ed, true);
    }

    function finishDraft(ed) {
        if (ed.draftPoints.length < 3) {
            if (typeof showToast === 'function') showToast('Need at least 3 points for a polygon', 'warning');
            return;
        }
        const n = ed.regions.length + 1;
        const region = normalizeRegion({
            id: uid(),
            label: `region_${n}`,
            shape: 'polygon',
            points: ed.draftPoints.map((p) => [p[0], p[1]]),
            filter: ed.drawFilter,
            tag_detections: true,
            analytics: true,
            notify: { enabled: false, classes: [], cooldown_s: 60 },
        });
        ed.regions.push(region);
        ed.draftPoints = [];
        ed.selectedId = region.id;
        renderOverlay(ed);
        renderRegionList(ed);
        renderProperties(ed);
        saveRegions(ed, true);
    }

    function cancelDraft(ed) {
        ed.draftPoints = [];
        renderOverlay(ed);
    }

    async function loadRegions(ed) {
        const res = await fetch(`/api/rois/${ed.camId}`);
        const data = await res.json();
        ed.regions = parseRegionsResponse(data).map(normalizeRegion);
        ed.selectedId = null;
        renderOverlay(ed);
        renderRegionList(ed);
        renderProperties(ed);
    }

    async function loadClasses(ed) {
        try {
            const res = await fetch('/api/classes/available');
            const data = await res.json();
            ed.availableClasses = data.data || data.classes || [];
        } catch (_) {
            ed.availableClasses = ['person', 'car', 'truck', 'bus', 'motorcycle', 'bicycle'];
        }
    }

    async function saveRegions(ed, quiet) {
        try {
            const res = await fetch(`/api/rois/${ed.camId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ regions: ed.regions }),
            });
            const data = await res.json();
            if (!res.ok || data.ok === false) throw new Error(data.error || 'Save failed');
            ed.regions = parseRegionsResponse(data).map(normalizeRegion);
            renderOverlay(ed);
            renderRegionList(ed);
            renderProperties(ed);
            if (!quiet && typeof showToast === 'function') showToast('Regions saved', 'success');
        } catch (err) {
            if (typeof showToast === 'function') showToast('Failed to save regions: ' + err.message, 'error');
        }
    }

    function clearAll(ed) {
        if (!confirm('Remove all regions for this camera?')) return;
        ed.regions = [];
        ed.selectedId = null;
        ed.draftPoints = [];
        renderOverlay(ed);
        renderRegionList(ed);
        renderProperties(ed);
        saveRegions(ed, true);
    }

    function bindDrawing(ed) {
        const layer = ed.els.layer;
        if (!layer || layer.dataset.bound === '1') return;
        layer.dataset.bound = '1';

        layer.addEventListener('click', (e) => {
            if (!ed.active) return;
            const pt = pointerNorm(ed, e.clientX, e.clientY);
            if (!pt) return;

            if (ed.draftPoints.length >= 3) {
                const first = ed.draftPoints[0];
                const closeDist = dist([pt.x, pt.y], first);
                if (closeDist < 0.025) {
                    finishDraft(ed);
                    return;
                }
            }

            ed.draftPoints.push([pt.x, pt.y]);
            renderOverlay(ed);
        });

        layer.addEventListener('mousemove', (e) => {
            if (!ed.active || ed.draftPoints.length === 0) return;
            const pt = pointerNorm(ed, e.clientX, e.clientY);
            if (!pt) return;
            const svg = ed.els.svg;
            let ghost = svg && svg.querySelector('.region-draft-ghost');
            if (!ghost && svg) {
                ghost = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                ghost.setAttribute('class', 'region-draft-ghost');
                ghost.setAttribute('stroke', 'var(--color-primary)');
                ghost.setAttribute('stroke-width', '1.5');
                ghost.setAttribute('stroke-dasharray', '3 3');
                svg.appendChild(ghost);
            }
            if (ghost) {
                const last = ed.draftPoints[ed.draftPoints.length - 1];
                ghost.setAttribute('x1', last[0] * 100 + '%');
                ghost.setAttribute('y1', last[1] * 100 + '%');
                ghost.setAttribute('x2', pt.x * 100 + '%');
                ghost.setAttribute('y2', pt.y * 100 + '%');
            }
        });

        layer.addEventListener('dblclick', (e) => {
            if (!ed.active) return;
            e.preventDefault();
            finishDraft(ed);
        });
    }

    function bindKeys(ed) {
        if (ed._keysBound) return;
        ed._keysBound = true;
        document.addEventListener('keydown', (e) => {
            if (!ed.active) return;
            if (e.key === 'Enter') {
                e.preventDefault();
                finishDraft(ed);
            } else if (e.key === 'Backspace' && ed.draftPoints.length) {
                e.preventDefault();
                ed.draftPoints.pop();
                renderOverlay(ed);
            } else if (e.key === 'Escape') {
                if (ed.draftPoints.length) cancelDraft(ed);
            }
        });
    }

    async function toggleEdit(ed) {
        ed.active = !ed.active;
        const btn = ed.els.toggleBtn;
        const container = ed.els.container;
        const panel = ed.els.panel;
        const layer = ed.els.layer;

        if (ed.active) {
            if (btn) {
                btn.innerHTML = '<span class="material-icons-outlined">close</span> Done Editing';
                btn.classList.replace('btn-secondary', 'btn-danger');
            }
            if (container) container.classList.add('region-edit-mode');
            if (panel) panel.hidden = false;
            if (layer) layer.hidden = false;
            setDrawFilter(ed, ed.drawFilter);
            await loadClasses(ed);
            await loadRegions(ed);
        } else {
            if (btn) {
                btn.innerHTML = '<span class="material-icons-outlined">edit</span> Edit Regions';
                btn.classList.replace('btn-danger', 'btn-secondary');
            }
            if (container) container.classList.remove('region-edit-mode');
            if (panel) panel.hidden = true;
            if (layer) layer.hidden = true;
            cancelDraft(ed);
        }
    }

    function init(camId, opts) {
        opts = opts || {};
        const ed = createEditor(camId);
        ed.els = {
            container: document.getElementById(opts.containerId || ('cameraImg' + camId)),
            layer: document.getElementById(opts.layerId || ('regionLayer' + camId)),
            svg: document.getElementById(opts.svgId || ('regionSvg' + camId)),
            panel: document.getElementById(opts.panelId || ('regionEditorPanel' + camId)),
            list: document.getElementById(opts.listId || ('regionList' + camId)),
            props: document.getElementById(opts.propsId || ('regionProps' + camId)),
            toggleBtn: document.getElementById(opts.toggleBtnId || ('regionBtn' + camId)),
            filterHint: document.getElementById(opts.filterHintId || ('regionFilterHint' + camId)),
            filterBtns: {},
        };
        FILTER_MODES.forEach((m) => {
            ed.els.filterBtns[m] = document.getElementById('regionFilter' + m.charAt(0).toUpperCase() + m.slice(1) + camId);
        });

        const finishBtn = document.getElementById('regionFinishBtn' + camId);
        const saveBtn = document.getElementById('regionSaveBtn' + camId);
        const clearBtn = document.getElementById('regionClearBtn' + camId);

        FILTER_MODES.forEach((m) => {
            const btn = ed.els.filterBtns[m];
            if (btn) btn.addEventListener('click', () => setDrawFilter(ed, m));
        });
        if (finishBtn) finishBtn.addEventListener('click', () => finishDraft(ed));
        if (saveBtn) saveBtn.addEventListener('click', () => saveRegions(ed));
        if (clearBtn) clearBtn.addEventListener('click', () => clearAll(ed));
        if (ed.els.toggleBtn) {
            ed.els.toggleBtn.addEventListener('click', () => toggleEdit(ed));
        }

        bindDrawing(ed);
        bindKeys(ed);
        editors[camId] = ed;
        return ed;
    }

    global.RegionEditor = {
        init,
        get: (camId) => editors[camId],
        toggle: (camId) => { const ed = editors[camId]; if (ed) toggleEdit(ed); },
        save: (camId, quiet) => { const ed = editors[camId]; if (ed) saveRegions(ed, quiet); },
    };
})(window);

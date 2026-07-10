// Full-screen Three.js point-cloud viewer for 3D multiview reconstructions.
// ES module — loaded via <script type="module"> with an import map for 'three'.
// Exposes window.MultiviewViewer = { open(meta), close() } for the classic
// (non-module) page script.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';

const VIRIDIS = [
    [0.267, 0.005, 0.329],
    [0.229, 0.322, 0.545],
    [0.128, 0.567, 0.551],
    [0.369, 0.789, 0.383],
    [0.993, 0.906, 0.144]
];

const BG_DARK = 0x0b0e14;
const BG_LIGHT = 0xe8eaed;

let viewer = null;

function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function toast(msg, type) {
    if (typeof window.showToast === 'function') window.showToast(msg, type);
    else if (type === 'error') console.error(msg);
}

function fmtLen(m) {
    return m < 1 ? (m * 100).toFixed(1) + ' cm' : m.toFixed(2) + ' m';
}

function fmtArea(m2) {
    return m2 < 0.1 ? (m2 * 10000).toFixed(1) + ' cm²' : m2.toFixed(3) + ' m²';
}

function fmtVol(m3) {
    return (m3 * 1000).toFixed(2) + ' L';
}

function fmtRel(v) {
    return (v === null || v === undefined || !isFinite(v)) ? 'N/A' : Number(v).toPrecision(3);
}

function fmtDate(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
}

function upAxisVector(upAxis) {
    switch (upAxis) {
        case '-y': return { index: 1, sign: -1 };
        case '+z': return { index: 2, sign: 1 };
        case '-z': return { index: 2, sign: -1 };
        case '+y':
        default: return { index: 1, sign: 1 };
    }
}

function viridisColor(t) {
    t = Math.max(0, Math.min(1, t));
    const seg = t * (VIRIDIS.length - 1);
    const i = Math.min(Math.floor(seg), VIRIDIS.length - 2);
    const f = seg - i;
    const a = VIRIDIS[i], b = VIRIDIS[i + 1];
    return [
        a[0] + (b[0] - a[0]) * f,
        a[1] + (b[1] - a[1]) * f,
        a[2] + (b[2] - a[2]) * f
    ];
}

// ========== DOM ==========
function buildModal(meta) {
    const modal = document.createElement('div');
    modal.className = 'mv-viewer-modal';
    modal.innerHTML = `
        <div class="mv-viewer-header">
            <h2>
                <span class="material-icons-outlined">view_in_ar</span>
                ${esc(fmtDate(meta.timestamp))}
                <span class="mv-badge mv-badge-engine">${esc(meta.engine)}</span>
            </h2>
            <span class="mv-viewer-pointcount" data-role="pointcount"></span>
            <button type="button" class="btn btn-secondary" data-role="close">
                <span class="material-icons-outlined">close</span> Close
            </button>
        </div>
        <div class="mv-viewer-body">
            <div class="mv-viewer-canvas-wrap" data-role="canvaswrap">
                <div class="mv-viewer-spinner" data-role="spinner">
                    <div class="mv-viewer-spinner-ring"></div>
                    <div data-role="spinnertext">Downloading point cloud…</div>
                </div>
            </div>

            <aside class="mv-viewer-panel mv-viewer-panel-left" data-role="metricspanel">
                <div class="mv-viewer-panel-header">
                    <span><span class="material-icons-outlined">straighten</span> Metrics</span>
                    <button type="button" data-role="collapse-left" title="Collapse">
                        <span class="material-icons-outlined">unfold_less</span>
                    </button>
                </div>
                <div class="mv-viewer-panel-body" data-role="metricsbody"></div>
            </aside>

            <aside class="mv-viewer-panel mv-viewer-panel-right" data-role="controlspanel">
                <div class="mv-viewer-panel-header">
                    <span><span class="material-icons-outlined">tune</span> Controls</span>
                    <button type="button" data-role="collapse-right" title="Collapse">
                        <span class="material-icons-outlined">unfold_less</span>
                    </button>
                </div>
                <div class="mv-viewer-panel-body">
                    <label class="mv-viewer-field">
                        <span>Point size</span>
                        <input type="range" data-role="pointsize" min="0.001" max="0.05" step="0.0005" value="0.005">
                    </label>
                    <label class="mv-viewer-field">
                        <span>Color mode</span>
                        <select data-role="colormode">
                            <option value="rgb">RGB (original)</option>
                            <option value="height">Height gradient</option>
                        </select>
                    </label>
                    <label class="mv-viewer-check">
                        <input type="checkbox" data-role="bglight"> Light background
                    </label>
                    <label class="mv-viewer-check">
                        <input type="checkbox" data-role="grid" checked> Grid &amp; axes
                    </label>
                    <button type="button" class="btn btn-secondary btn-block" data-role="resetview">
                        <span class="material-icons-outlined">center_focus_strong</span> Reset view
                    </button>

                    <hr class="mv-viewer-sep">

                    <button type="button" class="btn btn-secondary btn-block" data-role="measure">
                        <span class="material-icons-outlined">straighten</span> Measure
                    </button>
                    <button type="button" class="btn btn-secondary btn-block" data-role="clearmeasure">
                        <span class="material-icons-outlined">layers_clear</span> Clear measurements
                    </button>
                    <p class="mv-viewer-hint" data-role="measurehint" hidden>
                        Click two points on the cloud to measure the distance between them.
                    </p>

                    <hr class="mv-viewer-sep">

                    <button type="button" class="btn btn-primary btn-block" data-role="scaletoggle">
                        <span class="material-icons-outlined">square_foot</span> Set real-world scale
                    </button>
                    <div class="mv-viewer-scaleform" data-role="scaleform" hidden>
                        <p class="mv-viewer-hint">
                            Measure a known object (e.g. pot width) with the measure tool,
                            then enter its real length below.
                        </p>
                        <p class="mv-viewer-hint" data-role="scalemeasured">No measurement yet.</p>
                        <label class="mv-viewer-field">
                            <span>Real length (cm)</span>
                            <input type="number" data-role="scalecm" min="0.1" step="0.1" placeholder="e.g. 25">
                        </label>
                        <label class="mv-viewer-field">
                            <span>Up axis</span>
                            <select data-role="scaleupaxis">
                                <option value="+y">+Y</option>
                                <option value="-y">-Y</option>
                                <option value="+z">+Z</option>
                                <option value="-z">-Z</option>
                            </select>
                        </label>
                        <button type="button" class="btn btn-success btn-block" data-role="scaleapply">
                            <span class="material-icons-outlined">check</span> Apply scale
                        </button>
                    </div>
                </div>
            </aside>
        </div>
    `;
    return modal;
}

function renderMetricsPanel() {
    if (!viewer) return;
    const body = viewer.modal.querySelector('[data-role="metricsbody"]');
    const m = viewer.meta.metrics;

    if (!m) {
        body.innerHTML = '<p class="mv-viewer-hint">Metrics not computed.</p>';
        return;
    }

    const metric = m.units === 'metric';
    const val = (v, fmt) => {
        if (v === null || v === undefined || !isFinite(v)) return 'N/A';
        return metric ? fmt(v) : fmtRel(v);
    };

    const rows = [
        ['Height', val(m.height, fmtLen)],
        ['Canopy', val(m.canopy_width, fmtLen) + ' × ' + val(m.canopy_depth, fmtLen)],
        ['Canopy area', val(m.canopy_area, fmtArea)],
        ['Hull volume', val(m.hull_volume, fmtVol)],
        ['Points', m.num_points != null ? Number(m.num_points).toLocaleString() : 'N/A'],
        ['Up axis', m.up_axis || '+y']
    ];

    let html = '<table class="mv-viewer-metrics-table">';
    for (const [label, value] of rows) {
        html += `<tr><td>${esc(label)}</td><td>${esc(value)}</td></tr>`;
    }
    html += '</table>';
    if (!metric) {
        html += '<p class="mv-viewer-hint">(relative units — set scale)</p>';
    }
    body.innerHTML = html;
}

// ========== SCENE ==========
function initScene(wrap) {
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG_DARK);

    const w = wrap.clientWidth || window.innerWidth;
    const h = wrap.clientHeight || window.innerHeight;

    const camera = new THREE.PerspectiveCamera(50, w / h, 0.001, 2000);
    camera.position.set(1, 1, 2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h);
    wrap.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    return { scene, camera, renderer, controls };
}

function frameToFit() {
    const v = viewer;
    const sphere = new THREE.Sphere();
    v.bbox.getBoundingSphere(sphere);
    const dist = (sphere.radius || 1) * 2.4;
    v.camera.position.set(dist * 0.6, dist * 0.5, dist * 0.75);
    v.camera.near = Math.max(sphere.radius / 1000, 0.0001);
    v.camera.far = sphere.radius * 100;
    v.camera.updateProjectionMatrix();
    v.controls.target.set(0, 0, 0);
    v.controls.update();
    v.homePosition = v.camera.position.clone();
}

function onPlyLoaded(geometry) {
    const v = viewer;
    if (!v) { geometry.dispose(); return; }

    geometry.computeBoundingBox();
    const center = new THREE.Vector3();
    geometry.boundingBox.getCenter(center);
    geometry.translate(-center.x, -center.y, -center.z);
    geometry.computeBoundingBox();

    v.bbox = geometry.boundingBox.clone();
    v.bboxDiag = v.bbox.getSize(new THREE.Vector3()).length() || 1;

    const hasColor = !!geometry.getAttribute('color');
    if (hasColor) {
        v.originalColors = geometry.getAttribute('color').array.slice();
    }

    const defaultSize = v.bboxDiag * 0.004;
    const material = new THREE.PointsMaterial({
        size: defaultSize,
        vertexColors: hasColor,
        sizeAttenuation: true
    });
    if (!hasColor) material.color.setHex(0x6abf69);

    v.points = new THREE.Points(geometry, material);
    v.scene.add(v.points);

    // Grid and axes sized relative to the cloud, sitting under it
    const gridSize = v.bboxDiag * 1.4;
    v.grid = new THREE.GridHelper(gridSize, 20, 0x3d4459, 0x2d3344);
    v.grid.position.y = v.bbox.min.y;
    v.axes = new THREE.AxesHelper(v.bboxDiag * 0.5);
    v.scene.add(v.grid);
    v.scene.add(v.axes);

    // Adapt point-size slider range to the cloud scale
    const slider = v.modal.querySelector('[data-role="pointsize"]');
    slider.min = String(v.bboxDiag * 0.0005);
    slider.max = String(v.bboxDiag * 0.02);
    slider.step = String(v.bboxDiag * 0.0002);
    slider.value = String(defaultSize);

    v.raycaster.params.Points.threshold = v.bboxDiag * 0.005;

    frameToFit();

    const count = geometry.getAttribute('position').count;
    v.modal.querySelector('[data-role="pointcount"]').textContent =
        count.toLocaleString() + ' points';
    v.modal.querySelector('[data-role="spinner"]').style.display = 'none';

    applyColorMode(v.modal.querySelector('[data-role="colormode"]').value);
}

function applyColorMode(mode) {
    const v = viewer;
    if (!v || !v.points) return;
    const geometry = v.points.geometry;
    const material = v.points.material;
    const posAttr = geometry.getAttribute('position');

    if (mode === 'height') {
        const upAxis = (v.meta.metrics && v.meta.metrics.up_axis) || '+y';
        const { index, sign } = upAxisVector(upAxis);
        let colorAttr = geometry.getAttribute('color');
        if (!colorAttr) {
            colorAttr = new THREE.BufferAttribute(new Float32Array(posAttr.count * 3), 3);
            geometry.setAttribute('color', colorAttr);
        }
        let min = Infinity, max = -Infinity;
        for (let i = 0; i < posAttr.count; i++) {
            const c = posAttr.array[i * 3 + index] * sign;
            if (c < min) min = c;
            if (c > max) max = c;
        }
        const span = (max - min) || 1;
        for (let i = 0; i < posAttr.count; i++) {
            const t = (posAttr.array[i * 3 + index] * sign - min) / span;
            const [r, g, b] = viridisColor(t);
            colorAttr.array[i * 3] = r;
            colorAttr.array[i * 3 + 1] = g;
            colorAttr.array[i * 3 + 2] = b;
        }
        colorAttr.needsUpdate = true;
        material.vertexColors = true;
        material.color.setHex(0xffffff);
        material.needsUpdate = true;
    } else {
        if (v.originalColors) {
            const colorAttr = geometry.getAttribute('color');
            colorAttr.array.set(v.originalColors);
            colorAttr.needsUpdate = true;
            material.vertexColors = true;
            material.color.setHex(0xffffff);
        } else {
            material.vertexColors = false;
            material.color.setHex(0x6abf69);
        }
        material.needsUpdate = true;
    }
}

// ========== MEASUREMENTS ==========
function formatMeasureText(distUnits) {
    let txt = distUnits.toFixed(4) + ' units';
    const scale = viewer.meta.metrics && viewer.meta.metrics.scale_m_per_unit;
    if (scale) {
        const m = distUnits * scale;
        txt += ' = ' + (m < 1 ? (m * 100).toFixed(1) + ' cm' : m.toFixed(3) + ' m');
    }
    return txt;
}

function makeMarker(pos) {
    const v = viewer;
    const geo = new THREE.SphereGeometry(v.bboxDiag * 0.006, 12, 12);
    const mat = new THREE.MeshBasicMaterial({ color: 0xf59e0b, depthTest: false });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = 10;
    mesh.position.copy(pos);
    v.measureGroup.add(mesh);
    return mesh;
}

function handleMeasureClick(ndc) {
    const v = viewer;
    if (!v.points) return;

    v.raycaster.setFromCamera(ndc, v.camera);
    const hits = v.raycaster.intersectObject(v.points);
    if (!hits.length) return;

    // Use the actual vertex position of the nearest hit for precision
    const hit = hits[0];
    const pos = new THREE.Vector3().fromBufferAttribute(
        v.points.geometry.getAttribute('position'), hit.index);

    if (!v.pendingAnchor) {
        v.pendingAnchor = { pos: pos, marker: makeMarker(pos) };
        return;
    }

    const a = v.pendingAnchor.pos;
    const b = pos;
    const markerB = makeMarker(b);

    const lineGeo = new THREE.BufferGeometry().setFromPoints([a, b]);
    const line = new THREE.Line(lineGeo,
        new THREE.LineBasicMaterial({ color: 0xfbbf24, depthTest: false }));
    line.renderOrder = 10;
    v.measureGroup.add(line);

    const dist = a.distanceTo(b);
    const label = document.createElement('div');
    label.className = 'mv-measure-label';
    label.textContent = formatMeasureText(dist);
    v.canvasWrap.appendChild(label);

    v.measurements.push({
        a, b, dist, label,
        objects: [v.pendingAnchor.marker, markerB, line]
    });
    v.lastMeasuredUnits = dist;
    v.pendingAnchor = null;
    updateScaleMeasuredHint();
}

function clearMeasurements() {
    const v = viewer;
    if (!v) return;
    for (const m of v.measurements) {
        m.label.remove();
    }
    v.measurements = [];
    if (v.pendingAnchor) v.pendingAnchor = null;
    while (v.measureGroup.children.length) {
        const child = v.measureGroup.children.pop();
        if (child.geometry) child.geometry.dispose();
        if (child.material) child.material.dispose();
        v.measureGroup.remove(child);
    }
    v.lastMeasuredUnits = null;
    updateScaleMeasuredHint();
}

function refreshMeasureLabels() {
    for (const m of viewer.measurements) {
        m.label.textContent = formatMeasureText(m.dist);
    }
}

function updateMeasureLabelPositions() {
    const v = viewer;
    if (!v.measurements.length) return;
    const rect = { w: v.canvasWrap.clientWidth, h: v.canvasWrap.clientHeight };
    const mid = new THREE.Vector3();
    for (const m of v.measurements) {
        mid.addVectors(m.a, m.b).multiplyScalar(0.5).project(v.camera);
        if (mid.z > 1) {
            m.label.style.display = 'none';
            continue;
        }
        m.label.style.display = '';
        m.label.style.left = ((mid.x + 1) / 2 * rect.w) + 'px';
        m.label.style.top = ((1 - (mid.y + 1) / 2) * rect.h) + 'px';
    }
}

function updateScaleMeasuredHint() {
    const v = viewer;
    const el = v.modal.querySelector('[data-role="scalemeasured"]');
    if (v.lastMeasuredUnits) {
        el.textContent = 'Measured: ' + v.lastMeasuredUnits.toFixed(4) + ' units';
    } else {
        el.textContent = 'No measurement yet.';
    }
}

// ========== SET SCALE ==========
async function applyScale() {
    const v = viewer;
    if (!v.lastMeasuredUnits) {
        toast('Measure a known distance first (use the measure tool)', 'error');
        return;
    }
    const cmInput = v.modal.querySelector('[data-role="scalecm"]');
    const cm = parseFloat(cmInput.value);
    if (!cm || cm <= 0) {
        toast('Enter a valid real length in cm', 'error');
        return;
    }
    const upAxis = v.modal.querySelector('[data-role="scaleupaxis"]').value;

    try {
        const response = await fetch(
            `/api/multiview/reconstruction/${encodeURIComponent(v.meta.id)}/scale`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    known_distance_units: v.lastMeasuredUnits,
                    known_distance_m: cm / 100,
                    up_axis: upAxis
                })
            });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'Failed to set scale');

        v.meta.metrics = data.metrics;
        renderMetricsPanel();
        refreshMeasureLabels();
        if (v.modal.querySelector('[data-role="colormode"]').value === 'height') {
            applyColorMode('height');
        }
        toast('Scale applied — metrics updated', 'success');
    } catch (error) {
        toast(error.message, 'error');
    }
}

// ========== EVENTS ==========
function bindEvents() {
    const v = viewer;
    const q = sel => v.modal.querySelector(sel);

    q('[data-role="close"]').addEventListener('click', close);

    v.keyHandler = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', v.keyHandler);

    v.resizeHandler = () => {
        const w = v.canvasWrap.clientWidth;
        const h = v.canvasWrap.clientHeight;
        if (!w || !h) return;
        v.camera.aspect = w / h;
        v.camera.updateProjectionMatrix();
        v.renderer.setSize(w, h);
    };
    window.addEventListener('resize', v.resizeHandler);

    q('[data-role="collapse-left"]').addEventListener('click', () => {
        q('[data-role="metricspanel"]').classList.toggle('collapsed');
    });
    q('[data-role="collapse-right"]').addEventListener('click', () => {
        q('[data-role="controlspanel"]').classList.toggle('collapsed');
    });

    q('[data-role="pointsize"]').addEventListener('input', e => {
        if (v.points) v.points.material.size = parseFloat(e.target.value);
    });

    q('[data-role="colormode"]').addEventListener('change', e => {
        applyColorMode(e.target.value);
    });

    q('[data-role="bglight"]').addEventListener('change', e => {
        v.scene.background.setHex(e.target.checked ? BG_LIGHT : BG_DARK);
    });

    q('[data-role="grid"]').addEventListener('change', e => {
        if (v.grid) v.grid.visible = e.target.checked;
        if (v.axes) v.axes.visible = e.target.checked;
    });

    q('[data-role="resetview"]').addEventListener('click', () => {
        if (v.homePosition) {
            v.camera.position.copy(v.homePosition);
            v.controls.target.set(0, 0, 0);
            v.controls.update();
        }
    });

    q('[data-role="measure"]').addEventListener('click', e => {
        v.measureActive = !v.measureActive;
        e.currentTarget.classList.toggle('active', v.measureActive);
        q('[data-role="measurehint"]').hidden = !v.measureActive;
        v.renderer.domElement.style.cursor = v.measureActive ? 'crosshair' : '';
        if (!v.measureActive && v.pendingAnchor) {
            // Abort a half-finished measurement
            const marker = v.pendingAnchor.marker;
            v.measureGroup.remove(marker);
            marker.geometry.dispose();
            marker.material.dispose();
            v.pendingAnchor = null;
        }
    });

    q('[data-role="clearmeasure"]').addEventListener('click', clearMeasurements);

    q('[data-role="scaletoggle"]').addEventListener('click', () => {
        const form = q('[data-role="scaleform"]');
        form.hidden = !form.hidden;
        if (!form.hidden) updateScaleMeasuredHint();
    });

    q('[data-role="scaleapply"]').addEventListener('click', applyScale);

    // Measure picking: click without drag
    const canvas = v.renderer.domElement;
    let downPos = null;
    canvas.addEventListener('pointerdown', e => {
        downPos = { x: e.clientX, y: e.clientY };
    });
    canvas.addEventListener('pointerup', e => {
        if (!v.measureActive || !downPos) return;
        const moved = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
        downPos = null;
        if (moved > 5) return;
        const rect = canvas.getBoundingClientRect();
        const ndc = new THREE.Vector2(
            ((e.clientX - rect.left) / rect.width) * 2 - 1,
            -((e.clientY - rect.top) / rect.height) * 2 + 1
        );
        handleMeasureClick(ndc);
    });
}

// ========== LIFECYCLE ==========
function animate() {
    if (!viewer) return;
    viewer.rafId = requestAnimationFrame(animate);
    viewer.controls.update();
    updateMeasureLabelPositions();
    viewer.renderer.render(viewer.scene, viewer.camera);
}

function open(meta) {
    if (viewer) close();
    if (!meta || !meta.ply_url) {
        toast('Reconstruction has no point cloud file', 'error');
        return;
    }

    const modal = buildModal(meta);
    document.body.appendChild(modal);
    document.body.style.overflow = 'hidden';

    const canvasWrap = modal.querySelector('[data-role="canvaswrap"]');
    const { scene, camera, renderer, controls } = initScene(canvasWrap);

    const measureGroup = new THREE.Group();
    scene.add(measureGroup);

    viewer = {
        meta,
        modal,
        canvasWrap,
        scene,
        camera,
        renderer,
        controls,
        points: null,
        originalColors: null,
        bbox: null,
        bboxDiag: 1,
        grid: null,
        axes: null,
        homePosition: null,
        raycaster: new THREE.Raycaster(),
        measureGroup,
        measureActive: false,
        pendingAnchor: null,
        measurements: [],
        lastMeasuredUnits: null,
        rafId: null,
        keyHandler: null,
        resizeHandler: null
    };
    viewer.raycaster.params.Points = { threshold: 0.01 };

    const upSelect = modal.querySelector('[data-role="scaleupaxis"]');
    if (meta.metrics && meta.metrics.up_axis) upSelect.value = meta.metrics.up_axis;

    renderMetricsPanel();
    bindEvents();
    animate();

    const loader = new PLYLoader();
    const spinnerText = modal.querySelector('[data-role="spinnertext"]');
    loader.load(
        meta.ply_url,
        onPlyLoaded,
        progress => {
            if (progress.total) {
                const pct = Math.round((progress.loaded / progress.total) * 100);
                spinnerText.textContent = `Downloading point cloud… ${pct}%`;
            }
        },
        err => {
            console.error('Failed to load PLY:', err);
            const spinner = modal.querySelector('[data-role="spinner"]');
            spinner.innerHTML = '<div class="mv-viewer-error">Failed to load point cloud.</div>';
        }
    );
}

function close() {
    if (!viewer) return;
    const v = viewer;
    viewer = null;

    if (v.rafId) cancelAnimationFrame(v.rafId);
    document.removeEventListener('keydown', v.keyHandler);
    window.removeEventListener('resize', v.resizeHandler);

    for (const m of v.measurements) m.label.remove();
    v.measureGroup.traverse(obj => {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) obj.material.dispose();
    });
    if (v.points) {
        v.points.geometry.dispose();
        v.points.material.dispose();
    }
    if (v.grid) v.grid.dispose();
    if (v.axes) v.axes.dispose();
    v.controls.dispose();
    v.renderer.dispose();

    v.modal.remove();
    document.body.style.overflow = '';
}

window.MultiviewViewer = { open, close };

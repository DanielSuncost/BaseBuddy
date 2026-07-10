/**
 * Camera Detail Page — recording, stats, and labeled region editor.
 */

async function toggleRecording(cam) {
    const btn = document.getElementById('recordBtn' + cam);
    if (!btn) return;

    const isRecording = btn.classList.contains('recording');
    const action = isRecording ? 'stop' : 'start';

    try {
        const response = await fetch(`/record/${cam}/${action}`, { method: 'POST' });
        const data = await response.json();

        if (response.ok) {
            if (action === 'start') {
                btn.classList.add('recording');
                btn.innerHTML = '<span class="material-icons-outlined">stop</span> Stop';
            } else {
                btn.classList.remove('recording');
                btn.innerHTML = '<span class="material-icons-outlined">fiber_manual_record</span> Record';
            }
        } else {
            showToast(data.error || 'Unknown error', 'error');
        }
    } catch (error) {
        showToast('Network error: ' + error.message, 'error');
    }
}

function viewRecordings(cam) {
    window.location.href = `/recordings?camera=${cam}`;
}

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
    } else {
        document.exitFullscreen();
    }
}

async function clearTracks(cam) {
    if (!confirm(`Clear all tracking data for Camera ${cam + 1}?`)) return;

    try {
        const response = await fetch(`/api/tracking/clear/${cam}`, { method: 'POST' });
        const result = await response.json();
        showToast(result.message || 'Tracks cleared', 'success');
    } catch (error) {
        showToast('Error: ' + error.message, 'error');
    }
}

async function updateStats() {
    try {
        const response = await fetch('/api/tracking/status');
        const data = await response.json();
        const status = data.status && data.status[`camera_${camId}`];
        if (status) {
            const tracksEl = document.getElementById(`tracks${camId}`);
            if (tracksEl) tracksEl.textContent = status.tracks_active;
        }
    } catch (error) {
        console.error('Error updating stats:', error);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    RegionEditor.init(camId);
    updateStats();
    setInterval(updateStats, 2000);
});

document.addEventListener('keydown', function (e) {
    const ed = RegionEditor.get(camId);
    if (e.key === 'Escape' && ed && ed.active && !ed.draftPoints.length) {
        RegionEditor.toggle(camId);
    }
    if (e.key === 'f' && !e.ctrlKey && !e.metaKey) {
        toggleFullscreen();
    }
});

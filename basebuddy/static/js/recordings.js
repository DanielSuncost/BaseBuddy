/**
 * Recordings Page — playback, view modes, and range filters.
 */

function playRecording(videoPath) {
    const videoPlayer = document.getElementById('videoPlayer');
    const videoContainer = document.getElementById('videoContainer');
    const nowPlaying = document.getElementById('nowPlaying');
    if (!videoPlayer || !videoContainer) return;

    const ext = (videoPath.split('.').pop() || '').toLowerCase();
    const typeMap = {
        mp4: 'video/mp4',
        webm: 'video/webm',
        avi: 'video/x-msvideo',
    };
    videoPlayer.src = videoPath;
    if (typeMap[ext]) {
        videoPlayer.type = typeMap[ext];
    }
    videoPlayer.load();
    videoContainer.style.display = 'block';
    nowPlaying.textContent = 'Playing: ' + videoPath.split('/').pop();

    videoPlayer.scrollIntoView({ behavior: 'smooth', block: 'center' });
    videoPlayer.play().catch(() => {});
}

function closePlayer() {
    const videoPlayer = document.getElementById('videoPlayer');
    const videoContainer = document.getElementById('videoContainer');
    if (!videoPlayer || !videoContainer) return;
    videoPlayer.pause();
    videoPlayer.src = '';
    videoContainer.style.display = 'none';
}

function buildRecordingsUrl(opts) {
    const toolbar = document.getElementById('recToolbar');
    const params = new URLSearchParams();

    const view = opts.view || toolbar?.dataset.view || 'clips';
    const range = opts.range || toolbar?.dataset.range || 'today';
    const from = opts.from || toolbar?.dataset.from;
    const to = opts.to || toolbar?.dataset.to;
    const cam = opts.cam !== undefined ? opts.cam : toolbar?.dataset.cam;

    params.set('view', view);
    params.set('range', range);
    if (range === 'custom' && from && to) {
        params.set('from', from);
        params.set('to', to);
    }
    if (cam !== undefined && cam !== null && cam !== '') {
        params.set('cam', String(cam));
    }
    return '/recordings?' + params.toString();
}

function navigateRecordings(opts) {
    window.location.href = buildRecordingsUrl(opts);
}

function initRecordingsToolbar() {
    const toolbar = document.getElementById('recToolbar');
    if (!toolbar) return;

    const rangeSelect = document.getElementById('recRangePreset');
    const customPanel = document.getElementById('recCustomRange');
    const dateFrom = document.getElementById('recDateFrom');
    const dateTo = document.getElementById('recDateTo');
    const applyCustom = document.getElementById('recApplyCustom');
    const clearCam = document.getElementById('recClearCamFilter');

    toolbar.querySelectorAll('.rec-view-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.view;
            if (view === toolbar.dataset.view) return;
            navigateRecordings({ view });
        });
    });

    if (rangeSelect) {
        rangeSelect.addEventListener('change', () => {
            const range = rangeSelect.value;
            if (range === 'custom') {
                customPanel?.classList.remove('hidden');
                return;
            }
            customPanel?.classList.add('hidden');
            navigateRecordings({ range });
        });
    }

    if (applyCustom && dateFrom && dateTo) {
        applyCustom.addEventListener('click', () => {
            if (!dateFrom.value || !dateTo.value) return;
            navigateRecordings({
                range: 'custom',
                from: dateFrom.value,
                to: dateTo.value,
            });
        });
    }

    if (clearCam) {
        clearCam.addEventListener('click', (e) => {
            e.preventDefault();
            navigateRecordings({ cam: '' });
        });
    }
}

document.addEventListener('DOMContentLoaded', initRecordingsToolbar);

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePlayer();
});

const videoPlayer = document.getElementById('videoPlayer');
if (videoPlayer) {
    videoPlayer.addEventListener('error', () => {
        alert('Unable to play this recording in browser. Check codec/container compatibility.');
    });
}

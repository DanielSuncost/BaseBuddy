/**
 * Timelapse Page JavaScript
 * Handles frame browsing, selection, timelapse creation, and scheduling
 */

// State
let allImages = [];  // All images from API (most recent first)
let images = [];     // Paginated images for current page
let selectedImages = new Set();
let currentPage = 1;
const IMAGES_PER_PAGE = 56;
let showMasksMode = false;

// ============================================================
// Initialization
// ============================================================

document.addEventListener('DOMContentLoaded', function() {
    // Set default date range (last 7 days)
    const today = new Date();
    const weekAgo = new Date(today - 7 * 24 * 60 * 60 * 1000);
    document.getElementById('endDate').value = today.toISOString().split('T')[0];
    document.getElementById('startDate').value = weekAgo.toISOString().split('T')[0];
    document.getElementById('startTime').value = '00:00';
    document.getElementById('endTime').value = '23:59';
    
    // Initial load
    loadImages();
    
    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closePreview();
            closeVideoPlayer();
            closeShareModal();
            closeSaveScheduleModal();
        }
        if (e.key === 'a' && e.ctrlKey) {
            e.preventDefault();
            selectAllInRange();
        }
    });
});

// ============================================================
// Tab Switching
// ============================================================

function switchTab(tabName) {
    console.log('switchTab:', tabName);
    
    // Cancel any pending timelapse modal timeout
    if (window.pendingTimelapseShowTimeout) {
        clearTimeout(window.pendingTimelapseShowTimeout);
        window.pendingTimelapseShowTimeout = null;
    }
    
    // Close all modals
    closeVideoPlayer();
    closeShareModal();
    closePreview();
    
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Show selected tab
    if (tabName === 'browser') {
        document.getElementById('browserTab').classList.add('active');
        document.querySelectorAll('.tab-btn')[0].classList.add('active');
    } else if (tabName === 'gallery') {
        const galleryTab = document.getElementById('galleryTab');
        if (galleryTab) {
            galleryTab.classList.add('active');
            document.querySelectorAll('.tab-btn')[1].classList.add('active');
            loadTimelapseGallery();
        }
    } else if (tabName === 'schedules') {
        document.getElementById('schedulesTab').classList.add('active');
        document.querySelectorAll('.tab-btn')[2].classList.add('active');
        loadSchedulesView();
    }
}

// ============================================================
// Date Quick Selectors
// ============================================================

function selectToday() {
    const now = new Date();
    const dateStr = now.toISOString().split('T')[0];
    document.getElementById('startDate').value = dateStr;
    document.getElementById('endDate').value = dateStr;
    document.getElementById('startTime').value = '00:00';
    document.getElementById('endTime').value = '23:59';
    loadImages();
}

function selectYesterday() {
    const now = new Date();
    now.setDate(now.getDate() - 1);
    const dateStr = now.toISOString().split('T')[0];
    document.getElementById('startDate').value = dateStr;
    document.getElementById('endDate').value = dateStr;
    document.getElementById('startTime').value = '00:00';
    document.getElementById('endTime').value = '23:59';
    loadImages();
}

// ============================================================
// Filters
// ============================================================

function updateBrightnessLabel() {
    const slider = document.getElementById('brightnessThreshold');
    const label = document.getElementById('brightnessLabel');
    if (slider && label) {
        label.textContent = slider.value + '%';
    }
}

function toggleDarkFrameFilter() {
    const checkbox = document.getElementById('excludeDarkFrames');
    const container = document.getElementById('brightnessThresholdContainer');
    
    if (checkbox && container) {
        container.style.display = checkbox.checked ? 'block' : 'none';
    }
    
    loadImages();
}

function toggleDailyTimeRange() {
    const checkbox = document.getElementById('applyDailyTimeRange');
    const container = document.getElementById('dailyTimeRangeContainer');
    
    if (checkbox && container) {
        container.style.display = checkbox.checked ? 'block' : 'none';
    }
    
    loadImages();
}

// ============================================================
// Image Loading
// ============================================================

async function loadImages() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const startTime = document.getElementById('startTime').value || '00:00';
    const endTime = document.getElementById('endTime').value || '23:59';
    const excludeDark = document.getElementById('excludeDarkFrames')?.checked || false;
    const brightnessThreshold = parseInt(document.getElementById('brightnessThreshold')?.value || '10');
    const applyDailyTime = document.getElementById('applyDailyTimeRange')?.checked || false;
    const dailyStart = document.getElementById('dailyStartTime')?.value || '06:00';
    const dailyEnd = document.getElementById('dailyEndTime')?.value || '23:00';
    
    let url = '/api/timelapse/images';
    if (camId !== null) url += '/' + camId;
    url += '?start=' + startDate + '&end=' + endDate;
    url += '&startTime=' + startTime + '&endTime=' + endTime;
    
    if (excludeDark) {
        url += '&excludeDark=true&brightnessThreshold=' + brightnessThreshold;
    }
    
    if (applyDailyTime) {
        url += '&applyDailyTime=true&dailyStart=' + dailyStart + '&dailyEnd=' + dailyEnd;
    }
    
    console.log('Loading images with URL:', url);
    
    try {
        const resp = await fetch(url);
        const data = await resp.json();
        console.log('Received', data.images?.length || 0, 'images');
        
        // Reverse to show most recent first
        allImages = (data.images || []).reverse();
        currentPage = 1;
        updateStats();
        renderGallery();
        updateExportPreview();
    } catch (e) {
        console.error('Error loading images:', e);
    }
}

// ============================================================
// Statistics
// ============================================================

function updateStats() {
    document.getElementById('totalFrames').textContent = allImages.length;
    document.getElementById('selectedFrames').textContent = selectedImages.size;
    
    const avgInterval = calculateAverageInterval(allImages);
    document.getElementById('avgInterval').textContent = avgInterval ? formatInterval(avgInterval) : '-';
    
    const totalDuration = calculateTotalDuration(allImages);
    document.getElementById('totalDuration').textContent = totalDuration ? formatTimeDuration(totalDuration) : '-';
}

function calculateAverageInterval(imageList) {
    if (imageList.length < 2) return null;
    
    const timestamps = [];
    const chronological = [...imageList].reverse();
    
    for (const img of chronological) {
        if (img.datetime) {
            const timestamp = new Date(img.datetime).getTime() / 1000;
            timestamps.push(timestamp);
        }
    }
    
    if (timestamps.length < 2) return null;
    
    let totalInterval = 0;
    for (let i = 1; i < timestamps.length; i++) {
        totalInterval += timestamps[i] - timestamps[i-1];
    }
    return totalInterval / (timestamps.length - 1);
}

function calculateTotalDuration(imageList) {
    if (imageList.length < 2) return null;
    
    const timestamps = [];
    for (const img of imageList) {
        if (img.datetime) {
            const timestamp = new Date(img.datetime).getTime() / 1000;
            timestamps.push(timestamp);
        }
    }
    
    if (timestamps.length < 2) return null;
    
    const earliest = Math.min(...timestamps);
    const latest = Math.max(...timestamps);
    return latest - earliest;
}

function formatTimeDuration(seconds) {
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return mins + 'm ' + secs + 's';
    }
    const hours = Math.floor(seconds / 3600);
    const mins = Math.round((seconds % 3600) / 60);
    if (hours < 24) {
        return hours + 'h ' + mins + 'm';
    }
    const days = Math.floor(hours / 24);
    const remainingHours = hours % 24;
    return days + 'd ' + remainingHours + 'h';
}

function formatInterval(seconds) {
    if (seconds < 1) return (seconds * 1000).toFixed(0) + 'ms';
    if (seconds < 60) return seconds.toFixed(1) + 's';
    if (seconds < 3600) return (seconds / 60).toFixed(1) + 'm';
    return (seconds / 3600).toFixed(1) + 'h';
}

// ============================================================
// Export Preview
// ============================================================

function updateExportPreview() {
    const frameSkip = parseInt(document.getElementById('frameSkip').value) || 1;
    const fps = parseInt(document.getElementById('outputFps').value) || 15;
    
    const selectedList = allImages.filter(img => selectedImages.has(img.path));
    const avgInterval = calculateAverageInterval(selectedList);
    
    // Frame skip display with time interval
    let skipText = frameSkip === 1 ? 'Every frame' : 'Every ' + frameSkip + getSuffix(frameSkip);
    if (avgInterval && frameSkip > 1) {
        const effectiveInterval = avgInterval * frameSkip;
        skipText += ' (~' + formatInterval(effectiveInterval) + ')';
    }
    document.getElementById('frameSkipValue').textContent = skipText;
    
    const selectedCount = selectedImages.size;
    const afterSkip = Math.ceil(selectedCount / frameSkip);
    const duration = afterSkip / fps;
    
    document.getElementById('previewAfterSkip').textContent = afterSkip;
    document.getElementById('previewDuration').textContent = formatDuration(duration);
    
    // Enable/disable export buttons
    const canExport = selectedCount >= 2;
    document.getElementById('exportMp4Btn').disabled = !canExport;
    document.getElementById('exportGifBtn').disabled = !canExport;
    document.getElementById('saveScheduleBtn').disabled = !canExport;
    
    renderFilmstrip();
    updateStats();
}

function getSuffix(n) {
    if (n === 1) return 'st';
    if (n === 2) return 'nd';
    if (n === 3) return 'rd';
    return 'th';
}

function formatDuration(seconds) {
    if (seconds < 1) return (seconds * 1000).toFixed(0) + 'ms';
    if (seconds < 60) return seconds.toFixed(1) + 's';
    const mins = Math.floor(seconds / 60);
    const secs = (seconds % 60).toFixed(0);
    return mins + 'm ' + secs + 's';
}

// ============================================================
// Filmstrip
// ============================================================

function renderFilmstrip() {
    const container = document.getElementById('filmstrip');
    const framesDiv = document.getElementById('filmstripFrames');
    const frameSkip = parseInt(document.getElementById('frameSkip').value) || 1;
    
    const selectedList = allImages.filter(img => selectedImages.has(img.path)).reverse();
    
    if (selectedList.length < 2) {
        container.style.display = 'none';
        return;
    }
    
    container.style.display = 'block';
    
    const maxPreview = 20;
    let html = '';
    for (let i = 0; i < Math.min(selectedList.length, maxPreview * frameSkip); i++) {
        const isIncluded = i % frameSkip === 0;
        const img = selectedList[i];
        html += `
            <div class="filmstrip-frame ${isIncluded ? 'included' : 'skipped'}">
                <img src="${img.url}" alt="">
                <span class="frame-num">${i + 1}</span>
            </div>
        `;
    }
    if (selectedList.length > maxPreview * frameSkip) {
        html += `<div style="color:#666; padding:10px; font-size:11px;">...+${selectedList.length - maxPreview * frameSkip} more</div>`;
    }
    framesDiv.innerHTML = html;
}

// ============================================================
// Selection
// ============================================================

function selectAllInRange() {
    selectedImages.clear();
    allImages.forEach(img => selectedImages.add(img.path));
    renderGallery();
}

function selectPage() {
    images.forEach(img => selectedImages.add(img.path));
    renderGallery();
}

function invertSelection() {
    allImages.forEach(img => {
        if (selectedImages.has(img.path)) {
            selectedImages.delete(img.path);
        } else {
            selectedImages.add(img.path);
        }
    });
    renderGallery();
}

function clearSelection() {
    selectedImages.clear();
    renderGallery();
}

function toggleSelect(path) {
    if (selectedImages.has(path)) {
        selectedImages.delete(path);
    } else {
        selectedImages.add(path);
    }
    renderGallery();
}

// ============================================================
// Pagination
// ============================================================

function getTotalPages() {
    return Math.ceil(allImages.length / IMAGES_PER_PAGE);
}

function getPageImages() {
    const start = (currentPage - 1) * IMAGES_PER_PAGE;
    const end = start + IMAGES_PER_PAGE;
    return allImages.slice(start, end);
}

function goToPage(page) {
    const totalPages = getTotalPages();
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    currentPage = page;
    renderGallery();
}

function renderPagination() {
    const totalPages = getTotalPages();
    const totalImages = allImages.length;
    
    if (totalImages === 0) {
        document.getElementById('pagination').innerHTML = '';
        return;
    }
    
    const start = (currentPage - 1) * IMAGES_PER_PAGE + 1;
    const end = Math.min(currentPage * IMAGES_PER_PAGE, totalImages);
    
    let html = `
        <button class="page-btn" onclick="goToPage(1)" ${currentPage === 1 ? 'disabled' : ''}>⏮</button>
        <button class="page-btn" onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>◀</button>
        <span class="page-info">Page ${currentPage} of ${totalPages} (${start}-${end})</span>
        <button class="page-btn" onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>▶</button>
        <button class="page-btn" onclick="goToPage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''}>⏭</button>
    `;
    
    if (totalPages > 1 && totalPages <= 10) {
        html += '<div style="display:flex; gap:4px; margin-left:8px;">';
        for (let p = 1; p <= totalPages; p++) {
            html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" onclick="goToPage(${p})">${p}</button>`;
        }
        html += '</div>';
    }
    
    document.getElementById('pagination').innerHTML = html;
    document.getElementById('browseInfo').textContent = `(showing ${start}-${end} of ${totalImages})`;
}

// ============================================================
// Gallery Rendering
// ============================================================

function toggleMaskView() {
    showMasksMode = document.getElementById('showMasks')?.checked || false;
    renderGallery();
}

function getMaskUrl(imageUrl, filename) {
    if (!camId) return null;
    const cameraId = 'camera_' + camId;
    return `/api/plant-tracking/${cameraId}/mask/${filename}`;
}

function renderGallery() {
    const gallery = document.getElementById('gallery');
    images = getPageImages();
    
    if (allImages.length === 0) {
        gallery.innerHTML = `
            <div class="empty-state">
                <div class="icon"><span class="material-icons-outlined">image_not_supported</span></div>
                <p>No timelapse images found for this date/time range</p>
                <p style="font-size:13px;">Adjust date/time range or enable timelapse capture</p>
            </div>
        `;
        document.getElementById('pagination').innerHTML = '';
        document.getElementById('browseInfo').textContent = '';
        return;
    }
    
    gallery.innerHTML = images.map((img, i) => {
        const filename = img.url.split('/').pop();
        const maskUrl = showMasksMode && camId !== null ? getMaskUrl(img.url, filename) : null;
        const displayUrl = maskUrl || img.url;
        
        const maskBadge = showMasksMode ? 
            '<div style="position:absolute; top:5px; left:5px; background:rgba(16,185,129,0.9); color:#fff; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:600;"><span class="material-icons-outlined" style="font-size:10px; vertical-align:middle;">nature</span> MASK</div>' : 
            '';
        
        return `
            <div class="gallery-item ${selectedImages.has(img.path) ? 'selected' : ''}" 
                 onclick="toggleSelect('${img.path}')" ondblclick="previewImage('${displayUrl}')">
                <img src="${displayUrl}" loading="lazy" alt="${img.timestamp}" 
                     onerror="this.src='${img.url}'; this.style.opacity='0.5';">
                <div class="timestamp">${img.timestamp}</div>
                <div class="check-badge"><span class="material-icons-outlined">check</span></div>
                ${maskBadge}
            </div>
        `;
    }).join('');
    
    renderPagination();
    updateStats();
    updateExportPreview();
}

// ============================================================
// Preview Modal
// ============================================================

function previewImage(url) {
    document.getElementById('previewImage').src = url;
    document.getElementById('previewModal').style.display = 'flex';
}

function closePreview() {
    document.getElementById('previewModal').style.display = 'none';
}

// ============================================================
// Timelapse Creation
// ============================================================

async function createTimelapse(format) {
    if (selectedImages.size < 2) {
        alert('Please select at least 2 images');
        return;
    }
    
    const frameSkip = parseInt(document.getElementById('frameSkip').value) || 1;
    const fps = parseInt(document.getElementById('outputFps').value) || 15;
    
    const selectedList = allImages.filter(img => selectedImages.has(img.path)).reverse();
    
    // Apply frame skip
    const imagesToUse = [];
    for (let i = 0; i < selectedList.length; i += frameSkip) {
        imagesToUse.push(selectedList[i].path);
    }
    
    if (imagesToUse.length < 2) {
        alert('After frame skip, only ' + imagesToUse.length + ' frame(s) remain. Need at least 2.');
        return;
    }
    
    const btn = document.getElementById(format === 'gif' ? 'exportGifBtn' : 'exportMp4Btn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    
    // Show progress bar
    const progressDiv = document.getElementById('exportProgress');
    const progressBar = document.getElementById('exportProgressBar');
    const progressText = document.getElementById('exportProgressText');
    const progressPercent = document.getElementById('exportProgressPercent');
    const etaText = document.getElementById('exportEta');
    
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';
    progressPercent.textContent = '0%';
    progressText.textContent = `Creating ${format.toUpperCase()}...`;
    
    const framesPerSec = format === 'gif' ? 8 : 15;
    const estimatedSeconds = Math.ceil(imagesToUse.length / framesPerSec);
    etaText.textContent = `Processing ${imagesToUse.length} frames (est. ~${estimatedSeconds}s)`;
    
    const startTime = Date.now();
    let pollInterval;
    
    pollInterval = setInterval(() => {
        const elapsed = (Date.now() - startTime) / 1000;
        const progress = Math.min(95, (elapsed / estimatedSeconds) * 100);
        progressBar.style.width = progress + '%';
        progressPercent.textContent = Math.round(progress) + '%';
        
        const remaining = Math.max(0, estimatedSeconds - elapsed);
        etaText.textContent = remaining > 0 ? 
            `Est. ${Math.ceil(remaining)}s remaining...` : 
            'Finalizing...';
    }, 300);
    
    try {
        const addProgressMeter = document.getElementById('addProgressMeter').checked;
        const addClockFace = document.getElementById('addClockFace').checked;
        
        const resp = await fetch('/api/timelapse/create', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                images: imagesToUse,
                format: format,
                fps: fps,
                add_progress_meter: addProgressMeter,
                add_clock_face: addClockFace
            })
        });
        const data = await resp.json();
        
        clearInterval(pollInterval);
        progressBar.style.width = '100%';
        progressPercent.textContent = '100%';
        
        if (data.ok) {
            const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
            etaText.textContent = `Complete! (${elapsed}s)`;
            progressText.textContent = `${format.toUpperCase()} created successfully`;
            
            window.pendingTimelapseShowTimeout = setTimeout(() => {
                progressDiv.style.display = 'none';
                
                const browserTab = document.getElementById('browserTab');
                if (!browserTab || !browserTab.classList.contains('active')) {
                    return;
                }
                
                playTimelapse(data.download_url, data.filename);
            }, 1000);
        } else {
            progressText.textContent = 'Error creating ' + format;
            etaText.textContent = data.error;
            progressBar.style.background = '#ef4444';
            setTimeout(() => { progressDiv.style.display = 'none'; }, 5000);
            alert('Error: ' + data.error);
        }
    } catch (e) {
        clearInterval(pollInterval);
        progressText.textContent = 'Error';
        etaText.textContent = e.message;
        progressBar.style.background = '#ef4444';
        setTimeout(() => { progressDiv.style.display = 'none'; }, 5000);
        alert('Error creating timelapse: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

// ============================================================
// Video Player Modal
// ============================================================

function playTimelapse(path, name) {
    console.log('playTimelapse:', name, path);
    
    const modal = document.getElementById('videoPlayerModal');
    const video = document.getElementById('timelapseVideo');
    const gifImg = document.getElementById('timelapseGif');
    const info = document.getElementById('videoInfo');
    const downloadBtn = document.getElementById('downloadTimelapseBtn');
    
    const isGif = path.toLowerCase().endsWith('.gif');
    
    if (isGif) {
        video.style.display = 'none';
        gifImg.style.display = 'block';
        gifImg.src = path;
        video.src = '';
    } else {
        gifImg.style.display = 'none';
        video.style.display = 'block';
        gifImg.src = '';
        video.src = path;
        video.load();
        video.onloadeddata = () => {
            video.play().catch(err => {
                console.error('Error playing video:', err);
            });
        };
    }
    
    info.textContent = name || 'Timelapse';
    downloadBtn.href = path;
    downloadBtn.download = name || 'timelapse';
    
    modal.classList.add('modal-visible');
}

function closeVideoPlayer() {
    const modal = document.getElementById('videoPlayerModal');
    const video = document.getElementById('timelapseVideo');
    
    modal.classList.remove('modal-visible');
    
    if (video) {
        video.pause();
        video.src = '';
    }
}

// ============================================================
// Timelapse Gallery Tab
// ============================================================

async function loadTimelapseGallery() {
    const grid = document.getElementById('timelapseGalleryGrid');
    
    if (!grid) return;
    
    try {
        const url = '/api/timelapse/gallery' + (camId !== null ? `?cam_id=${camId}` : '');
        const resp = await fetch(url);
        const data = await resp.json();
        
        if (!data.ok) {
            throw new Error(data.error || 'API error');
        }
        
        if (data.timelapses && data.timelapses.length > 0) {
            grid.innerHTML = data.timelapses.map((tl, idx) => {
                const isGif = tl.path.toLowerCase().endsWith('.gif');
                const thumbnailContent = isGif ? `
                    <img src="${tl.path}" style="width:100%; height:100%; object-fit:contain;">
                ` : `
                    <video src="${tl.path}" style="width:100%; height:100%; object-fit:contain;" 
                           muted preload="metadata" onloadedmetadata="this.currentTime = 0.5"></video>
                `;
                
                return `
                    <div class="timelapse-item" data-path="${tl.path}" data-name="${tl.name}"
                         style="background:white; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1); cursor:pointer; transition:transform 0.2s;"
                         onmouseenter="this.style.transform='scale(1.02)'"
                         onmouseleave="this.style.transform='scale(1)'"
                         onclick="playTimelapse('${tl.path}', '${tl.name}')">
                        <div style="position:relative; aspect-ratio:16/9; background:#1a1a1a; overflow:hidden;">
                            ${thumbnailContent}
                            <div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; background:rgba(0,0,0,0.4);">
                                <div style="background:rgba(255,255,255,0.95); border-radius:50%; width:64px; height:64px; display:flex; align-items:center; justify-content:center;">
                                    <span class="material-icons-outlined" style="font-size:36px; color:#1a73e8; margin-left:4px;">play_arrow</span>
                                </div>
                            </div>
                        </div>
                        <div style="padding:12px;">
                            <div style="font-weight:600; color:#333; margin-bottom:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${tl.name}</div>
                            <div style="font-size:12px; color:#888; display:flex; justify-content:space-between;">
                                <span>${tl.created}</span>
                                <span>${tl.size}</span>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="icon"><span class="material-icons-outlined">movie_filter</span></div>
                    <p>No timelapses created yet</p>
                    <small style="color:#888;">Export a timelapse from the Frame Browser to get started</small>
                </div>
            `;
        }
    } catch (e) {
        console.error('Error loading gallery:', e);
        grid.innerHTML = `
            <div class="empty-state">
                <div class="icon"><span class="material-icons-outlined" style="color:#ef4444;">error</span></div>
                <p style="color:#ef4444;">Error loading timelapses</p>
                <small style="color:#888;">${e.message}</small>
                <button onclick="loadTimelapseGallery()" style="margin-top:12px; padding:8px 16px; background:#3b82f6; color:white; border:none; border-radius:6px; cursor:pointer;">Retry</button>
            </div>
        `;
    }
}

function refreshGalleryView() {
    loadTimelapseGallery();
}

// ============================================================
// Schedules Tab
// ============================================================

async function loadSchedulesView() {
    const grid = document.getElementById('schedulesGrid');
    
    try {
        const resp = await fetch('/api/timelapse/schedule');
        const data = await resp.json();
        
        if (!data.ok) {
            throw new Error(data.error || 'API error');
        }
        
        if (data.schedules && data.schedules.length > 0) {
            grid.innerHTML = data.schedules.map(schedule => {
                const createdDate = new Date(schedule.created_at).toLocaleDateString();
                const cameraNames = schedule.camera_ids && schedule.camera_ids.length > 0
                    ? schedule.camera_ids.map(id => `Camera ${id + 1}`).join(', ')
                    : 'All Cameras';
                
                return `
                    <div style="background:#fff; border-radius:12px; border:1px solid #e5e7eb; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                        <div style="background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding:20px; color:white;">
                            <div style="display:flex; justify-content:space-between; align-items:start;">
                                <div>
                                    <div style="font-size:18px; font-weight:600; margin-bottom:4px;">${schedule.name || cameraNames}</div>
                                    <div class="timelapse-camera-names">${cameraNames}</div>
                                </div>
                                <button onclick="deleteSchedule(${schedule.id})" style="padding:8px 14px; background:rgba(239,68,68,0.8); color:white; border:none; border-radius:8px; cursor:pointer; font-size:13px;">
                                    <span class="material-icons-outlined" style="font-size:16px;">delete</span> Delete
                                </button>
                            </div>
                        </div>
                        <div style="padding:20px;">
                            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:12px;">
                                <div style="padding:12px; background:#f9fafb; border-radius:8px;">
                                    <div style="font-size:11px; color:#9ca3af; text-transform:uppercase;">Run Time</div>
                                    <div style="font-size:16px; font-weight:700; color:#111827;">${schedule.time}</div>
                                    <div style="font-size:11px; color:#6b7280;">Every ${schedule.interval_hours || 24}h</div>
                                </div>
                                <div style="padding:12px; background:#f9fafb; border-radius:8px;">
                                    <div style="font-size:11px; color:#9ca3af; text-transform:uppercase;">Window</div>
                                    <div style="font-size:16px; font-weight:700; color:#111827;">Last ${schedule.window_hours}h</div>
                                </div>
                                <div style="padding:12px; background:#f9fafb; border-radius:8px;">
                                    <div style="font-size:11px; color:#9ca3af; text-transform:uppercase;">Settings</div>
                                    <div style="font-size:16px; font-weight:700; color:#111827;">${schedule.fps}fps ${(schedule.format || 'mp4').toUpperCase()}</div>
                                </div>
                            </div>
                            <div style="margin-top:12px; font-size:12px; color:#6b7280;">
                                Created: ${createdDate}
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="icon"><span class="material-icons-outlined">event_available</span></div>
                    <p>No schedules created yet</p>
                    <small style="color:#888;">Click "New Schedule" to create your first automated timelapse</small>
                </div>
            `;
        }
    } catch (e) {
        console.error('Error loading schedules:', e);
        grid.innerHTML = `
            <div class="empty-state">
                <div class="icon"><span class="material-icons-outlined" style="color:#ef4444;">error</span></div>
                <p style="color:#ef4444;">Error loading schedules</p>
                <button onclick="loadSchedulesView()" style="margin-top:12px; padding:8px 16px; background:#3b82f6; color:white; border:none; border-radius:6px; cursor:pointer;">Retry</button>
            </div>
        `;
    }
}

function refreshSchedulesView() {
    loadSchedulesView();
}

async function deleteSchedule(scheduleId) {
    if (!confirm('Delete this schedule?')) return;
    
    try {
        const resp = await fetch('/api/timelapse/schedule?id=' + scheduleId, { method: 'DELETE' });
        const result = await resp.json();
        
        if (result.ok) {
            loadSchedulesView();
        } else {
            alert('Error: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Error deleting schedule: ' + e.message);
    }
}

// ============================================================
// Schedule Modal
// ============================================================

function openSaveScheduleModal() {
    document.getElementById('saveScheduleModal').style.display = 'flex';
}

function closeSaveScheduleModal() {
    document.getElementById('saveScheduleModal').style.display = 'none';
}

async function confirmSaveSchedule() {
    const name = document.getElementById('newScheduleName').value || 'New Schedule';
    const windowHours = parseInt(document.getElementById('newScheduleTimeWindow').value) || 24;
    const intervalHours = parseInt(document.getElementById('newScheduleInterval').value) || 24;
    const frameSkip = parseInt(document.getElementById('newScheduleFrameSkip').value) || 1;
    const fps = parseInt(document.getElementById('newScheduleFps').value) || 15;
    const format = document.getElementById('newScheduleFormat').value || 'mp4';
    const addProgressMeter = document.getElementById('newScheduleProgressMeter').checked;
    const addClockFace = document.getElementById('newScheduleClockFace').checked;
    
    const scheduleData = {
        name: name,
        camera_ids: camId !== null ? [camId] : [],
        time: '00:00',
        window_hours: windowHours,
        interval_hours: intervalHours,
        frame_skip: frameSkip,
        fps: fps,
        format: format,
        add_progress_meter: addProgressMeter,
        add_clock_face: addClockFace
    };
    
    try {
        const resp = await fetch('/api/timelapse/schedule', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(scheduleData)
        });
        const result = await resp.json();
        
        if (result.ok) {
            closeSaveScheduleModal();
            alert('Schedule created successfully!');
            switchTab('schedules');
        } else {
            alert('Error: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Error creating schedule: ' + e.message);
    }
}

// ============================================================
// Share Modal
// ============================================================

function openShareModal() {
    document.getElementById('shareModal').style.display = 'flex';
    const downloadBtn = document.getElementById('downloadTimelapseBtn');
    document.getElementById('shareFileInfo').textContent = downloadBtn.download || 'Timelapse file';
}

function closeShareModal() {
    document.getElementById('shareModal').style.display = 'none';
}

function shareToTwitter() {
    const caption = document.getElementById('shareCaption').value || '';
    const encodedCaption = encodeURIComponent(caption);
    window.open(`https://twitter.com/intent/tweet?text=${encodedCaption}`, '_blank');
    
    // Also trigger download
    document.getElementById('downloadTimelapseBtn').click();
}

async function shareNative() {
    const downloadBtn = document.getElementById('downloadTimelapseBtn');
    const url = downloadBtn.href;
    const filename = downloadBtn.download;
    
    try {
        const response = await fetch(url);
        const blob = await response.blob();
        const file = new File([blob], filename, { type: blob.type });
        
        if (navigator.share && navigator.canShare({ files: [file] })) {
            await navigator.share({
                files: [file],
                title: 'Timelapse',
                text: document.getElementById('shareCaption').value || ''
            });
        } else {
            alert('Native sharing not supported. Use Download instead.');
        }
    } catch (e) {
        console.error('Share error:', e);
        alert('Error sharing: ' + e.message);
    }
}

function copyShareLink() {
    const downloadBtn = document.getElementById('downloadTimelapseBtn');
    const url = window.location.origin + downloadBtn.href;
    
    navigator.clipboard.writeText(url).then(() => {
        alert('Link copied to clipboard!');
    }).catch(err => {
        prompt('Copy this link:', url);
    });
}

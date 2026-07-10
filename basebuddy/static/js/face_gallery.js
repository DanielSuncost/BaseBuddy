let currentFaces = [];
let currentPage = 1;
const facesPerPage = 50;

async function loadFaceGallery(page = 1) {
    currentPage = page;
    const container = document.getElementById('face-gallery');

    container.innerHTML = '<div class="text-center py-5"><div class="spinner-border" role="status"></div><p class="mt-2">Loading faces...</p></div>';

    try {
        const response = await fetch(`/api/people/face_gallery?page=${page}&per_page=${facesPerPage}`);
        const data = await response.json();

        currentFaces = data.faces;

        if (data.faces.length === 0) {
            container.innerHTML = `
                <div class="text-center py-5">
                    <p class="text-muted">No faces found yet.</p>
                    <button class="btn btn-primary" onclick="scanForNewFaces()">Scan for Faces</button>
                </div>
            `;
            return;
        }

        // Create gallery grid
        let galleryHTML = '<div class="face-gallery-grid">';
        data.faces.forEach(face => {
            const statusClass = face.has_embedding ? 'has-embedding' : 'no-embedding';
            const statusText = face.has_embedding ? '✓ Processed' : '⚠ Not Processed';

            galleryHTML += `
                <div class="face-gallery-item ${statusClass}" data-face-id="${face.id}">
                    <input type="checkbox" class="face-checkbox" data-face-id="${face.id}" onchange="updateSelection()" onclick="event.stopPropagation()">
                    <div class="face-gallery-item-content" onclick="openFaceModal(${face.id}, '${face.image_url}', '${face.timestamp}', ${face.camera_id}, ${face.confidence || 0}, ${face.has_embedding})">
                        <img src="${face.thumbnail_url}" alt="Face detection" class="face-gallery-thumb">
                        <div class="face-gallery-overlay">
                            <small class="face-gallery-status">${statusText}</small>
                            <small class="face-gallery-time">${new Date(face.timestamp).toLocaleTimeString()}</small>
                        </div>
                    </div>
                </div>
            `;
        });
        galleryHTML += '</div>';

        // Calculate display range
        const startFace = (page - 1) * facesPerPage + 1;
        const endFace = Math.min(page * facesPerPage, data.total_faces);
        const displayInfo = `Showing faces ${startFace}-${endFace} of ${data.total_faces}`;

        // Add pagination with info and navigation
        let paginationHTML = '';
        if (data.total_pages > 0) {
            paginationHTML += `
                <div class="d-flex justify-content-between align-items-center mt-4 mb-3">
                    <div class="text-muted">
                        <strong>${displayInfo}</strong>
                    </div>
                    <nav>
                        <ul class="pagination mb-0">
            `;

            // Previous button
            const prevDisabled = page <= 1 ? 'disabled' : '';
            paginationHTML += `
                <li class="page-item ${prevDisabled}">
                    <a class="page-link" href="#" onclick="loadFaceGallery(${page - 1}); return false;" ${prevDisabled ? 'tabindex="-1" aria-disabled="true"' : ''}>
                        <span aria-hidden="true">&laquo;</span> Previous
                    </a>
                </li>
            `;

            // Page numbers (show up to 7 pages around current)
            const maxPagesToShow = 7;
            let startPage = Math.max(1, page - Math.floor(maxPagesToShow / 2));
            let endPage = Math.min(data.total_pages, startPage + maxPagesToShow - 1);
            
            // Adjust if we're near the end
            if (endPage - startPage < maxPagesToShow - 1) {
                startPage = Math.max(1, endPage - maxPagesToShow + 1);
            }

            // First page if not in range
            if (startPage > 1) {
                paginationHTML += `<li class="page-item"><a class="page-link" href="#" onclick="loadFaceGallery(1); return false;">1</a></li>`;
                if (startPage > 2) {
                    paginationHTML += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
                }
            }

            // Page numbers
            for (let i = startPage; i <= endPage; i++) {
                const activeClass = i === page ? 'active' : '';
                paginationHTML += `<li class="page-item ${activeClass}"><a class="page-link" href="#" onclick="loadFaceGallery(${i}); return false;">${i}</a></li>`;
            }

            // Last page if not in range
            if (endPage < data.total_pages) {
                if (endPage < data.total_pages - 1) {
                    paginationHTML += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
                }
                paginationHTML += `<li class="page-item"><a class="page-link" href="#" onclick="loadFaceGallery(${data.total_pages}); return false;">${data.total_pages}</a></li>`;
            }

            // Next button
            const nextDisabled = page >= data.total_pages ? 'disabled' : '';
            paginationHTML += `
                <li class="page-item ${nextDisabled}">
                    <a class="page-link" href="#" onclick="loadFaceGallery(${page + 1}); return false;" ${nextDisabled ? 'tabindex="-1" aria-disabled="true"' : ''}>
                        Next <span aria-hidden="true">&raquo;</span>
                    </a>
                </li>
            `;

            paginationHTML += `
                        </ul>
                    </nav>
                </div>
            `;
        } else {
            paginationHTML += `
                <div class="text-center mt-4 text-muted">
                    <strong>No faces found</strong>
                </div>
            `;
        }

        container.innerHTML = galleryHTML + paginationHTML;

    } catch (error) {
        container.innerHTML = `<div class="alert alert-danger">Error loading faces: ${error.message}</div>`;
    }
}

let currentModalFaceId = null;

// Helper function to close modals (works with or without Bootstrap)
function closeModal(modalId) {
    const modalEl = document.getElementById(modalId);
    if (!modalEl) return;
    
    if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) {
            modal.hide();
        } else {
            modalEl.style.display = 'none';
            modalEl.classList.remove('show');
        }
    } else {
        modalEl.style.display = 'none';
        modalEl.classList.remove('show');
    }
}

// Helper function to show modals
function showModal(modalId) {
    const modalEl = document.getElementById(modalId);
    if (!modalEl) return;
    
    if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } else {
        modalEl.style.display = 'block';
        modalEl.classList.add('show');
    }
}

function openFaceModal(faceId, imageUrl, timestamp, cameraId, confidence, hasEmbedding) {
    currentModalFaceId = faceId;
    document.getElementById('faceModalImg').src = imageUrl;
    document.getElementById('faceModalTitle').textContent = `Face Detection - Camera ${cameraId}`;
    document.getElementById('faceModalCamera').textContent = cameraId;
    document.getElementById('faceModalTime').textContent = new Date(timestamp).toLocaleString();
    document.getElementById('faceModalConfidence').textContent = (confidence * 100).toFixed(1) + '%';
    document.getElementById('faceModalStatus').textContent = hasEmbedding ? 'Face processed' : 'Face not processed';

    // Store face ID for actions
    document.getElementById('faceModal').dataset.faceId = faceId;
    document.getElementById('faceModal').dataset.hasEmbedding = hasEmbedding;

    showModal('faceModal');
}

async function findSimilarFaces() {
    if (!currentModalFaceId) {
        alert('No face selected');
        return;
    }

    // Close the face modal first
    closeModal('faceModal');

    try {
        const threshold = 0.85; // Default threshold for single-face search
        const response = await fetch(`/api/people/find_similar_faces?face_id=${currentModalFaceId}&threshold=${threshold}`);
        const data = await response.json();

        if (data.error) {
            alert('❌ Error: ' + data.error);
            return;
        }

        // Display similar faces
        displaySimilarFaces(data.similar_faces, currentModalFaceId);
    } catch (error) {
        alert('❌ Error finding similar faces: ' + error.message);
    }
}

function displaySimilarFaces(similarFaces, referenceFaceId) {
    const container = document.getElementById('similarity-clusters-list');
    
    if (similarFaces.length === 0) {
        container.innerHTML = '<div class="col-12"><p class="text-muted text-center py-4">No similar faces found. Try adjusting the similarity threshold.</p></div>';
    } else {
        let html = `
            <div class="col-12 mb-3">
                <div class="alert alert-info">
                    Found <strong>${similarFaces.length}</strong> similar face${similarFaces.length !== 1 ? 's' : ''} to the selected image.
                    <button class="btn btn-sm btn-danger ms-2" onclick="deleteAllSimilarFaces()">Delete All Similar</button>
                    <button class="btn btn-sm btn-success ms-2" onclick="selectAllSimilarFaces()">Select All</button>
                </div>
            </div>
            <div class="col-12">
                <div class="face-gallery-grid">
        `;

        // Include the reference face first
        const allFaces = [{id: referenceFaceId, is_reference: true}, ...similarFaces];
        
        allFaces.forEach(face => {
            const isRef = face.is_reference || face.id === referenceFaceId;
            html += `
                <div class="face-gallery-item ${isRef ? 'reference-face' : ''}" data-face-id="${face.id}">
                    <input type="checkbox" class="similar-face-checkbox" data-face-id="${face.id}" ${isRef ? 'checked disabled' : ''} onchange="updateSimilarSelection()">
                    <div class="face-gallery-item-content" onclick="openFaceModal(${face.id}, '${face.image_url}', '${face.timestamp}', ${face.camera_id}, ${face.confidence || 0}, ${face.has_embedding})">
                        ${isRef ? '<div class="reference-badge">Reference</div>' : ''}
                        <img src="${face.thumbnail_url}" alt="Face" class="face-gallery-thumb">
                        <div class="face-gallery-overlay">
                            <small class="face-gallery-status">Similarity: ${(face.similarity * 100).toFixed(1)}%</small>
                        </div>
                    </div>
                </div>
            `;
        });

        html += `
                </div>
            </div>
        `;

        container.innerHTML = html;
        
        // Store similar faces for deletion
        window.similarFacesList = similarFaces.map(f => f.id);
        window.referenceFaceId = referenceFaceId;
    }

    // Switch to similarity tab to show results
    const similarityTab = document.getElementById('similarity-tab');
    if (similarityTab) {
        similarityTab.click();
    }
}

function selectAllSimilarFaces() {
    document.querySelectorAll('.similar-face-checkbox:not(:disabled)').forEach(cb => {
        cb.checked = true;
    });
    updateSimilarSelection();
}

function updateSimilarSelection() {
    const selected = Array.from(document.querySelectorAll('.similar-face-checkbox:checked:not(:disabled)')).map(cb => parseInt(cb.dataset.faceId));
    window.selectedSimilarFaces = selected;
}

async function deleteAllSimilarFaces() {
    if (!window.similarFacesList || window.similarFacesList.length === 0) {
        alert('No similar faces to delete');
        return;
    }

    if (!confirm(`Delete all ${window.similarFacesList.length} similar faces? The reference face will be kept.`)) {
        return;
    }

    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: window.similarFacesList})
        });

        const data = await response.json();
        if (data.ok) {
            alert(`✅ Deleted ${data.deleted_count} similar faces`);
            loadFaceGallery(currentPage); // Reload gallery
            // Reload similarity clusters if on that tab
            const similarityTab = document.getElementById('similarity-tab');
            if (similarityTab && similarityTab.classList.contains('active')) {
                loadSimilarityClusters();
            }
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

// Add click handlers to close modals when clicking outside
window.addEventListener('click', function(event) {
    const faceModal = document.getElementById('faceModal');
    
    if (event.target === faceModal) {
        closeModal('faceModal');
    }
});

// Scanning state
let scanStatusInterval = null;
let scanStatusDiv = null;
let galleryUpdateInterval = null;
let lastFaceCount = 0; // Track last known face count to detect new faces

async function scanForNewFaces() {
    if (!confirm('Scan recent person detections for faces? This may take a while. You can pause and resume anytime.')) return;

    const btn = event.target;
    const originalText = btn.innerText;
    
    try {
        const response = await fetch('/api/people/scan_faces', { method: 'POST' });
        const data = await response.json();

        if (data.ok) {
            btn.innerText = 'Scanning...';
            btn.disabled = true;
            
            // Create progress UI
            createScanProgressUI();
            
            // Start polling for status
            startScanStatusPolling();
        } else {
            if (data.error === 'Scan already in progress') {
                // Resume existing scan UI
                createScanProgressUI();
                startScanStatusPolling();
            } else {
                alert('Scan failed: ' + data.error);
            }
        }
    } catch (error) {
        alert('Scan error: ' + error.message);
    }
}

function createScanProgressUI() {
    // Remove existing progress UI if any
    const existing = document.getElementById('scan-progress-ui');
    if (existing) existing.remove();

    // Create progress container
    const container = document.querySelector('.container-fluid, .container') || document.body;
    const progressDiv = document.createElement('div');
    progressDiv.id = 'scan-progress-ui';
    progressDiv.className = 'alert alert-info mb-3';
    progressDiv.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <h5 class="mb-0">Face Scanning Progress</h5>
            <div>
                <button class="btn btn-sm btn-warning me-2" onclick="pauseScan()" id="pause-btn">⏸️ Pause</button>
                <button class="btn btn-sm btn-success me-2" onclick="resumeScan()" id="resume-btn" style="display:none">▶️ Resume</button>
                <button class="btn btn-sm btn-danger" onclick="stopScan()" id="stop-btn">🛑 Stop</button>
            </div>
        </div>
        <div class="progress mb-2" style="height: 25px;">
            <div class="progress-bar progress-bar-striped progress-bar-animated" 
                 role="progressbar" 
                 id="scan-progress-bar"
                 style="width: 0%">0%</div>
        </div>
        <div id="scan-status-text" class="small">Initializing...</div>
    `;
    
    // Insert after the first h2 or at the top
    const firstH2 = container.querySelector('h2');
    if (firstH2) {
        firstH2.parentNode.insertBefore(progressDiv, firstH2.nextSibling);
    } else {
        container.insertBefore(progressDiv, container.firstChild);
    }
    
    scanStatusDiv = progressDiv;
}

function startScanStatusPolling() {
    // Clear existing interval
    if (scanStatusInterval) {
        clearInterval(scanStatusInterval);
    }
    if (galleryUpdateInterval) {
        clearInterval(galleryUpdateInterval);
    }
    
    lastFaceCount = 0; // Reset face count tracker

    // Poll status every second
    scanStatusInterval = setInterval(updateScanStatus, 1000);
    updateScanStatus(); // Immediate update
    
    // Update gallery every 3 seconds while scanning (to show new faces)
    galleryUpdateInterval = setInterval(async () => {
        try {
            const response = await fetch('/api/people/scan_status');
            const status = await response.json();
            
            // Only update gallery if scanning is active
            if (status.active) {
                // Check if we're on the face gallery tab
                const activeTab = document.querySelector('.nav-tabs .nav-link.active');
                if (activeTab && (activeTab.textContent.includes('Face Gallery') || activeTab.getAttribute('data-tab') === 'all')) {
                    // Only reload if we're on page 1 (where new faces appear)
                    const currentPage = parseInt(document.querySelector('.pagination .page-item.active .page-link')?.textContent || '1');
                    if (currentPage === 1) {
                        loadFaceGallery(1);
                    }
                }
            }
        } catch (error) {
            console.error('Error updating gallery:', error);
        }
    }, 3000); // Update gallery every 3 seconds
}

async function updateScanStatus() {
    try {
        const response = await fetch('/api/people/scan_status');
        const status = await response.json();

        if (!scanStatusDiv) {
            if (status.active) {
                createScanProgressUI();
            } else {
                // Scanning finished, stop polling
                if (scanStatusInterval) {
                    clearInterval(scanStatusInterval);
                    scanStatusInterval = null;
                }
                return;
            }
        }

        const progress = status.progress || {processed: 0, total: 0, faces_found: 0};
        const percent = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;
        
        // Update progress bar
        const progressBar = document.getElementById('scan-progress-bar');
        if (progressBar) {
            progressBar.style.width = percent + '%';
            progressBar.textContent = `${percent}% (${progress.processed}/${progress.total})`;
        }

        // Update status text
        const statusText = document.getElementById('scan-status-text');
        if (statusText) {
            let statusMsg = `Processed ${progress.processed} of ${progress.total} images`;
            if (progress.faces_found > 0) {
                statusMsg += ` • Found ${progress.faces_found} faces`;
            }
            if (status.paused) {
                statusMsg += ' • ⏸️ PAUSED';
            } else if (status.active) {
                statusMsg += ' • 🔄 Scanning...';
            } else {
                statusMsg += ' • ✅ Complete';
            }
            statusText.textContent = statusMsg;
        }

        // Update button visibility
        const pauseBtn = document.getElementById('pause-btn');
        const resumeBtn = document.getElementById('resume-btn');
        const stopBtn = document.getElementById('stop-btn');
        
        if (pauseBtn && resumeBtn) {
            if (status.paused) {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'inline-block';
            } else if (status.active) {
                pauseBtn.style.display = 'inline-block';
                resumeBtn.style.display = 'none';
            } else {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'none';
            }
        }

        // Check if new faces were found and update gallery if on first page
        if (status.active && progress.faces_found > lastFaceCount) {
            const newFaces = progress.faces_found - lastFaceCount;
            lastFaceCount = progress.faces_found;
            
            // If we're on the face gallery tab and on page 1, reload to show new faces
            const activeTab = document.querySelector('.nav-tabs .nav-link.active');
            if (activeTab && (activeTab.textContent.includes('Face Gallery') || activeTab.getAttribute('data-tab') === 'all')) {
                const currentPage = parseInt(document.querySelector('.pagination .page-item.active .page-link')?.textContent || '1');
                if (currentPage === 1) {
                    // Small delay to ensure database commit completed
                    setTimeout(() => {
                        loadFaceGallery(1);
                    }, 500);
                }
            }
        } else if (status.active && lastFaceCount === 0) {
            // Initialize face count on first update
            lastFaceCount = progress.faces_found;
        }

        // If scanning is complete, stop polling and reload gallery
        if (!status.active && progress.processed > 0) {
            if (scanStatusInterval) {
                clearInterval(scanStatusInterval);
                scanStatusInterval = null;
            }
            if (galleryUpdateInterval) {
                clearInterval(galleryUpdateInterval);
                galleryUpdateInterval = null;
            }
            lastFaceCount = 0;
            setTimeout(() => {
                if (scanStatusDiv) scanStatusDiv.remove();
                loadFaceGallery(1); // Reload gallery
                // Re-enable scan button
                const scanBtn = document.querySelector('button[onclick="scanForNewFaces()"]');
                if (scanBtn) {
                    scanBtn.innerText = 'Scan for New Faces';
                    scanBtn.disabled = false;
                }
            }, 2000);
        }
    } catch (error) {
        console.error('Error updating scan status:', error);
    }
}

async function pauseScan() {
    await fetch('/api/people/scan_pause', { method: 'POST' });
    updateScanStatus();
}

async function resumeScan() {
    await fetch('/api/people/scan_resume', { method: 'POST' });
    updateScanStatus();
}

async function stopScan() {
    if (!confirm('Stop scanning? Progress will be saved, but you can resume later.')) return;
    await fetch('/api/people/scan_stop', { method: 'POST' });
    if (scanStatusInterval) {
        clearInterval(scanStatusInterval);
        scanStatusInterval = null;
    }
    if (galleryUpdateInterval) {
        clearInterval(galleryUpdateInterval);
        galleryUpdateInterval = null;
    }
    lastFaceCount = 0;
    if (scanStatusDiv) scanStatusDiv.remove();
    updateScanStatus();
    // Re-enable scan button
    const scanBtn = document.querySelector('button[onclick="scanForNewFaces()"]');
    if (scanBtn) {
        scanBtn.innerText = 'Scan for New Faces';
        scanBtn.disabled = false;
    }
}

// Selection management
let selectedFaceIds = new Set();

function updateSelection() {
    selectedFaceIds.clear();
    document.querySelectorAll('.face-checkbox').forEach(cb => {
        const faceId = parseInt(cb.dataset.faceId);
        const item = cb.closest('.face-gallery-item');
        
        if (cb.checked) {
            selectedFaceIds.add(faceId);
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });

    const count = selectedFaceIds.size;
    document.getElementById('selectionCount').textContent = `${count} face${count !== 1 ? 's' : ''} selected`;
    
    if (count > 0) {
        document.getElementById('selectionControls').style.display = 'block';
        document.getElementById('deleteSelectedBtn').style.display = 'inline-block';
    } else {
        document.getElementById('selectionControls').style.display = 'none';
        document.getElementById('deleteSelectedBtn').style.display = 'none';
    }
}

function selectAllFaces() {
    document.querySelectorAll('.face-checkbox').forEach(cb => {
        cb.checked = true;
    });
    updateSelection();
}

function deselectAllFaces() {
    document.querySelectorAll('.face-checkbox').forEach(cb => {
        cb.checked = false;
    });
    updateSelection();
}

async function deleteSelectedFaces() {
    if (selectedFaceIds.size === 0) return;
    
    if (!confirm(`Delete ${selectedFaceIds.size} selected face${selectedFaceIds.size !== 1 ? 's' : ''}? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: Array.from(selectedFaceIds)})
        });

        const data = await response.json();
        if (data.ok) {
            alert(`✅ Deleted ${data.deleted_count} face${data.deleted_count !== 1 ? 's' : ''}`);
            selectedFaceIds.clear();
            loadFaceGallery(currentPage); // Reload current page
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

async function showClusterManagement() {
    try {
        const response = await fetch('/api/people/clusters');
        const clusters = await response.json();

        let clusterHTML = '<div class="row">';
        clusters.forEach(cluster => {
            clusterHTML += `
                <div class="col-md-4 mb-3">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <span>${cluster.name}</span>
                            <span class="badge bg-info">${cluster.count} faces</span>
                        </div>
                        <div class="card-body">
                            <div class="d-flex flex-wrap mb-2">
                                ${cluster.samples.slice(0, 4).map(s => `<img src="${s}" class="cluster-preview-thumb" style="width:60px;height:60px;object-fit:cover;border-radius:4px;margin:2px;">`).join('')}
                            </div>
                            <button class="btn btn-sm btn-danger w-100" onclick="deleteCluster(${cluster.id}, '${cluster.name}')">Delete Cluster</button>
                        </div>
                    </div>
                </div>
            `;
        });
        clusterHTML += '</div>';

        if (clusters.length === 0) {
            clusterHTML = '<p class="text-muted text-center py-4">No clusters found. Run clustering first.</p>';
        }

        document.getElementById('cluster-list').innerHTML = clusterHTML;
        showModal('clusterManagementModal');
    } catch (error) {
        alert('Error loading clusters: ' + error.message);
    }
}

async function deleteCluster(clusterId, clusterName) {
    if (!confirm(`Delete cluster "${clusterName}"? This will remove all faces in this cluster. This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch('/api/people/delete_cluster', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cluster_id: clusterId})
        });

        const data = await response.json();
        if (data.ok) {
            alert(`✅ Cluster deleted. Removed ${data.faces_deleted} face${data.faces_deleted !== 1 ? 's' : ''}`);
            showClusterManagement(); // Reload cluster list
            loadFaceGallery(currentPage); // Reload face gallery
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

async function clusterBySimilarity(event) {
    event = event || window.event;
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    
    console.log('clusterBySimilarity called');
    
    // Disable button and show loading
    const btn = (event && event.target) || document.getElementById('clusterBySimilarityBtn');
    const originalText = btn ? btn.innerText : 'Cluster by Similarity';
    
    if (!btn) {
        alert('Error: Could not find cluster button');
        return;
    }
    
    btn.disabled = true;
    btn.innerText = 'Clustering...';
    
    // Show loading state and modal
    const container = document.getElementById('similarity-clusters-list');
    if (!container) {
        alert('Error: Could not find similarity clusters container');
        btn.disabled = false;
        btn.innerText = originalText;
        return;
    }
    
    // Switch to similarity tab
    const similarityTab = document.getElementById('similarity-tab');
    if (similarityTab) {
        similarityTab.click();
    }
    
    container.innerHTML = '<div class="col-12"><div class="text-center py-5"><div class="spinner-border" role="status"></div><p class="mt-2">Clustering faces... This may take a moment.</p></div></div>';
    
    try {
        // Get threshold
        const thresholdInput = document.getElementById('similarityThreshold');
        const threshold = thresholdInput ? parseFloat(thresholdInput.value) : 0.85;
        
        const response = await fetch(`/api/people/cluster_by_similarity?threshold=${threshold}`);
        
        if (!response.ok) {
            const errorText = await response.text();
            container.innerHTML = `<div class="col-12"><div class="alert alert-danger">Error: ${response.status} - ${errorText.substring(0, 200)}</div></div>`;
            btn.disabled = false;
            btn.innerText = originalText;
            return;
        }
        
        const data = await response.json();

        if (data.error) {
            container.innerHTML = `<div class="col-12"><div class="alert alert-danger">Error: ${data.error}</div></div>`;
            btn.disabled = false;
            btn.innerText = originalText;
            return;
        }

        // Store clusters globally for deletion functions
        similarityClusters = data.clusters || [];

        // Display clusters using the render function
        if (!data.clusters || data.clusters.length === 0) {
            const message = data.message || 'No similar face clusters found. Try adjusting the similarity threshold or scan for more faces first.';
            container.innerHTML = `
                <div class="col-12">
                    <div class="alert alert-warning">
                        <p class="mb-2">${message}</p>
                        <button class="btn btn-primary" onclick="scanForNewFaces()">Scan for New Faces</button>
                    </div>
                </div>
            `;
        } else {
            renderSimilarityClusters(container);
        }
        
        // Re-enable button
        btn.disabled = false;
        btn.innerText = originalText;
    } catch (error) {
        console.error('Clustering error:', error);
        container.innerHTML = `<div class="col-12"><div class="alert alert-danger">Error: ${error.message}</div></div>`;
        btn.disabled = false;
        btn.innerText = originalText;
    }
}

let similarityClusters = [];

async function deleteFaceFromCluster(faceId, clusterIdx, event) {
    event.stopPropagation();
    if (!confirm('Delete this face from the gallery?')) return;

    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: [faceId]})
        });

        const data = await response.json();
        if (data.ok) {
            // Remove from cluster display
            similarityClusters[clusterIdx].faces = similarityClusters[clusterIdx].faces.filter(f => f.id !== faceId);
            clusterBySimilarity(); // Reload clusters
            loadFaceGallery(currentPage); // Reload gallery
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

async function deleteClusterByIndex(clusterIdx) {
    const cluster = similarityClusters[clusterIdx];
    if (!cluster) return;

    if (!confirm(`Delete all ${cluster.faces.length} faces in this cluster? This cannot be undone.`)) return;

    const faceIds = cluster.faces.map(f => f.id);
    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: faceIds})
        });

        const data = await response.json();
        if (data.ok) {
            // Reload clusters display
            const btn = document.getElementById('clusterBySimilarityBtn');
            if (btn && !btn.disabled) {
                clusterBySimilarity({target: btn});
            } else {
                // Just reload the display
                loadSimilarityClusters();
            }
            loadFaceGallery(currentPage); // Reload gallery
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

async function keepOneDeleteRest(clusterIdx) {
    const cluster = similarityClusters[clusterIdx];
    if (!cluster || cluster.faces.length < 2) return;

    if (!confirm(`Keep the first face and delete the other ${cluster.faces.length - 1} similar faces?`)) return;

    // Keep first, delete rest
    const faceIdsToDelete = cluster.faces.slice(1).map(f => f.id);
    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: faceIdsToDelete})
        });

        const data = await response.json();
        if (data.ok) {
            // Reload clusters display
            const btn = document.getElementById('clusterBySimilarityBtn');
            if (btn && !btn.disabled) {
                clusterBySimilarity({target: btn});
            } else {
                loadSimilarityClusters();
            }
            loadFaceGallery(currentPage); // Reload gallery
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

async function loadSimilarityClusters() {
    const container = document.getElementById('similarity-clusters-list');
    if (!container) return;
    
    // If clusters are already loaded in memory, render them
    if (similarityClusters && similarityClusters.length > 0) {
        renderSimilarityClusters(container);
        return;
    }
    
    // Otherwise, show message to cluster first
    container.innerHTML = `
        <div class="col-12">
            <div class="alert alert-info">
                <p class="mb-2">No clusters loaded yet. Click "Cluster" above to group similar faces together.</p>
                <p class="mb-0"><small>Make sure you have scanned for faces first using "Scan for New Faces" in the Face Gallery tab.</small></p>
            </div>
        </div>
    `;
}

function renderSimilarityClusters(container) {
    if (!similarityClusters || similarityClusters.length === 0) {
        container.innerHTML = `
            <div class="col-12">
                <div class="alert alert-info">
                    <p class="mb-0">No clusters found. Click "Cluster" to group similar faces.</p>
                </div>
            </div>
        `;
        return;
    }
    
    let clustersHTML = `
        <div class="col-12 mb-3">
            <div class="alert alert-success d-flex justify-content-between align-items-center">
                <span>Found <strong>${similarityClusters.length}</strong> cluster${similarityClusters.length !== 1 ? 's' : ''}</span>
                <button class="btn btn-sm btn-danger" onclick="deleteAllSelectedClusters()">Delete Selected Clusters</button>
            </div>
        </div>
    `;
    
    similarityClusters.forEach((cluster, idx) => {
        clustersHTML += `
            <div class="col-md-6 col-lg-4 mb-4">
                <div class="card h-100">
                    <div class="card-header d-flex justify-content-between align-items-center bg-light">
                        <div>
                            <input type="checkbox" class="form-check-input cluster-checkbox" data-cluster-idx="${idx}" onchange="updateClusterSelection()">
                            <strong class="ms-2">Cluster ${idx + 1}</strong>
                        </div>
                        <span class="badge bg-primary">${cluster.faces.length} faces</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex flex-wrap gap-2 mb-3" style="max-height: 200px; overflow-y: auto;">
                            ${cluster.faces.map(face => `
                                <div class="position-relative" style="width: 70px; height: 70px; flex-shrink: 0;">
                                    <img src="${face.thumbnail_url || face.image_url}" class="img-thumbnail" style="width: 100%; height: 100%; object-fit: cover; cursor: pointer;" onclick="openFaceModal(${face.id}, '${face.image_url}', '${face.timestamp}', ${face.camera_id}, ${face.confidence || 0}, ${face.has_embedding})" title="Click to view">
                                </div>
                            `).join('')}
                        </div>
                        <div class="d-grid gap-2">
                            <button class="btn btn-sm btn-danger" onclick="deleteClusterByIndex(${idx})">
                                Delete All (${cluster.faces.length})
                            </button>
                            <button class="btn btn-sm btn-outline-warning" onclick="keepOneDeleteRest(${idx})">
                                Keep One, Delete Rest
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    container.innerHTML = clustersHTML;
}

async function loadProcessedFaces() {
    const container = document.getElementById('processed-faces-gallery');
    if (!container) return;

    container.innerHTML = '<div class="text-center py-5"><div class="spinner-border" role="status"></div><p class="mt-2">Loading processed faces...</p></div>';

    try {
        const response = await fetch('/api/people/face_gallery?per_page=200&page=1');
        const data = await response.json();

        // Filter to only processed faces
        const processedFaces = data.faces.filter(f => f.has_embedding);

        if (processedFaces.length === 0) {
            container.innerHTML = '<div class="col-12"><div class="alert alert-info"><p class="mb-0">No processed faces yet. Use "Scan for New Faces" to process detections.</p></div></div>';
            return;
        }

        let html = '<div class="face-gallery-grid">';
        processedFaces.forEach(face => {
            html += `
                <div class="face-gallery-item has-embedding" data-face-id="${face.id}">
                    <div class="face-gallery-item-content" onclick="openFaceModal(${face.id}, '${face.image_url}', '${face.timestamp}', ${face.camera_id}, ${face.confidence || 0}, ${face.has_embedding})">
                        <img src="${face.thumbnail_url}" alt="Face" class="face-gallery-thumb">
                        <div class="face-gallery-overlay">
                            <small class="face-gallery-status">✓ Processed</small>
                            <small class="face-gallery-time">${new Date(face.timestamp).toLocaleTimeString()}</small>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        html += `<div class="mt-3 text-center"><small class="text-muted">Showing ${processedFaces.length} processed faces</small></div>`;

        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = `<div class="alert alert-danger">Error loading processed faces: ${error.message}</div>`;
    }
}

async function loadKnownPeople() {
    const container = document.getElementById('known-people-gallery');
    if (!container) return;

    container.innerHTML = '<div class="text-center py-5"><div class="spinner-border" role="status"></div><p class="mt-2">Loading known people...</p></div>';

    try {
        const response = await fetch('/api/people');
        const people = await response.json();

        if (people.length === 0) {
            container.innerHTML = '<div class="col-12"><div class="alert alert-info"><p class="mb-0">No known people yet. Label clusters in the People page to create known people.</p></div></div>';
            return;
        }

        let html = '';
        people.forEach(person => {
            html += `
                <div class="col-md-3 col-6 mb-3">
                    <div class="card h-100 text-center">
                        <div class="card-body">
                            <img src="${person.thumbnail_path || 'https://via.placeholder.com/100'}" class="rounded-circle mb-2" style="width:100px;height:100px;object-fit:cover;">
                            <h5 class="card-title">${person.name}</h5>
                            <small class="text-muted">ID: ${person.id}</small>
                        </div>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = `<div class="alert alert-danger">Error loading known people: ${error.message}</div>`;
    }
}

// Tab switching function (works with or without Bootstrap)
function switchTab(tabButton) {
    const targetId = tabButton.getAttribute('data-bs-target');
    const targetPane = document.querySelector(targetId);
    
    if (!targetPane) return;
    
    // Hide all tab panes
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active', 'show');
        pane.style.display = 'none';
    });
    
    // Remove active from all tabs
    document.querySelectorAll('#faceGalleryTabs .nav-link').forEach(link => {
        link.classList.remove('active');
        link.setAttribute('aria-selected', 'false');
    });
    
    // Show selected pane
    targetPane.classList.add('active', 'show');
    targetPane.style.display = 'block';
    
    // Activate selected tab
    tabButton.classList.add('active');
    tabButton.setAttribute('aria-selected', 'true');
    
    // Load content for specific tabs
    if (targetId === '#similarity-pane') {
        loadSimilarityClusters();
    } else if (targetId === '#processed-pane') {
        loadProcessedFaces();
    } else if (targetId === '#known-pane') {
        loadKnownPeople();
    } else if (targetId === '#gallery-pane') {
        // Reload gallery if needed
        if (currentFaces.length === 0) {
            loadFaceGallery(1);
        }
    }
}

// Tab change handlers
document.addEventListener('DOMContentLoaded', function() {
    // Handle tab clicks
    const tabs = document.querySelectorAll('#faceGalleryTabs button[data-bs-target]');
    tabs.forEach(tab => {
        // Remove Bootstrap data attribute and use custom handler
        tab.removeAttribute('data-bs-toggle');
        tab.addEventListener('click', function(e) {
            e.preventDefault();
            switchTab(this);
        });
    });
    
    // Also listen for Bootstrap events if Bootstrap is loaded
    tabs.forEach(tab => {
        tab.addEventListener('shown.bs.tab', function(e) {
            const targetId = e.target.getAttribute('data-bs-target');
            if (targetId === '#similarity-pane') {
                loadSimilarityClusters();
            } else if (targetId === '#processed-pane') {
                loadProcessedFaces();
            } else if (targetId === '#known-pane') {
                loadKnownPeople();
            }
        });
    });
    
    // Initialize first tab
    const firstTab = document.querySelector('#gallery-tab');
    if (firstTab) {
        switchTab(firstTab);
    }
});

function updateClusterSelection() {
    // Track selected clusters for bulk deletion
    // Implementation can be added if needed
}

async function deleteAllSelectedClusters() {
    const selected = Array.from(document.querySelectorAll('.cluster-checkbox:checked'));
    if (selected.length === 0) {
        alert('No clusters selected');
        return;
    }

    const faceIds = [];
    selected.forEach(cb => {
        const idx = parseInt(cb.dataset.clusterIdx);
        if (similarityClusters[idx]) {
            faceIds.push(...similarityClusters[idx].faces.map(f => f.id));
        }
    });

    if (!confirm(`Delete all ${faceIds.length} faces from ${selected.length} selected cluster${selected.length !== 1 ? 's' : ''}?`)) return;

    try {
        const response = await fetch('/api/people/delete_faces', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({face_ids: faceIds})
        });

        const data = await response.json();
        if (data.ok) {
            // Reload clusters display
            const btn = document.getElementById('clusterBySimilarityBtn');
            if (btn && !btn.disabled) {
                clusterBySimilarity({target: btn});
            } else {
                loadSimilarityClusters();
            }
            loadFaceGallery(currentPage); // Reload gallery
        } else {
            alert('❌ Error: ' + data.error);
        }
    } catch (error) {
        alert('❌ Network error: ' + error.message);
    }
}

// Load gallery on page load
loadFaceGallery(1);

// Ensure clusterBySimilarity is accessible and add event listener as backup
window.clusterBySimilarity = clusterBySimilarity;

// Add event listener to button as backup
document.addEventListener('DOMContentLoaded', function() {
    const btn = document.getElementById('clusterBySimilarityBtn');
    if (btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            clusterBySimilarity(e);
        });
    }
});

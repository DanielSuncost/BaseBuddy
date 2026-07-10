async function loadPeople() {
    const res = await fetch('/api/people');
    const people = await res.json();
    const container = document.getElementById('known-people');
    container.innerHTML = people.map(p => `
        <div class="col-md-2 col-6 mb-3">
            <div class="card person-card h-100 text-center p-3">
                <img src="${p.thumbnail_path || 'https://via.placeholder.com/100'}" class="face-thumb mb-2">
                <h5 class="card-title">${p.name}</h5>
                <small class="text-muted">ID: ${p.id}</small>
            </div>
        </div>
    `).join('');
}

async function loadClusters() {
    const res = await fetch('/api/people/clusters');
    const clusters = await res.json();
    const container = document.getElementById('unknown-clusters');
    
    if (clusters.length === 0) {
        container.innerHTML = '<p class="text-muted">No clusters found. Run clustering or wait for more data.</p>';
        return;
    }

    container.innerHTML = clusters.map(c => `
        <div class="col-md-4 mb-4">
            <div class="card h-100">
                <div class="card-header d-flex justify-content-between">
                    <span>${c.name}</span>
                    <span class="badge bg-info">${c.count} faces</span>
                </div>
                    <div class="card-body">
                    <div class="d-flex flex-wrap justify-content-center mb-3">
                        ${c.samples.map((s, idx) => `<img src="${s}" class="cluster-thumb" onclick="openClusterImageModal('${s}', '${c.name} - Image ${idx + 1}')">`).join('')}
                    </div>
                    <button class="btn btn-sm btn-success w-100" onclick="openLabelModal(${c.id}, '${c.name}')">Identify Person</button>
                </div>
            </div>
        </div>
    `).join('');
}

function openLabelModal(id, currentName) {
    document.getElementById('cluster-id').value = id;
    document.getElementById('person-name').value = currentName.startsWith('Cluster') ? '' : currentName;
    new bootstrap.Modal(document.getElementById('labelModal')).show();
}

async function saveLabel() {
    const id = document.getElementById('cluster-id').value;
    const name = document.getElementById('person-name').value;
    if (!name) return;
    
    await fetch('/api/people/label', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({person_id: id, name: name})
    });
    
    location.reload();
}

async function triggerClustering() {
    if(!confirm("Run clustering? This may take a moment.")) return;
    await fetch('/api/people/cluster', {method: 'POST'});
    alert("Clustering started/completed. Refreshing...");
    loadClusters();
}

async function triggerScan() {
    if(!confirm("Scan past detections for faces? This may take a while.")) return;
    const btn = event.target;
    const originalText = btn.innerText;
    btn.innerText = "Scanning...";
    btn.disabled = true;
    try {
        await fetch('/api/people/scan_history', {method: 'POST'});
        alert("Scan complete. Running clustering now...");
        await triggerClustering();
    } catch(e) {
        alert("Error during scan: " + e);
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

loadPeople();
loadClusters();

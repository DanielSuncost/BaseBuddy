/**
 * Tracking Configuration Page JavaScript
 */

function showTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.tab-btn').forEach(el => {
        el.classList.remove('active');
    });
    
    document.getElementById(tabId).style.display = 'block';
    
    const buttons = document.querySelectorAll('.tab-btn');
    for (const btn of buttons) {
        if (btn.getAttribute('onclick').includes(tabId)) {
            btn.classList.add('active');
            break;
        }
    }
}

async function loadConfig() {
    try {
        const res = await fetch('/api/tracking/config');
        const data = await res.json();
        
        if (data.ok) {
            updateStatusGrid(data.status_info);
            populateForm(data.config);
        } else {
            showMessage('Error loading configuration: ' + data.error, 'error');
        }
    } catch (error) {
        showMessage('Error loading configuration: ' + error.message, 'error');
    }
}

function updateStatusGrid(statusInfo) {
    const grid = document.getElementById('trackingStatus');
    grid.innerHTML = '';
    
    for (const [camId, info] of Object.entries(statusInfo)) {
        const isOnline = info.status === 'active';
        const bg = isOnline ? 'var(--color-success-bg)' : 'var(--color-danger-bg)';
        const borderColor = isOnline ? 'var(--color-success)' : 'var(--color-danger)';
        const icon = isOnline ? 'check_circle' : 'cancel';
        const iconColor = isOnline ? 'var(--color-success)' : 'var(--color-danger)';
        
        grid.innerHTML += `
            <div class="status-item" style="background: ${bg}; border-left: 4px solid ${borderColor};">
                <strong>Camera ${parseInt(camId) + 1}</strong><br>
                <span class="material-icons-outlined icon-sm" style="color:${iconColor};vertical-align:middle">${icon}</span>
                ${info.status.toUpperCase()}<br>
                <small>Tracks: ${info.track_count}</small>
            </div>
        `;
    }
}

function pageCameraIds() {
    return Array.from(document.querySelectorAll('.tab-content[data-cam-id]'))
        .map(el => parseInt(el.dataset.camId, 10));
}

function populateForm(config) {
    document.getElementById('global_max_age').value = config.global.max_age;
    document.getElementById('global_max_history').value = config.global.max_history;
    document.getElementById('global_cleanup_interval').value = config.global.cleanup_interval;
    document.getElementById('global_line_thickness').value = config.global.line_thickness;
    document.getElementById('global_line_length').value = config.global.line_length;
    
    for (const i of pageCameraIds()) {
        const camConfig = config.cameras[i];
        if (camConfig) {
            document.getElementById(`cam${i+1}_max_age`).value = camConfig.max_age;
            document.getElementById(`cam${i+1}_max_history`).value = camConfig.max_history;
            document.getElementById(`cam${i+1}_cleanup_interval`).value = camConfig.cleanup_interval;
            document.getElementById(`cam${i+1}_line_thickness`).value = camConfig.line_thickness;
            document.getElementById(`cam${i+1}_line_length`).value = camConfig.line_length;
        }
    }
}

async function applyGlobalConfig() {
    const config = {
        max_age: parseInt(document.getElementById('global_max_age').value),
        max_history: parseInt(document.getElementById('global_max_history').value),
        cleanup_interval: parseInt(document.getElementById('global_cleanup_interval').value),
        line_thickness: parseInt(document.getElementById('global_line_thickness').value),
        line_length: parseInt(document.getElementById('global_line_length').value)
    };
    
    try {
        const res = await fetch('/api/tracking/config/global', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        
        const data = await res.json();
        if (res.ok) {
            showMessage(data.message, 'success');
            loadConfig();
        } else {
            showMessage(data.error || 'Error saving global config', 'error');
        }
    } catch (error) {
        showMessage('Error saving global config: ' + error.message, 'error');
    }
}

async function applyCameraConfig(camId) {
    const camNum = camId + 1;
    const config = {
        max_age: parseInt(document.getElementById(`cam${camNum}_max_age`).value),
        max_history: parseInt(document.getElementById(`cam${camNum}_max_history`).value),
        cleanup_interval: parseInt(document.getElementById(`cam${camNum}_cleanup_interval`).value),
        line_thickness: parseInt(document.getElementById(`cam${camNum}_line_thickness`).value),
        line_length: parseInt(document.getElementById(`cam${camNum}_line_length`).value)
    };
    
    try {
        const res = await fetch(`/api/tracking/config/${camId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        
        const data = await res.json();
        if (res.ok) {
            showMessage(data.message, 'success');
            loadConfig();
        } else {
            showMessage(data.error || 'Error saving camera config', 'error');
        }
    } catch (error) {
        showMessage('Error saving camera config: ' + error.message, 'error');
    }
}

async function clearCameraTracks(camId) {
    if (!confirm('Are you sure you want to clear all tracks for this camera?')) return;
    
    try {
        const res = await fetch(`/api/tracking/clear/${camId}`, { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            showMessage(data.message, 'success');
            loadConfig();
        } else {
            showMessage(data.error || 'Error clearing tracks', 'error');
        }
    } catch (error) {
        showMessage('Error clearing tracks: ' + error.message, 'error');
    }
}

async function resetCameraTracker(camId) {
    if (!confirm('Are you sure you want to completely reset the tracker?')) return;
    
    try {
        const res = await fetch(`/api/tracking/reset/${camId}`, { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            showMessage(data.message, 'success');
            loadConfig();
        } else {
            showMessage(data.error || 'Error resetting tracker', 'error');
        }
    } catch (error) {
        showMessage('Error resetting tracker: ' + error.message, 'error');
    }
}

function showMessage(message, type = 'success') {
    const statusDiv = document.getElementById('statusMessage');
    const bgColor = type === 'success' ? '#d4edda' : '#f8d7da';
    const color = type === 'success' ? '#155724' : '#721c24';
    const borderColor = type === 'success' ? '#c3e6cb' : '#f5c6cb';
    
    statusDiv.innerHTML = `<div style="background: ${bgColor}; color: ${color}; border: 1px solid ${borderColor}; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-weight: 500;">${message}</div>`;
    setTimeout(() => {
        statusDiv.innerHTML = '';
    }, 5000);
}

// Initial load
document.addEventListener('DOMContentLoaded', function() {
    loadConfig();
    showTab('global');
});

// Auto-refresh status every 5 seconds
setInterval(async () => {
    try {
        const res = await fetch('/api/tracking/config');
        const data = await res.json();
        if (data.ok) {
            updateStatusGrid(data.status_info);
        }
    } catch (e) {
        console.error('Status update failed', e);
    }
}, 5000);

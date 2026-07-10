/**
 * Config Thresholds Page JavaScript
 */

function showMessage(message, type = 'success') {
    const statusDiv = document.getElementById('statusMessage');
    const bgColor = type === 'success' ? '#d4edda' : '#f8d7da';
    const color = type === 'success' ? '#155724' : '#721c24';
    const borderColor = type === 'success' ? '#c3e6cb' : '#f5c6cb';
    
    statusDiv.innerHTML = `<div style="background: ${bgColor}; color: ${color}; border: 1px solid ${borderColor}; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-weight: 500;">${message}</div>`;
    setTimeout(function() {
        statusDiv.innerHTML = '';
    }, 5000);
}

async function applyCameraThresholds(camId) {
    const thresholds = {};
    
    const inputs = document.querySelectorAll(`input[name^="threshold_${camId}_"]`);
    inputs.forEach(function(input) {
        const className = input.name.replace(`threshold_${camId}_`, '');
        thresholds[className] = parseFloat(input.value);
    });
    
    try {
        const response = await fetch(`/api/thresholds/camera/${camId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(thresholds)
        });
        
        const result = await response.json();
        if (response.ok) {
            showMessage(result.message, 'success');
        } else {
            showMessage(result.error, 'error');
        }
    } catch (error) {
        showMessage('Error updating thresholds: ' + error.message, 'error');
    }
}

async function resetCameraThresholds(camId) {
    if (!confirm(`Reset all thresholds for Camera ${camId + 1} to default values?`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/thresholds/camera/${camId}/reset`, {
            method: 'POST'
        });
        
        const result = await response.json();
        if (response.ok) {
            showMessage('Reset successful, reloading...', 'success');
            setTimeout(function() { location.reload(); }, 1000);
        } else {
            showMessage(result.error, 'error');
        }
    } catch (error) {
        showMessage('Error resetting thresholds: ' + error.message, 'error');
    }
}

function applyPreset(presetName) {
    if (!confirm(`Apply ${presetName} preset to all cameras?`)) return;
    
    let value = 0.5;
    if (presetName === 'conservative') value = 0.7;
    if (presetName === 'sensitive') value = 0.2;
    if (presetName === 'balanced') value = 0.4;
    if (presetName === 'reset') value = 0.35;
    
    document.querySelectorAll('input[type="number"]').forEach(function(input) {
        if (input.name && input.name.startsWith('threshold_')) {
            input.value = value;
            const span = input.nextElementSibling;
            if (span && span.classList.contains('threshold-value')) {
                span.textContent = value.toFixed(2);
            }
        }
    });
    
    showMessage(`Applied ${presetName} preset to all inputs. Please click "Apply" for each camera to save.`, 'success');
}

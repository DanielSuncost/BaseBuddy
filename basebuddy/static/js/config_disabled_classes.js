/**
 * Disabled Classes Page JavaScript
 */

function updateCheckboxStates() {
    const checkboxes = document.querySelectorAll('input[name="disabled_classes"]');
    checkboxes.forEach(function(checkbox) {
        const classItem = checkbox.closest('.class-item');
        if (!checkbox.checked) {
            classItem.classList.add('disabled');
        } else {
            classItem.classList.remove('disabled');
        }
    });
}

// Initialize checkbox states
document.addEventListener('DOMContentLoaded', updateCheckboxStates);

// Update states when checkboxes change
document.addEventListener('change', function(e) {
    if (e.target.name === 'disabled_classes') {
        updateCheckboxStates();
    }
});

async function saveDisabledClasses() {
    const checkboxes = document.querySelectorAll('input[name="disabled_classes"]:not(:checked)');
    const disabledClasses = Array.from(checkboxes).map(function(cb) { return cb.value; });
    
    try {
        const response = await fetch('/api/classes/disabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ classes: disabledClasses })
        });
        
        const result = await response.json();
        if (response.ok) {
            showMessage(result.message, 'success');
            setTimeout(function() { location.reload(); }, 1500);
        } else {
            showMessage(result.error || 'Error saving disabled classes', 'error');
        }
    } catch (error) {
        showMessage('Error saving disabled classes: ' + error.message, 'error');
    }
}

function selectAll() {
    const checkboxes = document.querySelectorAll('input[name="disabled_classes"]');
    checkboxes.forEach(function(cb) { cb.checked = true; });
    updateCheckboxStates();
}

function selectNone() {
    const checkboxes = document.querySelectorAll('input[name="disabled_classes"]');
    checkboxes.forEach(function(cb) { cb.checked = false; });
    updateCheckboxStates();
}

async function resetToDefaults() {
    if (!confirm('Reset to default settings? This will disable common unwanted classes.')) {
        return;
    }
    
    const defaultDisabled = ['frisbee', 'sports ball', 'baseball bat', 'baseball glove', 'tennis racket', 'kite', 'skateboard'];
    
    try {
        const response = await fetch('/api/classes/disabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ classes: defaultDisabled })
        });
        
        const result = await response.json();
        if (response.ok) {
            showMessage('Reset to default disabled classes: ' + defaultDisabled.join(', '), 'success');
            setTimeout(function() { location.reload(); }, 1500);
        } else {
            showMessage(result.error || 'Error resetting disabled classes', 'error');
        }
    } catch (error) {
        showMessage('Error resetting disabled classes: ' + error.message, 'error');
    }
}

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

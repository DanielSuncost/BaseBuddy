// Camera wall: recording toggles, tracking status/actions, misc actions,
// and page initialization. Must be loaded LAST - it kicks off loadCameras()
// and updateTrackingStatus(), which depend on functions in the other files.

function updateRecordingTileUI(cam, isRecording) {
  recordingState[cam] = isRecording;
  const tile = document.getElementById(`tile-${cam}`);
  if (!tile) return;
  if (isRecording) {
    tile.classList.add('is-recording');
  } else {
    tile.classList.remove('is-recording');
  }
}

function showRecordingStoppedBadge(cam) {
  const badge = document.getElementById(`rec-badge-${cam}`);
  if (!badge) return;
  badge.classList.add('visible');
  setTimeout(() => {
    badge.classList.remove('visible');
  }, 1800);
}

async function syncRecordingState(cam) {
  try {
    const res = await fetch(`/record/${cam}/status`);
    const data = await res.json();
    const isRecording = res.ok && data.ok && data.data && data.data.recording === true;
    updateRecordingTileUI(cam, isRecording);
  } catch (_error) {
    updateRecordingTileUI(cam, false);
  }
}

async function toggleRecording(cam, event = null, forceStop = false) {
  try {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const isRecording = recordingState[cam] === true;
    const shouldStop = forceStop || isRecording;
    const endpoint = shouldStop ? `/record/${cam}/stop` : `/record/${cam}/start`;
    const res = await fetch(endpoint, {method:'POST'});
    const data = await res.json();
    
    if (res.ok) {
      const stopped = data.status === 'stopped' || data.status === 'not_recording' || shouldStop;
      updateRecordingTileUI(cam, !stopped);
      if (stopped) {
        showRecordingStoppedBadge(cam);
      }
    } else {
      showToast('Recording error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (error) {
    console.error('Error toggling recording:', error);
    showToast('Recording network error: ' + error.message, 'error');
  }
}

async function viewRecordings(cam) {
  window.location.href = `/recordings?camera=${cam}`;
}
function openNightPreview(cam) {
  const gInput = document.getElementById(`gamma${cam}`);
  const g = gInput ? gInput.value : '0.6';
  const url = g ? `/api/night-preview/${cam}?gamma=${encodeURIComponent(g)}` : `/api/night-preview/${cam}`;
  const w = window.open(url, '_blank');
  if (!w) {
    alert('Popup blocked. Please allow popups to view the night preview.');
  }
}

async function clearTracks(cam) {
  if (!confirm(`Clear all tracking data for Camera ${cam + 1}? This will reset all track IDs.`)) {
    return;
  }

  try {
    const response = await fetch(`/api/tracking/clear/${cam}`, {
      method: 'POST'
    });
    const result = await response.json();
    showToast(result.message || `Tracks cleared for Camera ${cam + 1}`, 'success');
  } catch (error) {
    showToast('Error clearing tracks: ' + error.message, 'error');
  }
}

async function updateTrackingStatus() {
  try {
    const response = await fetch('/api/tracking/status');
    const data = await response.json();

    if (!data || !data.status) {
      return;
    }

    cameras.forEach(cam => {
      const status = data.status[`camera_${cam.id}`];
      if (status) {
        const tracksElement = document.getElementById(`tracks${cam.id}`);
        if (tracksElement) {
          tracksElement.textContent = status.tracks_active;
        }
      }
    });
  } catch (error) {
    console.error('Error updating tracking status:', error);
  }
}

// Load cameras on page load
loadCameras();

// Update tracking status every 2 seconds
setInterval(updateTrackingStatus, 2000);

// Initial update
updateTrackingStatus();

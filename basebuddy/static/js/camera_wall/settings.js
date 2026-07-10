// Camera wall: add/edit/delete cameras, wall activation, and the camera
// add & profile settings modals.

async function removeFromWall(camId) {
  console.log('removeFromWall called for camera', camId);
  if (!confirm('Remove this camera from the wall? (Configuration will be preserved)')) {
    console.log('User cancelled removal');
    return;
  }
  
  // Show immediate feedback
  showToast('Removing camera...', 'info');
  
  // Hide the tile immediately for instant feedback
  const tile = document.getElementById('tile-' + camId);
  if (tile) {
    tile.style.opacity = '0.5';
    tile.style.pointerEvents = 'none';
  }
  
  console.log('Sending deactivate request...');
  try {
    const response = await fetch(`/api/wall/cameras/${camId}/deactivate`, { method: 'POST' });
    console.log('Response status:', response.status);
    const data = await response.json();
    console.log('Response data:', data);
    if (data.ok) {
      showToast('Camera removed from wall', 'success');
      // Remove tile from DOM
      if (tile) tile.remove();
      loadCameras();  // This will restart the streams
    } else {
      // Restore tile on error
      if (tile) {
        tile.style.opacity = '1';
        tile.style.pointerEvents = 'auto';
      }
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    console.error('removeFromWall error:', e);
    // Restore tile on error
    if (tile) {
      tile.style.opacity = '1';
      tile.style.pointerEvents = 'auto';
    }
    showToast('Network error: ' + e.message, 'error');
  }
}

async function addToWall(camId) {
  try {
    const response = await fetch(`/api/wall/cameras/${camId}/activate`, { method: 'POST' });
    const data = await response.json();
    if (data.ok) {
      showToast('Camera added to wall! Connecting...', 'success');
      closeAddCameraModal();
      
      // Try to hot-reload cameras
      try {
        await fetch('/api/cameras/reload', { method: 'POST' });
      } catch (e) {
        console.log('Hot reload not available');
      }
      
      loadCameras();  // This restarts streams
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

function toggleSavedCameras() {
  const collapse = document.getElementById('savedCamerasCollapse');
  const icon = document.getElementById('savedCamerasToggleIcon');
  
  if (collapse.style.display === 'none') {
    collapse.style.display = 'block';
    icon.style.transform = 'rotate(180deg)';
  } else {
    collapse.style.display = 'none';
    icon.style.transform = 'rotate(0deg)';
  }
}

function openAddCameraModal(camId = null) {
  const modalEl = document.getElementById('addCameraModal');
  
  // Clear form
  document.getElementById('cameraName').value = '';
  document.getElementById('cameraUrl').value = '';
  document.getElementById('cameraStreamType').value = 'rtsp';
  document.getElementById('cameraGroup').value = '';
  updateCameraUrlPlaceholder(); // Initialize placeholder
  modalEl.dataset.camId = camId !== null ? camId : '';
  
  // Reflect add vs edit mode in the modal title
  const titleEl = modalEl.querySelector('.modal-title');
  if (titleEl) titleEl.textContent = camId !== null ? 'Edit Camera' : 'Add Camera';
  
  // Populate group dropdown
  const groupSelect = document.getElementById('cameraGroup');
  groupSelect.innerHTML = '<option value="">No Group</option>';
  if (cameraGroups && cameraGroups.length > 0) {
    cameraGroups.forEach(group => {
      const option = document.createElement('option');
      option.value = group.id;
      option.textContent = group.name;
      groupSelect.appendChild(option);
    });
  }
  
  // Populate saved cameras section
  const savedSection = document.getElementById('savedCamerasSection');
  const savedList = document.getElementById('savedCamerasList');
  const savedCount = document.getElementById('savedCamerasCount');
  const savedCollapse = document.getElementById('savedCamerasCollapse');
  
  if (inactiveCameras && inactiveCameras.length > 0) {
    savedSection.style.display = 'block';
    savedCount.textContent = inactiveCameras.length;
    savedCollapse.style.display = 'none'; // Start collapsed
    savedList.innerHTML = inactiveCameras.map(cam => `
      <div class="saved-camera-item">
        <div class="saved-camera-row">
          <div class="saved-camera-meta">
            <div class="saved-camera-name">${cam.name || 'Camera ' + (cam.id + 1)}</div>
            <div class="saved-camera-url">${cam.url}</div>
          </div>
          <div class="saved-camera-actions">
            <button type="button" class="btn btn-success btn-sm" onclick="addToWall(${cam.id})">
              <span class="material-icons-outlined">add</span> Add
            </button>
            <button type="button" class="btn btn-danger btn-sm btn-icon" onclick="deleteCamera(${cam.id})" title="Delete">
              <span class="material-icons-outlined">delete</span>
            </button>
          </div>
        </div>
      </div>
    `).join('');
  } else {
    savedSection.style.display = 'none';
    savedList.innerHTML = '';
  }
  
  // Show modal
  try {
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
  } catch (e) {
    // Fallback if Bootstrap not loaded
    modalEl.style.display = 'block';
    modalEl.classList.add('show');
    document.body.classList.add('modal-open');
  }
}

async function deleteCamera(camId) {
  if (!confirm('Delete this camera permanently? This will remove the RTSP URL from your configuration.')) return;
  
  try {
    const response = await fetch(`/api/cameras/${camId}`, { method: 'DELETE' });
    const data = await response.json();
    if (data.ok) {
      showToast('Camera deleted permanently', 'success');
      closeAddCameraModal();
      loadCameras();
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

function updateCameraUrlPlaceholder() {
  const streamType = document.getElementById('cameraStreamType').value;
  const urlInput = document.getElementById('cameraUrl');
  const urlLabel = document.getElementById('urlLabel');
  const urlHint = document.getElementById('urlHint');
  const streamTypeHint = document.getElementById('streamTypeHint');
  const infoTip = document.getElementById('infoTip');
  const pollRateGroup = document.getElementById('pollRateGroup');
  
  if (streamType === 'mjpeg') {
    urlLabel.textContent = 'MJPEG Stream URL';
    urlInput.placeholder = 'http://192.168.1.100/stream';
    urlHint.textContent = 'http://192.168.1.100/stream (ESP32-CAM typical)';
    streamTypeHint.textContent = 'HTTP-based motion JPEG stream - viewable like RTSP';
    infoTip.textContent = 'MJPEG streams appear in camera wall with live video feed.';
    pollRateGroup.style.display = 'none';
  } else if (streamType === 'still') {
    urlLabel.textContent = 'Still Image URL';
    urlInput.placeholder = 'http://192.168.1.100/capture';
    urlHint.textContent = 'http://192.168.1.100/capture (returns single JPEG)';
    streamTypeHint.textContent = 'Polls endpoint periodically for still images';
    infoTip.textContent = 'Adjust polling rate below to balance updates vs device load.';
    pollRateGroup.style.display = 'block';
  } else {
    urlLabel.textContent = 'RTSP URL';
    urlInput.placeholder = 'rtsp://username:password@192.168.1.100:554/stream1';
    urlHint.textContent = 'rtsp://username:password@192.168.1.100:554/stream1';
    streamTypeHint.textContent = 'Standard camera protocol for continuous video streaming';
    infoTip.textContent = 'After adding a camera, restart the app to connect to the stream.';
    pollRateGroup.style.display = 'none';
  }
}

function updatePollRateDisplay() {
  const slider = document.getElementById('cameraPollRate');
  const display = document.getElementById('pollRateDisplay');
  if (slider && display) {
    const sec = parseFloat(slider.value);
    let displayText;
    if (sec < 60) {
      displayText = sec + 's';
    } else if (sec < 3600) {
      const mins = Math.round(sec / 60);
      displayText = mins + 'm';
    } else {
      const hours = Math.round(sec / 3600 * 10) / 10;  // 1 decimal place
      displayText = hours + 'h';
    }
    display.textContent = displayText;
  }
}

async function addCamera() {
  console.log('addCamera() called');
  
  const nameEl = document.getElementById('cameraName');
  const urlEl = document.getElementById('cameraUrl');
  const streamTypeEl = document.getElementById('cameraStreamType');
  const modalEl = document.getElementById('addCameraModal');
  const submitBtn = document.getElementById('addCameraSubmitBtn');
  
  if (!nameEl || !urlEl || !streamTypeEl || !modalEl) {
    console.error('Form elements not found:', { nameEl, urlEl, streamTypeEl, modalEl });
    showToast('Error: Form elements not found. Please refresh the page.', 'error');
    return;
  }
  
  const name = nameEl.value.trim();
  const url = urlEl.value.trim();
  const streamType = streamTypeEl.value;
  const camId = modalEl.dataset.camId;
  const groupId = document.getElementById('cameraGroup').value;
  
  // Get poll rate if it's a still camera
  let pollRate = 2.0; // Default
  if (streamType === 'still') {
    const pollRateEl = document.getElementById('cameraPollRate');
    if (pollRateEl) {
      pollRate = parseFloat(pollRateEl.value);
    }
  }
  
  console.log('Adding camera:', { name, url, streamType, pollRate, groupId, camId });
  
  if (!url) {
    showToast('Please enter a camera URL', 'warning');
    return;
  }
  
  // Show loading state on button
  const originalBtnText = submitBtn ? submitBtn.innerHTML : '';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="material-icons-outlined spinning">sync</span> Adding...';
  }
  
  // Close modal immediately
  closeAddCameraModal();
  
  // Add loading placeholder to the grid
  const loadingId = 'loading-' + Date.now();
  addLoadingPlaceholder(loadingId, name || 'New Camera');
  
  // Note: Don't pause streams - WebSocket will handle reconnection
  
  try {
    console.log('Sending POST request to /api/cameras');
    
    const response = await fetch('/api/cameras', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: camId ? parseInt(camId) : null,
        name: name,
        url: url,
        stream_type: streamType,
        poll_rate: pollRate
      })
    });
    
    console.log('Response status:', response.status);
    const data = await response.json();
    console.log('Response data:', data);
    
    // Remove loading placeholder
    removeLoadingPlaceholder(loadingId);
    
    if (data.ok) {
      showToast('Camera saved! Connecting...', 'success');
      
      // Add to group if selected
      if (groupId && data.id) {
        try {
          await fetch(`/api/camera-groups/${groupId}/cameras/${data.id}`, { method: 'POST' });
          console.log(`Added camera ${data.id} to group ${groupId}`);
        } catch (e) {
          console.warn('Could not add camera to group:', e);
        }
      }
      
      // Clear form
      document.getElementById('cameraName').value = '';
      document.getElementById('cameraUrl').value = '';
      
      // Try to hot-reload cameras without restart
      try {
        const reloadResp = await fetch('/api/cameras/reload', { method: 'POST' });
        const reloadData = await reloadResp.json();
        if (reloadData.ok) {
          showToast('Camera connected! ' + reloadData.message, 'success');
        } else {
          showToast('Camera saved but needs app restart to connect', 'warning');
        }
      } catch (reloadErr) {
        console.error('Could not hot-reload:', reloadErr);
        showToast('Camera saved. Restart app to connect.', 'warning');
      }
      
      // Reload camera list
      loadCameras();
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (error) {
    console.error('Network error:', error);
    // Remove loading placeholder
    removeLoadingPlaceholder(loadingId);
    
    if (error.name === 'AbortError') {
      showToast('Request timed out. Check server logs for details.', 'warning');
    } else {
      showToast('Network error: ' + error.message, 'error');
    }
    // Note: WebSocket handles reconnection automatically
  } finally {
    // Reset button state
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalBtnText;
    }
  }
}

function closeAddCameraModal() {
  const modalEl = document.getElementById('addCameraModal');
  if (!modalEl) return;
  
  try {
    const modalInstance = bootstrap.Modal.getInstance(modalEl);
    if (modalInstance) {
      modalInstance.hide();
    }
  } catch (e) {
    console.warn('Bootstrap modal hide failed:', e);
  }
  
  // Force close as backup
  modalEl.style.display = 'none';
  modalEl.classList.remove('show');
  modalEl.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('modal-open');
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  
  // Remove all backdrops
  document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
}

function editCamera(camId) {
  const cam = cameras.find(c => c.id === camId) || inactiveCameras.find(c => c.id === camId);
  if (!cam) return;
  closeCameraProfileModal();
  // Open first: openAddCameraModal clears the form, so prefill afterwards
  openAddCameraModal(camId);
  document.getElementById('cameraName').value = cam.name || '';
  document.getElementById('cameraUrl').value = cam.url || '';
  if (cam.stream_type) {
    document.getElementById('cameraStreamType').value = cam.stream_type;
    updateCameraUrlPlaceholder();
  }
}

async function openCameraProfile(camId) {
  const modalEl = document.getElementById('cameraProfileModal');
  if (!modalEl) {
    showToast('Profile modal not found. Please refresh the page.', 'error');
    return;
  }

  await setProfileFormValues(camId);

  const existingModal = bootstrap.Modal.getInstance(modalEl);
  if (existingModal) {
    existingModal.dispose();
  }

  const modal = new bootstrap.Modal(modalEl);
  modal.show();
}

async function setProfileFormValues(camId) {
  const profileCameraId = document.getElementById('profileCameraId');
  const profileModalTitle = document.getElementById('profileModalTitle');
  const profileCameraName = document.getElementById('profileCameraName');
  const profilePurpose = document.getElementById('profilePurpose');
  const profileDetectionEnabled = document.getElementById('profileDetectionEnabled');
  const profileCameraEnabled = document.getElementById('profileCameraEnabled');
  
  if (!profileCameraId || !profileModalTitle || !profileCameraName || !profilePurpose || !profileDetectionEnabled || !profileCameraEnabled) {
    console.error('Profile form elements not found!');
    return;
  }
  
  // Clear form first to prevent showing previous camera's values
  profileCameraId.value = '';
  profileCameraName.value = '';
  profilePurpose.value = '';
  profileDetectionEnabled.checked = true;
  profileCameraEnabled.value = 'true';
  
  profileCameraId.value = camId;
  profileModalTitle.textContent = `Camera ${camId + 1} Profile`;
  
  // Load existing profile FIRST before setting defaults - wait for it to complete
  try {
    const response = await fetch(`/api/cameras/${camId}/profile`);
    const data = await response.json();
    console.log('Loaded profile for camera', camId, data);
    
    if (data.ok && data.profile) {
      const profile = data.profile;
      profileCameraName.value = profile.name || '';
      profilePurpose.value = profile.purpose || '';
      profileDetectionEnabled.checked = profile.detection_enabled !== false;
      profileCameraEnabled.value = profile.camera_enabled !== false ? 'true' : 'false';
      console.log('Set form values:', {name: profile.name, purpose: profile.purpose, detection_enabled: profile.detection_enabled, camera_enabled: profile.camera_enabled});
    } else {
      // Only set defaults if no profile exists
      profileCameraName.value = '';
      profilePurpose.value = '';
      profileDetectionEnabled.checked = true;
      profileCameraEnabled.value = 'true';
      console.log('No profile found, using defaults');
    }
  } catch (fetchError) {
    console.warn('Could not load profile, using defaults:', fetchError);
    // Set defaults on error
    profileCameraName.value = '';
    profilePurpose.value = '';
    profileDetectionEnabled.checked = true;
    profileCameraEnabled.value = 'true';
  }
}

function closeCameraProfileModal() {
  const modalEl = document.getElementById('cameraProfileModal');
  if (!modalEl) return;

  try {
    const modalInstance = bootstrap.Modal.getInstance(modalEl);
    if (modalInstance) {
      modalInstance.hide();
    }
  } catch (e) {
    console.warn('Bootstrap modal hide failed:', e);
  }

  modalEl.classList.remove('show');
  modalEl.setAttribute('aria-hidden', 'true');
  modalEl.style.display = 'none';
  document.body.classList.remove('modal-open');
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
}

async function saveCameraProfile() {
  const camIdEl = document.getElementById('profileCameraId');
  const nameEl = document.getElementById('profileCameraName');
  const purposeEl = document.getElementById('profilePurpose');
  const detectionEnabledEl = document.getElementById('profileDetectionEnabled');
  const cameraEnabledEl = document.getElementById('profileCameraEnabled');
  
  if (!camIdEl || !nameEl || !purposeEl || !detectionEnabledEl || !cameraEnabledEl) {
    showToast('Form elements not found', 'error');
    console.error('Missing form elements:', {camIdEl, nameEl, purposeEl, detectionEnabledEl, cameraEnabledEl});
    return;
  }
  
  const camId = parseInt(camIdEl.value);
  const name = nameEl.value.trim();
  const purpose = purposeEl.value.trim();
  const detectionEnabled = detectionEnabledEl.checked;
  const currentCameraEnabled = cameraEnabledEl.value !== 'false';
  
  if (isNaN(camId)) {
    showToast('Invalid camera ID: ' + camIdEl.value, 'error');
    return;
  }
  console.log('Saving profile for camera', camId, {name, purpose, detectionEnabled, camera_enabled: currentCameraEnabled});

  const saveBtn = document.querySelector('#cameraProfileModal .btn.btn-primary-modern');
  let originalSaveContent = null;
  if (saveBtn) {
    saveBtn.disabled = true;
    originalSaveContent = saveBtn.innerHTML;
    saveBtn.innerHTML = `
      <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
      <span class="ms-1">Saving...</span>
    `;
  }
  
  try {
    const response = await fetch(`/api/cameras/${camId}/profile`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: name,
        purpose: purpose,
        detection_enabled: detectionEnabled,
        camera_enabled: currentCameraEnabled  // Preserve current state
      })
    });
    
    const data = await response.json();
    console.log('Save profile response:', data);
    
    if (data.ok) {
      // Close modal immediately (don't wait for alert)
      closeCameraProfileModal();
      
      // Reload cameras to update UI
      await loadCameras();
      refreshCameraFeed(camId);
      
      // Show success message briefly
      const successMsg = document.createElement('div');
      successMsg.className = 'alert alert-success alert-dismissible fade show position-fixed';
      successMsg.style.cssText = 'top: 80px; right: 20px; z-index: 9999; min-width: 300px;';
      successMsg.innerHTML = `
        <strong>Profile saved!</strong> Detection ${detectionEnabled ? 'enabled' : 'disabled'} for Camera ${camId + 1}.
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
      `;
      document.body.appendChild(successMsg);
      setTimeout(() => successMsg.remove(), 3000);
    } else {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = originalSaveContent || '<span class="material-icons-outlined">save</span> Save Profile';
      }
      showToast(data.error || 'Unknown error', 'error');
      if (data.traceback) {
        console.error('Server traceback:', data.traceback);
      }
    }
  } catch (error) {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = originalSaveContent || '<span class="material-icons-outlined">save</span> Save Profile';
    }
    console.error('Error saving profile:', error);
    showToast('Network error: ' + error.message, 'error');
  }

  if (saveBtn) {
    saveBtn.disabled = false;
    saveBtn.innerHTML = originalSaveContent || '<span class="material-icons-outlined">save</span> Save Profile';
  }
}

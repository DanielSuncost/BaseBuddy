// Camera wall: camera group filtering, group buttons, and group manager/edit modals.

// ============ Camera Groups ============

async function loadCameraGroups() {
  try {
    const response = await fetch('/api/camera-groups');
    const data = await response.json();
    if (data.ok) {
      cameraGroups = data.groups || [];
      
      // Restore selected group from localStorage
      const savedGroup = localStorage.getItem('selectedCameraGroup');
      if (savedGroup && (savedGroup === 'all' || cameraGroups.find(g => g.id === savedGroup))) {
        selectedGroup = savedGroup;
      }
      
      renderGroupButtons();
    }
  } catch (e) {
    console.error('Error loading camera groups:', e);
  }
}

function renderGroupButtons() {
  const container = document.getElementById('group-buttons');
  if (!container) return;
  
  // Start with "All" button
  let html = `
    <button class="group-btn ${selectedGroup === 'all' ? 'active' : ''}" data-group="all" onclick="selectGroup('all')">
      <span class="material-icons-outlined">apps</span>
      <span class="group-name">All</span>
    </button>
  `;
  
  // Add group buttons
  cameraGroups.forEach(group => {
    html += `
      <button class="group-btn ${selectedGroup === group.id ? 'active' : ''}" 
              data-group="${group.id}" 
              onclick="selectGroup('${group.id}')"
              style="${selectedGroup === group.id ? 'background:' + group.color + ';border-color:' + group.color : ''}">
        <span class="material-icons-outlined">${group.icon}</span>
        <span class="group-name">${group.name}</span>
      </button>
    `;
  });
  
  container.innerHTML = html;
}

function selectGroup(groupId) {
  selectedGroup = groupId;
  
  // Save to localStorage for persistence
  localStorage.setItem('selectedCameraGroup', groupId);
  
  // Clear canvas contexts (will be recreated for new canvases)
  canvasContexts = {};
  
  renderGroupButtons();
  renderCameras();
  
  // Re-subscribe WebSocket to visible cameras after a short delay
  // (allows DOM to update)
  setTimeout(() => {
    if (socket && socket.connected) {
      const visibleCams = getVisibleCameraIds();
      console.log('[WS] Re-subscribing to cameras:', visibleCams);
      socket.emit('subscribe', { cameras: visibleCams });
    } else {
      console.log('[WS] Socket not connected, reinitializing...');
      initWebSocketStreaming();
    }
  }, 100);
}

function getVisibleCameraIds() {
  if (selectedGroup === 'all') {
    return cameras.map(c => c.id);
  }
  const group = cameraGroups.find(g => g.id === selectedGroup);
  if (group) {
    return cameras.filter(c => group.camera_ids.includes(c.id)).map(c => c.id);
  }
  return [];
}

function openGroupManager() {
  // Create modal for managing groups
  const modal = document.createElement('div');
  modal.id = 'groupManagerModal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-content" style="max-width:600px;">
      <div class="modal-header">
        <h3><span class="material-icons-outlined">folder_special</span> Manage Camera Groups</h3>
        <button onclick="closeGroupManager()" class="modal-close">&times;</button>
      </div>
      <div class="modal-body">
        <div id="groupList" style="margin-bottom:20px;"></div>
        <button class="btn btn-primary" onclick="createNewGroup()">
          <span class="material-icons-outlined">add</span> New Group
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'flex';
  renderGroupList();
}

function closeGroupManager() {
  const modal = document.getElementById('groupManagerModal');
  if (modal) modal.remove();
}

function renderGroupList() {
  const container = document.getElementById('groupList');
  if (!container) return;
  
  if (cameraGroups.length === 0) {
    container.innerHTML = '<p style="color:#888;">No groups yet. Create one to organize your cameras.</p>';
    return;
  }
  
  let html = '';
  cameraGroups.forEach(group => {
    html += `
      <div class="group-list-item" style="display:flex; align-items:center; gap:12px; padding:12px; background:#f5f5f5; border-radius:8px; margin-bottom:8px;">
        <span class="material-icons-outlined" style="color:${group.color};">${group.icon}</span>
        <div style="flex:1;">
          <div style="font-weight:500;">${group.name}</div>
          <div style="font-size:12px; color:#888;">${group.camera_ids.length} camera(s)</div>
        </div>
        <button class="btn btn-sm btn-secondary" onclick="editGroup('${group.id}')">
          <span class="material-icons-outlined" style="font-size:14px;">edit</span>
        </button>
        <button class="btn btn-sm btn-danger" onclick="deleteGroup('${group.id}')">
          <span class="material-icons-outlined" style="font-size:14px;">delete</span>
        </button>
      </div>
    `;
  });
  
  container.innerHTML = html;
}

async function createNewGroup() {
  const name = prompt('Enter group name:', 'New Group');
  if (!name) return;
  
  try {
    const response = await fetch('/api/camera-groups', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, icon: 'folder', camera_ids: [] })
    });
    const data = await response.json();
    if (data.ok) {
      await loadCameraGroups();
      renderGroupList();
      editGroup(data.group.id);  // Open editor immediately
    }
  } catch (e) {
    alert('Error creating group: ' + e.message);
  }
}

async function editGroup(groupId) {
  const group = cameraGroups.find(g => g.id === groupId);
  if (!group) return;
  
  closeGroupManager();
  
  // Create edit modal
  const icons = ['home', 'business', 'storefront', 'warehouse', 'yard', 'garage', 'meeting_room', 'security', 'videocam', 'nature', 'park', 'pets', 'directions_car', 'fitness_center', 'factory'];
  const colors = ['#1a73e8', '#ea4335', '#34a853', '#fbbc04', '#9334e6', '#ff6d01', '#46bdc6', '#185abc'];
  
  const modal = document.createElement('div');
  modal.id = 'groupEditModal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-content" style="max-width:500px;">
      <div class="modal-header">
        <h3><span class="material-icons-outlined">${group.icon}</span> Edit: ${group.name}</h3>
        <button onclick="closeGroupEdit()" class="modal-close">&times;</button>
      </div>
      <div class="modal-body">
        <div style="margin-bottom:16px;">
          <label style="display:block; margin-bottom:6px; font-weight:500;">Group Name</label>
          <input type="text" id="editGroupName" value="${group.name}" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:6px;">
        </div>
        
        <div style="margin-bottom:16px;">
          <label style="display:block; margin-bottom:6px; font-weight:500;">Icon</label>
          <div style="display:flex; flex-wrap:wrap; gap:8px;">
            ${icons.map(icon => `
              <button type="button" onclick="selectGroupIcon('${icon}')" 
                      class="icon-btn ${group.icon === icon ? 'selected' : ''}"
                      style="padding:8px; border:2px solid ${group.icon === icon ? group.color : '#ddd'}; border-radius:8px; background:${group.icon === icon ? '#e8f0fe' : '#fff'}; cursor:pointer;">
                <span class="material-icons-outlined">${icon}</span>
              </button>
            `).join('')}
          </div>
        </div>
        
        <div style="margin-bottom:16px;">
          <label style="display:block; margin-bottom:6px; font-weight:500;">Color</label>
          <div style="display:flex; gap:8px;">
            ${colors.map(color => `
              <button type="button" onclick="selectGroupColor('${color}')"
                      style="width:32px; height:32px; border-radius:50%; background:${color}; border:3px solid ${group.color === color ? '#000' : 'transparent'}; cursor:pointer;">
              </button>
            `).join('')}
          </div>
        </div>
        
        <div style="margin-bottom:16px;">
          <label style="display:block; margin-bottom:6px; font-weight:500;">Cameras in Group</label>
          <div id="groupCamerasList" style="max-height:200px; overflow-y:auto; border:1px solid #ddd; border-radius:6px; padding:8px;">
            ${cameras.map(cam => `
              <label style="display:flex; align-items:center; gap:8px; padding:6px; cursor:pointer;">
                <input type="checkbox" name="groupCameras" value="${cam.id}" ${group.camera_ids.includes(cam.id) ? 'checked' : ''}>
                <span>${cam.name || 'Camera ' + (cam.id + 1)}</span>
              </label>
            `).join('')}
          </div>
        </div>
        
        <div style="display:flex; gap:12px; justify-content:flex-end;">
          <button class="btn btn-secondary" onclick="closeGroupEdit()">Cancel</button>
          <button class="btn btn-primary" onclick="saveGroupEdit('${groupId}')">Save Changes</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'flex';
  
  // Store current edit state
  window.editingGroup = { ...group };
}

function selectGroupIcon(icon) {
  window.editingGroup.icon = icon;
  // Update UI
  document.querySelectorAll('.icon-btn').forEach(btn => {
    btn.classList.remove('selected');
    btn.style.borderColor = '#ddd';
    btn.style.background = '#fff';
  });
  event.target.closest('.icon-btn').classList.add('selected');
  event.target.closest('.icon-btn').style.borderColor = window.editingGroup.color;
  event.target.closest('.icon-btn').style.background = '#e8f0fe';
}

function selectGroupColor(color) {
  window.editingGroup.color = color;
  // Update UI
  document.querySelectorAll('#groupEditModal button[style*="border-radius:50%"]').forEach(btn => {
    btn.style.borderColor = 'transparent';
  });
  event.target.style.borderColor = '#000';
}

async function saveGroupEdit(groupId) {
  const name = document.getElementById('editGroupName').value.trim();
  const cameraCheckboxes = document.querySelectorAll('input[name="groupCameras"]:checked');
  const camera_ids = Array.from(cameraCheckboxes).map(cb => parseInt(cb.value));
  
  try {
    const response = await fetch('/api/camera-groups/' + groupId, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name,
        icon: window.editingGroup.icon,
        color: window.editingGroup.color,
        camera_ids
      })
    });
    const data = await response.json();
    if (data.ok) {
      closeGroupEdit();
      await loadCameraGroups();
      renderCameras();
    } else {
      alert('Error: ' + data.error);
    }
  } catch (e) {
    alert('Error saving group: ' + e.message);
  }
}

function closeGroupEdit() {
  const modal = document.getElementById('groupEditModal');
  if (modal) modal.remove();
  window.editingGroup = null;
}

async function deleteGroup(groupId) {
  if (!confirm('Delete this group? Cameras will not be deleted.')) return;
  
  try {
    const response = await fetch('/api/camera-groups/' + groupId, { method: 'DELETE' });
    const data = await response.json();
    if (data.ok) {
      if (selectedGroup === groupId) {
        selectedGroup = 'all';
      }
      await loadCameraGroups();
      renderGroupList();
      renderCameras();
    }
  } catch (e) {
    alert('Error deleting group: ' + e.message);
  }
}

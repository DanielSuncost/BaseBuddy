// Camera wall: shared global state and camera list loading.
// Must be loaded FIRST - other camera_wall/*.js files reference these top-level declarations.

// Camera management - Clean Wall View
let cameras = [];
let inactiveCameras = [];
let cameraGroups = [];
let selectedGroup = 'all';
let wallViewMode = localStorage.getItem('wallViewMode') || 'grid';
const birdseyeFrames = {};
const recordingState = {};

// WebSocket Video Streaming - Unlimited cameras!
let socket = null;
let canvasContexts = {};

async function loadCameras() {
  console.log('loadCameras called');
  try {
    const response = await fetch('/api/wall/cameras');
    console.log('loadCameras response status:', response.status);
    const data = await response.json();
    console.log('loadCameras data:', data);
    if (data.ok) {
      cameras = data.active || [];
      inactiveCameras = data.inactive || [];
      console.log('Active cameras:', cameras.length, 'Inactive:', inactiveCameras.length);
      
      // Load groups
      await loadCameraGroups();
    } else {
      console.warn('loadCameras failed:', data.error);
      cameras = [];
      inactiveCameras = [];
    }
    renderCameras();
  } catch (error) {
    console.error('Error loading cameras:', error);
    cameras = [];
    inactiveCameras = [];
    renderCameras();
  }
}

// Listen for camera updates from other tabs/windows
window.addEventListener('storage', (e) => {
  if (e.key === 'camera_update_event') {
    console.log('Camera update detected, reloading cameras...');
    loadCameras();
  }
});

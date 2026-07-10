// Camera wall: WebSocket video streaming, frame drawing, and MJPEG fallback.

function initWebSocketStreaming() {
  // Load Socket.IO client library
  if (typeof io === 'undefined') {
    const script = document.createElement('script');
    script.src = 'https://cdn.socket.io/4.7.2/socket.io.min.js';
    script.onload = () => connectWebSocket();
    document.head.appendChild(script);
  } else {
    connectWebSocket();
  }
}

function subscribeActiveCameras() {
  const camIds = cameras.map(c => c.id);
  socket.emit('subscribe', { cameras: camIds });
  
  camIds.forEach(id => {
    const loading = document.getElementById('loading-' + id);
    if (loading) loading.innerHTML = '<span class="material-icons-outlined spinning">sync</span><span>Streaming...</span>';
  });
}

function connectWebSocket() {
  // Already connected (e.g. after a re-render): just refresh the subscription
  // instead of stacking duplicate event handlers on the same socket
  if (socket) {
    if (socket.connected) {
      console.log('[WS] Already connected, re-subscribing...');
      subscribeActiveCameras();
    }
    return;
  }
  
  console.log('[WS] Connecting to WebSocket...');
  socket = io();
  
  socket.on('connect', () => {
    console.log('[WS] Connected! Subscribing to cameras...');
    subscribeActiveCameras();
  });
  
  socket.on('disconnect', () => {
    console.log('[WS] Disconnected');
    // Show reconnecting state
    cameras.forEach(cam => {
      const loading = document.getElementById('loading-' + cam.id);
      if (loading) {
        loading.classList.remove('hidden');
        loading.innerHTML = '<span class="material-icons-outlined spinning">sync</span><span>Reconnecting...</span>';
      }
    });
  });
  
  socket.on('frame', (data) => {
    const { cam_id, frame } = data;
    drawFrame(cam_id, frame);
  });
  
  socket.on('connect_error', (error) => {
    console.error('[WS] Connection error:', error);
    // Fall back to MJPEG if WebSocket fails
    console.log('[WS] Falling back to MJPEG streaming...');
    fallbackToMJPEG();
  });
}

function drawFrame(camId, frameB64) {
  birdseyeFrames[camId] = frameB64;
  if (wallViewMode === 'birdseye') {
    renderBirdseye();
  }
  const canvas = document.getElementById('canvas-' + camId);
  if (!canvas) return;
  
  // Get or create context; the cached context may belong to a canvas that was
  // replaced by a re-render, so verify it still points at the current element
  let ctx = canvasContexts[camId];
  if (!ctx || ctx.canvas !== canvas) {
    ctx = canvas.getContext('2d');
    canvasContexts[camId] = ctx;
  }
  
  // Create image from base64
  const img = new Image();
  img.onload = () => {
    // Set canvas size to match container
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    
    // Draw image scaled to fit
    const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
    const x = (canvas.width - img.width * scale) / 2;
    const y = (canvas.height - img.height * scale) / 2;
    
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, x, y, img.width * scale, img.height * scale);
    
    // Hide loading indicator on first frame
    const loading = document.getElementById('loading-' + camId);
    if (loading) loading.classList.add('hidden');
  };
  img.src = 'data:image/jpeg;base64,' + frameB64;
}

function fallbackToMJPEG() {
  // Replace canvases with img tags using MJPEG
  cameras.forEach(cam => {
    const canvas = document.getElementById('canvas-' + cam.id);
    if (canvas) {
      const img = document.createElement('img');
      img.src = '/video/raw/' + cam.id;
      img.style.cssText = 'width:100%; height:100%; object-fit:contain;';
      img.onload = () => {
        const loading = document.getElementById('loading-' + cam.id);
        if (loading) loading.classList.add('hidden');
      };
      canvas.parentNode.replaceChild(img, canvas);
    }
  });
}

// Pause/resume for API calls (WebSocket doesn't need this but keep for compatibility)
function pauseVideoStreams() {
  // WebSocket handles this automatically - no action needed
}

function resumeVideoStreams() {
  // WebSocket handles this automatically - no action needed
}

function refreshCameraFeed(camId) {
  const imgContainer = document.getElementById(`cameraImg${camId}`);
  if (!imgContainer) return;
  const img = imgContainer.querySelector('img');
  if (!img) return;
  const cacheBuster = Date.now();
  const baseSrc = `/video/${camId}`;
  img.src = `${baseSrc}?t=${cacheBuster}`;
}

// Stop video streams before page unload to prevent browser hanging on refresh
window.addEventListener('beforeunload', function() {
  // Find all video img elements and clear their src to stop MJPEG streams
  document.querySelectorAll('img[src*="/video/"]').forEach(img => {
    img.src = '';
  });
});

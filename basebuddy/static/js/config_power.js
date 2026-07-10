(function () {
  var statusInterval = null;

  function toast(msg) {
    if (window.UI && UI.toast) UI.toast(msg);
    else alert(msg);
  }

  async function loadStatus() {
    try {
      var r = await fetch('/api/power/status');
      var data = await r.json();
      if (data.error) return;
      document.getElementById('current-profile').textContent = (data.current_profile || '').toUpperCase();
      document.getElementById('detection-rate').textContent = data.detection_rate;
      document.getElementById('frame-skip').textContent = '1/' + data.frame_skip_rate;
      document.getElementById('gpu-status').textContent = data.gpu_enabled ? 'On' : 'Off';
      document.getElementById('current-time').textContent = new Date().toLocaleTimeString();
      document.getElementById('night-mode').textContent = data.is_night_time ? 'Yes' : 'No';
      document.getElementById('profile-mode').textContent = data.manual_override ? 'Manual' : 'Auto';
      document.getElementById('profile-select').value = data.manual_override ? data.current_profile : 'auto';
      updateProfileTable(data.profiles);
    } catch (e) { console.error(e); }
  }

  function updateProfileTable(profiles) {
    var tbody = document.getElementById('profile-table');
    var settings = [
      { key: 'ai_fps', label: 'Detection FPS', format: function (v) { return v + ' fps'; } },
      { key: 'frame_skip_rate', label: 'Frame skip', format: function (v) { return '1/' + v; } },
      { key: 'recording_quality', label: 'Recording quality', format: function (v) { return v; } },
      { key: 'gpu_enabled', label: 'GPU', format: function (v) { return v ? 'On' : 'Off'; } },
    ];
    tbody.innerHTML = settings.map(function (s) {
      return '<tr><td><strong>' + s.label + '</strong></td>' +
        ['maximum', 'high', 'medium', 'low', 'minimum'].map(function (p) {
          return '<td>' + s.format(profiles[p][s.key]) + '</td>';
        }).join('') + '</tr>';
    }).join('');
  }

  async function setProfile(profile) {
    var r = await fetch('/api/power/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: profile }),
    });
    var data = await r.json();
    toast(data.ok ? 'Profile updated' : ('Error: ' + (data.error || 'failed')));
    if (data.ok) loadStatus();
  }

  document.getElementById('power-apply').addEventListener('click', function () {
    setProfile(document.getElementById('profile-select').value);
  });
  document.getElementById('power-reset-auto').addEventListener('click', function () { setProfile('auto'); });

  document.getElementById('power-night-save').addEventListener('click', async function () {
    var r = await fetch('/api/power/night-hours', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start: document.getElementById('night-start').value,
        end: document.getElementById('night-end').value,
      }),
    });
    var data = await r.json();
    toast(data.ok ? 'Night hours updated' : ('Error: ' + (data.error || 'failed')));
    if (data.ok) loadStatus();
  });

  loadStatus();
  statusInterval = setInterval(loadStatus, 5000);
  window.addEventListener('beforeunload', function () { clearInterval(statusInterval); });
})();

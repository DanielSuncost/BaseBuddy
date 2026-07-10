(function () {
  function toast(msg, ok) {
    var el = document.getElementById('setup-toast');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    el.className = 'setup-toast ' + (ok ? 'ok' : 'err');
    setTimeout(function () { el.hidden = true; }, 6000);
  }

  function applyStatus(data) {
    var fill = document.getElementById('setup-progress-fill');
    var label = document.getElementById('setup-progress-label');
    if (fill) fill.style.width = (data.progress || 0) + '%';
    if (label) label.textContent = (data.progress || 0) + '% complete';

    (data.steps || []).forEach(function (step) {
      var section = document.querySelector('.setup-step[data-step="' + step.id + '"]');
      if (!section) return;
      section.classList.toggle('done', !!step.done);
      var check = section.querySelector('.setup-step-check');
      if (check) check.hidden = !step.done;
    });

    var v = data.values || {};
    if (v.cam1 != null) document.getElementById('setup-cam1').value = v.cam1;
    if (v.cam2 != null) document.getElementById('setup-cam2').value = v.cam2;
    document.getElementById('setup-detection').checked = v.detection_enabled !== false;
    if (v.telegram_chat_id) document.getElementById('setup-telegram-chat').value = v.telegram_chat_id;
    document.getElementById('setup-fallback').checked = v.notify_fallback !== false;
  }

  async function loadStatus() {
    var r = await fetch('/api/setup/status');
    var j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Failed to load status');
    applyStatus(j);
    return j;
  }

  document.getElementById('setup-save-cameras').addEventListener('click', async function () {
    try {
      var r = await fetch('/api/setup/cameras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cam1: document.getElementById('setup-cam1').value.trim(),
          cam2: document.getElementById('setup-cam2').value.trim(),
          detection_enabled: document.getElementById('setup-detection').checked,
          notify_enabled: true,
        }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error);
      applyStatus(j.status);
      toast('Cameras saved. Restart BaseBuddy if this is a new stream.', true);
    } catch (e) { toast(e.message, false); }
  });

  document.getElementById('setup-save-telegram').addEventListener('click', async function () {
    try {
      var r = await fetch('/api/setup/telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bot_token: document.getElementById('setup-telegram-token').value.trim(),
          chat_id: document.getElementById('setup-telegram-chat').value.trim(),
        }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error);
      applyStatus(j.status);
      toast('Telegram credentials saved.', true);
    } catch (e) { toast(e.message, false); }
  });

  document.getElementById('setup-test-telegram').addEventListener('click', async function () {
    var token = document.getElementById('setup-telegram-token').value.trim();
    var chat = document.getElementById('setup-telegram-chat').value.trim();
    if (token && chat) {
      await fetch('/api/setup/telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: token, chat_id: chat }),
      });
    }
    try {
      var r = await fetch('/api/setup/telegram/test', { method: 'POST' });
      var j = await r.json();
      toast(j.ok ? 'Test message sent — check Telegram.' : (j.error || 'Test failed'), !!j.ok);
      if (j.ok) await loadStatus();
    } catch (e) { toast(e.message, false); }
  });

  document.getElementById('setup-save-alerts').addEventListener('click', async function () {
    try {
      var r = await fetch('/api/setup/alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          create_rule: document.getElementById('setup-create-rule').checked,
          use_fallback: document.getElementById('setup-fallback').checked,
        }),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error);
      applyStatus(j.status);
      toast('Alert settings saved.', true);
    } catch (e) { toast(e.message, false); }
  });

  document.getElementById('setup-finish').addEventListener('click', async function () {
    try {
      var r = await fetch('/api/setup/complete', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error);
      toast('Setup complete!', true);
      setTimeout(function () { window.location.href = '/'; }, 800);
    } catch (e) { toast(e.message, false); }
  });

  loadStatus().catch(function (e) { toast(e.message, false); });
})();

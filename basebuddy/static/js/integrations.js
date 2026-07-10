(function () {
  const BOOL = [
    'MQTT_ENABLED', 'NOTIFY_ENABLED', 'NOTIFY_FALLBACK_GLOBAL', 'NOTIFY_INCLUDE_CLIP_DEFAULT',
    'MULTIPROC_DETECTION', 'LPR_ENABLED', 'SMTP_USE_TLS',
  ];
  const FIELDS = [
    'MQTT_HOST', 'MQTT_PORT', 'MQTT_TOPIC_PREFIX', 'MQTT_CLIENT_ID', 'MQTT_USERNAME', 'MQTT_PASSWORD',
    'NOTIFY_WEBHOOK_URL', 'NOTIFY_PUBLIC_BASE_URL',
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID',
    'PUSHOVER_USER_KEY', 'PUSHOVER_API_TOKEN',
    'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'SMTP_TO',
    'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM_NUMBER', 'TWILIO_TO_NUMBER',
    'RECORDING_MODE', 'EVENT_CLIP_PRE_S', 'EVENT_CLIP_POST_S',
    'FFMPEG_HWACCEL', 'INFERENCE_BACKEND', 'LPR_CLASSES',
  ].concat(BOOL);

  const CHANNELS = [
    { id: 'telegram', label: 'Telegram' },
    { id: 'email', label: 'Email' },
    { id: 'pushover', label: 'Pushover' },
    { id: 'sms', label: 'SMS' },
    { id: 'webhook', label: 'Webhook' },
  ];

  let cameras = [];
  let rules = [];

  function toast(msg, ok) {
    const el = document.getElementById('int-toast');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    el.className = 'int-toast ' + (ok ? 'ok' : 'err');
    setTimeout(function () { el.hidden = true; }, 5000);
  }

  async function loadCameras() {
    const r = await fetch('/api/integrations/cameras');
    const j = await r.json();
    cameras = j.ok ? (j.cameras || []) : [];
  }

  async function loadRules() {
    const r = await fetch('/api/integrations/notification-rules');
    const j = await r.json();
    rules = j.ok ? (j.rules || []) : [];
    renderRules();
  }

  function camOptions(selected) {
    let html = '<option value="">All cameras</option>';
    cameras.forEach(function (c) {
      const sel = String(selected) === String(c.id) ? ' selected' : '';
      html += '<option value="' + c.id + '"' + sel + '>' + escapeHtml(c.name) + '</option>';
    });
    return html;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function channelChecks(rule, idx) {
    const active = rule.channels || [];
    return CHANNELS.map(function (ch) {
      const ck = active.indexOf(ch.id) >= 0 ? ' checked' : '';
      return '<label class="nr-ch"><input type="checkbox" data-idx="' + idx + '" data-ch="' + ch.id + '"' + ck + '>' + ch.label + '</label>';
    }).join('');
  }

  function renderRules() {
    const body = document.getElementById('nr-body');
    const empty = document.getElementById('nr-empty');
    if (!body) return;
    if (!rules.length) {
      body.innerHTML = '';
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    body.innerHTML = rules.map(function (rule, idx) {
      return (
        '<tr data-idx="' + idx + '">' +
        '<td><input type="checkbox" class="nr-enabled" data-idx="' + idx + '"' + (rule.enabled ? ' checked' : '') + '></td>' +
        '<td><select class="nr-camera" data-idx="' + idx + '">' + camOptions(rule.camera_id) + '</select></td>' +
        '<td><input type="text" class="nr-class" data-idx="' + idx + '" value="' + escapeHtml(rule.class_name || '*') + '" placeholder="person"></td>' +
        '<td><select class="nr-when" data-idx="' + idx + '">' +
        ['start', 'end', 'both', 'region'].map(function (w) {
          return '<option value="' + w + '"' + (rule.notify_on === w ? ' selected' : '') + '>' + w + '</option>';
        }).join('') + '</select></td>' +
        '<td><input type="number" class="nr-conf" data-idx="' + idx + '" min="0" max="1" step="0.05" value="' + (rule.min_confidence || 0) + '" style="width:4rem"></td>' +
        '<td><input type="number" class="nr-cd" data-idx="' + idx + '" min="5" max="3600" value="' + (rule.cooldown_s || 60) + '" style="width:4rem"></td>' +
        '<td class="nr-channels">' + channelChecks(rule, idx) + '</td>' +
        '<td><input type="checkbox" class="nr-snap" data-idx="' + idx + '"' + (rule.include_snapshot !== false ? ' checked' : '') + '></td>' +
        '<td><input type="checkbox" class="nr-clip" data-idx="' + idx + '"' + (rule.include_clip ? ' checked' : '') + '></td>' +
        '<td><button type="button" class="btn btn-secondary btn-sm nr-del" data-id="' + (rule.id || '') + '" data-idx="' + idx + '">×</button></td>' +
        '</tr>'
      );
    }).join('');

    body.querySelectorAll('.nr-del').forEach(function (btn) {
      btn.addEventListener('click', function () {
        deleteRule(parseInt(btn.getAttribute('data-id'), 10), parseInt(btn.getAttribute('data-idx'), 10));
      });
    });
    body.querySelectorAll('input, select').forEach(function (el) {
      el.addEventListener('change', function () { syncRuleFromRow(parseInt(el.getAttribute('data-idx'), 10)); });
    });
  }

  function syncRuleFromRow(idx) {
    const row = document.querySelector('tr[data-idx="' + idx + '"]');
    if (!row || !rules[idx]) return;
    const channels = [];
    row.querySelectorAll('.nr-ch input:checked').forEach(function (inp) {
      channels.push(inp.getAttribute('data-ch'));
    });
    const camSel = row.querySelector('.nr-camera');
    rules[idx] = {
      id: rules[idx].id,
      enabled: row.querySelector('.nr-enabled').checked,
      camera_id: camSel.value === '' ? null : parseInt(camSel.value, 10),
      class_name: row.querySelector('.nr-class').value.trim() || '*',
      notify_on: row.querySelector('.nr-when').value,
      min_confidence: parseFloat(row.querySelector('.nr-conf').value) || 0,
      cooldown_s: parseFloat(row.querySelector('.nr-cd').value) || 60,
      channels: channels,
      include_snapshot: row.querySelector('.nr-snap').checked,
      include_clip: row.querySelector('.nr-clip').checked,
    };
  }

  async function saveRule(rule) {
    const r = await fetch('/api/integrations/notification-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'save rule failed');
    return j.rule;
  }

  async function saveAllRules() {
    for (let i = 0; i < rules.length; i++) {
      syncRuleFromRow(i);
      rules[i] = await saveRule(rules[i]);
    }
  }

  async function deleteRule(id, idx) {
    if (id) {
      await fetch('/api/integrations/notification-rules/' + id, { method: 'DELETE' });
    }
    rules.splice(idx, 1);
    renderRules();
  }

  document.getElementById('nr-add').addEventListener('click', function () {
    rules.push({
      enabled: true,
      camera_id: null,
      class_name: 'person',
      notify_on: 'start',
      min_confidence: 0.35,
      cooldown_s: 60,
      channels: ['telegram'],
      include_snapshot: true,
      include_clip: false,
    });
    renderRules();
  });

  async function loadSettings() {
    const r = await fetch('/api/integrations/settings');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    const d = j.data;
    FIELDS.forEach(function (k) {
      const el = document.getElementById(k);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!d[k];
      else el.value = d[k] != null ? d[k] : '';
    });
  }

  function collectSettings() {
    const body = {};
    FIELDS.forEach(function (k) {
      const el = document.getElementById(k);
      if (!el) return;
      body[k] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return body;
  }

  document.getElementById('int-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    try {
      await saveAllRules();
      const r = await fetch('/api/integrations/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectSettings()),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error);
      toast('Saved rules and settings.', true);
      await loadRules();
    } catch (err) {
      toast(err.message || String(err), false);
    }
  });

  document.getElementById('int-test-mqtt').addEventListener('click', async function () {
    try {
      const r = await fetch('/api/integrations/test-mqtt', { method: 'POST' });
      const j = await r.json();
      toast(j.message || j.error || 'Done', !!j.ok);
    } catch (err) { toast(String(err), false); }
  });

  document.querySelectorAll('.int-test').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      try {
        const r = await fetch('/api/integrations/test-notify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel: btn.getAttribute('data-channel') }),
        });
        const j = await r.json();
        toast(j.ok ? 'Test sent.' : (j.error || 'Failed'), !!j.ok);
      } catch (err) { toast(String(err), false); }
    });
  });

  (async function init() {
    try {
      await loadCameras();
      await loadSettings();
      await loadRules();
    } catch (err) {
      toast(err.message, false);
    }
  })();
})();

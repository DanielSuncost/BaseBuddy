(function () {
  var listEl = document.getElementById('blogger-list');
  var historyEl = document.getElementById('blogger-history');
  var modal = document.getElementById('blogger-modal');
  var previewModal = document.getElementById('blogger-preview-modal');
  var form = document.getElementById('blogger-form');
  var destFields = document.getElementById('blogger-dest-fields');
  var destType = document.getElementById('blogger-dest-type');
  var scheduleMode = document.getElementById('blogger-schedule-mode');
  var monitorsCache = [];

  function toast(msg, err) {
    var el = document.getElementById('plants-toast');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    el.style.background = err ? '#d93025' : '#202124';
    setTimeout(function () { el.hidden = true; }, 4500);
  }

  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function fmtTs(ts) {
    if (!ts) return 'Never';
    return new Date(ts * 1000).toLocaleString();
  }

  function scheduleLabel(ch) {
    var s = ch.schedule || {};
    if (s.mode === 'times' && s.times && s.times.length) {
      return 'Daily ' + s.times.join(', ');
    }
    var sec = s.interval_s || 86400;
    if (sec >= 86400) return 'Every ' + Math.round(sec / 86400) + ' day(s)';
    if (sec >= 3600) return 'Every ' + Math.round(sec / 3600) + ' hour(s)';
    return 'Every ' + Math.round(sec / 60) + ' min';
  }

  var DEST_FIELDS = {
    webhook: [
      { key: 'url', label: 'Webhook URL', type: 'url', required: true, placeholder: 'https://hooks.zapier.com/…' },
      { key: 'method', label: 'HTTP method', type: 'select', options: ['POST', 'GET', 'PUT'] },
      { key: 'mode', label: 'Payload', type: 'select', options: ['json', 'multipart'] },
      { key: 'include_image_base64', label: 'Include base64 image in JSON', type: 'checkbox' },
    ],
    mastodon: [
      { key: 'instance', label: 'Instance URL', type: 'url', required: true, placeholder: 'https://mastodon.social' },
      { key: 'access_token', label: 'Access token', type: 'password', required: true },
      { key: 'visibility', label: 'Visibility', type: 'select', options: ['public', 'unlisted', 'private'] },
    ],
    wordpress: [
      { key: 'site_url', label: 'Site URL', type: 'url', required: true, placeholder: 'https://myblog.com' },
      { key: 'username', label: 'Username', type: 'text', required: true },
      { key: 'app_password', label: 'Application password', type: 'password', required: true },
      { key: 'status', label: 'Post status', type: 'select', options: ['publish', 'draft', 'pending'] },
    ],
    telegram: [
      { key: 'chat_id', label: 'Chat ID', type: 'text', placeholder: 'Uses Integrations default if empty' },
      { key: 'bot_token', label: 'Bot token override', type: 'password', placeholder: 'Uses Integrations default if empty' },
    ],
    bluesky: [
      { key: 'handle', label: 'Handle', type: 'text', required: true, placeholder: 'you.bsky.social' },
      { key: 'app_password', label: 'App password', type: 'password', required: true },
      { key: 'service', label: 'PDS URL', type: 'url', placeholder: 'https://bsky.social' },
    ],
    mqtt: [
      { key: 'topic', label: 'Topic', type: 'text', required: true, placeholder: 'home/plants/monstera/post' },
      { key: 'qos', label: 'QoS', type: 'number', placeholder: '0' },
    ],
  };

  function renderDestFields(type, cfg) {
    cfg = cfg || {};
    var fields = DEST_FIELDS[type] || DEST_FIELDS.webhook;
    destFields.innerHTML = fields.map(function (f) {
      var id = 'bdest-' + f.key;
      if (f.type === 'checkbox') {
        return (
          '<label class="blogger-check">' +
          '<input type="checkbox" id="' + id + '" data-key="' + f.key + '"' +
          (cfg[f.key] ? ' checked' : '') + '> ' + escapeHtml(f.label) + '</label>'
        );
      }
      if (f.type === 'select') {
        var opts = (f.options || []).map(function (o) {
          return '<option value="' + o + '"' + (cfg[f.key] === o ? ' selected' : '') + '>' + o + '</option>';
        }).join('');
        return '<label>' + escapeHtml(f.label) +
          '<select id="' + id + '" data-key="' + f.key + '">' + opts + '</select></label>';
      }
      var val = cfg[f.key] != null ? cfg[f.key] : '';
      return '<label>' + escapeHtml(f.label) +
        '<input type="' + (f.type || 'text') + '" id="' + id + '" data-key="' + f.key + '"' +
        (f.required ? ' required' : '') +
        ' value="' + escapeHtml(String(val)) + '"' +
        (f.placeholder ? ' placeholder="' + escapeHtml(f.placeholder) + '"' : '') + '></label>';
    }).join('');
  }

  function readDestConfig() {
    var cfg = {};
    destFields.querySelectorAll('[data-key]').forEach(function (el) {
      var key = el.getAttribute('data-key');
      if (el.type === 'checkbox') cfg[key] = el.checked;
      else if (el.type === 'number') cfg[key] = parseInt(el.value, 10) || 0;
      else cfg[key] = el.value.trim();
    });
    return cfg;
  }

  destType.addEventListener('change', function () {
    renderDestFields(destType.value, {});
  });

  scheduleMode.addEventListener('change', function () {
    var times = scheduleMode.value === 'times';
    document.getElementById('blogger-times-wrap').hidden = !times;
    document.getElementById('blogger-interval-wrap').hidden = times;
  });

  async function loadMonitorsForSelect(selectedId) {
    var r = await fetch('/api/plants/monitors');
    var j = await r.json();
    monitorsCache = j.monitors || [];
    var sel = document.getElementById('blogger-monitor-select');
    sel.innerHTML = monitorsCache.map(function (m) {
      return '<option value="' + m.id + '">' + escapeHtml(m.name) + '</option>';
    }).join('') || '<option value="">No plants — add one in Monitoring tab</option>';
    if (selectedId) sel.value = selectedId;
  }

  function renderChannels(channels) {
    if (!channels.length) {
      listEl.innerHTML = '<p class="scenes-muted scenes-empty">No post channels yet. Create one to auto-publish plant updates.</p>';
      return;
    }
    listEl.innerHTML = channels.map(function (ch) {
      var dest = (ch.destination || {}).type || 'webhook';
      var last = ch.last_post;
      var lastLine = last
        ? (last.ok ? 'Last posted ' + fmtTs(last.posted_at) : 'Failed ' + fmtTs(last.posted_at))
        : 'Not posted yet';
      return (
        '<article class="plants-card blogger-card" data-id="' + ch.id + '">' +
        '<div class="plants-card-head">' +
        '<div><h3>' + escapeHtml(ch.name) + '</h3>' +
        '<p class="plants-meta">' + escapeHtml(ch.monitor_name || ch.monitor_id) +
        ' · ' + escapeHtml(scheduleLabel(ch)) +
        ' · ' + escapeHtml(dest) +
        (ch.enabled === false ? ' · <span class="blogger-paused">Paused</span>' : '') +
        '</p><p class="plants-meta">' + escapeHtml(lastLine) + '</p></div></div>' +
        '<div class="plants-actions">' +
        '<button type="button" class="btn btn-primary btn-sm blogger-publish" data-id="' + ch.id + '">Post now</button>' +
        '<button type="button" class="btn btn-secondary btn-sm blogger-preview" data-id="' + ch.id + '">Preview</button>' +
        '<button type="button" class="btn btn-secondary btn-sm blogger-edit" data-id="' + ch.id + '">Edit</button>' +
        '<button type="button" class="btn btn-secondary btn-sm blogger-delete" data-id="' + ch.id + '">Remove</button>' +
        '</div></article>'
      );
    }).join('');

    listEl.querySelectorAll('.blogger-publish').forEach(function (btn) {
      btn.addEventListener('click', function () { publish(btn.getAttribute('data-id'), btn); });
    });
    listEl.querySelectorAll('.blogger-preview').forEach(function (btn) {
      btn.addEventListener('click', function () { preview(btn.getAttribute('data-id')); });
    });
    listEl.querySelectorAll('.blogger-edit').forEach(function (btn) {
      btn.addEventListener('click', function () { openEdit(btn.getAttribute('data-id')); });
    });
    listEl.querySelectorAll('.blogger-delete').forEach(function (btn) {
      btn.addEventListener('click', function () { removeChannel(btn.getAttribute('data-id')); });
    });
  }

  function renderHistory(posts) {
    if (!posts.length) {
      historyEl.innerHTML = '<p class="scenes-muted">No posts yet.</p>';
      return;
    }
    historyEl.innerHTML = (
      '<ul class="blogger-history-list">' +
      posts.map(function (p) {
        return (
          '<li class="blogger-history-item' + (p.ok ? '' : ' failed') + '">' +
          '<span class="blogger-history-ts">' + fmtTs(p.posted_at) + '</span> ' +
          '<span class="blogger-history-dest">' + escapeHtml(p.destination) + '</span> — ' +
          escapeHtml(p.title || p.text || '').slice(0, 80) +
          (p.error ? ' <em>(' + escapeHtml(p.error) + ')</em>' : '') +
          '</li>'
        );
      }).join('') +
      '</ul>'
    );
  }

  async function loadChannels() {
    var r = await fetch('/api/plants/blogger/channels');
    var j = await r.json();
    renderChannels(j.channels || []);
  }

  async function loadHistory() {
    var r = await fetch('/api/plants/blogger/history?limit=15');
    var j = await r.json();
    renderHistory(j.posts || []);
  }

  function openCreate() {
    document.getElementById('blogger-modal-title').textContent = 'New post channel';
    document.getElementById('blogger-channel-id').value = '';
    form.reset();
    document.getElementById('blogger-enabled').checked = true;
    form.querySelector('[name="title_template"]').value = '{{monitor_name}} update — {{date}}';
    scheduleMode.value = 'interval';
    scheduleMode.dispatchEvent(new Event('change'));
    destType.value = 'webhook';
    renderDestFields('webhook', {});
    loadMonitorsForSelect();
    modal.hidden = false;
  }

  async function openEdit(id) {
    var r = await fetch('/api/plants/blogger/channels');
    var j = await r.json();
    var ch = (j.channels || []).find(function (c) { return c.id === id; });
    if (!ch) return;
    document.getElementById('blogger-modal-title').textContent = 'Edit post channel';
    document.getElementById('blogger-channel-id').value = ch.id;
    form.querySelector('[name="name"]').value = ch.name || '';
    document.getElementById('blogger-enabled').checked = ch.enabled !== false;
    await loadMonitorsForSelect(ch.monitor_id);

    var sch = ch.schedule || {};
    scheduleMode.value = sch.mode || 'interval';
    scheduleMode.dispatchEvent(new Event('change'));
    if (sch.mode === 'times') {
      form.schedule_times.value = (sch.times || []).join(', ');
    } else {
      form.interval_s.value = sch.interval_s || 86400;
    }

    var dest = ch.destination || {};
    destType.value = dest.type || 'webhook';
    renderDestFields(destType.value, dest.config || {});

    var c = ch.content || {};
    form.querySelector('[name="include_image"]').checked = c.include_image !== false;
    form.querySelector('[name="include_health_score"]').checked = c.include_health_score !== false;
    form.querySelector('[name="include_summary"]').checked = c.include_summary !== false;
    form.querySelector('[name="include_species"]').checked = c.include_species !== false;
    form.querySelector('[name="include_greenness"]').checked = !!c.include_greenness;
    form.querySelector('[name="include_coverage"]').checked = !!c.include_coverage;
    form.querySelector('[name="include_recommendations"]').checked = !!c.include_recommendations;
    form.querySelector('[name="run_vision_before_post"]').checked = c.run_vision_before_post !== false;
    form.title_template.value = c.title_template || '{{monitor_name}} update — {{date}}';
    form.custom_intro.value = c.custom_intro || '';
    form.hashtags.value = c.hashtags || '';
    form.caption_template.value = c.caption_template || '';
    modal.hidden = false;
  }

  function buildPayload() {
    var fd = new FormData(form);
    var mode = fd.get('schedule_mode') || 'interval';
    var schedule;
    if (mode === 'times') {
      schedule = {
        mode: 'times',
        times: String(fd.get('schedule_times') || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean),
        enabled: true,
      };
    } else {
      schedule = {
        mode: 'interval',
        interval_s: parseInt(fd.get('interval_s'), 10) || 86400,
        enabled: true,
      };
    }
    return {
      name: fd.get('name'),
      monitor_id: fd.get('monitor_id'),
      enabled: document.getElementById('blogger-enabled').checked,
      schedule: schedule,
      destination: {
        type: destType.value,
        config: readDestConfig(),
      },
      content: {
        include_image: form.querySelector('[name="include_image"]').checked,
        include_health_score: form.querySelector('[name="include_health_score"]').checked,
        include_summary: form.querySelector('[name="include_summary"]').checked,
        include_species: form.querySelector('[name="include_species"]').checked,
        include_greenness: form.querySelector('[name="include_greenness"]').checked,
        include_coverage: form.querySelector('[name="include_coverage"]').checked,
        include_recommendations: form.querySelector('[name="include_recommendations"]').checked,
        run_vision_before_post: form.querySelector('[name="run_vision_before_post"]').checked,
        title_template: fd.get('title_template'),
        custom_intro: fd.get('custom_intro'),
        hashtags: fd.get('hashtags'),
        caption_template: fd.get('caption_template'),
      },
    };
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var id = document.getElementById('blogger-channel-id').value;
    var body = buildPayload();
    try {
      var r = await fetch(id ? '/api/plants/blogger/channels/' + id : '/api/plants/blogger/channels', {
        method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Save failed');
      modal.hidden = true;
      toast('Post channel saved', false);
      loadChannels();
    } catch (err) { toast(err.message, true); }
  });

  async function publish(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Posting…'; }
    try {
      var r = await fetch('/api/plants/blogger/channels/' + id + '/publish', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Post failed');
      toast('Published successfully', false);
      loadChannels();
      loadHistory();
    } catch (e) { toast(e.message, true); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Post now'; }
    }
  }

  async function preview(id) {
    try {
      var r = await fetch('/api/plants/blogger/channels/' + id + '/preview', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Preview failed');
      var html = '<h3>' + escapeHtml(j.title) + '</h3>';
      if (j.image_path) {
        html += '<img class="blogger-preview-img" src="' + escapeHtml(j.image_path) + '?t=' + Date.now() + '" alt="">';
      }
      html += '<pre class="blogger-preview-text">' + escapeHtml(j.text) + '</pre>';
      document.getElementById('blogger-preview-body').innerHTML = html;
      previewModal.hidden = false;
    } catch (e) { toast(e.message, true); }
  }

  async function removeChannel(id) {
    if (!confirm('Remove this post channel?')) return;
    await fetch('/api/plants/blogger/channels/' + id, { method: 'DELETE' });
    loadChannels();
  }

  document.getElementById('blogger-add').addEventListener('click', openCreate);
  document.getElementById('blogger-cancel').addEventListener('click', function () { modal.hidden = true; });
  document.getElementById('blogger-preview-close').addEventListener('click', function () { previewModal.hidden = true; });

  document.querySelectorAll('[data-plants-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.getAttribute('data-plants-tab');
      document.querySelectorAll('[data-plants-tab]').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      document.getElementById('plants-panel-monitor').hidden = tab !== 'monitor';
      document.getElementById('plants-panel-blogger').hidden = tab !== 'blogger';
      if (tab === 'blogger') {
        loadChannels();
        loadHistory();
      }
    });
  });

  renderDestFields('webhook', {});
})();

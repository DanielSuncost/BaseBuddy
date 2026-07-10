(function () {
  var toastEl = document.getElementById('training-toast');

  function toast(msg, err) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    toastEl.className = 'training-toast' + (err ? ' err' : '');
    setTimeout(function () { toastEl.hidden = true; }, 4500);
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function fmtTs(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  async function loadStats() {
    var r = await fetch('/api/training/stats');
    var j = await r.json();
    if (!j.ok) return;
    var s = j.stats || {};
    document.getElementById('training-stats').innerHTML = [
      ['False positives', s.false_positives],
      ['Labeled', s.labeled_detections],
      ['Person labels', s.person_labels],
      ['FP zones', s.false_positive_zones],
      ['Face embeddings', s.person_embeddings],
      ['Named people', s.named_people],
    ].map(function (row) {
      return '<div class="stat-box"><div class="stat-val">' + row[1] + '</div><div class="stat-lbl">' + esc(row[0]) + '</div></div>';
    }).join('');
    var g = j.gpu || {};
    document.getElementById('training-gpu').textContent = g.cuda_available
      ? 'GPU: ' + (g.device_name || 'CUDA') + (g.vram_gb ? ' (' + g.vram_gb + ' GB)' : '')
      : 'Local training requires CUDA + ultralytics. ' + (g.error || 'CPU-only mode not supported for fine-tune.');
  }

  function renderDatasets(list) {
    var el = document.getElementById('datasets-list');
    if (!list.length) {
      el.innerHTML = '<p class="muted">No datasets yet. Label detections in Gallery, then build a dataset.</p>';
      return;
    }
    el.innerHTML = list.map(function (d) {
      var st = d.stats || {};
      var remote = d.remote_uri ? '<span class="tag ok">R2</span>' : '';
      return (
        '<article class="training-row">' +
        '<div><strong>' + esc(d.name) + '</strong> <code>' + esc(d.id) + '</code> ' + remote +
        '<p class="muted">' + esc(d.dataset_type) + ' · ' + fmtTs(d.created_at) + ' · ' + esc(d.status) +
        (st.train != null ? ' · train ' + st.train + ' / val ' + (st.val || 0) : '') +
        '</p></div>' +
        '<div class="row-actions">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-upload="' + d.id + '">Upload to R2</button>' +
        '<button type="button" class="btn btn-primary btn-sm" data-train="' + d.id + '" data-dtype="' + esc(d.dataset_type) + '">Train</button>' +
        '<button type="button" class="btn btn-secondary btn-sm" data-del-ds="' + d.id + '">Delete</button>' +
        '</div></article>'
      );
    }).join('');
    el.querySelectorAll('[data-upload]').forEach(function (btn) {
      btn.addEventListener('click', function () { uploadDataset(btn.getAttribute('data-upload'), btn); });
    });
    el.querySelectorAll('[data-train]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openTrainModal(btn.getAttribute('data-train'), btn.getAttribute('data-dtype'));
      });
    });
    el.querySelectorAll('[data-del-ds]').forEach(function (btn) {
      btn.addEventListener('click', function () { deleteDataset(btn.getAttribute('data-del-ds')); });
    });
  }

  function renderJobs(list) {
    var el = document.getElementById('jobs-list');
    if (!list.length) {
      el.innerHTML = '<p class="muted">No training jobs yet.</p>';
      return;
    }
    el.innerHTML = list.map(function (j) {
      return (
        '<article class="training-row">' +
        '<div><strong>' + esc(j.id) + '</strong> · ' + esc(j.job_type) + ' · ' + esc(j.status) +
        '<p class="muted">' + esc(j.base_model || '') + ' · dataset ' + esc(j.dataset_id) + ' · ' + fmtTs(j.created_at) +
        (j.error ? ' · <span class="err">' + esc(j.error) + '</span>' : '') + '</p></div>' +
        '<div class="row-actions">' +
        (j.status === 'completed' && j.output_path
          ? '<button type="button" class="btn btn-primary btn-sm" data-deploy="' + j.id + '">Set active model</button>'
          : '') +
        '<button type="button" class="btn btn-secondary btn-sm" data-refresh-job="' + j.id + '">Refresh</button>' +
        '</div></article>'
      );
    }).join('');
    el.querySelectorAll('[data-deploy]').forEach(function (btn) {
      btn.addEventListener('click', function () { deployJob(btn.getAttribute('data-deploy'), btn); });
    });
    el.querySelectorAll('[data-refresh-job]').forEach(function (btn) {
      btn.addEventListener('click', function () { refreshJob(btn.getAttribute('data-refresh-job')); });
    });
  }

  async function loadDatasets() {
    var r = await fetch('/api/training/datasets');
    var j = await r.json();
    renderDatasets(j.datasets || []);
  }

  async function loadJobs() {
    var r = await fetch('/api/training/jobs');
    var j = await r.json();
    renderJobs(j.jobs || []);
  }

  async function loadFpZones() {
    var el = document.getElementById('fp-zones-list');
    try {
      var r = await fetch('/api/gallery/false_positive_zones');
      var j = await r.json();
      var zones = j.zones || j.data || [];
      if (!zones.length) {
        el.innerHTML = '<p class="muted">No ignore zones. Mark a detection as false positive + ignore region in Gallery.</p>';
        return;
      }
      el.innerHTML = '<ul class="fp-zone-ul">' + zones.map(function (z) {
        return (
          '<li><span>Cam ' + (z.camera_id + 1) + ' · ' + esc(z.class_name) + '</span>' +
          '<button type="button" class="btn btn-secondary btn-sm" data-del-zone="' + z.id + '">Delete</button></li>'
        );
      }).join('') + '</ul>';
      el.querySelectorAll('[data-del-zone]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          deleteZone(btn.getAttribute('data-del-zone'));
        });
      });
    } catch (e) {
      el.innerHTML = '<p class="muted">Could not load zones.</p>';
    }
  }

  async function deleteZone(id) {
    if (!confirm('Delete this ignore zone?')) return;
    await fetch('/api/gallery/false_positive_zones/' + id, { method: 'DELETE' });
    loadFpZones();
    loadStats();
  }

  var buildModal = document.getElementById('build-modal');
  var buildForm = document.getElementById('build-form');

  document.getElementById('btn-build-yolo').addEventListener('click', function () {
    document.getElementById('build-modal-title').textContent = 'Build YOLO dataset';
    document.getElementById('build-type').value = 'yolo';
    document.getElementById('val-ratio-wrap').hidden = false;
    document.getElementById('labeled-only-wrap').hidden = false;
    document.getElementById('neg-wrap').hidden = false;
    buildModal.hidden = false;
  });

  document.getElementById('btn-build-reid').addEventListener('click', function () {
    document.getElementById('build-modal-title').textContent = 'Build person re-ID pack';
    document.getElementById('build-type').value = 'person_reid';
    document.getElementById('val-ratio-wrap').hidden = true;
    document.getElementById('labeled-only-wrap').hidden = true;
    document.getElementById('neg-wrap').hidden = true;
    buildModal.hidden = false;
  });

  document.getElementById('build-cancel').addEventListener('click', function () { buildModal.hidden = true; });

  buildForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    var fd = new FormData(buildForm);
    var body = {
      type: fd.get('type'),
      name: fd.get('name'),
      hours: parseInt(fd.get('hours'), 10) || 8760,
      val_ratio: parseFloat(fd.get('val_ratio')) || 0.2,
      labeled_only: buildForm.querySelector('[name="labeled_only"]').checked,
      include_negatives: buildForm.querySelector('[name="include_negatives"]').checked,
    };
    try {
      var r = await fetch('/api/training/datasets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Build failed');
      buildModal.hidden = true;
      toast('Dataset built: ' + j.dataset.id, false);
      loadDatasets();
      loadStats();
    } catch (err) { toast(err.message, true); }
  });

  async function uploadDataset(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Uploading…'; }
    try {
      var r = await fetch('/api/training/datasets/' + id + '/upload', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Upload failed');
      toast('Uploaded ' + j.uploaded + ' files to R2', false);
      loadDatasets();
    } catch (e) { toast(e.message, true); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Upload to R2'; }
    }
  }

  async function deleteDataset(id) {
    if (!confirm('Delete this dataset locally?')) return;
    await fetch('/api/training/datasets/' + id, { method: 'DELETE' });
    loadDatasets();
  }

  var trainModal = document.getElementById('train-modal');
  var trainForm = document.getElementById('train-form');

  function openTrainModal(datasetId, dtype) {
    document.getElementById('train-dataset-id').value = datasetId;
    trainForm.dataset.dtype = dtype || 'yolo';
    var localRadio = trainForm.querySelector('input[value="local"]');
    if (localRadio) {
      localRadio.disabled = dtype === 'person_reid';
      if (dtype === 'person_reid') {
        trainForm.querySelector('input[value="cloud"]').checked = true;
      }
    }
    trainModal.hidden = false;
  }

  document.getElementById('train-cancel').addEventListener('click', function () { trainModal.hidden = true; });

  trainForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    var fd = new FormData(trainForm);
    var mode = fd.get('mode');
    var body = {
      dataset_id: fd.get('dataset_id'),
      base_model: fd.get('base_model'),
      epochs: parseInt(fd.get('epochs'), 10) || 50,
    };
    var url = mode === 'cloud' ? '/api/training/jobs/cloud' : '/api/training/jobs/local';
    if (mode === 'cloud') {
      body.job_type = trainForm.dataset.dtype === 'person_reid' ? 'person_reid' : 'yolo';
    }
    try {
      var r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Start failed');
      trainModal.hidden = true;
      toast('Job started: ' + (j.job_id || j.cloud_job_id), false);
      loadJobs();
    } catch (err) { toast(err.message, true); }
  });

  async function deployJob(id, btn) {
    if (btn) btn.disabled = true;
    try {
      var r = await fetch('/api/training/jobs/' + id + '/deploy', { method: 'POST' });
      var j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Deploy failed');
      toast('Active model updated — restart detection workers', false);
    } catch (e) { toast(e.message, true); }
    finally { if (btn) btn.disabled = false; }
  }

  async function refreshJob(id) {
    await fetch('/api/training/jobs/' + id);
    loadJobs();
  }

  loadStats();
  loadDatasets();
  loadJobs();
  loadFpZones();
})();

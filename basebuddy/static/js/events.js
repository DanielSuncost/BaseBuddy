(function () {
  const list = document.getElementById('events-list');
  const modal = document.getElementById('ev-modal');
  const modalImg = document.getElementById('ev-modal-img');
  const modalVideo = document.getElementById('ev-modal-video');
  const modalGif = document.getElementById('ev-modal-gif');
  const frameStrip = document.getElementById('ev-frame-strip');
  const statusEl = document.getElementById('ev-modal-status');
  const stitchPanel = document.getElementById('ev-stitch-panel');
  const stitchList = document.getElementById('ev-stitch-list');

  const PER_PAGE = 50;
  const pager = document.getElementById('events-pager');
  const pagerBottom = document.getElementById('events-pager-bottom');
  const pagerMeta = document.getElementById('events-pager-meta');
  const pagerMetaBottom = document.getElementById('events-pager-meta-bottom');
  const loadEarlierBtns = [
    document.getElementById('ev-load-earlier'),
    document.getElementById('ev-load-earlier-bottom'),
  ].filter(Boolean);

  let currentSession = null;
  let nearbyDetections = [];
  let adjacentSessions = [];
  let loadedSessions = [];
  let listOffset = 0;
  let listTotal = 0;
  let listTotalKnown = false;
  let listHasMore = false;
  let listLoading = false;

  function fmtTs(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function setStatus(msg, show) {
    if (!statusEl) return;
    if (show === false || !msg) {
      statusEl.hidden = true;
      statusEl.textContent = '';
      return;
    }
    statusEl.hidden = false;
    statusEl.textContent = msg;
  }

  function showMedia(kind) {
    modalImg.hidden = kind !== 'image';
    modalVideo.hidden = kind !== 'video';
    modalGif.hidden = kind !== 'gif';
    if (kind !== 'video') {
      modalVideo.pause();
      modalVideo.removeAttribute('src');
      modalVideo.load();
    }
    if (kind !== 'gif') modalGif.removeAttribute('src');
  }

  async function loadCameras() {
    const sel = document.getElementById('ev-filter-camera');
    try {
      const r = await fetch('/api/wall/cameras');
      const j = await r.json();
      (j.active || []).forEach(function (c) {
        const o = document.createElement('option');
        o.value = c.id;
        o.textContent = c.name || ('Camera ' + (c.id + 1));
        sel.appendChild(o);
      });
    } catch (e) { /* ignore */ }
  }

  function filterParams(offset) {
    const params = new URLSearchParams();
    const cam = document.getElementById('ev-filter-camera').value;
    const cls = document.getElementById('ev-filter-class').value.trim();
    const hours = document.getElementById('ev-filter-hours').value;
    if (cam !== '') params.set('camera_id', cam);
    if (cls) params.set('class', cls);
    params.set('hours', hours);
    params.set('limit', String(PER_PAGE));
    params.set('offset', String(offset || 0));
    return params;
  }

  function formatTotal(n) {
    if (typeof n !== 'number' || !isFinite(n)) return String(n);
    return n.toLocaleString();
  }

  function setLoadEarlierBusy(busy) {
    loadEarlierBtns.forEach(function (btn) {
      btn.disabled = busy || !listHasMore;
      btn.setAttribute('aria-disabled', btn.disabled ? 'true' : 'false');
      if (busy) {
        btn.textContent = 'Loading…';
      } else {
        var icon = btn.id === 'ev-load-earlier-bottom' ? 'expand_more' : 'history';
        btn.innerHTML =
          '<span class="material-icons-outlined">' + icon + '</span> Load earlier';
      }
    });
  }

  function updatePager() {
    var shown = loadedSessions.length;
    var empty = !shown && !listTotal;
    var totalLabel;
    if (empty) {
      totalLabel = null;
    } else if (listTotalKnown) {
      totalLabel = formatTotal(listTotal);
    } else if (listHasMore) {
      totalLabel = formatTotal(shown) + '+';
    } else {
      totalLabel = formatTotal(shown);
    }
    var meta = empty
      ? 'No events'
      : 'Showing ' + formatTotal(shown) + ' of ' + totalLabel;

    if (pager) pager.hidden = false;
    if (pagerMeta) pagerMeta.textContent = meta;
    if (pagerBottom) pagerBottom.hidden = empty;
    if (pagerMetaBottom) pagerMetaBottom.textContent = meta;

    loadEarlierBtns.forEach(function (btn) {
      // Keep the control in the layout; disable (don't display:none) when nothing left.
      btn.hidden = false;
      btn.disabled = listLoading || !listHasMore || empty;
      btn.setAttribute('aria-disabled', btn.disabled ? 'true' : 'false');
      btn.classList.toggle('is-exhausted', !listHasMore && !listLoading && !empty);
      if (!listLoading) {
        var icon = btn.id === 'ev-load-earlier-bottom' ? 'expand_more' : 'history';
        var label = listHasMore ? 'Load earlier' : 'All loaded';
        btn.innerHTML =
          '<span class="material-icons-outlined">' + icon + '</span> ' + label;
      }
    });
  }

  function inferHasMore(j, batch, nextOffset) {
    if (typeof j.has_more === 'boolean') return j.has_more;
    if (typeof j.total === 'number') return nextOffset < j.total;
    // Old API without pagination fields: a full page usually means more exist.
    return batch.length >= PER_PAGE;
  }

  async function loadEvents(opts) {
    const append = !!(opts && opts.append);
    if (listLoading) return;
    listLoading = true;

    if (!append) {
      listOffset = 0;
      loadedSessions = [];
      listTotal = 0;
      listTotalKnown = false;
      listHasMore = false;
      list.innerHTML = '<p class="events-loading">Loading…</p>';
      if (pagerMeta) pagerMeta.textContent = 'Loading…';
      if (pagerBottom) pagerBottom.hidden = true;
      setLoadEarlierBusy(true);
    } else {
      setLoadEarlierBusy(true);
    }

    try {
      const r = await fetch('/api/events/sessions?' + filterParams(listOffset).toString());
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'failed');
      const batch = j.sessions || [];
      const baseOffset = typeof j.offset === 'number' ? j.offset : listOffset;
      listOffset = baseOffset + batch.length;
      listHasMore = inferHasMore(j, batch, listOffset);

      if (typeof j.total === 'number') {
        listTotal = j.total;
        listTotalKnown = true;
      } else {
        listTotalKnown = false;
        listTotal = listOffset;
      }

      if (append) {
        loadedSessions = loadedSessions.concat(batch);
        appendRows(batch);
      } else {
        loadedSessions = batch;
        render(loadedSessions);
      }
      updatePager();
    } catch (err) {
      if (!append) {
        list.innerHTML = '<p class="events-error">' + err.message + '</p>';
        listTotal = 0;
        listTotalKnown = false;
        loadedSessions = [];
        listHasMore = false;
        updatePager();
        if (pagerMeta) pagerMeta.textContent = 'Could not load events';
      } else if (pagerMeta) {
        pagerMeta.textContent = 'Could not load earlier: ' + err.message;
        if (pagerMetaBottom) pagerMetaBottom.textContent = pagerMeta.textContent;
      }
    } finally {
      listLoading = false;
      updatePager();
    }
  }

  function sessionRowHtml(s) {
    const thumb = s.snapshot_url
      ? '<img class="ev-thumb" src="' + esc(s.snapshot_url) + '" alt="">'
      : '<div class="ev-thumb ev-thumb-empty"><span class="material-icons-outlined">image</span></div>';
    const clip = s.clip_url
      ? '<a class="btn btn-secondary btn-sm ev-clip-link" href="' + esc(s.clip_url) + '" target="_blank" rel="noopener"><span class="material-icons-outlined">play_circle</span> Clip</a>'
      : '';
    const plate = s.plate_text ? '<span class="ev-plate">' + esc(s.plate_text) + '</span>' : '';
    return (
      '<article class="ev-row" data-session-id="' + esc(s.id) + '" tabindex="0" role="button">' + thumb +
      '<div class="ev-body">' +
      '<div class="ev-title"><strong>' + esc(s.class_name) + '</strong> · Camera ' + (s.camera_id + 1) +
      (s.track_id != null ? ' · track ' + esc(s.track_id) : '') + plate + '</div>' +
      '<div class="ev-meta">' + fmtTs(s.started_at) +
      (s.ended_at ? ' → ' + fmtTs(s.ended_at) : ' · active') +
      ' · score ' + (s.max_confidence != null ? (s.max_confidence * 100).toFixed(0) + '%' : '—') + '</div>' +
      (s.region_labels ? '<div class="ev-regions">' + esc(s.region_labels) + '</div>' : '') +
      '</div><div class="ev-actions">' + clip + '</div></article>'
    );
  }

  function bindRow(row) {
    row.addEventListener('click', function (e) {
      if (e.target.closest('.ev-clip-link')) return;
      openModalById(row.getAttribute('data-session-id'));
    });
    row.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openModalById(row.getAttribute('data-session-id'));
      }
    });
  }

  function render(sessions) {
    if (!sessions.length) {
      list.innerHTML = '<p class="events-empty">No events in this period.</p>';
      return;
    }
    list.innerHTML = sessions.map(sessionRowHtml).join('');
    list.querySelectorAll('.ev-row').forEach(bindRow);
  }

  function appendRows(sessions) {
    if (!sessions.length) return;
    const empty = list.querySelector('.events-empty, .events-loading, .events-error');
    if (empty) empty.remove();
    const wrap = document.createElement('div');
    wrap.innerHTML = sessions.map(sessionRowHtml).join('');
    Array.prototype.forEach.call(wrap.children, function (row) {
      bindRow(row);
      list.appendChild(row);
    });
  }

  async function openModalById(sessionId) {
    if (!sessionId) return;
    try {
      const r = await fetch('/api/events/sessions/' + encodeURIComponent(sessionId));
      const j = await r.json();
      if (!j.ok || !j.session) throw new Error(j.error || 'not found');
      openModal(j.session);
    } catch (err) {
      setStatus('Could not open event: ' + err.message, true);
    }
  }

  function openModal(session) {
    currentSession = session;
    nearbyDetections = [];
    adjacentSessions = [];
    stitchPanel.hidden = true;
    stitchList.innerHTML = '';
    frameStrip.hidden = true;
    frameStrip.innerHTML = '';

    document.getElementById('ev-modal-title').textContent =
      (session.class_name || 'Event') +
      (session.track_id != null ? ' · track ' + session.track_id : '');
    document.getElementById('ev-modal-badge').textContent = session.class_name || 'object';
    document.getElementById('ev-modal-meta').textContent =
      'Camera ' + (session.camera_id + 1) + ' · ' + fmtTs(session.started_at) +
      (session.ended_at ? ' → ' + fmtTs(session.ended_at) : ' · active') +
      (session.max_confidence != null
        ? ' · ' + (session.max_confidence * 100).toFixed(0) + '%'
        : '') +
      (session.plate_text ? ' · plate ' + session.plate_text : '');

    const regions = document.getElementById('ev-modal-regions');
    if (session.region_labels) {
      regions.hidden = false;
      regions.textContent = session.region_labels;
    } else {
      regions.hidden = true;
    }

    showMedia('image');
    modalImg.src = session.snapshot_url || '';
    modalImg.alt = session.class_name || 'Event snapshot';

    const clipBtn = document.getElementById('ev-btn-clip');
    if (session.clip_url) {
      clipBtn.hidden = false;
      clipBtn.href = session.clip_url;
    } else {
      clipBtn.hidden = true;
      clipBtn.removeAttribute('href');
    }

    document.getElementById('ev-btn-stitch').hidden = true;
    setStatus('', false);
    modal.hidden = false;
    document.body.style.overflow = 'hidden';

    loadAdjacent(session.id);
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
    showMedia('image');
    currentSession = null;
    setStatus('', false);
  }

  async function loadAdjacent(sessionId) {
    try {
      const r = await fetch(
        '/api/events/sessions/' + encodeURIComponent(sessionId) + '/adjacent-clips?gap_s=15'
      );
      const j = await r.json();
      if (!j.ok) return;
      adjacentSessions = j.sessions || [];
      const stitchBtn = document.getElementById('ev-btn-stitch');
      stitchBtn.hidden = !j.can_stitch;
      if (j.can_stitch) {
        stitchBtn.textContent = '';
        stitchBtn.innerHTML =
          '<span class="material-icons-outlined">library_add</span> Stitch adjacent clips (' +
          j.stitchable_count + ')';
      }
    } catch (e) { /* ignore */ }
  }

  function renderFrameStrip(dets) {
    if (!dets.length) {
      frameStrip.hidden = true;
      frameStrip.innerHTML = '';
      return;
    }
    frameStrip.hidden = false;
    frameStrip.innerHTML = dets.map(function (d, i) {
      const src = d.thumb_url || d.image_url || '';
      return '<img src="' + esc(src) + '" alt="" data-idx="' + i + '"' +
        (i === 0 ? ' class="active"' : '') + '>';
    }).join('');
    frameStrip.querySelectorAll('img').forEach(function (img) {
      img.addEventListener('click', function () {
        const idx = parseInt(img.getAttribute('data-idx'), 10);
        const det = nearbyDetections[idx];
        if (!det) return;
        frameStrip.querySelectorAll('img').forEach(function (el) {
          el.classList.toggle('active', el === img);
        });
        showMedia('image');
        modalImg.src = det.image_url || det.thumb_url || '';
      });
    });
  }

  async function playNearbyGif() {
    if (!currentSession) return;
    const btn = document.getElementById('ev-btn-gif');
    const windowS = document.getElementById('ev-gif-window').value || '10';
    const defaultHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Loading frames…';
    setStatus('Finding nearby detections (±' + windowS + 's)…', true);

    try {
      const nr = await fetch(
        '/api/events/sessions/' + encodeURIComponent(currentSession.id) +
        '/nearby?window_s=' + encodeURIComponent(windowS) + '&same_class=1'
      );
      const nj = await nr.json();
      if (!nj.ok) throw new Error(nj.error || 'nearby failed');
      nearbyDetections = nj.detections || [];
      renderFrameStrip(nearbyDetections);

      if (nearbyDetections.length < 2) {
        setStatus(
          'Only ' + nearbyDetections.length +
          ' frame(s) found (' + (nj.mode || '?') + '). Try a wider window.',
          true
        );
        if (nearbyDetections.length === 1) {
          showMedia('image');
          modalImg.src = nearbyDetections[0].image_url || nearbyDetections[0].thumb_url || '';
        }
        return;
      }

      const imagePaths = nearbyDetections
        .map(function (d) { return d.full_image_path || d.thumbnail_path; })
        .filter(function (p) { return p && String(p).trim(); });

      btn.textContent = 'Generating GIF…';
      setStatus(
        nearbyDetections.length + ' frames (' + nj.mode + ') — generating GIF…',
        true
      );

      const gr = await fetch('/api/gallery/generate_gif', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_paths: imagePaths,
          fps: 5,
          name: 'ev_' + currentSession.id + '_' + windowS + 's',
        }),
      });
      const gj = await gr.json();
      const gifUrl = gj.gif_path || (gj.data && gj.data.gif_url) || null;
      if (!gj.ok || !gifUrl) throw new Error(gj.error || 'GIF failed');

      showMedia('gif');
      modalGif.src = gifUrl + '?t=' + Date.now();
      setStatus(
        'Playing ' + nearbyDetections.length + ' nearby frames (' + nj.mode + ', ±' + windowS + 's)',
        true
      );
    } catch (err) {
      setStatus('Nearby frames failed: ' + err.message, true);
    } finally {
      btn.disabled = false;
      btn.innerHTML = defaultHtml;
    }
  }

  function playClipInModal(e) {
    if (!currentSession || !currentSession.clip_url) return;
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1)) return;
    e.preventDefault();
    showMedia('video');
    modalVideo.src = currentSession.clip_url;
    modalVideo.play().catch(function () { /* autoplay blocked */ });
    setStatus('Playing event clip', true);
  }

  function showStitchPanel() {
    if (!adjacentSessions.length) return;
    const withClips = adjacentSessions.filter(function (s) {
      return s.clip_url || s.clip_path;
    });
    document.getElementById('ev-stitch-count').textContent =
      '(' + withClips.length + ' clips, overlaps trimmed)';
    stitchList.innerHTML = withClips.map(function (s) {
      const checked = ' checked';
      return (
        '<li><label>' +
        '<input type="checkbox" class="ev-stitch-cb" value="' + esc(s.id) + '"' + checked + '>' +
        '<span><strong>' + esc(s.class_name) + '</strong> · ' + fmtTs(s.started_at) +
        (s.id === currentSession.id ? ' (this event)' : '') +
        '</span></label>' +
        (s.clip_url
          ? '<a href="' + esc(s.clip_url) + '" target="_blank" rel="noopener">open</a>'
          : '') +
        '</li>'
      );
    }).join('');
    stitchPanel.hidden = false;
  }

  async function runStitch() {
    const ids = Array.prototype.map.call(
      stitchList.querySelectorAll('.ev-stitch-cb:checked'),
      function (cb) { return cb.value; }
    );
    if (ids.length < 2) {
      setStatus('Select at least two clips to stitch.', true);
      return;
    }
    const btn = document.getElementById('ev-btn-stitch-run');
    const defaultHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Stitching…';
    setStatus('Stitching ' + ids.length + ' clips (deduping overlap)…', true);
    try {
      const r = await fetch('/api/events/stitch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_ids: ids, gap_s: 15 }),
      });
      const j = await r.json();
      if (!j.ok || !j.url) throw new Error(j.error || 'stitch failed');
      showMedia('video');
      modalVideo.src = j.url;
      modalVideo.play().catch(function () {});
      const trimmed = (j.plan || []).filter(function (p) { return p.trim_start > 0.05; }).length;
      setStatus(
        'Stitched ' + j.clip_count + ' clip(s)' +
        (trimmed ? ' · trimmed ' + trimmed + ' overlapping segment(s)' : '') +
        '. ',
        true
      );
      const link = document.createElement('a');
      link.href = j.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Open stitched clip';
      statusEl.appendChild(link);
    } catch (err) {
      setStatus('Stitch failed: ' + err.message, true);
    } finally {
      btn.disabled = false;
      btn.innerHTML = defaultHtml;
    }
  }

  modal.querySelectorAll('[data-ev-close]').forEach(function (el) {
    el.addEventListener('click', closeModal);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
  });

  document.getElementById('ev-btn-gif').addEventListener('click', playNearbyGif);
  document.getElementById('ev-btn-clip').addEventListener('click', playClipInModal);
  document.getElementById('ev-btn-stitch').addEventListener('click', showStitchPanel);
  document.getElementById('ev-btn-stitch-run').addEventListener('click', runStitch);

  document.getElementById('ev-refresh').addEventListener('click', function () {
    loadEvents();
  });
  ['ev-filter-camera', 'ev-filter-hours'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', function () {
      loadEvents();
    });
  });
  document.getElementById('ev-filter-class').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') loadEvents();
  });
  if (loadEarlierBtns.length) {
    loadEarlierBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (listHasMore) loadEvents({ append: true });
      });
    });
  }

  loadCameras().then(function () { loadEvents(); });
})();

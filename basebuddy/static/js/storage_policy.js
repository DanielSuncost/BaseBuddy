(function () {
  const toast = document.getElementById('sp-toast');
  const logEl = document.getElementById('sp-log');

  const PLANNER_ROWS = [
    { key: 'recordings', label: 'Video recordings', icon: 'movie', hint: 'MP4 segments per camera' },
    { key: 'detections', label: 'Detection gallery', icon: 'photo_library', hint: 'AI snapshot per detection' },
    { key: 'stills', label: 'Timelapse captures', icon: 'camera', hint: 'Source stills', timelapseLink: true },
    { key: 'timelapse', label: 'Timelapse videos', icon: 'slow_motion_video', hint: 'Exported timelapse MP4s' },
  ];

  let loadedSettings = null;
  let editionInfo = null;
  let planType = 'storage';
  let plannerContext = { camera_count: 10, still_interval_sec: 60, timelapse_note: '' };
  let estimateTimer = null;

  function showToast(msg, ok) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, ok ? 'success' : 'error');
    }
    if (!toast) return;
    toast.textContent = msg;
    toast.hidden = false;
    toast.className = 'sp-toast ' + (ok ? 'ok' : 'err');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () { toast.hidden = true; }, 5000);
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function nInt(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const v = parseInt(el.value, 10);
    return Number.isNaN(v) ? null : v;
  }

  function getRemoteMode() {
    const r = document.querySelector('input[name="remote_mode"]:checked');
    return r ? r.value : 'none';
  }

  function buildPlannerRows() {
    const localWrap = document.getElementById('sp-local-rows');
    const cloudWrap = document.getElementById('sp-cloud-rows');
    if (!localWrap || !cloudWrap) return;

    localWrap.innerHTML = PLANNER_ROWS.map(function (row) {
      return (
        '<div class="sp-planner-row" data-key="' + row.key + '">' +
        '<span class="material-icons-outlined sp-planner-icon">' + row.icon + '</span>' +
        '<div class="sp-planner-row-text">' +
        '<span class="sp-planner-row-label">' + row.label + '</span>' +
        '<span class="sp-planner-row-hint">' + row.hint +
        (row.timelapseLink ? ' · <a href="/timelapse">Timelapse tab</a>' : '') +
        '</span></div>' +
        '<div class="sp-planner-row-input">' +
        '<input type="number" class="sp-days-local" data-service="' + row.key + '" min="0" max="3650" value="0" aria-label="' + row.label + ' days on server">' +
        '<span class="sp-planner-unit">days</span></div>' +
        '<span class="sp-planner-row-gb sp-row-gb-local" data-key="' + row.key + '"></span></div>'
      );
    }).join('');

    cloudWrap.innerHTML = PLANNER_ROWS.map(function (row) {
      return (
        '<div class="sp-planner-row" data-key="' + row.key + '">' +
        '<span class="material-icons-outlined sp-planner-icon">' + row.icon + '</span>' +
        '<div class="sp-planner-row-text">' +
        '<span class="sp-planner-row-label">' + row.label + '</span></div>' +
        '<div class="sp-planner-row-input">' +
        '<input type="number" class="sp-days-cloud" data-service="' + row.key + '" min="0" max="3650" value="0" aria-label="' + row.label + ' days in cloud">' +
        '<span class="sp-planner-unit">days</span></div>' +
        '<span class="sp-planner-row-gb sp-row-gb-cloud" data-key="' + row.key + '"></span></div>'
      );
    }).join('');

    document.querySelectorAll('.sp-days-local, .sp-days-cloud').forEach(function (inp) {
      inp.addEventListener('input', scheduleEstimate);
    });
  }

  function populatePlannerFromPolicy(policy) {
    policy = policy || {};
    PLANNER_ROWS.forEach(function (row) {
      const cfg = policy[row.key] || {};
      const localInp = document.querySelector('.sp-days-local[data-service="' + row.key + '"]');
      const cloudInp = document.querySelector('.sp-days-cloud[data-service="' + row.key + '"]');
      if (localInp) localInp.value = cfg.local_days ?? 0;
      if (cloudInp) cloudInp.value = cfg.remote_days ?? 0;
    });
  }

  function collectDaysMap(className) {
    const out = {};
    document.querySelectorAll('.' + className).forEach(function (inp) {
      out[inp.getAttribute('data-service')] = parseInt(inp.value, 10) || 0;
    });
    return out;
  }

  function collectRetentionPolicy() {
    const local = collectDaysMap('sp-days-local');
    const cloud = collectDaysMap('sp-days-cloud');
    const policy = {};
    PLANNER_ROWS.forEach(function (row) {
      policy[row.key] = {
        local_days: local[row.key] || 0,
        remote_days: cloud[row.key] || 0,
      };
    });
    const recLocal = policy.recordings.local_days || 0;
    const detLocal = policy.detections.local_days || 0;
    policy.video_thumbs = { local_days: Math.min(recLocal, 7), remote_days: 0 };
    policy.recording_thumbs = { local_days: Math.min(recLocal, 7), remote_days: 0 };
    if (policy.detections.remote_days > 0 && policy.video_thumbs.remote_days === undefined) {
      policy.video_thumbs.remote_days = 0;
    }
    return policy;
  }

  function setRemoteMode(mode) {
    document.querySelectorAll('input[name="remote_mode"]').forEach(function (r) {
      r.checked = r.value === mode;
    });
    document.querySelectorAll('.sp-dest-pill').forEach(function (pill) {
      const inp = pill.querySelector('input');
      pill.classList.toggle('active', inp && inp.checked);
    });

    const note = document.getElementById('sp-panel-none-note');
    const byo = document.getElementById('sp-panel-byo');
    const managed = document.getElementById('sp-panel-managed');
    const connectDetails = document.getElementById('sp-cloud-connect-details');
    if (note) note.hidden = mode !== 'none';
    if (byo) byo.hidden = mode !== 'byo';
    if (managed) managed.hidden = mode !== 'managed';
    if (connectDetails) connectDetails.open = mode !== 'none';

    const cloudDisabled = mode === 'none';
    document.querySelectorAll('.sp-days-cloud').forEach(function (inp) {
      inp.disabled = cloudDisabled;
    });
    document.getElementById('sp-cloud-rows').classList.toggle('sp-planner-disabled', cloudDisabled);

    scheduleEstimate();
  }

  function scheduleEstimate() {
    clearTimeout(estimateTimer);
    estimateTimer = setTimeout(refreshEstimate, 350);
  }

  async function refreshEstimate() {
    const mode = getRemoteMode();
    const localDays = collectDaysMap('sp-days-local');
    const cloudDays = mode === 'none'
      ? { recordings: 0, detections: 0, stills: 0, timelapse: 0 }
      : collectDaysMap('sp-days-cloud');

    try {
      const r = await fetch('/api/storage-policy/estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cameras: plannerContext.camera_count || 10,
          detections_per_cam_day: 100,
          still_interval_sec: plannerContext.still_interval_sec || 60,
          local_days: localDays,
          cloud_days: cloudDays,
        }),
      });
      const j = await r.json();
      if (!j.ok) return;
      const d = j.data;

      setText('sp-local-est', d.local_total_gb + ' GiB');
      setText('sp-cloud-est', mode === 'none' ? 'Off' : d.cloud_total_gb + ' GiB');

      (d.local_breakdown || []).forEach(function (line) {
        const el = document.querySelector('.sp-row-gb-local[data-key="' + line.id + '"]');
        if (el) el.textContent = line.gb > 0 ? '~' + line.gb + ' GiB' : '';
      });
      (d.cloud_breakdown || []).forEach(function (line) {
        const el = document.querySelector('.sp-row-gb-cloud[data-key="' + line.id + '"]');
        if (el) el.textContent = line.gb > 0 ? '~' + line.gb + ' GiB' : '';
      });

      renderTierSuggest(d.suggested_tier, mode, d.cloud_total_gb);
    } catch (e) {
      /* ignore */
    }
  }

  function renderTierSuggest(tier, mode, cloudGb) {
    const el = document.getElementById('sp-tier-suggest');
    if (!el) return;
    if (mode !== 'managed' || !tier || !tier.label || cloudGb <= 0) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const price = planType === 'inference' ? tier.with_inference_usd : tier.storage_only_usd;
    if (tier.fits) {
      el.innerHTML = '<span class="material-icons-outlined">verified</span> ' +
        '<strong>' + tier.label + '</strong> plan fits (~' + cloudGb + ' GiB, ' + tier.cloud_buffer_days + 'd buffer) · from $' + price + '/mo';
      el.className = 'sp-tier-suggest sp-tier-suggest-ok';
    } else {
      el.innerHTML = '<span class="material-icons-outlined">warning</span> ' +
        'Need more than <strong>' + tier.label + '</strong> (' + tier.cloud_storage_gb + ' GB) — ~' + tier.over_by_gb + ' GiB over';
      el.className = 'sp-tier-suggest sp-tier-suggest-warn';
    }
  }

  function renderPricingTiers(managed) {
    const grid = document.getElementById('sp-tier-grid');
    if (!grid || !managed || !managed.pricing_tiers) return;
    const inference = planType === 'inference';
    grid.innerHTML = managed.pricing_tiers.map(function (tier) {
      const price = inference ? tier.with_inference_usd : tier.storage_only_usd;
      const featured = tier.featured ? ' featured' : '';
      const gb = tier.cloud_storage_gb || tier.storage_gb || 0;
      const days = tier.cloud_buffer_days || 30;
      return (
        '<div class="sp-tier-card' + featured + '">' +
        '<div class="sp-tier-name">' + tier.label + '</div>' +
        '<div class="sp-tier-price">$' + price + '<span class="sp-tier-per">/mo</span></div>' +
        '<ul class="sp-tier-features">' +
        '<li><strong>' + gb + ' GB</strong> cloud storage</li>' +
        '<li><strong>' + days + ' days</strong> rolling buffer</li>' +
        '<li>' + (tier.cameras_hint || '') + '</li></ul></div>'
      );
    }).join('');
  }

  function renderCloudBufferExplain(managed) {
    const list = document.getElementById('sp-quota-policy');
    if (!list || !managed) return;
    const items = managed.cloud_buffer_explain || managed.quota_policy || [];
    list.innerHTML = items.map(function (t) { return '<li>' + t + '</li>'; }).join('');
  }

  function formatGiB(gb) {
    if (gb == null || Number.isNaN(gb)) return '—';
    if (gb >= 100) return gb.toFixed(1) + ' GiB';
    if (gb >= 10) return gb.toFixed(2) + ' GiB';
    if (gb >= 1) return gb.toFixed(2) + ' GiB';
    if (gb > 0) return gb.toFixed(3) + ' GiB';
    return '0 GiB';
  }

  function formatRetentionDays(localDays, remoteDays, cloudActive) {
    const local = (localDays || 0) + 'd local';
    if (!cloudActive || !(remoteDays > 0)) return local;
    return local + ' · ' + remoteDays + 'd cloud';
  }

  function usageBarClass(fraction) {
    if (fraction >= 0.85) return ' sp-usage-bar-warn';
    if (fraction >= 0.6) return ' sp-usage-bar-mid';
    return '';
  }

  function renderUsageBar(gb, maxGb) {
    if (!gb || gb <= 0) {
      return '<span class="sp-usage-bar-empty">—</span>';
    }
    const pct = maxGb > 0 ? Math.max(4, Math.round((gb / maxGb) * 100)) : 0;
    return (
      '<div class="sp-usage-bar-track">' +
      '<div class="sp-usage-bar-fill' + usageBarClass(gb / maxGb) + '" style="width:' + pct + '%"></div>' +
      '</div>'
    );
  }

  function renderUsageBreakdown(breakdown) {
    const tbody = document.getElementById('sp-usage-table-body');
    const tfoot = document.getElementById('sp-usage-table-foot');
    const footnote = document.getElementById('sp-usage-footnote');
    if (!tbody || !breakdown || !Array.isArray(breakdown.rows)) return;

    const cloudActive = !!breakdown.cloud_active;
    const rows = breakdown.rows.filter(function (row) {
      return (row.local_gb || 0) > 0 || (row.cloud_gb || 0) > 0 || (row.local_days || 0) > 0 || (row.remote_days || 0) > 0;
    });
    const displayRows = rows.length ? rows : breakdown.rows.slice(0, 4);

    const maxLocal = Math.max.apply(null, displayRows.map(function (r) { return r.local_gb || 0; }).concat([0.01]));
    const maxCloud = cloudActive
      ? Math.max.apply(null, displayRows.map(function (r) { return r.cloud_gb || 0; }).concat([0.01]))
      : 0.01;

    setText('sp-breakdown-local-total', formatGiB(breakdown.local_total_gb));
    setText('sp-breakdown-cloud-total', cloudActive ? formatGiB(breakdown.cloud_total_gb) : 'Off');

    tbody.innerHTML = displayRows.map(function (row) {
      const cloudCell = cloudActive
        ? '<div class="sp-usage-cell">' + renderUsageBar(row.cloud_gb, maxCloud) +
          '<span class="sp-usage-gb">' + formatGiB(row.cloud_gb) + '</span></div>'
        : '<span class="sp-usage-muted">Off</span>';
      return (
        '<tr>' +
        '<th scope="row" class="sp-usage-type">' + row.label + '</th>' +
        '<td class="sp-col-local"><div class="sp-usage-cell">' + renderUsageBar(row.local_gb, maxLocal) +
        '<span class="sp-usage-gb">' + formatGiB(row.local_gb) + '</span></div></td>' +
        '<td class="sp-col-cloud">' + cloudCell + '</td>' +
        '<td class="sp-col-policy"><span class="sp-usage-policy">' +
        formatRetentionDays(row.local_days, row.remote_days, cloudActive) + '</span></td>' +
        '</tr>'
      );
    }).join('');

    if (tfoot) {
      tfoot.hidden = false;
      tfoot.innerHTML =
        '<tr class="sp-usage-total-row">' +
        '<th scope="row">Total</th>' +
        '<td class="sp-col-local"><strong>' + formatGiB(breakdown.local_total_gb) + '</strong></td>' +
        '<td class="sp-col-cloud"><strong>' + (cloudActive ? formatGiB(breakdown.cloud_total_gb) : 'Off') + '</strong></td>' +
        '<td></td></tr>';
    }

    if (footnote) {
      let note = breakdown.cloud_note || '';
      if (cloudActive && breakdown.cloud_backend === 'byo') {
        note = (note ? note + ' ' : '') + 'Cloud totals reflect your S3/R2 bucket prefix.';
      } else if (cloudActive && breakdown.cloud_backend === 'managed') {
        note = (note ? note + ' ' : '') + 'Cloud totals reflect BaseBuddy Cloud storage.';
      }
      note = (note ? note + ' ' : '') + 'Figures update when you click Refresh.';
      footnote.textContent = note.trim();
    }
  }

  function renderStorageAlerts(forecast) {
    const wrap = document.getElementById('sp-alerts');
    if (!wrap || !forecast || !Array.isArray(forecast.warnings)) return;

    const warnings = forecast.warnings;
    if (!warnings.length) {
      wrap.hidden = true;
      wrap.innerHTML = '';
      return;
    }

    wrap.hidden = false;
    wrap.innerHTML = warnings.map(function (w) {
      const level = w.level || 'info';
      const icon = level === 'critical' ? 'error' : (level === 'warning' ? 'warning' : 'info');
      let html =
        '<div class="sp-alert sp-alert-' + level + '" role="alert">' +
        '<span class="material-icons-outlined sp-alert-icon">' + icon + '</span>' +
        '<div class="sp-alert-body">' +
        '<div class="sp-alert-title">' + w.title + '</div>' +
        '<p class="sp-alert-message">' + w.message + '</p>';
      if (w.action) {
        html += '<p class="sp-alert-action"><strong>Suggested:</strong> ' + w.action + '</p>';
      }
      html += '</div></div>';
      return html;
    }).join('');

    // Scroll first critical alert into view once per session
    if (warnings.some(function (w) { return w.level === 'critical'; }) && !window._spAlertShown) {
      window._spAlertShown = true;
      wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function renderStorageForecast(forecast) {
    const tbody = document.getElementById('sp-forecast-table-body');
    if (!tbody || !forecast) return;

    const rows = (forecast.rows || []).filter(function (r) {
      return (r.gb_per_day || 0) > 0 || (r.used_gb || 0) > 0.01 || (r.local_days || 0) > 0;
    });
    const display = rows.length ? rows : (forecast.rows || []);

    setText('sp-ingest-rate', forecast.ingest_gb_per_day != null
      ? forecast.ingest_gb_per_day.toFixed(2) + ' GiB/day' : '—');
    setText('sp-steady-state', forecast.steady_state_media_gb != null
      ? '~' + forecast.steady_state_media_gb + ' GiB' : '—');

    if (forecast.days_until_full != null && forecast.days_until_full > 0) {
      const d = forecast.days_until_full;
      const cls = d < 7 ? ' sp-forecast-danger' : (d < 30 ? ' sp-forecast-warn' : '');
      const el = document.getElementById('sp-days-until-full');
      if (el) {
        el.textContent = '~' + d + ' days';
        el.className = cls;
      }
    } else {
      setText('sp-days-until-full', '—');
    }

    tbody.innerHTML = display.map(function (row) {
      let rateTxt = row.gb_per_day > 0 ? row.gb_per_day.toFixed(2) + ' GiB/day' : '—';
      if (row.detail) {
        rateTxt += '<br><span class="sp-usage-muted">' + row.detail + '</span>';
      }
      const steady = row.steady_state_gb > 0 ? '~' + row.steady_state_gb + ' GiB' : '—';
      const policy = row.local_days > 0 ? row.local_days + 'd local' : 'not set';
      return (
        '<tr>' +
        '<th scope="row" class="sp-usage-type">' + row.label + '</th>' +
        '<td>' + rateTxt + '</td>' +
        '<td>' + formatGiB(row.used_gb) + '</td>' +
        '<td>' + steady + '</td>' +
        '<td class="sp-col-policy">' + policy + '</td>' +
        '</tr>'
      );
    }).join('');

    const foot = document.getElementById('sp-forecast-footnote');
    if (foot && forecast.headroom_gb != null && forecast.projected_disk_used_gb != null) {
      foot.textContent =
        'Steady-state media ~' + forecast.steady_state_media_gb + ' GiB at current rates. ' +
        'Projected total disk use ~' + forecast.projected_disk_used_gb + ' GiB ' +
        '(~' + (forecast.other_on_disk_gb || 0) + ' GiB non-BaseBuddy). ' +
        'Retention deletes by age, not free space.';
    }

    renderCameraRanking(forecast.camera_ranking);
  }

  function renderCameraRanking(ranking) {
    const panel = document.getElementById('sp-camera-rank-panel');
    const tbody = document.getElementById('sp-camera-rank-body');
    const recs = document.getElementById('sp-camera-recommendations');
    if (!panel || !tbody) return;

    const cameras = (ranking && ranking.cameras) || [];
    if (!cameras.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    const lead = document.getElementById('sp-camera-rank-lead');
    if (lead) {
      lead.textContent =
        'Last ' + (ranking.days || 7) + ' days · ' +
        (ranking.total_events || 0).toLocaleString() + ' detections — ' +
        'filter, disable, or clear the noisiest cameras to free disk.';
    }

    const maxShare = Math.max.apply(null, cameras.map(function (c) { return c.share_pct || 0; }).concat([1]));
    tbody.innerHTML = cameras.slice(0, 12).map(function (cam, idx) {
      const barPct = Math.max(4, Math.round(((cam.share_pct || 0) / maxShare) * 100));
      const hot = (cam.share_pct || 0) >= 25 ? ' sp-cam-hot' : '';
      const classes = (cam.top_classes || []).map(function (c) {
        return c.class + ' (' + Number(c.count).toLocaleString() + ')';
      }).join(', ') || '—';
      const disk = cam.est_gb_per_day
        ? '~' + cam.est_gb_per_day.toFixed(2) + ' GiB/day' +
          (cam.est_steady_gb ? '<br><span class="sp-usage-muted">~' + cam.est_steady_gb + ' GiB at limit</span>' : '')
        : '—';
      return (
        '<tr class="' + hot + '" data-camera-id="' + cam.camera_id + '">' +
        '<th scope="row" class="sp-usage-type">' +
        (idx + 1) + '. ' + cam.label +
        '</th>' +
        '<td><div class="sp-usage-cell">' +
        '<div class="sp-usage-bar-track"><div class="sp-usage-bar-fill' +
        (cam.share_pct >= 25 ? ' sp-usage-bar-warn' : '') +
        '" style="width:' + barPct + '%"></div></div>' +
        '<span class="sp-usage-gb">' + (cam.share_pct || 0).toFixed(0) + '%</span></div></td>' +
        '<td>' + Number(cam.events_per_day || 0).toLocaleString() + '</td>' +
        '<td>' + disk + '</td>' +
        '<td class="sp-cam-classes">' + classes + '</td>' +
        '<td><button type="button" class="btn btn-secondary btn-sm sp-cam-clear-btn"' +
        ' data-camera-id="' + cam.camera_id + '"' +
        ' data-camera-label="' + String(cam.label).replace(/"/g, '&quot;') + '"' +
        ' data-events="' + (cam.events || 0) + '"' +
        ' data-est-gb="' + (cam.est_steady_gb || 0) + '">' +
        'Clear</button></td>' +
        '</tr>'
      );
    }).join('');

    tbody.querySelectorAll('.sp-cam-clear-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        clearCameraDetections(btn);
      });
    });

    const recommendations = (ranking && ranking.recommendations) || [];
    if (recs) {
      if (!recommendations.length) {
        recs.hidden = true;
        recs.innerHTML = '';
      } else {
        recs.hidden = false;
        recs.innerHTML = recommendations.map(function (r) {
          const actions = (r.actions || []).map(function (a) {
            return '<a class="sp-cam-action" href="' + (a.href || '/config') + '">' +
              a.label + '</a>';
          }).join(' ');
          const clearBtn =
            '<button type="button" class="btn btn-secondary btn-sm sp-cam-rec-clear"' +
            ' data-camera-id="' + r.camera_id + '"' +
            ' data-camera-label="' + String(r.label).replace(/"/g, '&quot;') + '">' +
            'Clear detections</button>';
          return (
            '<div class="sp-cam-rec">' +
            '<span class="material-icons-outlined">tune</span>' +
            '<div><strong>' + r.label + '</strong> — ' + r.headline +
            '<div class="sp-cam-action-row">' + actions + clearBtn + '</div></div></div>'
          );
        }).join('');

        recs.querySelectorAll('.sp-cam-rec-clear').forEach(function (btn) {
          btn.addEventListener('click', function () {
            clearCameraDetections(btn);
          });
        });
      }
    }
  }

  async function clearCameraDetections(btn) {
    const cameraId = parseInt(btn.getAttribute('data-camera-id'), 10);
    const label = btn.getAttribute('data-camera-label') || ('Camera ' + (cameraId + 1));
    const events = parseInt(btn.getAttribute('data-events') || '0', 10);
    const estGb = parseFloat(btn.getAttribute('data-est-gb') || '0');
    if (Number.isNaN(cameraId) || cameraId < 0) return;

    let msg = 'Delete ALL detection gallery images and records for ' + label + '?';
    if (events > 0) {
      msg += '\n\nAbout ' + events.toLocaleString() + ' recent events';
      if (estGb > 0) msg += ' (~' + estGb.toFixed(1) + ' GiB at current retention)';
      msg += '.';
    }
    msg += '\n\nThis does not delete recordings or timelapse stills. It cannot be undone.';
    if (!window.confirm(msg)) return;

    const oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Clearing…';
    try {
      const r = await fetch('/api/storage-policy/clear-camera-detections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: cameraId, confirm: true }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Clear failed');
      const res = j.result || {};
      showToast(
        label + ': deleted ' + (res.deleted_events || 0).toLocaleString() +
        ' events, freed ' + (res.freed_gb != null ? res.freed_gb + ' GiB' : (res.freed_mb || 0) + ' MiB'),
        true
      );
      await loadStatus();
    } catch (err) {
      showToast(err.message || String(err), false);
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }

  function updateCloudMeter(usage) {
    const wrap = document.getElementById('sp-cloud-meter');
    const fill = document.getElementById('sp-cloud-meter-fill');
    const text = document.getElementById('sp-cloud-meter-text');
    if (!wrap || !usage || usage.used_gb == null) {
      if (wrap) wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const used = usage.used_gb;
    const quota = usage.quota_gb || 0;
    let pct = quota > 0 ? Math.min(100, Math.round((used / quota) * 100)) : 0;
    fill.style.width = pct + '%';
    fill.className = 'sp-cloud-meter-fill' + (usage.over_quota ? ' over' : pct > 85 ? ' warn' : '');
    text.textContent = quota > 0
      ? used + ' / ' + quota + ' GiB' + (usage.over_quota ? ' — over quota' : '')
      : used + ' GiB used';
  }

  function applyEditionUI() {
    const managed = editionInfo && editionInfo.managed_cloud;
    if (!managed) return;

    setText('sp-premium-headline', managed.headline || 'BaseBuddy Cloud');
    setText('sp-premium-tagline', managed.tagline || '');
    renderPricingTiers(managed);
    renderCloudBufferExplain(managed);

    const lostKey = document.getElementById('sp-premium-lost-key');
    if (lostKey) lostKey.textContent = managed.lost_key_help || '';

    const statusEl = document.getElementById('sp-premium-status');
    if (statusEl && managed.status_label) {
      statusEl.textContent = managed.status_label;
      statusEl.hidden = false;
    }

    const cta = document.getElementById('sp-premium-cta');
    if (cta && managed.cta_url) {
      cta.href = managed.cta_url;
      cta.textContent = managed.cta_label || 'Pricing';
    }

    const signup = document.getElementById('sp-premium-signup');
    if (signup && managed.signup_url) {
      signup.href = managed.signup_url + (planType === 'inference' ? '?plan=inference' : '');
      signup.textContent = managed.active ? 'Manage plan' : 'Get started';
    }

    const acct = document.getElementById('sp-premium-account-link');
    if (acct && managed.account_url) acct.href = managed.account_url;
    const sup = document.getElementById('sp-premium-support-link');
    if (sup && managed.support_url) sup.href = managed.support_url;

    const radio = document.querySelector('input[name="remote_mode"][value="managed"]');
    const premiumInstalled = loadedSettings && loadedSettings.premium_package_installed;
    if (radio) radio.disabled = !(managed.available || premiumInstalled);

    const installHint = document.getElementById('sp-premium-install-hint');
    if (installHint) installHint.hidden = premiumInstalled || !managed.available;

    const activeEl = document.getElementById('sp-premium-active');
    if (activeEl) {
      activeEl.hidden = !(managed.active || (loadedSettings && loadedSettings.BASEBUDDY_MANAGED_CLOUD_ENABLED));
    }
  }

  async function loadContext() {
    const r = await fetch('/api/storage-policy/context');
    const j = await r.json();
    if (j.ok && j.data) {
      plannerContext = j.data;
      const hint = document.getElementById('sp-timelapse-hint');
      if (hint) {
        const cams = plannerContext.camera_count || 0;
        hint.innerHTML = (cams ? cams + ' camera' + (cams === 1 ? '' : 's') + ' · ' : '') +
          plannerContext.timelapse_note + ' · <a href="' + (plannerContext.timelapse_url || '/timelapse') + '">Open timelapse settings</a>';
      }
    }
  }

  async function loadEdition() {
    const r = await fetch('/api/storage-policy/edition');
    const j = await r.json();
    if (j.ok) {
      editionInfo = j.data;
      applyEditionUI();
    }
  }

  async function loadSettings() {
    const r = await fetch('/api/storage-policy/settings');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'settings');
    const d = j.data;
    loadedSettings = d;

    populatePlannerFromPolicy(d.RETENTION_POLICY);
    document.getElementById('RECORD_ROOT').value = d.RECORD_ROOT || '';
    document.getElementById('RETENTION_SCAN_HOURS').value = d.RETENTION_SCAN_HOURS ?? 1;

    document.getElementById('ARCHIVE_ENABLED').checked = !!d.ARCHIVE_ENABLED;
    document.getElementById('ARCHIVE_DRIVE_PATH').value = d.ARCHIVE_DRIVE_PATH || '';
    document.getElementById('ARCHIVE_FOLDER').value = d.ARCHIVE_FOLDER || '';
    document.getElementById('ARCHIVE_MIN_AGE_DAYS').value = d.ARCHIVE_MIN_AGE_DAYS ?? '';
    document.getElementById('ARCHIVE_INTERVAL_DAYS').value = d.ARCHIVE_INTERVAL_DAYS ?? '';

    document.getElementById('BACKUP_ENABLED').checked = !!d.BACKUP_ENABLED;
    document.getElementById('BACKUP_DRIVE_PATH').value = d.BACKUP_DRIVE_PATH || '';
    document.getElementById('BACKUP_FOLDER').value = d.BACKUP_FOLDER || '';
    document.getElementById('BACKUP_INTERVAL_HOURS').value = d.BACKUP_INTERVAL_HOURS ?? '';

    const q = d.STORAGE_QUOTA_GB;
    document.getElementById('STORAGE_QUOTA_GB').value = q === 0 || q === '0' ? '0' : (q ?? '');
    const df = d.DISK_FREE_MIN_GB;
    const dfEl = document.getElementById('DISK_FREE_MIN_GB');
    if (dfEl) dfEl.value = df == null ? '20' : df;

    document.getElementById('REMOTE_STORAGE_ENABLED').checked = !!d.REMOTE_STORAGE_ENABLED;
    document.getElementById('REMOTE_STORAGE_PROVIDER').value = d.REMOTE_STORAGE_PROVIDER || 's3';
    document.getElementById('REMOTE_BUCKET').value = d.REMOTE_BUCKET || '';
    document.getElementById('REMOTE_PREFIX').value = d.REMOTE_PREFIX || 'basebuddy';
    document.getElementById('REMOTE_ENDPOINT').value = d.REMOTE_ENDPOINT || '';
    document.getElementById('REMOTE_REGION').value = d.REMOTE_REGION || 'auto';
    document.getElementById('REMOTE_ACCESS_KEY').value = d.REMOTE_ACCESS_KEY || '';
    document.getElementById('REMOTE_SECRET_KEY').value = '';
    document.getElementById('sp-secret-mask').textContent = d.REMOTE_SECRET_KEY_SET
      ? 'Saved: ' + (d.REMOTE_SECRET_KEY_MASK || '****') : '';

    document.getElementById('BASEBUDDY_CLOUD_API_URL').value = d.BASEBUDDY_CLOUD_API_URL || '';
    document.getElementById('BASEBUDDY_CLOUD_API_KEY').value = '';
    document.getElementById('sp-cloud-key-mask').textContent = d.BASEBUDDY_CLOUD_API_KEY_SET
      ? 'Saved: ' + (d.BASEBUDDY_CLOUD_API_KEY_MASK || '****') : '';

    if (d.remote_backend_active === 'managed' || d.BASEBUDDY_MANAGED_CLOUD_ENABLED) {
      setRemoteMode('managed');
    } else if (d.REMOTE_STORAGE_ENABLED) {
      setRemoteMode('byo');
    } else {
      setRemoteMode('none');
    }
    applyEditionUI();
    scheduleEstimate();
  }

  async function loadStatus() {
    const r = await fetch('/api/storage-policy/status');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'status');

    const loc = j.local;
    setText('sp-local-total', loc.total_gb != null ? loc.total_gb + ' GiB' : '—');

    const sys = j.system_disk || {};
    setText('sp-sys-disk', sys.free_gb != null ? sys.used_gb + ' / ' + sys.total_gb + ' GiB' : '—');

    const q = j.quota || {};
    if (q.enabled) {
      document.getElementById('sp-quota').innerHTML = q.over
        ? '<span class="quota-warn">' + q.used_gb + ' / ' + q.quota_gb + ' GiB</span>'
        : q.used_gb + ' / ' + q.quota_gb + ' GiB';
    } else {
      setText('sp-quota', 'Off');
    }

    const rbLabel = { none: 'Local only', byo: 'Your bucket', managed: 'BaseBuddy Cloud' };
    setText('sp-remote-status', rbLabel[j.remote_backend] || j.remote_backend);

    renderUsageBreakdown(j.usage_breakdown);
    renderStorageAlerts(j.storage_forecast);
    renderStorageForecast(j.storage_forecast);

    updateCloudMeter(j.cloud_usage);

    const raw = document.getElementById('sp-raw-status');
    if (raw) {
      raw.textContent = JSON.stringify({ retention: j.retention, cloud: j.cloud_usage }, null, 2);
    }
  }

  function collectForm() {
    const mode = getRemoteMode();
    const quotaRaw = document.getElementById('STORAGE_QUOTA_GB').value.trim();
    const q = quotaRaw === '' ? 0 : parseFloat(quotaRaw);
    const policy = collectRetentionPolicy();

    const body = {
      RETENTION_POLICY: policy,
      RETENTION_DAYS: policy.recordings ? policy.recordings.local_days : 7,
      RETENTION_SCAN_HOURS: nInt('RETENTION_SCAN_HOURS'),
      RECORD_ROOT: document.getElementById('RECORD_ROOT').value.trim(),
      ARCHIVE_ENABLED: document.getElementById('ARCHIVE_ENABLED').checked,
      ARCHIVE_DRIVE_PATH: document.getElementById('ARCHIVE_DRIVE_PATH').value.trim(),
      ARCHIVE_FOLDER: document.getElementById('ARCHIVE_FOLDER').value.trim(),
      ARCHIVE_MIN_AGE_DAYS: nInt('ARCHIVE_MIN_AGE_DAYS'),
      ARCHIVE_INTERVAL_DAYS: nInt('ARCHIVE_INTERVAL_DAYS'),
      BACKUP_ENABLED: document.getElementById('BACKUP_ENABLED').checked,
      BACKUP_DRIVE_PATH: document.getElementById('BACKUP_DRIVE_PATH').value.trim(),
      BACKUP_FOLDER: document.getElementById('BACKUP_FOLDER').value.trim(),
      BACKUP_INTERVAL_HOURS: nInt('BACKUP_INTERVAL_HOURS'),
      STORAGE_QUOTA_GB: Number.isNaN(q) ? 0 : q,
      DISK_FREE_MIN_GB: (function () {
        const raw = document.getElementById('DISK_FREE_MIN_GB');
        if (!raw) return 20;
        const v = parseFloat(raw.value.trim());
        return Number.isNaN(v) ? 20 : Math.max(0, v);
      })(),
      REMOTE_STORAGE_ENABLED: mode === 'byo' && document.getElementById('REMOTE_STORAGE_ENABLED').checked,
      REMOTE_STORAGE_PROVIDER: document.getElementById('REMOTE_STORAGE_PROVIDER').value,
      REMOTE_BUCKET: document.getElementById('REMOTE_BUCKET').value.trim(),
      REMOTE_PREFIX: document.getElementById('REMOTE_PREFIX').value.trim(),
      REMOTE_ENDPOINT: document.getElementById('REMOTE_ENDPOINT').value.trim(),
      REMOTE_REGION: document.getElementById('REMOTE_REGION').value.trim(),
      REMOTE_ACCESS_KEY: document.getElementById('REMOTE_ACCESS_KEY').value.trim(),
    };

    const secret = document.getElementById('REMOTE_SECRET_KEY').value.trim();
    if (secret) body.REMOTE_SECRET_KEY = secret;

    if (mode === 'none') {
      body.REMOTE_STORAGE_ENABLED = false;
      body.BASEBUDDY_MANAGED_CLOUD_ENABLED = false;
    } else if (mode === 'byo') {
      body.BASEBUDDY_MANAGED_CLOUD_ENABLED = false;
    } else if (mode === 'managed') {
      body.REMOTE_STORAGE_ENABLED = false;
      body.BASEBUDDY_MANAGED_CLOUD_ENABLED = true;
      body.BASEBUDDY_CLOUD_API_URL = document.getElementById('BASEBUDDY_CLOUD_API_URL').value.trim();
      const cloudKey = document.getElementById('BASEBUDDY_CLOUD_API_KEY').value.trim();
      if (cloudKey) body.BASEBUDDY_CLOUD_API_KEY = cloudKey;
    }

    return body;
  }

  document.querySelectorAll('.sp-plan-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      planType = tab.getAttribute('data-plan') || 'storage';
      document.querySelectorAll('.sp-plan-tab').forEach(function (t) {
        t.classList.toggle('active', t === tab);
      });
      applyEditionUI();
      scheduleEstimate();
    });
  });

  document.querySelectorAll('input[name="remote_mode"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (this.value === 'managed' && this.disabled) {
        setRemoteMode('none');
        showToast('Install premium or subscribe to use BaseBuddy Cloud.', false);
        return;
      }
      setRemoteMode(this.value);
    });
  });

  document.getElementById('sp-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    try {
      const body = collectForm();
      if (body.RETENTION_SCAN_HOURS === null) {
        showToast('Invalid retention scan interval', false);
        return;
      }
      const r = await fetch('/api/storage-policy/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'Save failed');
      showToast('Settings saved.', true);
      await loadSettings();
      await loadStatus();
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  });

  document.getElementById('sp-refresh').addEventListener('click', async function () {
    try {
      await loadStatus();
      scheduleEstimate();
      showToast('Refreshed.', true);
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  });

  document.getElementById('sp-test-managed').addEventListener('click', async function () {
    if (logEl) { logEl.hidden = false; logEl.textContent = 'Testing cloud…'; }
    try {
      const body = {
        BASEBUDDY_CLOUD_API_URL: document.getElementById('BASEBUDDY_CLOUD_API_URL').value.trim(),
        BASEBUDDY_CLOUD_API_KEY: document.getElementById('BASEBUDDY_CLOUD_API_KEY').value.trim(),
      };
      const r = await fetch('/api/storage-policy/test-managed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (logEl) logEl.textContent = JSON.stringify(j.result || j, null, 2);
      showToast(j.result && j.result.ok ? 'Cloud OK.' : ((j.result && j.result.error) || 'Failed'), !!(j.result && j.result.ok));
      if (j.result && j.result.ok) updateCloudMeter({ used_gb: j.result.used_gb, quota_gb: j.result.quota_gb });
    } catch (err) {
      if (logEl) logEl.textContent = String(err);
      showToast(String(err), false);
    }
  });

  document.getElementById('sp-test-remote').addEventListener('click', async function () {
    if (logEl) { logEl.hidden = false; logEl.textContent = 'Testing bucket…'; }
    try {
      const body = {
        REMOTE_STORAGE_PROVIDER: document.getElementById('REMOTE_STORAGE_PROVIDER').value,
        REMOTE_BUCKET: document.getElementById('REMOTE_BUCKET').value.trim(),
        REMOTE_PREFIX: document.getElementById('REMOTE_PREFIX').value.trim(),
        REMOTE_ENDPOINT: document.getElementById('REMOTE_ENDPOINT').value.trim(),
        REMOTE_REGION: document.getElementById('REMOTE_REGION').value.trim(),
        REMOTE_ACCESS_KEY: document.getElementById('REMOTE_ACCESS_KEY').value.trim(),
        REMOTE_SECRET_KEY: document.getElementById('REMOTE_SECRET_KEY').value.trim(),
      };
      const r = await fetch('/api/storage-policy/test-remote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (logEl) logEl.textContent = JSON.stringify(j.result || j, null, 2);
      showToast(j.result && j.result.ok ? 'Bucket OK.' : 'Failed', !!(j.result && j.result.ok));
    } catch (err) {
      if (logEl) logEl.textContent = String(err);
      showToast(String(err), false);
    }
  });

  document.querySelectorAll('.sp-test').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      const field = btn.getAttribute('data-test');
      const path = document.getElementById(field).value.trim();
      if (logEl) { logEl.hidden = false; logEl.textContent = 'Testing…'; }
      try {
        const r = await fetch('/api/storage-policy/test-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path }),
        });
        const j = await r.json();
        if (logEl) logEl.textContent = JSON.stringify(j.result || j, null, 2);
        showToast(j.result && j.result.ok ? 'Write test OK.' : 'Test failed', !!(j.result && j.result.ok));
      } catch (err) {
        if (logEl) logEl.textContent = String(err);
      }
    });
  });

  ['sp-run-retention', 'sp-run-archive', 'sp-run-backup'].forEach(function (id) {
    document.getElementById(id).addEventListener('click', async function () {
      const ep = id.replace('sp-run-', '');
      if (logEl) { logEl.hidden = false; logEl.textContent = 'Running ' + ep + '…'; }
      try {
        const r = await fetch('/api/storage-policy/run-' + ep, { method: 'POST' });
        const j = await r.json();
        if (logEl) logEl.textContent = JSON.stringify(j, null, 2);
        showToast(j.ok ? ep + ' finished.' : (j.error || 'Failed'), !!j.ok);
        await loadStatus();
      } catch (err) {
        if (logEl) logEl.textContent = String(err);
      }
    });
  });

  document.getElementById('sp-list-drives').addEventListener('click', async function () {
    if (logEl) { logEl.hidden = false; logEl.textContent = 'Loading…'; }
    try {
      const r = await fetch('/api/storage-policy/drives');
      const j = await r.json();
      if (logEl) logEl.textContent = JSON.stringify(j.data || j, null, 2);
    } catch (err) {
      if (logEl) logEl.textContent = String(err);
    }
  });

  buildPlannerRows();

  (async function init() {
    try {
      await loadContext();
      await loadEdition();
      await loadSettings();
      await loadStatus();
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  })();
})();

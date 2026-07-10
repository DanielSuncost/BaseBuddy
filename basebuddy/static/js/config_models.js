(function () {
    const systemEl = document.getElementById('models-system');
    const listEl = document.getElementById('models-list');
    const msgEl = document.getElementById('models-message');
    const downloadBtn = document.getElementById('models-download-btn');
    if (!systemEl || !listEl || !downloadBtn) return;

    let lastStatus = null;

    function icon(name) {
        return `<span class="material-icons-outlined models-icon">${name}</span>`;
    }

    function renderSystem(sys) {
        const gpu = sys.cuda_available
            ? `${sys.gpu_name || 'CUDA GPU'} · ${Math.round(sys.gpu_vram_free_mb || sys.gpu_vram_total_mb || 0)} MB VRAM free`
            : 'No CUDA GPU — CPU inference';
        const ram = `${sys.ram_available_gb || '?'} / ${sys.ram_total_gb || '?'} GB RAM`;
        const disk = `${sys.disk_free_gb || '?'} GB disk free`;

        systemEl.innerHTML = `
            <span class="models-badge models-badge-profile">${sys.profile_label || sys.profile}</span>
            <span class="models-badge">${gpu}</span>
            <span class="models-badge">${ram}</span>
            <span class="models-badge">${disk}</span>
        `;
    }

    function statusTag(model) {
        if (model.installed) {
            return '<span class="models-tag models-tag-ok">Installed</span>';
        }
        if (!model.sufficient) {
            return '<span class="models-tag models-tag-warn">Heavy for this system</span>';
        }
        if (model.recommended) {
            return '<span class="models-tag models-tag-rec">Recommended</span>';
        }
        return '<span class="models-tag">Optional</span>';
    }

    function renderModels(models) {
        listEl.innerHTML = models.map(m => {
            const dlBtn = m.installed
                ? ''
                : `<button type="button" class="btn btn-secondary btn-sm models-dl-one" data-model="${m.id}">Download</button>`;
            const req = m.required ? '<span class="models-tag models-tag-req">Required</span>' : '';
            return `<li class="models-row ${m.installed ? 'installed' : ''}">
                <div class="models-row-main">
                    <strong>${m.label}</strong>
                    <code class="models-filename">${m.id}</code>
                    ${req}${statusTag(m)}
                </div>
                <div class="models-row-meta">
                    ~${m.download_size_mb || '?'} MB
                    ${m.installed && m.on_disk_mb ? ` · ${m.on_disk_mb} MB on disk` : ''}
                    ${dlBtn}
                </div>
            </li>`;
        }).join('');

        listEl.querySelectorAll('.models-dl-one').forEach(btn => {
            btn.addEventListener('click', () => downloadModels('missing', [btn.dataset.model]));
        });
    }

    function updateDownloadButton(data) {
        const missing = data.recommended_missing || [];
        if (missing.length === 0) {
            downloadBtn.disabled = true;
            downloadBtn.innerHTML = `${icon('check')} All recommended installed`;
        } else {
            downloadBtn.disabled = false;
            const mb = data.recommended_download_mb || '?';
            downloadBtn.innerHTML = `${icon('download')} Download recommended (~${mb} MB)`;
        }
    }

    function showMessage(text, isError) {
        msgEl.hidden = false;
        msgEl.textContent = text;
        msgEl.classList.toggle('models-error', !!isError);
    }

    async function loadStatus() {
        systemEl.innerHTML = '<span class="models-badge models-badge-loading">Checking system…</span>';
        try {
            const res = await fetch('/api/models/status');
            const json = await res.json();
            if (!json.ok) throw new Error(json.error || 'Status failed');
            lastStatus = json.data;
            renderSystem(json.data.system);
            renderModels(json.data.models || []);
            updateDownloadButton(json.data);
            if (json.data.all_required_installed) {
                showMessage('Required detection models are ready.', false);
            } else if ((json.data.missing_required || []).length) {
                showMessage(`Missing required: ${json.data.missing_required.join(', ')}`, false);
            } else {
                msgEl.hidden = true;
            }
        } catch (err) {
            systemEl.innerHTML = '<span class="models-badge models-tag-warn">Could not assess system</span>';
            showMessage(err.message, true);
        }
    }

    async function downloadModels(scope, models) {
        downloadBtn.disabled = true;
        downloadBtn.innerHTML = `${icon('hourglass_empty')} Downloading…`;
        listEl.querySelectorAll('.models-dl-one').forEach(b => { b.disabled = true; });

        try {
            const body = { scope };
            if (models && models.length) body.models = models;
            const res = await fetch('/api/models/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const json = await res.json();
            if (!json.ok && res.status !== 207) throw new Error(json.error || 'Download failed');
            const downloaded = (json.data && json.data.downloaded) || [];
            const failed = (json.data && json.data.failed) || [];
            if (downloaded.length) {
                showMessage(`Downloaded: ${downloaded.join(', ')}`, false);
            }
            if (failed.length) {
                showMessage(`Failed: ${failed.map(f => f.id).join(', ')}`, true);
            }
            if (json.message && !downloaded.length && !failed.length) {
                showMessage(json.message, false);
            }
            await loadStatus();
        } catch (err) {
            showMessage(err.message, true);
            await loadStatus();
        }
    }

    downloadBtn.addEventListener('click', () => downloadModels('recommended'));
    loadStatus();
})();

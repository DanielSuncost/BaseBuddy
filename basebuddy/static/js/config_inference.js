(function () {
    const panel = document.getElementById('inference-setup');
    if (!panel) return;

    const modeEl = document.getElementById('inference-mode');
    const hybridEl = document.getElementById('inference-hybrid');
    const endpointEl = document.getElementById('inference-endpoint');
    const keyEl = document.getElementById('inference-api-key');
    const statusEl = document.getElementById('inference-status');
    const saveBtn = document.getElementById('inference-save');

    async function load() {
        try {
            const [cfgRes, statRes] = await Promise.all([
                fetch('/api/config/inference'),
                fetch('/api/inference/status'),
            ]);
            const cfg = await cfgRes.json();
            const stat = await statRes.json();
            if (cfg.ok && cfg.data) {
                modeEl.value = cfg.data.mode || 'local';
                hybridEl.checked = !!cfg.data.hybrid_fallback;
                endpointEl.value = cfg.data.cloud_endpoint || '';
                if (cfg.data.cloud_api_key_mask) {
                    keyEl.placeholder = 'Saved: ' + cfg.data.cloud_api_key_mask;
                }
            }
            if (stat.ok && stat.data) {
                let txt = 'Mode: ' + stat.data.mode;
                if (stat.data.cloud_configured) txt += ' · Cloud connected';
                if (stat.data.usage && stat.data.usage.quota_remaining != null) {
                    txt += ' · Quota: ' + stat.data.usage.quota_remaining;
                }
                statusEl.textContent = txt;
            }
        } catch (e) {
            statusEl.textContent = 'Could not load inference settings';
        }
    }

    saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        try {
            const body = {
                mode: modeEl.value,
                hybrid_fallback: hybridEl.checked,
                cloud_endpoint: endpointEl.value.trim(),
            };
            const key = keyEl.value.trim();
            if (key) body.cloud_api_key = key;
            const res = await fetch('/api/config/inference', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const json = await res.json();
            alert(json.message || json.error || (json.ok ? 'Saved' : 'Failed'));
            keyEl.value = '';
            load();
        } finally {
            saveBtn.disabled = false;
        }
    });

    load();
})();

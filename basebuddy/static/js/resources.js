let updateInterval;

function updateResourceStats() {
    fetch('/api/resources/status')
        .then(response => response.json())
        .then(data => {
            // Update GPU stats
            if (data.gpu && data.gpu.stats) {
                const gpu = data.gpu.stats;
                document.getElementById('gpuUsed').textContent = Math.round(gpu.memory_used_mb);
                document.getElementById('gpuFree').textContent = Math.round(gpu.memory_free_mb);
                document.getElementById('gpuTotal').textContent = Math.round(gpu.memory_total_mb);
                const gpuPercent = gpu.utilization_percent;
                document.getElementById('gpuProgress').style.width = gpuPercent + '%';
                document.getElementById('gpuProgress').className = 'progress-bar ' +
                    (gpuPercent > 85 ? 'bg-danger' : gpuPercent > 70 ? 'bg-warning' : 'bg-success');
            }

            // Update CPU stats
            if (data.system) {
                const cpu = data.system.cpu_percent;
                document.getElementById('cpuPercent').textContent = Math.round(cpu);
                document.getElementById('cpuProgress').style.width = cpu + '%';
            }

            // Update RAM stats
            if (data.system) {
                const ram = data.system;
                document.getElementById('ramUsed').textContent = Math.round(ram.ram_used_mb);
                document.getElementById('ramFree').textContent = Math.round(ram.ram_free_mb);
                document.getElementById('ramTotal').textContent = Math.round(ram.ram_total_mb);
                const ramPercent = ram.ram_percent;
                document.getElementById('ramProgress').style.width = ramPercent + '%';
            }

            // Update GPU queue
            if (data.gpu) {
                document.getElementById('currentGpuUser').textContent = data.gpu.current_user || 'None';
                document.getElementById('queueLength').textContent = data.gpu.queue_length;

                const queueList = document.getElementById('queueList');
                if (data.gpu.queue && data.gpu.queue.length > 0) {
                    queueList.innerHTML = '<ul class="list-group">' +
                        data.gpu.queue.map(item =>
                            `<li class="list-group-item">
                                <strong>${item.requester_id}</strong>
                                (${item.priority}, ${Math.round(item.estimated_memory_mb)}MB,
                                waiting ${Math.round(item.waiting_seconds)}s)
                            </li>`
                        ).join('') +
                        '</ul>';
                } else {
                    queueList.innerHTML = '<p class="text-muted">No pending requests</p>';
                }
            }
        })
        .catch(error => {
            console.error('Error updating resource stats:', error);
        });
}

function updateProfilingData() {
    fetch('/api/profiling/summary')
        .then(response => response.json())
        .then(data => {
            if (data.ok && data.summary) {
                const summary = data.summary;
                const cpuColor = (summary.system_cpu_percent || 0) > 80 ? 'text-danger' :
                                (summary.system_cpu_percent || 0) > 60 ? 'text-warning' : 'text-success';
                const summaryHtml = `
                    <div class="row">
                        <div class="col-md-3">
                            <strong>Total Cameras:</strong> ${summary.total_cameras}
                        </div>
                        <div class="col-md-3">
                            <strong>Avg FPS:</strong> ${summary.avg_fps.toFixed(1)}
                        </div>
                        <div class="col-md-3">
                            <strong>Avg Processing:</strong> ${summary.avg_frame_processing_time_ms.toFixed(1)}ms
                        </div>
                        <div class="col-md-3">
                            <strong>Resource Errors:</strong> ${summary.total_resource_errors}
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-md-4">
                            <strong>System CPU:</strong> <span class="${cpuColor}">${(summary.system_cpu_percent || 0).toFixed(1)}%</span>
                        </div>
                        <div class="col-md-4">
                            <strong>Process CPU:</strong> ${(summary.process_cpu_percent || 0).toFixed(1)}%
                        </div>
                        <div class="col-md-4">
                            <strong>Avg CPU/Camera:</strong> ${(summary.avg_cpu_per_camera || 0).toFixed(1)}%
                        </div>
                    </div>
                    ${summary.cameras_with_bottlenecks > 0 ?
                        `<div class="alert alert-warning mt-2">
                            <strong>${summary.cameras_with_bottlenecks} camera(s) have bottlenecks</strong>
                        </div>` : ''}
                `;
                const profilingDiv = document.getElementById('profilingSummary');
                if (profilingDiv) {
                    profilingDiv.innerHTML = summaryHtml;
                }
            }
        })
        .catch(error => {
            console.error('Error fetching profiling summary:', error);
        });

    fetch('/api/profiling/cameras')
        .then(response => response.json())
        .then(data => {
            if (data.ok && data.metrics) {
                const metricsHtml = Object.entries(data.metrics).map(([camId, metrics]) => {
                    const bottlenecks = (data.bottlenecks && data.bottlenecks[camId]) || [];
                    const bottleneckHtml = bottlenecks.length > 0 ?
                        `<div class="alert alert-sm alert-warning mb-2">
                            ${bottlenecks.map(b => `<div>• ${b}</div>`).join('')}
                        </div>` : '';

                    const cpuColor = (metrics.avg_cpu_percent || 0) > 80 ? 'text-danger' :
                                    (metrics.avg_cpu_percent || 0) > 60 ? 'text-warning' : 'text-success';

                    return `
                        <div class="card mb-2">
                            <div class="card-body">
                                <h6>Camera ${camId}</h6>
                                <div class="row">
                                    <div class="col-md-2">
                                        <small>FPS: <strong>${metrics.current_fps.toFixed(1)}</strong></small>
                                    </div>
                                    <div class="col-md-2">
                                        <small>Processing: <strong>${metrics.avg_frame_processing_time_ms.toFixed(1)}ms</strong></small>
                                    </div>
                                    <div class="col-md-2">
                                        <small>Detection: <strong>${metrics.avg_detection_time_ms.toFixed(1)}ms</strong></small>
                                    </div>
                                    <div class="col-md-2">
                                        <small>Queue: <strong>${metrics.avg_queue_depth.toFixed(1)}</strong></small>
                                    </div>
                                    <div class="col-md-2">
                                        <small>CPU: <strong class="${cpuColor}">${(metrics.avg_cpu_percent || 0).toFixed(1)}%</strong></small>
                                    </div>
                                    <div class="col-md-2">
                                        <small>Frames: <strong>${metrics.frames_processed || 0}</strong></small>
                                    </div>
                                </div>
                                ${bottleneckHtml}
                            </div>
                        </div>
                    `;
                }).join('');
                const metricsDiv = document.getElementById('cameraMetricsList');
                if (metricsDiv) {
                    metricsDiv.innerHTML = metricsHtml || '<p class="text-muted">No camera metrics available yet</p>';
                }
            }
        })
        .catch(error => {
            console.error('Error fetching camera metrics:', error);
        });
}

// Update stats every second
updateInterval = setInterval(() => {
    updateResourceStats();
    updateProfilingData();
}, 2000);
updateResourceStats(); // Initial update
updateProfilingData(); // Initial profiling update

// Handle configuration form
document.getElementById('resourceConfigForm').addEventListener('submit', function(e) {
    e.preventDefault();

    const config = {
        gpu_memory_threshold_percent: parseFloat(document.getElementById('gpuMemoryThreshold').value) / 100,
        enable_opportunistic_processing: document.getElementById('opportunisticProcessing').checked,
        allow_critical_override: document.getElementById('allowCriticalOverride').checked,
        base_detection_fps: parseFloat(document.getElementById('baseDetectionFps').value),
        face_recognition_interval: parseFloat(document.getElementById('faceRecognitionInterval').value)
    };

    fetch('/api/resources/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(config)
    })
    .then(response => response.json())
    .then(data => {
        if (data.ok) {
            showToast('Configuration saved successfully', 'success');
        } else {
            showToast('Error saving configuration: ' + (data.error || 'Unknown error'), 'error');
        }
    })
    .catch(error => {
        console.error('Error saving configuration:', error);
        showToast('Network error saving configuration', 'error');
    });
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (updateInterval) {
        clearInterval(updateInterval);
    }
});

// Dashboard-specific SSE handler
window.addEventListener('rclone-stats', function(e) {
    const data = e.detail;
    const runs = data.runs || {};

    // Update global stats cards (aggregated from our app's runs only)
    const speedEl = document.getElementById('global-speed');
    if (speedEl) speedEl.textContent = formatSpeed(data.total_speed || 0);

    const bytesEl = document.getElementById('global-bytes');
    if (bytesEl) bytesEl.textContent = formatBytes(data.total_bytes || 0);

    const activeEl = document.getElementById('active-count');
    if (activeEl) activeEl.textContent = data.active_count || 0;

    // Update each transfer item with its own per-run stats
    const transferItems = document.querySelectorAll('.transfer-item');
    transferItems.forEach(item => {
        const runId = item.dataset.runId;
        if (!runId || !runs[runId]) return;

        const stats = runs[runId].stats;
        if (!stats) return;

        // Update speed display
        const speedDisplay = item.querySelector('.transfer-speed');
        if (speedDisplay) speedDisplay.textContent = formatSpeed(stats.speed);

        // Update detail text (dashboard view)
        const detailDisplay = item.querySelector('.transfer-detail');
        if (detailDisplay) {
            detailDisplay.textContent =
                formatBytes(stats.bytes) + ' transferred, ' +
                (stats.transfers || 0) + ' files, ' +
                (stats.errors || 0) + ' errors';
        }

        // Update per-run stats columns (operations/running page)
        const bytesCol = item.querySelector('.transfer-bytes');
        const filesCol = item.querySelector('.transfer-files');
        const errorsCol = item.querySelector('.transfer-errors');
        const etaCol = item.querySelector('.transfer-eta');

        if (bytesCol) bytesCol.textContent = formatBytes(stats.bytes);
        if (filesCol) filesCol.textContent = stats.transfers || 0;
        if (errorsCol) errorsCol.textContent = stats.errors || 0;
        if (etaCol) etaCol.textContent = formatETA(stats.eta);

        // Show individual file progress
        if (stats.transferring && stats.transferring.length > 0) {
            const currentFiles = item.querySelector('.current-files');
            const fileList = item.querySelector('.file-list');
            if (currentFiles && fileList) {
                currentFiles.style.display = 'block';
                fileList.innerHTML = '';
                stats.transferring.forEach(t => {
                    const div = document.createElement('div');
                    div.className = 'file-item d-flex justify-content-between';
                    const pct = t.percentage || 0;
                    div.innerHTML =
                        '<span class="text-truncate me-2" style="max-width: 70%;">' + t.name + '</span>' +
                        '<span class="text-nowrap">' + pct + '% ' + formatSpeed(t.speed) + '</span>';
                    fileList.appendChild(div);
                });
            }

            // Update progress bar
            const progressBar = item.querySelector('.progress-bar');
            if (progressBar) {
                let avgPct = 0;
                stats.transferring.forEach(t => { avgPct += (t.percentage || 0); });
                avgPct = Math.round(avgPct / stats.transferring.length);
                progressBar.style.width = avgPct + '%';
                progressBar.textContent = avgPct + '%';
            }
        }
    });

    // Hide "no transfers" message if there are active runs
    const noMsg = document.getElementById('no-transfers-msg');
    if (noMsg) {
        noMsg.style.display = (data.active_count > 0) ? 'none' : '';
    }
});

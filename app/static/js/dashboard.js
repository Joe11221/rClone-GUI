// Dashboard-specific SSE handler
window.addEventListener('rclone-stats', function(e) {
    const data = e.detail;
    const stats = data.stats;

    // Update global stats cards
    const speedEl = document.getElementById('global-speed');
    if (speedEl) speedEl.textContent = formatSpeed(stats.speed);

    const bytesEl = document.getElementById('global-bytes');
    if (bytesEl) bytesEl.textContent = formatBytes(stats.bytes);

    const activeEl = document.getElementById('active-count');
    if (activeEl && data.jobs) {
        const count = data.jobs.jobids ? data.jobs.jobids.length : 0;
        activeEl.textContent = count;
    }

    // Update transfer items
    const transferItems = document.querySelectorAll('.transfer-item');
    transferItems.forEach(item => {
        const runId = item.dataset.runId;
        if (!runId) return;

        // Update progress from per-file transferring info
        if (stats.transferring && stats.transferring.length > 0) {
            const speedDisplay = item.querySelector('.transfer-speed');
            if (speedDisplay) speedDisplay.textContent = formatSpeed(stats.speed);

            const detailDisplay = item.querySelector('.transfer-detail');
            if (detailDisplay) {
                detailDisplay.textContent =
                    formatBytes(stats.bytes) + ' transferred, ' +
                    (stats.transfers || 0) + ' files, ' +
                    (stats.errors || 0) + ' errors';
            }

            // Show individual file progress
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

            // Update progress bar — use average of transferring files or overall percentage
            const progressBar = item.querySelector('.progress-bar');
            if (progressBar && stats.transferring.length > 0) {
                let avgPct = 0;
                stats.transferring.forEach(t => { avgPct += (t.percentage || 0); });
                avgPct = Math.round(avgPct / stats.transferring.length);
                progressBar.style.width = avgPct + '%';
                progressBar.textContent = avgPct + '%';
            }
        }

        // Update per-run stats columns (on operations/running page)
        const speedCol = item.querySelector('.transfer-speed');
        const bytesCol = item.querySelector('.transfer-bytes');
        const filesCol = item.querySelector('.transfer-files');
        const errorsCol = item.querySelector('.transfer-errors');
        const etaCol = item.querySelector('.transfer-eta');

        if (speedCol) speedCol.textContent = formatSpeed(stats.speed);
        if (bytesCol) bytesCol.textContent = formatBytes(stats.bytes);
        if (filesCol) filesCol.textContent = stats.transfers || 0;
        if (errorsCol) errorsCol.textContent = stats.errors || 0;
        if (etaCol) etaCol.textContent = formatETA(stats.eta);
    });

    // Hide "no transfers" message if there are active transfers
    const noMsg = document.getElementById('no-transfers-msg');
    if (noMsg && data.jobs && data.jobs.jobids && data.jobs.jobids.length > 0) {
        noMsg.style.display = 'none';
    }
});

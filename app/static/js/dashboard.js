// Dashboard-specific SSE handler

// Track last known stats per run to avoid flickering back to zero
const lastKnownStats = {};

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

    // Track which run IDs are still active so we can clean up lastKnownStats
    const activeRunIds = new Set(Object.keys(runs));

    // Update each transfer item with its own per-run stats
    const transferItems = document.querySelectorAll('.transfer-item');
    transferItems.forEach(item => {
        const runId = item.dataset.runId;
        if (!runId || !runs[runId]) return;

        const stats = runs[runId].stats;
        if (!stats) return;

        // Initialize tracking for this run if needed
        if (!lastKnownStats[runId]) {
            lastKnownStats[runId] = {};
        }
        const prev = lastKnownStats[runId];

        // Use current speed, but keep last known if current is 0 and we had data before
        // (rclone can briefly report 0 speed between file transfers)
        const speed = stats.speed || 0;
        const displaySpeed = speed > 0 ? speed : (prev.speed || 0);
        if (speed > 0) prev.speed = speed;

        // These are cumulative and should never decrease
        const bytes = stats.bytes || 0;
        const transfers = stats.transfers || 0;
        const checks = stats.checks || 0;
        const errors = stats.errors || 0;

        // Track max values seen (cumulative values shouldn't go backwards)
        if (bytes > (prev.bytes || 0)) prev.bytes = bytes;
        if (transfers > (prev.transfers || 0)) prev.transfers = transfers;
        if (checks > (prev.checks || 0)) prev.checks = checks;

        const displayBytes = Math.max(bytes, prev.bytes || 0);
        const displayTransfers = Math.max(transfers, prev.transfers || 0);
        const displayChecks = Math.max(checks, prev.checks || 0);

        // Update speed display
        const speedDisplay = item.querySelector('.transfer-speed');
        if (speedDisplay) speedDisplay.textContent = formatSpeed(displaySpeed);

        // Update detail text (dashboard view)
        const detailDisplay = item.querySelector('.transfer-detail');
        if (detailDisplay) {
            if (displayBytes > 0 || displayTransfers > 0 || displayChecks > 0) {
                detailDisplay.textContent =
                    formatBytes(displayBytes) + ' transferred, ' +
                    displayTransfers + ' new files, ' +
                    displayChecks + ' unchanged, ' +
                    errors + ' errors';
            }
            // else: keep "Starting..." text until we have real data
        }

        // Update per-run stats columns (operations/running page)
        const bytesCol = item.querySelector('.transfer-bytes');
        const filesCol = item.querySelector('.transfer-files');
        const checksCol = item.querySelector('.transfer-checks');
        const errorsCol = item.querySelector('.transfer-errors');
        const etaCol = item.querySelector('.transfer-eta');

        if (bytesCol) bytesCol.textContent = formatBytes(displayBytes);
        if (filesCol) filesCol.textContent = displayTransfers;
        if (checksCol) checksCol.textContent = displayChecks;
        if (errorsCol) errorsCol.textContent = errors;
        if (etaCol) etaCol.textContent = formatETA(stats.eta);

        // Calculate overall progress from totalBytes (much more stable than per-file average)
        const totalBytes = stats.totalBytes || 0;
        if (totalBytes > 0) {
            const overallPct = Math.min(100, Math.round((bytes / totalBytes) * 100));
            if (overallPct > (prev.pct || 0)) prev.pct = overallPct;
            const displayPct = Math.max(overallPct, prev.pct || 0);

            const progressBar = item.querySelector('.progress-bar');
            if (progressBar) {
                progressBar.style.width = displayPct + '%';
                progressBar.textContent = displayPct + '%';
            }
        } else if (stats.transferring && stats.transferring.length > 0) {
            // Fallback: if totalBytes not available, use average of in-flight files
            let avgPct = 0;
            stats.transferring.forEach(t => { avgPct += (t.percentage || 0); });
            avgPct = Math.round(avgPct / stats.transferring.length);

            const progressBar = item.querySelector('.progress-bar');
            if (progressBar && avgPct > 0) {
                progressBar.style.width = avgPct + '%';
                progressBar.textContent = avgPct + '%';
            }
            // Don't update progress bar when avgPct is 0 — keep last shown value
        }

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
        }
        // Don't hide current-files when transferring is empty — keep showing last state
    });

    // Hide "no transfers" message if there are active runs
    const noMsg = document.getElementById('no-transfers-msg');
    if (noMsg) {
        noMsg.style.display = (data.active_count > 0) ? 'none' : '';
    }

    // Clean up tracking for runs that are no longer active
    for (const runId in lastKnownStats) {
        if (!activeRunIds.has(runId)) {
            delete lastKnownStats[runId];
        }
    }
});

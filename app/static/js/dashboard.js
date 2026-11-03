// Dashboard-specific SSE handler

// Track last known stats per run to avoid flickering back to zero
const lastKnownStats = {};

// Run ids from the previous SSE message; null until the first one arrives.
let knownRunIds = null;
let refreshTimer = null;

function announce(message) {
    let region = document.getElementById('live-status');
    if (!region) {
        region = document.createElement('div');
        region.id = 'live-status';
        region.className = 'visually-hidden';
        region.setAttribute('role', 'status');
        region.setAttribute('aria-live', 'polite');
        document.body.appendChild(region);
    }
    region.textContent = message;
}

function setProgress(bar, pct) {
    bar.style.width = pct + '%';
    bar.textContent = pct + '%';
    bar.setAttribute('aria-valuenow', pct);
}

// Re-fetch this page and swap in the server-rendered sections, so a run that
// starts or finishes while the page is open shows up without a manual reload.
async function refreshSections() {
    try {
        const response = await fetch(location.href, { headers: { Accept: 'text/html' } });
        if (!response.ok) return null;
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        document.querySelectorAll('[data-refresh]').forEach(el => {
            const fresh = doc.querySelector('[data-refresh="' + el.dataset.refresh + '"]');
            if (fresh) el.replaceWith(document.importNode(fresh, true));
        });
        return doc;
    } catch (e) {
        return null;
    }
}

function runNameFor(runId) {
    const item = document.querySelector('.transfer-item[data-run-id="' + runId + '"]');
    return item && item.dataset.jobName ? item.dataset.jobName : 'A backup';
}

// Compare the runs rclone reports with the previous message and react to changes.
function handleRunSetChange(currentIds) {
    if (knownRunIds === null) {
        knownRunIds = currentIds;
        return;
    }
    const started = [...currentIds].filter(id => !knownRunIds.has(id));
    const finished = [...knownRunIds].filter(id => !currentIds.has(id));
    knownRunIds = currentIds;
    if (!started.length && !finished.length) return;

    finished.forEach(id => announce(runNameFor(id) + ' finished.'));
    const onDashboard = !!document.querySelector('[data-refresh]');
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
        if (onDashboard) {
            await refreshSections();
            started.forEach(id => announce(runNameFor(id) + ' started.'));
        } else {
            location.reload();  // the running page has per-run scripts bound to its cards
        }
    }, finished.length ? 1500 : 500);  // let the run record settle before re-reading it
}

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
    handleRunSetChange(activeRunIds);

    // The Active transfers section exists only while something runs
    const activeSection = document.getElementById('active-section');
    if (activeSection) activeSection.classList.toggle('d-none', !(data.active_count > 0));

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
        const remainingCol = item.querySelector('.transfer-remaining');
        const filesLeftCol = item.querySelector('.transfer-files-left');
        const filesCol = item.querySelector('.transfer-files');
        const checksCol = item.querySelector('.transfer-checks');
        const errorsCol = item.querySelector('.transfer-errors');
        const etaCol = item.querySelector('.transfer-eta');

        // Calculate overall progress from totalBytes (much more stable than per-file average)
        const totalBytes = stats.totalBytes || 0;

        if (bytesCol) bytesCol.textContent = formatBytes(displayBytes);
        if (remainingCol) {
            remainingCol.textContent = totalBytes > 0
                ? formatBytes(Math.max(0, totalBytes - displayBytes))
                : '--';
        }
        if (filesLeftCol) {
            // totalTransfers grows as rclone finds files, so this is "known so far".
            const totalFiles = stats.totalTransfers || 0;
            filesLeftCol.textContent = totalFiles > 0
                ? Math.max(0, totalFiles - displayTransfers).toLocaleString()
                : '--';
        }
        if (filesCol) filesCol.textContent = displayTransfers;
        if (checksCol) checksCol.textContent = displayChecks;
        if (errorsCol) errorsCol.textContent = errors;
        if (etaCol) etaCol.textContent = formatETA(stats.eta);

        if (totalBytes > 0) {
            const overallPct = Math.min(100, Math.round((bytes / totalBytes) * 100));
            if (overallPct > (prev.pct || 0)) prev.pct = overallPct;
            const displayPct = Math.max(overallPct, prev.pct || 0);

            const progressBar = item.querySelector('.progress-bar');
            if (progressBar) setProgress(progressBar, displayPct);
        } else if (stats.transferring && stats.transferring.length > 0) {
            // Fallback: if totalBytes not available, use average of in-flight files
            let avgPct = 0;
            stats.transferring.forEach(t => { avgPct += (t.percentage || 0); });
            avgPct = Math.round(avgPct / stats.transferring.length);

            const progressBar = item.querySelector('.progress-bar');
            if (progressBar && avgPct > 0) setProgress(progressBar, avgPct);
            // Don't update progress bar when avgPct is 0 — keep last shown value
        }

        // Show individual file progress
        if (stats.transferring && stats.transferring.length > 0) {
            const currentFiles = item.querySelector('.current-files');
            const fileList = item.querySelector('.file-list');
            if (currentFiles && fileList) {
                currentFiles.style.display = 'block';
                // Reconcile rows by file name so the list doesn't flash on every tick.
                const rows = new Map();
                fileList.querySelectorAll('.file-item').forEach(row => rows.set(row.dataset.file, row));
                const seen = new Set();
                stats.transferring.forEach(t => {
                    seen.add(t.name);
                    let div = rows.get(t.name);
                    let statSpan;
                    if (!div) {
                        div = document.createElement('div');
                        div.className = 'file-item d-flex justify-content-between';
                        div.dataset.file = t.name;
                        // File names come from the filesystem and are untrusted:
                        // always use textContent, never innerHTML.
                        const nameSpan = document.createElement('span');
                        nameSpan.className = 'text-truncate me-2';
                        nameSpan.style.maxWidth = '70%';
                        nameSpan.textContent = t.name;
                        statSpan = document.createElement('span');
                        statSpan.className = 'text-nowrap file-stat';
                        div.append(nameSpan, statSpan);
                        fileList.appendChild(div);
                    } else {
                        statSpan = div.querySelector('.file-stat');
                    }
                    statSpan.textContent = (t.percentage || 0) + '% ' + formatSpeed(t.speed);
                });
                rows.forEach((row, name) => { if (!seen.has(name)) row.remove(); });
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

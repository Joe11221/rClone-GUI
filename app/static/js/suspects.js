// "Possibly unwanted directories": caches, dependency folders and piles of tiny
// files, detected live by the server. Excluding one adds a rule to the job for
// its next run.

// The end of a long path says what the folder is.
function shortPath(path) {
    const parts = path.split('/');
    return parts.length > 4 ? '\u2026/' + parts.slice(-3).join('/') : path;
}

function suspectRow(runId, excluded, suspect) {
    const row = document.createElement('div');
    row.className = 'suspect-item d-flex align-items-center gap-3';

    const info = document.createElement('div');
    info.className = 'flex-grow-1 text-truncate';
    const path = document.createElement('code');
    path.className = 'suspect-path';
    path.dataset.path = suspect.path;
    path.textContent = shortPath(suspect.path);
    path.title = suspect.path;
    const meta = document.createElement('small');
    meta.className = 'suspect-meta text-muted d-block';
    info.append(path, meta);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-secondary text-nowrap';
    const icon = document.createElement('i');
    icon.className = 'bi bi-slash-circle';
    icon.setAttribute('aria-hidden', 'true');
    button.append(icon, ' Exclude');
    // Several rows say just "Exclude"; name the folder for screen readers.
    button.setAttribute('aria-label', 'Exclude ' + suspect.path + ' from the next run');
    const markExcluded = () => {
        button.disabled = true;
        button.className = 'btn btn-sm btn-outline-secondary text-nowrap';
        button.textContent = 'Excluded next run';
        button.setAttribute('aria-label', suspect.path + ' will be excluded from the next run');
    };
    if (excluded.has(suspect.pattern)) markExcluded();
    button.addEventListener('click', () => {
        button.disabled = true;
        fetch('/ops/exclude/' + runId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: suspect.path }),
        })
            .then(r => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
            .then(data => {
                excluded.add(data.pattern);
                markExcluded();
            })
            .catch(() => {
                button.disabled = false;
                button.textContent = 'Failed, retry';
                button.setAttribute('aria-label', 'Exclude failed for ' + suspect.path + ', retry');
            });
    });

    row.append(info, button);
    return row;
}

function updateSuspectRow(row, suspect) {
    row.querySelector('.suspect-meta').textContent =
        (suspect.known ? 'Cache or generated folder · ' : 'Mostly tiny files · ') +
        suspect.files.toLocaleString() + '+ files seen · avg ' + formatBytes(suspect.avg_bytes);
}

window.addEventListener('rclone-stats', function(e) {
    const runs = (e.detail && e.detail.runs) || {};
    document.querySelectorAll('.transfer-item[data-excludes]').forEach(item => {
        const runId = item.dataset.runId;
        const section = item.querySelector('.suspects');
        const list = item.querySelector('.suspect-list');
        if (!section || !list || !runs[runId]) return;

        if (!item.suspectExcluded) {
            try { item.suspectExcluded = new Set(JSON.parse(item.dataset.excludes)); }
            catch (err) { item.suspectExcluded = new Set(); }
        }
        const suspects = runs[runId].suspects || [];
        section.style.display = suspects.length ? '' : 'none';

        // Update rows in place so a button under the cursor isn't rebuilt mid-click.
        const existing = new Map();
        list.querySelectorAll('.suspect-item').forEach(row =>
            existing.set(row.querySelector('.suspect-path').dataset.path, row));
        const wanted = new Set(suspects.map(s => s.path));
        existing.forEach((row, path) => { if (!wanted.has(path)) row.remove(); });
        suspects.forEach(suspect => {
            let row = existing.get(suspect.path);
            if (!row) {
                row = suspectRow(runId, item.suspectExcluded, suspect);
                list.appendChild(row);
            }
            updateSuspectRow(row, suspect);
        });
    });
});

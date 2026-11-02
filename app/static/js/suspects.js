// "Possibly unwanted directories": folders full of tiny files, detected live by
// the server. Excluding one adds a rule to the job for its next run.

function suspectRow(runId, excluded, suspect) {
    const row = document.createElement('div');
    row.className = 'suspect-item d-flex align-items-center gap-3';

    const info = document.createElement('div');
    info.className = 'flex-grow-1 text-truncate';
    const path = document.createElement('code');
    path.className = 'suspect-path';
    path.textContent = suspect.path;
    path.title = suspect.path;
    const meta = document.createElement('small');
    meta.className = 'suspect-meta text-muted ms-2';
    info.append(path, meta);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-warning text-nowrap';
    button.innerHTML = '<i class="bi bi-slash-circle"></i> Exclude';
    const markExcluded = () => {
        button.disabled = true;
        button.className = 'btn btn-sm btn-outline-secondary text-nowrap';
        button.textContent = 'Excluded next run';
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
            });
    });

    row.append(info, button);
    return row;
}

function updateSuspectRow(row, suspect) {
    row.querySelector('.suspect-meta').textContent =
        suspect.files.toLocaleString() + ' files · avg ' +
        formatBytes(suspect.avg_bytes) + ' · ' + formatBytes(suspect.bytes) + ' total';
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
            existing.set(row.querySelector('.suspect-path').textContent, row));
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

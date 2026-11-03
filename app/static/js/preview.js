// Running page: a run in its preview phase is counting deletions, not
// transferring. When that phase ends and the real sync takes over, the card
// needs its progress bar and counters, so reload to get them.
window.addEventListener('rclone-stats', function (e) {
    const runs = (e.detail && e.detail.runs) || {};
    const waiting = document.querySelectorAll('.transfer-item[data-phase="preview"]');
    for (const item of waiting) {
        const run = runs[item.dataset.runId];
        if (run && run.phase !== 'preview') {
            location.reload();
            return;
        }
    }
});

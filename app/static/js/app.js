// SSE connection and global state
let evtSource = null;
let lastData = null;

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + units[i];
}

function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec === 0) return '0 B/s';
    return formatBytes(bytesPerSec) + '/s';
}

function formatETA(seconds) {
    if (!seconds || seconds <= 0) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + s + 's';
    return s + 's';
}

function updateConnectionStatus(connected) {
    const el = document.getElementById('connection-status');
    if (!el) return;
    if (connected) {
        el.innerHTML = '<i class="bi bi-circle-fill text-success"></i> Connected';
    } else {
        el.innerHTML = '<i class="bi bi-circle-fill text-danger"></i> Disconnected';
    }
}

function updateRunningBadge(count) {
    const badge = document.getElementById('running-badge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count;
        badge.classList.remove('d-none');
    } else {
        badge.classList.add('d-none');
    }
}

function connectSSE() {
    if (evtSource) {
        evtSource.close();
    }

    evtSource = new EventSource('/api/events');

    evtSource.onopen = function() {
        updateConnectionStatus(true);
    };

    evtSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            if (!data) return;
            lastData = data;

            // Update running badge (only counts our app's runs)
            updateRunningBadge(data.active_count || 0);

            // Dispatch custom event for page-specific handlers
            window.dispatchEvent(new CustomEvent('rclone-stats', { detail: data }));
        } catch (e) {
            // Keepalive or parse error, ignore
        }
    };

    evtSource.onerror = function() {
        updateConnectionStatus(false);
    };
}

// Connect on page load
document.addEventListener('DOMContentLoaded', connectSSE);

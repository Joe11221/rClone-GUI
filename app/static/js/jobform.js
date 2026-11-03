// Behaviour of the job form: what each type does to the typed destination,
// the safety and warning panels, the schedule preview and the Advanced summary.
(function () {
    const form = document.getElementById('job-form');
    if (!form) return;
    const $ = id => document.getElementById(id);

    // --- Type cards: name the real destination, show what applies ---

    const destination = $('destination');
    const typeInputs = [...form.querySelectorAll('input[name="sync_type"]')];
    const autoResume = $('auto_resume');

    const selectedType = () => (typeInputs.find(i => i.checked) || {}).value;

    function refreshType() {
        const typed = destination.value.trim();
        form.querySelectorAll('[data-dest-name]').forEach(el => {
            el.textContent = typed || 'the destination';
            // Only a real path is set as code.
            el.classList.toggle('jf-dest-placeholder', !typed);
        });
        // The delete limit is only applied to syncs.
        $('safety-group').hidden = selectedType() !== 'sync';
        const risky = selectedType() === 'move' && autoResume.checked;
        form.querySelectorAll('[data-move-resume-warning]').forEach(el => { el.hidden = !risky; });
    }

    destination.addEventListener('input', refreshType);
    typeInputs.forEach(i => i.addEventListener('change', refreshType));

    // --- Auto-resume options ---

    autoResume.addEventListener('change', () => {
        $('resume-options').hidden = !autoResume.checked;
        refreshType();
    });

    // --- Order by: the window only matters for "mixed" ---

    const direction = $('order_by_direction');
    function refreshOrder() {
        $('order-by-window-row').hidden = direction.value !== 'mixed';
        refreshAdvanced();
    }
    direction.addEventListener('change', refreshOrder);

    // --- Quick-select remote links ---

    form.querySelectorAll('a[data-remote]').forEach(link => {
        link.addEventListener('click', event => {
            event.preventDefault();
            browseRemote('destination', link.dataset.remote);
        });
    });

    // --- Schedule: presets and a plain-words reading of the cron text ---

    const cron = $('schedule_cron');
    const preview = $('cron-preview');
    let previewTimer = null;
    let previewToken = 0;

    function refreshPreview() {
        const expr = cron.value.trim().replace(/\s+/g, ' ');
        if (!expr) { preview.textContent = ''; return; }
        const token = ++previewToken;
        fetch('/schedules/describe?expr=' + encodeURIComponent(expr))
            .then(r => r.json())
            .then(data => {
                if (token !== previewToken) return;  // a newer keystroke won
                preview.textContent = data.described ? data.text : '';
            })
            .catch(() => { if (token === previewToken) preview.textContent = ''; });
    }

    cron.addEventListener('input', () => {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(refreshPreview, 250);
    });
    form.querySelectorAll('.cron-preset').forEach(button => {
        button.addEventListener('click', () => {
            cron.value = button.dataset.cron;
            refreshPreview();
        });
    });

    // --- Advanced: how many things differ from the defaults ---

    const advanced = $('advanced');
    const count = $('advanced-count');
    const bwRules = advanced.querySelector('.bw-rules');

    function refreshAdvanced() {
        let changed = 0;
        advanced.querySelectorAll('[data-default]').forEach(el => {
            if (el.closest('[hidden]')) return;
            if (el.value.trim() !== el.dataset.default) changed += 1;
        });
        advanced.querySelectorAll('[data-default-checked]').forEach(el => {
            if (el.checked !== (el.dataset.defaultChecked === '1')) changed += 1;
        });
        if (bwRules && bwRules.children.length) changed += 1;
        count.hidden = changed === 0;
        count.textContent = changed + ' changed from defaults';
    }

    advanced.addEventListener('input', refreshAdvanced);
    advanced.addEventListener('change', refreshAdvanced);
    if (bwRules) new MutationObserver(refreshAdvanced).observe(bwRules, { childList: true });

    // A browser can't focus an invalid field inside a closed <details>, and
    // would silently refuse to submit. Open it so the message is visible.
    form.addEventListener('invalid', event => {
        const section = event.target.closest('details');
        if (section && !section.open) section.open = true;
    }, true);

    refreshType();
    refreshOrder();
})();

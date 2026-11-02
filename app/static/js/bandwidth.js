// Weekly bandwidth rules editor (see partials/bandwidth_editor.html).
// The server re-checks every rule; this only has to be friendly. Remote names
// and other text go in via textContent, never innerHTML.

(function () {
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const UNIT_BYTES = { KiB: 1024, MiB: 1048576, GiB: 1073741824, Mbit: 125000 };
    const UNIT_LABEL = { KiB: 'KiB/s', MiB: 'MiB/s', GiB: 'GiB/s', Mbit: 'Mbit/s' };
    const SLOTS_PER_DAY = 48;  // half hours in the overview
    const MAX_RULES = 50;

    function toMinutes(text) {
        const match = /^(\d{1,2}):(\d{2})$/.exec(text || '');
        if (!match || +match[1] > 23 || +match[2] > 59) return null;
        return +match[1] * 60 + +match[2];
    }

    // Same rules as window_active() in services/bandwidth.py.
    function windowActive(days, start, end, day, minute) {
        if (start === end) return days.includes(day);
        if (start < end) return days.includes(day) && minute >= start && minute < end;
        return (days.includes(day) && minute >= start)
            || (days.includes((day + 6) % 7) && minute < end);
    }

    function daysLabel(days) {
        const sorted = [...new Set(days)].sort((a, b) => a - b);
        const key = sorted.join();
        if (key === '0,1,2,3,4,5,6') return 'Every day';
        if (key === '0,1,2,3,4') return 'Weekdays';
        if (key === '5,6') return 'Weekends';
        const runs = [];
        sorted.forEach(day => {
            const last = runs[runs.length - 1];
            if (last && day === last[last.length - 1] + 1) last.push(day);
            else runs.push([day]);
        });
        return runs.map(run => run.length < 3
            ? run.map(d => DAYS[d]).join(', ')
            : `${DAYS[run[0]]}–${DAYS[run[run.length - 1]]}`).join(', ');
    }

    function limitLabel(rule) {
        return `${rule.value} ${UNIT_LABEL[rule.unit]}`;
    }

    function init(root) {
        const scoped = root.dataset.scoped === '1';
        const remotes = JSON.parse(root.dataset.remotes || '[]');
        const template = root.querySelector('.bw-template');
        const list = root.querySelector('.bw-rules');
        const empty = root.querySelector('.bw-empty');
        const errorBox = root.querySelector('.bw-error');
        const output = root.querySelector('.bw-output');
        const addButton = root.querySelector('.bw-add');
        const overview = root.querySelector('.bw-overview');
        const grid = root.querySelector('.bw-grid');

        const rows = () => [...list.querySelectorAll('.bw-rule')];

        // --- Reading a row ---

        function readRow(row) {
            return {
                enabled: row.querySelector('.bw-enabled').checked,
                days: [...row.querySelectorAll('.bw-day.active')].map(b => +b.dataset.day),
                all_day: row.querySelector('.bw-allday').checked,
                start: row.querySelector('.bw-start').value,
                end: row.querySelector('.bw-end').value,
                value: row.querySelector('.bw-value').value.trim(),
                unit: row.querySelector('.bw-unit').value,
                scope: scoped ? row.querySelector('.bw-scope').value : 'everything',
                remotes: [...row.querySelectorAll('.bw-remote-list input:checked')].map(i => i.value),
            };
        }

        // The first thing wrong with a rule, or '' if it is fine.
        function problem(rule) {
            if (!rule.days.length) return 'pick at least one day.';
            if (!rule.all_day) {
                const start = toMinutes(rule.start);
                const end = toMinutes(rule.end);
                if (start === null || end === null) return 'enter both times.';
                if (start === end) return 'the start and end time are the same. Tick “All day” to cover the whole day.';
            }
            const value = Number(rule.value);
            if (!rule.value || !Number.isFinite(value) || value <= 0) return 'enter a speed limit above zero.';
            if (value * UNIT_BYTES[rule.unit] < 1024) return 'the limit can’t be below 1 KiB/s.';
            if (rule.scope === 'remotes' && !rule.remotes.length) return 'choose at least one remote.';
            return '';
        }

        function scopeLabel(rule) {
            if (rule.scope === 'all_remotes') return 'all remotes';
            if (rule.scope === 'remotes') return rule.remotes.join(', ') || 'no remote chosen';
            return 'all transfers';
        }

        // --- Keeping the page in step ---

        function refreshRow(row) {
            const rule = readRow(row);
            row.classList.toggle('is-off', !rule.enabled);
            row.querySelector('.bw-time-fields').style.display = rule.all_day ? 'none' : '';
            const remotesBox = row.querySelector('.bw-remotes-box');  // absent without scopes
            if (remotesBox) remotesBox.style.display = rule.scope === 'remotes' ? '' : 'none';

            const start = toMinutes(rule.start);
            const end = toMinutes(rule.end);
            row.querySelector('.bw-overnight').style.display =
                !rule.all_day && start !== null && end !== null && end < start ? '' : 'none';

            const parts = [daysLabel(rule.days) || 'No days'];
            parts.push(rule.all_day ? 'all day' : `${rule.start || '--:--'}–${rule.end || '--:--'}`);
            parts.push(rule.value ? limitLabel(rule) : 'no limit set');
            if (scoped) parts.push(scopeLabel(rule));
            row.querySelector('.bw-summary').textContent = parts.join(' · ');
        }

        function refreshOverview() {
            const active = rows().map(readRow)
                .filter(rule => rule.enabled && !problem(rule))
                .map(rule => ({
                    days: rule.days,
                    start: rule.all_day ? 0 : toMinutes(rule.start),
                    end: rule.all_day ? 0 : toMinutes(rule.end),
                    speed: Number(rule.value) * UNIT_BYTES[rule.unit],
                    label: limitLabel(rule),
                }));
            overview.style.display = active.length ? '' : 'none';
            grid.replaceChildren();
            if (!active.length) return;

            const corner = document.createElement('div');
            grid.appendChild(corner);
            for (let hour = 0; hour < 24; hour += 6) {  // each label spans 6 hours
                const tick = document.createElement('div');
                tick.className = 'bw-tick';
                tick.textContent = String(hour).padStart(2, '0');
                grid.appendChild(tick);
            }
            DAYS.forEach((name, day) => {
                const label = document.createElement('div');
                label.className = 'bw-day-label';
                label.textContent = name;
                grid.appendChild(label);
                for (let slot = 0; slot < SLOTS_PER_DAY; slot++) {
                    const minute = slot * 30;
                    let best = null;
                    active.forEach(rule => {
                        if (windowActive(rule.days, rule.start, rule.end, day, minute)
                            && (!best || rule.speed < best.speed)) best = rule;
                    });
                    const cell = document.createElement('div');
                    cell.className = 'bw-cell' + (best ? ' limited' : '');
                    const clock = `${String(Math.floor(minute / 60)).padStart(2, '0')}:${minute % 60 ? '30' : '00'}`;
                    cell.title = `${name} ${clock}: ${best ? best.label : 'no limit'}`;
                    grid.appendChild(cell);
                }
            });
        }

        function refresh() {
            rows().forEach(refreshRow);
            empty.style.display = rows().length ? 'none' : '';
            addButton.disabled = rows().length >= MAX_RULES;
            refreshOverview();
        }

        // --- Building rows ---

        function addRow(rule) {
            const row = template.content.firstElementChild.cloneNode(true);
            const days = rule.days || [0, 1, 2, 3, 4, 5, 6];
            row.querySelectorAll('.bw-day').forEach(button => {
                const on = days.includes(+button.dataset.day);
                button.classList.toggle('active', on);
                button.setAttribute('aria-pressed', String(on));
            });
            row.querySelector('.bw-enabled').checked = rule.enabled !== false;
            row.querySelector('.bw-allday').checked = rule.all_day !== false;
            row.querySelector('.bw-start').value = rule.start || '09:00';
            row.querySelector('.bw-end').value = rule.end || '17:00';
            row.querySelector('.bw-value').value = rule.value ?? '';
            row.querySelector('.bw-unit').value = rule.unit || 'MiB';

            const scopeRow = row.querySelector('.bw-scope-row');
            if (scoped) {
                row.querySelector('.bw-scope').value = rule.scope || 'everything';
                const chosen = rule.remotes || [];
                // Keep remotes that were chosen but no longer exist, so saving
                // doesn't silently drop them.
                const names = [...remotes, ...chosen.filter(name => !remotes.includes(name))];
                const box = row.querySelector('.bw-remote-list');
                names.forEach(name => {
                    const label = document.createElement('label');
                    label.className = 'form-check mb-0';
                    const input = document.createElement('input');
                    input.type = 'checkbox';
                    input.className = 'form-check-input';
                    input.value = name;
                    input.checked = chosen.includes(name);
                    const text = document.createElement('span');
                    text.className = 'form-check-label';
                    text.textContent = remotes.includes(name) ? name : `${name} (not configured)`;
                    label.append(input, text);
                    box.appendChild(label);
                });
                row.querySelector('.bw-no-remotes').style.display = names.length ? 'none' : '';
            } else {
                scopeRow.remove();
            }
            list.appendChild(row);
            refreshRow(row);
            return row;
        }

        // --- Events ---

        root.addEventListener('click', event => {
            const row = event.target.closest('.bw-rule');
            const day = event.target.closest('.bw-day');
            const preset = event.target.closest('.bw-preset');
            if (day) {
                day.classList.toggle('active');
                day.setAttribute('aria-pressed', String(day.classList.contains('active')));
            } else if (preset) {
                const wanted = preset.dataset.days.split(',').map(Number);
                row.querySelectorAll('.bw-day').forEach(button => {
                    const on = wanted.includes(+button.dataset.day);
                    button.classList.toggle('active', on);
                    button.setAttribute('aria-pressed', String(on));
                });
            } else if (event.target.closest('.bw-remove')) {
                row.remove();
            } else {
                return;
            }
            errorBox.style.display = 'none';
            refresh();
        });

        root.addEventListener('input', event => {
            if (!event.target.closest('.bw-rule')) return;
            errorBox.style.display = 'none';
            refresh();
        });

        addButton.addEventListener('click', () => {
            const row = addRow({ value: '' });
            refresh();
            row.querySelector('.bw-value').focus();
        });

        // Rules go to the server as one JSON field when the form is sent.
        const form = root.closest('form');
        if (form) {
            form.addEventListener('submit', event => {
                const all = rows().map(readRow);
                for (let i = 0; i < all.length; i++) {
                    const message = problem(all[i]);
                    if (message) {
                        event.preventDefault();
                        errorBox.textContent = `Bandwidth rule ${i + 1}: ${message}`;
                        errorBox.style.display = '';
                        rows()[i].scrollIntoView({ block: 'center', behavior: 'smooth' });
                        return;
                    }
                }
                output.value = JSON.stringify(all.map(rule => ({
                    ...rule,
                    value: Number(rule.value),
                })));
            });
        }

        JSON.parse(root.dataset.rules || '[]').forEach(addRow);
        refresh();
    }

    document.querySelectorAll('.bw-editor').forEach(init);
})();

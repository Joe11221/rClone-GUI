// Weekly bandwidth rules editor (see partials/bandwidth_editor.html).
// The server re-checks every rule; this only has to be friendly. Remote names
// and other text go in via textContent, never innerHTML.
//
// Show and hide with the d-none class, not style.display: Bootstrap's d-flex
// is !important, so an inline "display: none" would lose to it.

(function () {
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const UNIT_BYTES = { KiB: 1024, MiB: 1048576, GiB: 1073741824, Mbit: 125000 };
    const UNIT_LABEL = { KiB: 'KiB/s', MiB: 'MiB/s', GiB: 'GiB/s', Mbit: 'Mbit/s' };
    const SLOTS_PER_DAY = 48;  // half hours in the preview
    const LOCAL = '';  // the preview's "local disk only" choice
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

    function clock(slot) {
        return `${String(Math.floor(slot / 2)).padStart(2, '0')}:${slot % 2 ? '30' : '00'}`;
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
        const preview = root.querySelector('.bw-preview');
        const timeline = root.querySelector('.bw-timeline');
        const previewEmpty = root.querySelector('.bw-preview-empty');
        const previewFor = root.querySelector('.bw-preview-for');  // absent without scopes

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
            row.querySelector('.bw-time-fields').classList.toggle('d-none', rule.all_day);
            const remotesBox = row.querySelector('.bw-remotes-box');  // absent without scopes
            if (remotesBox) remotesBox.classList.toggle('d-none', rule.scope !== 'remotes');

            const start = toMinutes(rule.start);
            const end = toMinutes(rule.end);
            row.querySelector('.bw-overnight').classList.toggle(
                'd-none', rule.all_day || start === null || end === null || end >= start);

            const when = [daysLabel(rule.days) || 'No days',
                rule.all_day ? 'all day' : `${rule.start || '--:--'}–${rule.end || '--:--'}`];
            const what = [rule.value ? limitLabel(rule) : 'no limit set'];
            if (scoped) what.push(scopeLabel(rule));
            row.querySelector('.bw-summary').textContent =
                `${when.join(', ')}: ${what.join(' on ')}`;
        }

        // Which rules reach a job using these remotes. Same rules as the server.
        function reaches(rule, names, hasRemote) {
            if (rule.scope === 'all_remotes') return hasRemote;
            if (rule.scope === 'remotes') return rule.remotes.some(name => names.includes(name));
            return true;
        }

        // The preview's "for a job that uses" list: configured remotes, plus any a rule names.
        function syncPreviewChoices(all) {
            const names = [...remotes];
            all.forEach(rule => rule.remotes.forEach(name => {
                if (!names.includes(name)) names.push(name);
            }));
            const kept = previewFor.dataset.touched ? previewFor.value : null;
            const named = all.find(rule => rule.scope === 'remotes' && rule.remotes.length);

            previewFor.replaceChildren();
            names.forEach(name => previewFor.add(new Option(`remote “${name}”`, name)));
            previewFor.add(new Option('local disk only', LOCAL));
            const values = [...previewFor.options].map(option => option.value);
            previewFor.value = kept !== null && values.includes(kept) ? kept
                : named ? named.remotes[0]
                : names.length ? names[0] : LOCAL;
        }

        function refreshPreview() {
            const all = rows().map(readRow);
            if (previewFor) syncPreviewChoices(all);
            const target = previewFor ? previewFor.value : null;
            const names = target ? [target] : [];
            const hasRemote = Boolean(target);

            const active = all
                .filter(rule => rule.enabled && !problem(rule))
                .filter(rule => !previewFor || reaches(rule, names, hasRemote))
                .map(rule => ({
                    days: rule.days,
                    start: rule.all_day ? 0 : toMinutes(rule.start),
                    end: rule.all_day ? 0 : toMinutes(rule.end),
                    speed: Number(rule.value) * UNIT_BYTES[rule.unit],
                    label: limitLabel(rule),
                }));

            preview.classList.toggle('d-none', !all.length);
            previewEmpty.classList.toggle('d-none', active.length > 0 || !all.length);
            previewEmpty.textContent = 'No limit would apply to a job like this.';
            timeline.replaceChildren();
            if (!active.length) return;

            const header = document.createElement('div');
            header.className = 'bw-line';
            const ticks = document.createElement('div');
            ticks.className = 'bw-ticks';
            ['00', '06', '12', '18', '24'].forEach(text => {
                const tick = document.createElement('span');
                tick.textContent = text;
                ticks.appendChild(tick);
            });
            header.append(document.createElement('span'), ticks);
            timeline.appendChild(header);

            DAYS.forEach((name, day) => {
                // The lowest limit for each half hour; neighbours with the same one become one bar.
                const slots = [];
                for (let slot = 0; slot < SLOTS_PER_DAY; slot++) {
                    let best = null;
                    active.forEach(rule => {
                        if (windowActive(rule.days, rule.start, rule.end, day, slot * 30)
                            && (!best || rule.speed < best.speed)) best = rule;
                    });
                    slots.push(best);
                }
                const track = document.createElement('div');
                track.className = 'bw-track';
                for (let from = 0; from < SLOTS_PER_DAY;) {
                    let to = from + 1;
                    while (to < SLOTS_PER_DAY && slots[to] === slots[from]) to++;
                    if (slots[from]) {
                        const bar = document.createElement('div');
                        bar.className = 'bw-bar';
                        bar.style.left = `${from / SLOTS_PER_DAY * 100}%`;
                        bar.style.width = `${(to - from) / SLOTS_PER_DAY * 100}%`;
                        bar.textContent = slots[from].label;
                        bar.title = `${name} ${clock(from)}–${to === SLOTS_PER_DAY ? '24:00' : clock(to)}: ${slots[from].label}`;
                        track.appendChild(bar);
                    }
                    from = to;
                }
                const line = document.createElement('div');
                line.className = 'bw-line';
                const label = document.createElement('span');
                label.className = 'bw-day-label';
                label.textContent = name;
                line.append(label, track);
                timeline.appendChild(line);
            });
        }

        function refresh() {
            rows().forEach(refreshRow);
            empty.classList.toggle('d-none', rows().length > 0);
            addButton.disabled = rows().length >= MAX_RULES;
            refreshPreview();
        }

        // --- Building rows ---

        function setDays(row, days) {
            row.querySelectorAll('.bw-day').forEach(button => {
                const on = days.includes(+button.dataset.day);
                button.classList.toggle('active', on);
                button.setAttribute('aria-pressed', String(on));
            });
        }

        function addRow(rule) {
            const row = template.content.firstElementChild.cloneNode(true);
            setDays(row, rule.days || [0, 1, 2, 3, 4, 5, 6]);
            row.querySelector('.bw-enabled').checked = rule.enabled !== false;
            row.querySelector('.bw-allday').checked = rule.all_day !== false;
            row.querySelector('.bw-start').value = rule.start || '09:00';
            row.querySelector('.bw-end').value = rule.end || '17:00';
            row.querySelector('.bw-value').value = rule.value ?? '';
            row.querySelector('.bw-unit').value = rule.unit || 'MiB';

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
                row.querySelector('.bw-no-remotes').classList.toggle('d-none', names.length > 0);
            } else {
                row.querySelector('.bw-scope-row').remove();
                row.querySelector('.bw-remotes-box').remove();
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
                setDays(row, preset.dataset.days.split(',').map(Number));
            } else if (event.target.closest('.bw-remove')) {
                row.remove();
            } else {
                return;
            }
            errorBox.style.display = 'none';
            refresh();
        });

        root.addEventListener('input', event => {
            if (event.target === previewFor) previewFor.dataset.touched = '1';
            else if (!event.target.closest('.bw-rule')) return;
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

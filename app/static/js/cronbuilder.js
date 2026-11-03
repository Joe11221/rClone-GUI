// Schedule editor: a simple builder over the plain cron text input.
// The text input stays the source of truth (it's what the form submits); Simple
// mode reads it on load and writes a standard 5-field expression back.
(function () {
    // Chips run Monday first; cron counts Sunday as 0 (and 7).
    const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
    const NAME_INDEX = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
    const MINUTE_STEPS = [5, 10, 15, 20, 30];
    const HOUR_STEPS = [1, 2, 3, 4, 6, 8, 12];

    const integer = (text, max) => (/^\d+$/.test(text) && +text <= max ? +text : null);

    // One day or a range like mon-fri / 1-5, as cron weekday numbers (Sunday 0 or 7).
    function dayNumbers(part) {
        const [lo, hi, extra] = part.split('-');
        if (extra !== undefined) return null;
        const value = (token, isEnd) => {
            if (Object.prototype.hasOwnProperty.call(NAME_INDEX, token)) return NAME_INDEX[token] || (isEnd ? 7 : 0);
            return integer(token, 7);
        };
        const start = value(lo, false);
        if (start === null) return null;
        if (hi === undefined) return [start];
        const end = value(hi, true);
        if (end === null || end < start) return null;
        const numbers = [];
        for (let n = start; n <= end; n += 1) numbers.push(n);
        return numbers;
    }

    // Weekday field -> chip names in Monday-first order, or null if it isn't plain days.
    function parseDays(field) {
        const picked = new Set();
        for (const part of field.toLowerCase().split(',')) {
            const numbers = dayNumbers(part);
            if (!numbers) return null;
            numbers.forEach(n => picked.add((n + 6) % 7));
        }
        return DAYS.filter((_, index) => picked.has(index));
    }

    // Chip names -> field text, with runs of three or more as ranges: mon-fri, sat,sun.
    function serializeDays(days) {
        const indexes = DAYS.map((d, i) => (days.includes(d) ? i : -1)).filter(i => i >= 0);
        if (indexes.length === 7) return '*';
        const parts = [];
        for (let i = 0; i < indexes.length;) {
            let end = i;
            while (end + 1 < indexes.length && indexes[end + 1] === indexes[end] + 1) end += 1;
            if (end - i >= 2) {
                parts.push(DAYS[indexes[i]] + '-' + DAYS[indexes[end]]);
            } else {
                for (let k = i; k <= end; k += 1) parts.push(DAYS[indexes[k]]);
            }
            i = end + 1;
        }
        return parts.join(',');
    }

    // Expression -> the builder's fields, or null when Simple mode can't express it.
    function parse(expression) {
        const text = expression.trim().replace(/\s+/g, ' ');
        if (!text) return { freq: 'manual' };
        const fields = text.split(' ');
        if (fields.length !== 5) return null;
        const [minute, hour, dom, month, dow] = fields;
        if (month !== '*') return null;

        const minuteStep = /^\*\/(\d+)$/.exec(minute);
        if (minuteStep && hour === '*' && dom === '*' && dow === '*') {
            return MINUTE_STEPS.includes(+minuteStep[1]) ? { freq: 'minutes', every: +minuteStep[1] } : null;
        }

        const m = integer(minute, 59);
        if (m === null) return null;
        if (dom === '*' && dow === '*') {
            if (hour === '*') return { freq: 'hours', every: 1, minute: m };
            const hourStep = /^\*\/(\d+)$/.exec(hour);
            if (hourStep) {
                return HOUR_STEPS.includes(+hourStep[1]) ? { freq: 'hours', every: +hourStep[1], minute: m } : null;
            }
        }

        const h = integer(hour, 23);
        if (h === null) return null;
        if (dom === '*' && dow === '*') return { freq: 'daily', hour: h, minute: m };
        if (dom === '*') {
            const days = parseDays(dow);
            return days && days.length ? { freq: 'weekly', days, hour: h, minute: m } : null;
        }
        const day = integer(dom, 31);
        if (dow === '*' && day >= 1) return { freq: 'monthly', dom: day, hour: h, minute: m };
        return null;
    }

    // The builder's fields -> expression, or null while the fields are incomplete.
    function serialize(state) {
        const inRange = (n, max) => Number.isInteger(n) && n >= 0 && n <= max;
        const clock = () => inRange(state.hour, 23) && inRange(state.minute, 59);
        switch (state.freq) {
            case 'manual':
                return '';
            case 'minutes':
                return MINUTE_STEPS.includes(state.every) ? '*/' + state.every + ' * * * *' : null;
            case 'hours':
                if (!HOUR_STEPS.includes(state.every) || !inRange(state.minute, 59)) return null;
                return state.minute + ' ' + (state.every === 1 ? '*' : '*/' + state.every) + ' * * *';
            case 'daily':
                return clock() ? state.minute + ' ' + state.hour + ' * * *' : null;
            case 'weekly':
                return clock() && state.days.length
                    ? state.minute + ' ' + state.hour + ' * * ' + serializeDays(state.days) : null;
            case 'monthly':
                return clock() && state.dom >= 1 && state.dom <= 31
                    ? state.minute + ' ' + state.hour + ' ' + state.dom + ' * *' : null;
            default:
                return null;
        }
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { parse, serialize, serializeDays, parseDays };
    }
    if (typeof document === 'undefined') return;

    function init(root) {
        const role = name => root.querySelector('[data-role="' + name + '"]');
        const input = root.querySelector('input[name][type="text"]');
        const modeBar = role('mode');
        const modeSimple = root.querySelector('input[type="radio"][value="simple"]');
        const modeCron = root.querySelector('input[type="radio"][value="cron"]');
        const note = role('note');
        const simple = role('simple');
        const expr = role('expr');
        const freq = role('freq');
        const everyMin = role('every-min');
        const everyHours = role('every-hours');
        const minuteField = role('minute');
        const domField = role('dom');
        const timeField = role('time');
        const dayBoxes = [...root.querySelectorAll('[data-role="day"]')];
        const daysError = role('days-error');
        const domNote = role('dom-note');
        const preview = document.getElementById('cron-preview');
        const raw = role('raw');
        const nextWrap = role('next-wrap');
        const nextList = role('next');
        const allowEmpty = root.dataset.allowEmpty === '1';
        const pad = n => String(n).padStart(2, '0');

        let mode = 'cron';
        let timer = null;
        let token = 0;

        const normalized = () => input.value.trim().replace(/\s+/g, ' ');

        // --- Reading and writing the fields ---

        function read() {
            return {
                freq: freq.value,
                every: +(freq.value === 'minutes' ? everyMin.value : everyHours.value),
                minute: freq.value === 'hours' ? integer(minuteField.value.trim(), 59)
                    : +(timeField.value.split(':')[1] || NaN),
                hour: +(timeField.value.split(':')[0] || NaN),
                dom: +domField.value,
                days: dayBoxes.filter(box => box.checked).map(box => box.value),
            };
        }

        function fill(state) {
            freq.value = state.freq;
            if (state.freq === 'minutes') everyMin.value = String(state.every);
            if (state.freq === 'hours') {
                everyHours.value = String(state.every);
                minuteField.value = String(state.minute);
            }
            if ('hour' in state) timeField.value = pad(state.hour) + ':' + pad(state.minute);
            if (state.freq === 'monthly') domField.value = String(state.dom);
            if (state.freq === 'weekly') dayBoxes.forEach(box => { box.checked = state.days.includes(box.value); });
            showFields();
        }

        // Only the fields for the chosen frequency are shown, and only those are
        // enabled, so hidden ones can't block the form or take a tab stop.
        function showFields() {
            const on = mode === 'simple';
            if (on && freq.value === 'weekly' && !dayBoxes.some(box => box.checked)) {
                dayBoxes.find(box => box.value === 'sun').checked = true;
            }
            root.querySelectorAll('[data-group]').forEach(group => {
                const shown = on && group.dataset.group.split(' ').includes(freq.value);
                group.hidden = !shown;
                group.querySelectorAll('input, select, button').forEach(c => { c.disabled = !shown; });
            });
            freq.disabled = !on;
            domNote.hidden = !(on && freq.value === 'monthly' && +domField.value > 28);
            const noDays = on && freq.value === 'weekly' && !dayBoxes.some(box => box.checked);
            daysError.hidden = !noDays;
            dayBoxes[0].setCustomValidity(noDays ? 'Choose at least one day.' : '');
        }

        // Fields -> text input. An incomplete set leaves the text as it was; the
        // browser's own validation stops the submit until it's complete.
        function write() {
            showFields();
            const text = serialize(read());
            if (text === null) return;
            input.value = text;
            clearServerError();
            schedulePreview(150);
        }

        function clearServerError() {
            input.classList.remove('is-invalid');
            input.removeAttribute('aria-invalid');
            const error = document.getElementById(input.id + '-error');
            if (error) error.hidden = true;
        }

        // --- Modes ---

        function setMode(next) {
            mode = next;
            (next === 'simple' ? modeSimple : modeCron).checked = true;
            simple.hidden = next !== 'simple';
            expr.hidden = next === 'simple';
            input.disabled = false;
            showFields();
            showPreview();
        }

        function defaultState() {
            return { freq: 'daily', hour: 2, minute: 0 };
        }

        function trySimple() {
            const text = normalized();
            let state = parse(text);
            if (state && state.freq === 'manual' && !allowEmpty) state = defaultState();
            if (!state) {
                note.hidden = false;
                return false;
            }
            note.hidden = true;
            fill(state);
            setMode('simple');
            if (!text && !allowEmpty) write();
            return true;
        }

        modeSimple.addEventListener('change', () => {
            if (!trySimple()) modeCron.checked = true;
        });
        modeCron.addEventListener('change', () => setMode('cron'));

        // --- Plain-words reading and next runs ---

        function showPreview() {
            const text = normalized();
            if (mode === 'simple') {
                raw.hidden = !text;
                role('raw-code').textContent = text;
            } else {
                raw.hidden = true;
            }
            if (!text) {
                preview.textContent = allowEmpty ? 'Only when you start it.' : '';
                preview.classList.remove('cron-bad');
                nextWrap.hidden = true;
                return;
            }
            schedulePreview(0);
        }

        function schedulePreview(delay) {
            clearTimeout(timer);
            timer = setTimeout(refreshPreview, delay);
        }

        function refreshPreview() {
            const text = normalized();
            raw.hidden = mode !== 'simple' || !text;
            role('raw-code').textContent = text;
            if (!text) { showPreview(); return; }
            const mine = ++token;
            fetch(root.dataset.describeUrl + '?expr=' + encodeURIComponent(text))
                .then(r => r.json())
                .then(data => {
                    if (mine !== token) return;  // a newer edit won
                    preview.classList.toggle('cron-bad', Boolean(data.error));
                    preview.textContent = data.error
                        || (data.described ? data.text : 'Cannot describe this expression; it will run as written.');
                    nextList.replaceChildren(...(data.next || []).map(when => {
                        const item = document.createElement('li');
                        item.textContent = when;
                        return item;
                    }));
                    nextWrap.hidden = !(data.next && data.next.length);
                })
                .catch(() => {});
        }

        // --- Wiring ---

        root.querySelectorAll('[data-role="freq"], [data-role="every-min"], [data-role="every-hours"], [data-role="dom"]')
            .forEach(el => el.addEventListener('change', write));
        [minuteField, timeField].forEach(el => el.addEventListener('input', write));
        dayBoxes.forEach(box => box.addEventListener('change', write));
        root.querySelectorAll('[data-role="days-preset"]').forEach(button => {
            button.addEventListener('click', () => {
                const wanted = button.dataset.days.split(',');
                dayBoxes.forEach(box => { box.checked = wanted.includes(box.value); });
                write();
            });
        });

        input.addEventListener('input', () => {
            clearServerError();
            note.hidden = true;
            schedulePreview(250);
        });

        // A preset sets the text, and Simple mode follows if it can express it.
        function setValue(text) {
            input.value = text;
            clearServerError();
            const state = parse(text);
            if (mode === 'simple' && state) {
                fill(state);
                note.hidden = true;
            } else if (mode === 'simple') {
                note.hidden = false;
                setMode('cron');
            }
            showPreview();
        }
        document.querySelectorAll('.cron-preset').forEach(button => {
            button.addEventListener('click', () => setValue(button.dataset.cron));
        });

        // --- Start: Simple when the saved text fits it, else the text with a note ---

        modeBar.hidden = false;
        fill({ freq: allowEmpty ? 'manual' : 'daily', hour: 2, minute: 0, every: 15, dom: 1, days: [] });
        everyMin.value = '15';
        everyHours.value = '6';
        minuteField.value = '0';
        const rejected = input.getAttribute('aria-invalid') === 'true';
        if (rejected || !trySimple()) {
            // Text the scheduler refused is shown as typed, without the "complex" note.
            note.hidden = rejected;
            setMode('cron');
        }
        refreshPreview();
    }

    document.querySelectorAll('[data-cron-builder]').forEach(init);
})();

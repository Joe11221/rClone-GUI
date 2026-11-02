// "Add remote": pick a storage type, then fill in the options rclone defines
// for it. Everything from rclone (names, help text) goes in via textContent.

const typeSearch = document.getElementById('type-search');
const typeList = document.getElementById('type-list');
const popularBox = document.getElementById('popular-types');
const form = document.getElementById('remote-form');
const nameInput = document.getElementById('remote-name');
const basicBox = document.getElementById('basic-options');
const advancedBox = document.getElementById('advanced-options');
const advancedWrap = document.getElementById('advanced-wrap');
const oauthBox = document.getElementById('oauth-box');
const tokenBox = document.getElementById('token-box');
const tokenInput = document.getElementById('remote-token');
const errorBox = document.getElementById('remote-error');
const waitingBox = document.getElementById('remote-waiting');
const submitButton = document.getElementById('remote-submit');

let current = null;  // detail of the chosen storage type
const labelToType = new Map();

function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = message ? '' : 'none';
}

function uniqueName(base) {
    if (!existingRemotes.includes(base)) return base;
    let n = 2;
    while (existingRemotes.includes(base + n)) n++;
    return base + n;
}

// --- Type picker ---

remoteTypes.forEach(t => {
    const label = t.label + ' (' + t.name + ')';
    labelToType.set(label.toLowerCase(), t.name);
    const option = document.createElement('option');
    option.value = label;
    typeList.appendChild(option);
    if (t.popular) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'btn btn-sm btn-outline-secondary';
        chip.textContent = t.label;
        chip.addEventListener('click', () => {
            typeSearch.value = label;
            chooseType(t.name);
        });
        popularBox.appendChild(chip);
    }
});

typeSearch.addEventListener('change', () => {
    const name = labelToType.get(typeSearch.value.trim().toLowerCase());
    if (name) chooseType(name);
});

function chooseType(name) {
    showError('');
    fetch('/settings/remotes/types/' + encodeURIComponent(name))
        .then(r => r.json().then(body => (r.ok ? body : Promise.reject(new Error(body.error)))))
        .then(renderType)
        .catch(err => {
            // The error box lives inside the form, so show the form to show it.
            form.style.display = '';
            showError(err.message);
        });
}

// --- Fields ---

// Options such as S3's region only apply to some values of its "provider" option.
function appliesTo(spec, value) {
    if (!spec) return true;
    const negated = spec.startsWith('!');
    const hit = (negated ? spec.slice(1) : spec).split(',').includes(value);
    return negated ? !hit : hit;
}

function firstParagraph(text) {
    const para = (text || '').trim().split(/\n\s*\n/)[0];
    return para.length > 320 ? para.slice(0, 317) + '...' : para;
}

function buildField(opt) {
    const wrap = document.createElement('div');
    wrap.className = 'mb-3 remote-field';
    wrap.dataset.provider = opt.provider;

    const id = 'opt-' + opt.name;
    const label = document.createElement('label');
    label.className = 'form-label';
    label.htmlFor = id;
    label.textContent = opt.name + (opt.required && !opt.default ? ' *' : '');
    wrap.appendChild(label);

    let input;
    const choices = opt.examples.length ? opt.examples
        : opt.type === 'bool' ? [{ value: 'true', help: '' }, { value: 'false', help: '' }] : [];
    if (choices.length && (opt.exclusive || opt.type === 'bool')) {
        input = document.createElement('select');
        input.className = 'form-select';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = opt.default ? 'Default (' + opt.default + ')' : 'Choose...';
        input.appendChild(blank);
        choices.forEach(choice => {
            const o = document.createElement('option');
            o.value = choice.value;
            o.textContent = choice.help ? choice.value + ' – ' + choice.help.split('\n')[0] : choice.value;
            input.appendChild(o);
        });
    } else {
        input = document.createElement('input');
        input.type = opt.password ? 'password' : 'text';
        input.className = 'form-control';
        input.autocomplete = opt.password ? 'new-password' : 'off';
        if (opt.default) input.placeholder = opt.default;
        if (choices.length) {
            const list = document.createElement('datalist');
            list.id = id + '-examples';
            choices.forEach(choice => {
                const o = document.createElement('option');
                o.value = choice.value;
                list.appendChild(o);
            });
            input.setAttribute('list', list.id);
            wrap.appendChild(list);
        }
    }
    input.id = id;
    input.dataset.option = opt.name;
    wrap.appendChild(input);

    const help = firstParagraph(opt.help);
    if (help) {
        const text = document.createElement('div');
        text.className = 'form-text';
        text.style.whiteSpace = 'pre-line';
        text.textContent = help;
        wrap.appendChild(text);
    }
    return wrap;
}

function refreshVisibility() {
    const providerField = form.querySelector('[data-option="provider"]');
    const value = providerField ? providerField.value : '';
    form.querySelectorAll('.remote-field').forEach(field => {
        field.style.display = appliesTo(field.dataset.provider, value) ? '' : 'none';
    });
}

function renderType(detail) {
    current = detail;
    showError('');
    form.style.display = '';
    document.getElementById('remote-title').textContent = '2. Details – ' + detail.description;
    nameInput.value = uniqueName(detail.name);
    basicBox.replaceChildren();
    advancedBox.replaceChildren();
    detail.options.forEach(opt => (opt.advanced ? advancedBox : basicBox).appendChild(buildField(opt)));
    advancedWrap.style.display = advancedBox.children.length ? '' : 'none';
    oauthBox.style.display = detail.oauth ? '' : 'none';
    document.getElementById('authorize-command').textContent = 'rclone authorize "' + detail.name + '"';
    tokenInput.value = '';
    refreshVisibility();
    form.querySelector('[data-option="provider"]')?.addEventListener('change', refreshVisibility);
}

// --- Submit ---

function wantsBrowser() {
    const radio = document.getElementById('signin-browser');
    return desktopMode && current.oauth && radio && radio.checked;
}

document.querySelectorAll('input[name="signin"]').forEach(radio =>
    radio.addEventListener('change', () => {
        tokenBox.style.display = wantsBrowser() ? 'none' : '';
    }));

function collectOptions() {
    const options = {};
    form.querySelectorAll('[data-option]').forEach(input => {
        if (input.closest('.remote-field').style.display === 'none') return;
        if (input.value.trim() !== '') options[input.dataset.option] = input.value;
    });
    return options;
}

function pollSetup(id) {
    fetch('/settings/remotes/status/' + id)
        .then(r => r.json())
        .then(job => {
            if (job.state === 'running') {
                setTimeout(() => pollSetup(id), 1500);
            } else if (job.state === 'done') {
                window.location = '/settings/';
            } else {
                waitingBox.style.display = 'none';
                submitButton.disabled = false;
                showError(job.error || 'Setup failed.');
            }
        })
        .catch(() => setTimeout(() => pollSetup(id), 3000));
}

form.addEventListener('submit', event => {
    event.preventDefault();
    if (!current) return;
    showError('');
    submitButton.disabled = true;
    fetch('/settings/remotes/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: nameInput.value,
            type: current.name,
            options: collectOptions(),
            token: wantsBrowser() ? '' : tokenInput.value,
            browser: wantsBrowser(),
        }),
    })
        .then(r => r.json().then(body => ({ ok: r.ok, status: r.status, body })))
        .then(({ ok, status, body }) => {
            if (status === 202) {
                waitingBox.style.display = '';
                pollSetup(body.pending);
            } else if (ok) {
                window.location = '/settings/';
            } else {
                submitButton.disabled = false;
                showError(body.error || 'Something went wrong.');
            }
        })
        .catch(err => {
            submitButton.disabled = false;
            showError('Request failed: ' + err);
        });
});

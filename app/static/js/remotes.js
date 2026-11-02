// "Add remote": pick a storage type, then fill in the options rclone defines
// for it. Everything from rclone (names, help text) goes in via textContent.

const typeSearch = document.getElementById('type-search');
const typePicker = document.getElementById('type-picker');
const typeTitle = document.getElementById('type-title');
const typeChange = document.getElementById('type-change');
const typeNone = document.getElementById('type-none');
const form = document.getElementById('remote-form');
const nameInput = document.getElementById('remote-name');
const basicBox = document.getElementById('basic-options');
const advancedBox = document.getElementById('advanced-options');
const advancedWrap = document.getElementById('advanced-wrap');
const oauthBox = document.getElementById('oauth-box');
const tokenInput = document.getElementById('remote-token');
const errorBox = document.getElementById('remote-error');
const signinPanel = document.getElementById('signin-panel');
const signinLink = document.getElementById('signin-link');
const signinUrl = document.getElementById('signin-url');
const signinFinish = document.getElementById('signin-finish');
const submitButton = document.getElementById('remote-submit');

let current = null;  // detail of the chosen storage type
let pendingSetup = null;  // id of the sign-in in progress

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

const typeButtons = [...document.querySelectorAll('.type-card')];

typeButtons.forEach(button =>
    button.addEventListener('click', () => chooseType(button.dataset.type, button.textContent)));

typeSearch.addEventListener('input', () => {
    const words = typeSearch.value.trim().toLowerCase().split(/\s+/);
    typeButtons.forEach(button => {
        button.hidden = !words.every(word => button.dataset.search.includes(word));
    });
    let any = false;
    document.querySelectorAll('.type-group').forEach(group => {
        const shown = [...group.querySelectorAll('.type-card')].some(b => !b.hidden);
        group.hidden = !shown;
        any = any || shown;
    });
    typeNone.style.display = any ? 'none' : '';
});

typeChange.addEventListener('click', () => {
    if (pendingSetup) cancelSetup();
    typePicker.style.display = '';
    typeChange.style.display = 'none';
    typeTitle.textContent = '1. Where is your storage?';
    form.style.display = 'none';
    submitButton.disabled = false;
    current = null;
    typeSearch.focus();
});

function chooseType(name, label) {
    showError('');
    typePicker.style.display = 'none';
    typeChange.style.display = '';
    typeTitle.textContent = '1. ' + label;
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
    if (!value) return false;
    const negated = spec.startsWith('!');
    const hit = (negated ? spec.slice(1) : spec).split(',').includes(value);
    return negated ? !hit : hit;
}

function firstParagraph(text) {
    const para = (text || '').trim().split(/\n\s*\n/)[0];
    return para.length > 320 ? para.slice(0, 317) + '...' : para;
}

const ACRONYMS = { api: 'API', id: 'ID', url: 'URL', ssh: 'SSH', tls: 'TLS', acl: 'ACL', sas: 'SAS', '2fa': '2FA' };

function fieldLabel(name) {
    const words = name.split('_').map(word => ACRONYMS[word] || word);
    const first = words[0];
    words[0] = first.charAt(0).toUpperCase() + first.slice(1);
    return words.join(' ');
}

function buildField(opt, index) {
    const wrap = document.createElement('div');
    wrap.className = 'mb-3 remote-field';
    wrap.dataset.provider = opt.provider;

    // S3 and others define an option several times, once per provider.
    const id = 'opt-' + index;
    const label = document.createElement('label');
    label.className = 'form-label';
    label.htmlFor = id;
    label.textContent = fieldLabel(opt.name) + (opt.required && !opt.default ? ' *' : '');
    wrap.appendChild(label);

    let input;
    const choices = opt.examples.length ? opt.examples
        : opt.type === 'bool' ? [{ value: 'true', help: '' }, { value: 'false', help: '' }] : [];
    if (choices.length && (opt.exclusive || opt.type === 'bool' || opt.name === 'provider')) {
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
        // Wrappers such as crypt point at another remote: offer the existing ones.
        const suggestions = opt.name === 'remote'
            ? existingRemotes.map(name => ({ value: name + ':' }))
            : choices;
        if (suggestions.length) {
            const list = document.createElement('datalist');
            list.id = id + '-examples';
            suggestions.forEach(choice => {
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
    input.dataset.default = opt.default;
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
    // Left blank, the provider option takes its default.
    const value = providerField ? providerField.value || providerField.dataset.default : '';
    form.querySelectorAll('.remote-field').forEach(field => {
        field.style.display = appliesTo(field.dataset.provider, value) ? '' : 'none';
    });
}

function renderType(detail) {
    current = detail;
    showError('');
    form.style.display = '';
    document.getElementById('remote-title').textContent = '2. Details';
    document.getElementById('remote-submit-label').textContent =
        detail.oauth ? 'Continue to sign-in' : 'Create remote';
    nameInput.value = uniqueName(detail.name);
    basicBox.replaceChildren();
    advancedBox.replaceChildren();
    detail.options.forEach((opt, index) =>
        (opt.advanced ? advancedBox : basicBox).appendChild(buildField(opt, index)));
    advancedWrap.style.display = advancedBox.children.length ? '' : 'none';
    oauthBox.style.display = detail.oauth ? '' : 'none';
    document.getElementById('authorize-command').textContent = 'rclone authorize "' + detail.name + '"';
    tokenInput.value = '';
    refreshVisibility();
    form.querySelector('[data-option="provider"]')?.addEventListener('change', refreshVisibility);
}

// --- Submit ---

function collectOptions() {
    const options = {};
    form.querySelectorAll('[data-option]').forEach(input => {
        if (input.closest('.remote-field').style.display === 'none') return;
        if (input.value.trim() !== '') options[input.dataset.option] = input.value;
    });
    return options;
}

function setupFailed(message) {
    pendingSetup = null;
    signinPanel.style.display = 'none';
    submitButton.disabled = false;
    showError(message);
}

function showStep(step) {
    ['starting', 'ready', 'finishing'].forEach(name => {
        document.getElementById('signin-' + name).style.display = name === step ? '' : 'none';
    });
}

function pollSetup(id) {
    if (pendingSetup !== id) return;
    fetch('/settings/remotes/status/' + id)
        .then(r => r.json())
        .then(job => {
            if (pendingSetup !== id) return;
            if (job.state === 'done') {
                window.location = '/settings/';
            } else if (job.state === 'error' || !job.state) {
                setupFailed(job.error || 'Setup failed.');
            } else {
                if (job.notices && job.notices.length) {
                    const notice = document.getElementById('signin-notice');
                    notice.textContent = job.notices.join('\n\n');
                    notice.style.display = '';
                }
                if (job.state === 'signin') {
                    signinLink.href = job.auth_url;
                    showStep('ready');
                } else if (job.state === 'finishing') {
                    showStep('finishing');
                }
                setTimeout(() => pollSetup(id), 1500);
            }
        })
        .catch(() => setTimeout(() => pollSetup(id), 3000));
}

function cancelSetup() {
    fetch('/settings/remotes/cancel/' + pendingSetup, { method: 'POST' });
    pendingSetup = null;
    signinPanel.style.display = 'none';
    submitButton.disabled = false;
}

if (signinFinish) {
    signinFinish.addEventListener('click', () => {
        showError('');
        signinFinish.disabled = true;
        fetch('/settings/remotes/finish/' + pendingSetup, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: signinUrl.value }),
        })
            .then(r => r.json().then(body => {
                if (!r.ok) showError(body.error || 'Something went wrong.');
            }))
            .catch(err => showError('Request failed: ' + err))
            .finally(() => { signinFinish.disabled = false; });
    });
}

// Leaving the page abandons a sign-in, and frees its name and rclone's port.
window.addEventListener('pagehide', () => {
    if (pendingSetup) navigator.sendBeacon('/settings/remotes/cancel/' + pendingSetup);
});

form.addEventListener('submit', event => {
    event.preventDefault();
    if (!current) return;
    const submitted = current;
    showError('');
    submitButton.disabled = true;
    fetch('/settings/remotes/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: nameInput.value,
            type: current.name,
            options: collectOptions(),
            token: tokenInput.value,
        }),
    })
        .then(r => r.json().then(body => ({ ok: r.ok, status: r.status, body })))
        .then(({ ok, status, body }) => {
            if (status === 202) {
                pendingSetup = body.pending;
                // The user may have moved on to another type in the meantime.
                if (current !== submitted) {
                    cancelSetup();
                    return;
                }
                showStep('starting');
                signinPanel.style.display = '';
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

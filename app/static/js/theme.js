// Theme selection. Loaded in <head> so the theme is applied before first paint.
// A theme (the look) and a mode (light, dark, or follow the system) are chosen
// separately; every theme has both modes.
const THEMES = {
    graphite:  { label: 'Graphite',     dark: ['#121316', '#7b97ff'], light: ['#f3f4f7', '#3f55d9'] },
    glass:     { label: 'Liquid Glass', dark: ['#4f46e5', '#8be0ff'], light: ['#bfdbfe', '#1f5fe0'] },
    keycaps:   { label: 'Keycaps',      dark: ['#1c1d3d', '#ff7ab8'], light: ['#fdf0d5', '#d81b6a'] },
    terminal:  { label: 'Terminal',     dark: ['#030a05', '#39ff7a'], light: ['#eef2e6', '#0b7a32'] },
    synthwave: { label: 'Synthwave',    dark: ['#2a0a4a', '#ff2e97'], light: ['#ffd1f0', '#c2006b'] },
    brutalist: { label: 'Brutalist',    dark: ['#151515', '#ffd600'], light: ['#f3efe4', '#ffd600'] },
};
const MODES = { light: 'Light', dark: 'Dark', system: 'System' };
const DEFAULT_THEME = 'graphite';
const DEFAULT_MODE = 'dark';

// Names from earlier versions of the picker.
const LEGACY_THEMES = { frost: { theme: 'glass', mode: 'light' } };

function readSetting(key) {
    try {
        return localStorage.getItem(key);
    } catch (e) {
        return null;
    }
}

function writeSetting(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (e) { /* storage unavailable */ }
}

function currentTheme() {
    const stored = readSetting('theme');
    if (THEMES[stored]) return stored;
    return (LEGACY_THEMES[stored] || {}).theme || DEFAULT_THEME;
}

function currentMode() {
    const stored = readSetting('mode');
    if (MODES[stored]) return stored;
    return (LEGACY_THEMES[readSetting('theme')] || {}).mode || DEFAULT_MODE;
}

function effectiveMode(mode) {
    if (mode !== 'system') return mode;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function applyTheme(theme, mode) {
    if (!THEMES[theme]) theme = DEFAULT_THEME;
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.bsTheme = effectiveMode(mode);
}

applyTheme(currentTheme(), currentMode());

document.addEventListener('DOMContentLoaded', () => {
    const menu = document.getElementById('theme-menu');
    if (!menu) return;

    const refresh = () => {
        const theme = currentTheme();
        const mode = currentMode();
        const shown = effectiveMode(mode);
        applyTheme(theme, mode);
        menu.querySelectorAll('[data-mode]').forEach(btn =>
            btn.classList.toggle('active', btn.dataset.mode === mode));
        menu.querySelectorAll('[data-theme-choice]').forEach(item => {
            const name = item.dataset.themeChoice;
            item.classList.toggle('active', name === theme);
            const [bg, accent] = THEMES[name][shown];
            const swatch = item.querySelector('.theme-swatch');
            swatch.style.setProperty('--swatch-bg', bg);
            swatch.style.setProperty('--swatch-accent', accent);
        });
    };

    const modeRow = document.createElement('li');
    modeRow.className = 'px-2 pb-2 mode-switch';
    const group = document.createElement('div');
    group.className = 'btn-group btn-group-sm w-100';
    group.setAttribute('role', 'group');
    for (const [value, label] of Object.entries(MODES)) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-outline-secondary';
        btn.dataset.mode = value;
        btn.textContent = label;
        btn.addEventListener('click', () => {
            writeSetting('mode', value);
            refresh();
        });
        group.appendChild(btn);
    }
    modeRow.appendChild(group);
    menu.appendChild(modeRow);

    const divider = document.createElement('li');
    divider.innerHTML = '<hr class="dropdown-divider">';
    menu.appendChild(divider);

    for (const [name, t] of Object.entries(THEMES)) {
        const li = document.createElement('li');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'dropdown-item';
        btn.dataset.themeChoice = name;
        btn.innerHTML = '<span class="theme-swatch"></span><span></span><i class="bi bi-check2"></i>';
        btn.children[1].textContent = t.label;
        btn.addEventListener('click', () => {
            writeSetting('theme', name);
            refresh();
        });
        li.appendChild(btn);
        menu.appendChild(li);
    }

    // "System" follows the OS setting live.
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
        if (currentMode() === 'system') refresh();
    });
    refresh();
});

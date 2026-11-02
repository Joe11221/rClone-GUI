// Theme selection. Loaded in <head> so the theme is applied before first paint.
const THEMES = {
    graphite:  { label: 'Graphite',     mode: 'dark',  bg: '#121316', accent: '#6c8cff' },
    glass:     { label: 'Liquid Glass', mode: 'dark',  bg: '#4f46e5', accent: '#8be0ff' },
    frost:     { label: 'Frost',        mode: 'light', bg: '#bfdbfe', accent: '#2f6fed' },
    keycaps:   { label: 'Keycaps',      mode: 'light', bg: '#fdf0d5', accent: '#f0357e' },
    terminal:  { label: 'Terminal',     mode: 'dark',  bg: '#030a05', accent: '#39ff7a' },
    synthwave: { label: 'Synthwave',    mode: 'dark',  bg: '#2a0a4a', accent: '#ff2e97' },
    brutalist: { label: 'Brutalist',    mode: 'light', bg: '#ffd600', accent: '#111111' },
};
const DEFAULT_THEME = 'graphite';

function applyTheme(name) {
    if (!THEMES[name]) name = DEFAULT_THEME;
    const root = document.documentElement;
    root.dataset.theme = name;
    root.dataset.bsTheme = THEMES[name].mode;
    return name;
}

function currentTheme() {
    try {
        return localStorage.getItem('theme') || DEFAULT_THEME;
    } catch (e) {
        return DEFAULT_THEME;
    }
}

applyTheme(currentTheme());

document.addEventListener('DOMContentLoaded', () => {
    const menu = document.getElementById('theme-menu');
    if (!menu) return;

    const mark = name => {
        menu.querySelectorAll('.dropdown-item').forEach(item =>
            item.classList.toggle('active', item.dataset.theme === name));
    };

    for (const [name, t] of Object.entries(THEMES)) {
        const li = document.createElement('li');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'dropdown-item';
        btn.dataset.theme = name;
        btn.innerHTML = '<span class="theme-swatch"></span><span></span><i class="bi bi-check2"></i>';
        const swatch = btn.firstChild;
        swatch.style.setProperty('--swatch-bg', t.bg);
        swatch.style.setProperty('--swatch-accent', t.accent);
        btn.children[1].textContent = t.label;
        btn.addEventListener('click', () => {
            applyTheme(name);
            try { localStorage.setItem('theme', name); } catch (e) { /* storage unavailable */ }
            mark(name);
        });
        li.appendChild(btn);
        menu.appendChild(li);
    }
    mark(currentTheme());
});

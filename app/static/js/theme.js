// Theme selection. Loaded in <head> so the theme is applied before first paint.
const THEMES = {
    graphite: { label: 'Graphite', mode: 'dark',  bg: '#121316', accent: '#6c8cff' },
    midnight: { label: 'Midnight', mode: 'dark',  bg: '#0b1020', accent: '#4f9dff' },
    nord:     { label: 'Nord',     mode: 'dark',  bg: '#2e3440', accent: '#88c0d0' },
    dracula:  { label: 'Dracula',  mode: 'dark',  bg: '#1e1f29', accent: '#bd93f9' },
    forest:   { label: 'Forest',   mode: 'dark',  bg: '#0f1a16', accent: '#34d399' },
    glass:    { label: 'Liquid Glass', mode: 'dark',  bg: '#4f46e5', accent: '#7dd3fc' },
    'soft-glass':  { label: 'Soft Glass',   mode: 'dark',  bg: '#0a0d1f', accent: '#7dd3fc' },
    frost:    { label: 'Frost',        mode: 'light', bg: '#bfdbfe', accent: '#3b82f6' },
    'soft-frost': { label: 'Soft Frost', mode: 'light', bg: '#eef2fa', accent: '#3b82f6' },
    paper:    { label: 'Paper',    mode: 'light', bg: '#ffffff', accent: '#4f46e5' },
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

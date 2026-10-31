// Failure lists for runs (see templates/partials/run_errors.html).
//
// Runs can fail thousands of files, so nothing here loads them all: the server
// groups failures by cause, and rows are fetched a page at a time using id
// cursors. The live feed on the Running page keeps only the newest rows.
// File names and messages come from remote systems and are untrusted: always
// build nodes with textContent only.

function makeEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

class RunErrors {
    constructor(root) {
        this.root = root;
        this.runId = root.dataset.runId;
        this.live = root.dataset.live === '1';
        this.maxRows = parseInt(root.dataset.maxRows, 10) || 0;  // 0 = no limit
        this.pageSize = parseInt(root.dataset.pageSize, 10) || 100;
        this.category = '';
        this.query = '';
        this.lastId = 0;     // newest failure shown, for the live feed
        this.oldestId = 0;   // oldest failure shown, for "Load older"
        this.total = 0;
        this.generation = 0; // bumped on filter changes to drop stale replies
        this.busy = false;

        this.rows = root.querySelector('.re-rows');
        this.moreBtn = root.querySelector('.re-more');
        this.moreBtn.addEventListener('click', () => this.loadOlder());

        let timer = null;
        root.querySelector('.re-search').addEventListener('input', event => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                this.query = event.target.value.trim();
                this.reset();
            }, 300);
        });

        if (this.live) {
            window.addEventListener('rclone-stats', event => this.onStats(event.detail));
        } else {
            this.reset();
        }
    }

    // --- Live feed ---------------------------------------------------------

    onStats(data) {
        const run = (data.runs || {})[this.runId];
        if (!run || !run.stats) return;
        const reported = run.stats.errors || 0;
        const latest = this.root.querySelector('.re-latest');
        // rclone's own latest error shows up before the stored list catches up.
        if (run.stats.lastError) {
            latest.textContent = 'Latest: ' + run.stats.lastError;
            latest.classList.remove('d-none');
        }
        if (reported > 0 || this.total > 0) {
            this.root.classList.remove('d-none');
            this.poll();
        }
    }

    async poll() {
        if (this.busy) return;
        if (this.lastId === 0 && this.rows.children.length === 0) {
            return this.reset();
        }
        const generation = this.generation;
        this.busy = true;
        try {
            const params = {
                after: this.lastId, limit: this.maxRows || this.pageSize, summary: 1,
            };
            if (this.category || this.query) params.count = 1;
            const data = await this.fetchErrors(params);
            if (generation !== this.generation) return;
            if (data.errors.length > 0) {
                // Replies are newest first, so this keeps the feed in order.
                this.rows.prepend(...data.errors.map(e => this.renderRow(e)));
                this.lastId = data.errors[0].id;
                while (this.maxRows && this.rows.children.length > this.maxRows) {
                    this.rows.lastElementChild.remove();
                }
                this.oldestId = parseInt(this.rows.lastElementChild.dataset.id, 10);
            }
            this.applySummary(data);
        } catch (e) {
            // Transient; the next stats event retries.
        } finally {
            this.busy = false;
        }
    }

    // --- Browsing ----------------------------------------------------------

    async reset() {
        this.generation++;
        const generation = this.generation;
        this.busy = true;
        try {
            const data = await this.fetchErrors({
                limit: this.pageSize, summary: 1, count: 1,
            });
            if (generation !== this.generation) return;
            this.rows.replaceChildren(...data.errors.map(e => this.renderRow(e)));
            this.lastId = data.errors.length ? data.errors[0].id : data.last_id;
            this.oldestId = data.errors.length ? data.errors[data.errors.length - 1].id : 0;
            this.moreBtn.classList.toggle('d-none', !data.has_more);
            this.applySummary(data);
        } catch (e) {
            this.root.querySelector('.re-status').textContent = 'Could not load failures.';
        } finally {
            if (generation === this.generation) this.busy = false;
        }
    }

    async loadOlder() {
        if (this.busy || !this.oldestId) return;
        const generation = this.generation;
        this.busy = true;
        this.moreBtn.disabled = true;
        try {
            const data = await this.fetchErrors({ before: this.oldestId, limit: this.pageSize });
            if (generation !== this.generation) return;
            this.rows.append(...data.errors.map(e => this.renderRow(e)));
            if (data.errors.length) this.oldestId = data.errors[data.errors.length - 1].id;
            this.moreBtn.classList.toggle('d-none', !data.has_more);
            this.applySummary(data);
        } catch (e) {
            this.root.querySelector('.re-status').textContent = 'Could not load more.';
        } finally {
            this.moreBtn.disabled = false;
            this.busy = false;
        }
    }

    async fetchErrors(params) {
        const search = new URLSearchParams(params);
        if (this.category) search.set('category', this.category);
        if (this.query) search.set('q', this.query);
        const response = await fetch(`/runs/${this.runId}/errors?${search}`);
        if (!response.ok) throw new Error(response.status);
        return response.json();
    }

    // --- Rendering ---------------------------------------------------------

    applySummary(data) {
        this.total = data.total;
        if (data.matched !== undefined) this.matched = data.matched;
        this.root.querySelector('.re-total').textContent = data.total.toLocaleString();
        if (data.total > 0) this.root.classList.remove('d-none');
        if (data.categories) this.renderCategories(data.categories);

        const filtered = this.category || this.query;
        const shown = this.rows.children.length;
        const of = filtered && this.matched !== undefined ? this.matched : data.total;
        let text = `Showing ${shown.toLocaleString()} of ${of.toLocaleString()}`;
        if (filtered) text += ' matching';
        if (this.live && of > shown) text += ' (newest first)';
        this.root.querySelector('.re-status').textContent = shown ? text : (
            filtered ? 'No failures match.' : ''
        );

        const csv = new URLSearchParams();
        if (this.category) csv.set('category', this.category);
        if (this.query) csv.set('q', this.query);
        const base = `/runs/${this.runId}/errors.csv`;
        this.root.querySelector('.re-csv').href = csv.toString() ? `${base}?${csv}` : base;
    }

    renderCategories(categories) {
        const box = this.root.querySelector('.re-categories');
        const limit = this.live ? 5 : categories.length;
        const items = categories.slice(0, limit).map(c => {
            const button = makeEl('button', 'btn btn-sm re-cat d-flex align-items-baseline gap-2 text-start');
            button.type = 'button';
            button.classList.add(c.category === this.category ? 'btn-danger' : 'btn-outline-danger');
            button.append(
                makeEl('span', 'badge bg-dark text-nowrap', c.count.toLocaleString()),
                makeEl('span', 'text-break', c.category),
            );
            button.addEventListener('click', () => {
                this.category = this.category === c.category ? '' : c.category;
                this.reset();
            });
            return button;
        });
        if (categories.length > limit) {
            items.push(makeEl('small', 'text-muted', `+${categories.length - limit} more causes`));
        }
        box.replaceChildren(...items);
    }

    renderRow(error) {
        const tr = makeEl('tr', 're-row');
        tr.dataset.id = error.id;
        const time = error.occurred_at ? new Date(error.occurred_at) : null;
        const when = makeEl('td', 'text-muted text-nowrap small',
            time ? time.toLocaleTimeString() : '');
        const path = makeEl('td', 'font-monospace small re-path text-break',
            error.path || '(no file)');
        path.title = error.path || '';
        const message = makeEl('td', 'small re-msg', error.message);
        tr.append(when, path, message);
        // Messages can be long; click a row to read all of it.
        tr.addEventListener('click', () => tr.classList.toggle('expanded'));
        return tr;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.run-errors').forEach(root => new RunErrors(root));
});

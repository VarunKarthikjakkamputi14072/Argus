const API_BASE = '/api';
// When the live event stream is up we only poll occasionally as a safety net.
// If it drops we fall back to the faster poll.
const POLL_INTERVAL = 5000;
const POLL_INTERVAL_LIVE = 30000;

let currentView = 'dashboard';
let pollTimer = null;
let eventSource = null;
let liveConnected = false;

// --- Navigation ---

document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        switchView(view);
    });
});

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`[data-view="${view}"]`).classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(`view-${view}`).classList.add('active');
    refreshCurrentView();
}

// --- API Helpers ---

async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return { ok: res.ok, status: res.status, data: await res.json() };
}

// --- Submit Form ---

document.getElementById('submit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('url-input');
    const feedback = document.getElementById('submit-feedback');
    const url = input.value.trim();

    if (!url) return;

    feedback.classList.add('hidden');

    try {
        const { ok, status, data } = await apiPost('/articles/submit', { url });
        if (ok) {
            feedback.textContent = `Submitted! Task ID: ${data.task_id}`;
            feedback.className = 'feedback success';
            input.value = '';
            setTimeout(() => refreshCurrentView(), 1000);
        } else if (status === 409) {
            feedback.textContent = 'This URL has already been submitted.';
            feedback.className = 'feedback error';
        } else {
            feedback.textContent = `Error: ${data.detail || 'Submission failed'}`;
            feedback.className = 'feedback error';
        }
    } catch (err) {
        feedback.textContent = `Network error: ${err.message}`;
        feedback.className = 'feedback error';
    }

    feedback.classList.remove('hidden');
});

// --- Search & Filter ---

let searchDebounce = null;
document.getElementById('search-input').addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(loadArticles, 400);
});

document.getElementById('status-filter').addEventListener('change', loadArticles);

// --- Data Loading ---

async function loadStats() {
    try {
        const stats = await apiGet('/stats');
        document.getElementById('stat-total').textContent = stats.total_articles;
        document.getElementById('stat-completed').textContent = stats.completed;
        document.getElementById('stat-progress').textContent = stats.in_progress;
        document.getElementById('stat-failed').textContent = stats.failed;
        document.getElementById('stat-sentiment').textContent = stats.avg_sentiment.toFixed(2);
        document.getElementById('stat-cached').textContent = stats.cache?.cached_urls || 0;
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

async function loadArticles() {
    const search = document.getElementById('search-input').value.trim();
    const status = document.getElementById('status-filter').value;
    const container = document.getElementById('articles-list');

    let queryParams = '?limit=50';
    if (search) queryParams += `&search=${encodeURIComponent(search)}`;
    if (status) queryParams += `&status=${encodeURIComponent(status)}`;

    try {
        const articles = await apiGet(`/articles${queryParams}`);
        if (articles.length === 0) {
            container.innerHTML = '<p class="empty-state">No articles found.</p>';
            return;
        }

        container.innerHTML = articles.map(a => `
            <div class="article-row" data-id="${a.id}">
                <div class="article-info">
                    <div class="article-title">${escapeHtml(a.title || 'Untitled')}</div>
                    <div class="article-url">${escapeHtml(a.url)}</div>
                </div>
                <span class="article-domain">${escapeHtml(a.source_domain || '—')}</span>
                <span class="status-badge status-${a.status}">${a.status}</span>
            </div>
        `).join('');

        container.querySelectorAll('.article-row').forEach(row => {
            row.addEventListener('click', () => openArticleModal(row.dataset.id));
        });
    } catch (err) {
        container.innerHTML = `<p class="empty-state">Error loading articles: ${err.message}</p>`;
    }
}

async function loadInsights() {
    const container = document.getElementById('insights-list');

    try {
        const insights = await apiGet('/insights?limit=20');
        if (insights.length === 0) {
            container.innerHTML = '<p class="empty-state">No processed insights yet. Submit and process articles to see results.</p>';
            return;
        }

        container.innerHTML = insights.map(i => `
            <div class="insight-card">
                <div class="insight-header">
                    <div>
                        <div class="insight-title">${escapeHtml(i.title || 'Untitled')}</div>
                        <div class="article-url">${escapeHtml(i.source_domain || i.url)}</div>
                    </div>
                    <span class="insight-sentiment sentiment-${i.sentiment_label}">
                        ${i.sentiment_label} (${i.sentiment_score.toFixed(2)})
                    </span>
                </div>
                <p class="insight-summary">${escapeHtml(i.summary || 'No summary available.')}</p>
                <div class="insight-entities">
                    ${(i.entities?.people || []).map(p => `<span class="entity-tag person">${escapeHtml(p)}</span>`).join('')}
                    ${(i.entities?.companies || []).map(c => `<span class="entity-tag company">${escapeHtml(c)}</span>`).join('')}
                </div>
            </div>
        `).join('');
    } catch (err) {
        container.innerHTML = `<p class="empty-state">Error loading insights: ${err.message}</p>`;
    }
}

async function loadQueueStatus() {
    try {
        const status = await apiGet('/queue/status');
        document.getElementById('queue-active').textContent = status.total_active;
        document.getElementById('queue-reserved').textContent = status.total_reserved;
        document.getElementById('queue-workers').textContent = status.total_workers;

        const workersContainer = document.getElementById('workers-list');
        if (status.workers.length === 0) {
            workersContainer.innerHTML = '<p class="empty-state">No workers connected.</p>';
        } else {
            workersContainer.innerHTML = status.workers.map(w => `
                <div class="worker-card">
                    <div>
                        <span class="worker-status-dot"></span>
                        <span class="worker-name">${escapeHtml(w.name)}</span>
                    </div>
                    <div class="worker-meta">
                        <span>Active: ${w.active_tasks}</span>
                        <span>Reserved: ${w.reserved_tasks}</span>
                        <span>Pool: ${w.pool_size}</span>
                    </div>
                </div>
            `).join('');
        }

        const tasksData = await apiGet('/queue/tasks');
        const tasksContainer = document.getElementById('inflight-tasks');
        if (tasksData.length === 0) {
            tasksContainer.innerHTML = '<p class="empty-state">No tasks currently in flight.</p>';
        } else {
            tasksContainer.innerHTML = tasksData.map(t => `
                <div class="task-row">
                    <span class="task-url">${escapeHtml(t.url)}</span>
                    <span class="status-badge status-${t.status}">${t.status}</span>
                </div>
            `).join('');
        }
    } catch (err) {
        document.getElementById('workers-list').innerHTML =
            `<p class="empty-state">Error connecting to queue: ${err.message}</p>`;
    }
}

// --- Article Modal ---

async function openArticleModal(articleId) {
    const modal = document.getElementById('article-modal');
    const body = document.getElementById('modal-body');

    body.innerHTML = '<p class="empty-state">Loading...</p>';
    modal.classList.remove('hidden');

    try {
        const article = await apiGet(`/articles/${articleId}`);
        let html = `<h3>${escapeHtml(article.title || 'Untitled Article')}</h3>`;
        html += `
            <div class="detail-row"><span class="detail-label">URL</span><span class="detail-value">${escapeHtml(article.url)}</span></div>
            <div class="detail-row"><span class="detail-label">Domain</span><span class="detail-value">${escapeHtml(article.source_domain || '—')}</span></div>
            <div class="detail-row"><span class="detail-label">Status</span><span class="detail-value"><span class="status-badge status-${article.status}">${article.status}</span></span></div>
            <div class="detail-row"><span class="detail-label">Word Count</span><span class="detail-value">${article.word_count || '—'}</span></div>
            <div class="detail-row"><span class="detail-label">Scraped At</span><span class="detail-value">${article.scraped_at || '—'}</span></div>
            <div class="detail-row"><span class="detail-label">Created</span><span class="detail-value">${article.created_at}</span></div>
        `;

        if (article.error_message) {
            html += `<div class="detail-row"><span class="detail-label">Error</span><span class="detail-value" style="color:var(--error)">${escapeHtml(article.error_message)}</span></div>`;
        }

        if (article.metadata) {
            const m = article.metadata;
            html += `<div class="summary-block"><strong>Summary:</strong><br>${escapeHtml(m.summary || 'N/A')}</div>`;
            html += `<div class="detail-row"><span class="detail-label">Sentiment</span><span class="detail-value">${m.sentiment_score?.toFixed(2)} (${m.sentiment_label})</span></div>`;
            html += `<div class="detail-row"><span class="detail-label">Model</span><span class="detail-value">${escapeHtml(m.llm_model_used || '—')}</span></div>`;

            if (m.entities) {
                const people = m.entities.people || [];
                const companies = m.entities.companies || [];
                if (people.length || companies.length) {
                    html += '<div style="margin-top:16px"><strong style="font-size:0.85rem;color:var(--text-secondary)">Entities:</strong><div class="insight-entities" style="margin-top:8px">';
                    html += people.map(p => `<span class="entity-tag person">${escapeHtml(p)}</span>`).join('');
                    html += companies.map(c => `<span class="entity-tag company">${escapeHtml(c)}</span>`).join('');
                    html += '</div></div>';
                }
            }
        }

        body.innerHTML = html;
    } catch (err) {
        body.innerHTML = `<p class="empty-state">Error loading article: ${err.message}</p>`;
    }
}

document.querySelector('.modal-close').addEventListener('click', closeModal);
document.querySelector('.modal-backdrop').addEventListener('click', closeModal);

function closeModal() {
    document.getElementById('article-modal').classList.add('hidden');
}

// --- Utilities ---

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- Polling & Refresh ---

function refreshCurrentView() {
    switch (currentView) {
        case 'dashboard':
            loadStats();
            loadArticles();
            break;
        case 'insights':
            loadInsights();
            break;
        case 'queue':
            loadQueueStatus();
            break;
    }
}

function startPolling(interval = POLL_INTERVAL) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refreshCurrentView, interval);
}

// --- Live updates (SSE) ---

let statsDebounce = null;
function refreshSoon() {
    // Coalesce bursts of events into a single refresh.
    clearTimeout(statsDebounce);
    statsDebounce = setTimeout(() => {
        loadStats();
        if (currentView === 'insights') loadInsights();
        if (currentView === 'queue') loadQueueStatus();
    }, 300);
}

function applyStatusEvent(evt) {
    // Update the matching row in place if it's on screen; otherwise reload the list.
    const row = document.querySelector(`.article-row[data-id="${evt.article_id}"]`);
    if (row) {
        const badge = row.querySelector('.status-badge');
        if (badge) {
            badge.textContent = evt.status;
            badge.className = `status-badge status-${evt.status}`;
        }
        if (evt.title) {
            const titleEl = row.querySelector('.article-title');
            if (titleEl && titleEl.textContent === 'Untitled') {
                titleEl.textContent = evt.title;
            }
        }
    } else if (currentView === 'dashboard') {
        loadArticles();
    }
    refreshSoon();
}

function connectEvents() {
    eventSource = new EventSource(`${API_BASE}/events`);

    eventSource.onopen = () => {
        liveConnected = true;
        startPolling(POLL_INTERVAL_LIVE);
    };

    eventSource.onmessage = (e) => {
        let evt;
        try {
            evt = JSON.parse(e.data);
        } catch {
            return;
        }
        if (evt.type === 'status' && evt.article_id) {
            applyStatusEvent(evt);
        }
    };

    eventSource.onerror = () => {
        // Browser auto-reconnects EventSource; until it does, poll faster.
        if (liveConnected) {
            liveConnected = false;
            startPolling(POLL_INTERVAL);
        }
    };
}

// --- Init ---

refreshCurrentView();
startPolling();
connectEvents();

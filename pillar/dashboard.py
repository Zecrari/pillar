"""
Pillar Live Dashboard — /dashboard

A custom, fully self-contained HTML page (zero external CDN dependencies)
that gives real-time visibility into:

  * System health (engine, uptime, DB)
  * Per-route request / error / p99 metrics — auto-refreshes every 5 s
  * Background task queue (pending / running / failed / done)
  * Full route map with method + path + tags
"""
from __future__ import annotations

from typing import List

from .router import RouteEntry, WebSocketEntry


# ──────────────────────────────────────────────────────────────────────
# Queue status helper — reads directly from TaskStorage singleton
# ──────────────────────────────────────────────────────────────────────

def queue_status() -> dict:
    try:
        from .queue.storage import TaskStorage
        storage = TaskStorage.instance()
        return {
            "pending": storage.pending_count(),
            "failed":  storage.failed_count(),
            "done":    storage.done_count(),
            "driver":  storage.driver_name(),
        }
    except Exception:
        return {"pending": 0, "failed": 0, "done": 0, "driver": "unknown"}


# ──────────────────────────────────────────────────────────────────────
# HTML generator
# ──────────────────────────────────────────────────────────────────────

_METHOD_COLOR = {
    "GET":    "#22c55e",
    "POST":   "#6366f1",
    "PUT":    "#f59e0b",
    "PATCH":  "#0ea5e9",
    "DELETE": "#ef4444",
    "WS":     "#8b5cf6",
}

_CSS = """
:root {
  --bg:      #080b10;
  --surface: #0f1623;
  --card:    #141c2b;
  --border:  #1e2d42;
  --accent:  #6366f1;
  --accent2: #818cf8;
  --green:   #22c55e;
  --red:     #ef4444;
  --yellow:  #f59e0b;
  --text:    #e2e8f0;
  --muted:   #64748b;
  --font:    -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --mono:    'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text);
       min-height: 100vh; overflow-x: hidden; }
a { color: var(--accent2); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── NAV ── */
.topbar {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 32px;
  height: 56px;
  display: flex;
  align-items: center;
  gap: 16px;
  position: sticky;
  top: 0;
  z-index: 100;
}
.topbar .logo { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
.topbar .logo span { color: var(--accent); }
.topbar .app-name { color: var(--muted); font-size: 13px; border-left: 1px solid var(--border); padding-left: 16px; }
.topbar nav { margin-left: auto; display: flex; gap: 6px; }
.topbar nav a {
  padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 500;
  color: var(--muted); transition: all .15s;
}
.topbar nav a:hover { background: var(--card); color: var(--text); text-decoration: none; }
.topbar nav a.active { background: var(--accent); color: #fff; }
.live-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--green);
  box-shadow: 0 0 6px var(--green);
  animation: pulse 2s ease-in-out infinite;
  margin-left: auto;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }

/* ── LAYOUT ── */
main { max-width: 1280px; margin: 0 auto; padding: 32px; }

/* ── STAT CARDS ── */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}
.stat-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: var(--accent);
}
.stat-card.green::before { background: var(--green); }
.stat-card.red::before   { background: var(--red);   }
.stat-card.yellow::before{ background: var(--yellow); }
.stat-card .label {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .8px; color: var(--muted); margin-bottom: 10px;
}
.stat-card .value {
  font-size: 28px; font-weight: 700; color: var(--text); line-height: 1;
}
.stat-card .sub { font-size: 12px; color: var(--muted); margin-top: 6px; }

/* ── SECTION HEADERS ── */
.section-header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 16px; margin-top: 40px;
}
.section-header h2 {
  font-size: 14px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1px; color: var(--accent);
}
.section-header .count {
  background: var(--accent); color: #fff;
  padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 700;
}
.divider { flex: 1; height: 1px; background: var(--border); }

/* ── TABLES ── */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 12px;
  color: var(--muted); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .6px;
  border-bottom: 2px solid var(--border);
}
tbody td { padding: 11px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
tbody tr { transition: background .1s; }
tbody tr:hover td { background: rgba(99,102,241,.05); }
code { font-family: var(--mono); background: var(--border); padding: 2px 6px;
       border-radius: 4px; font-size: 12px; }

/* ── METHOD BADGE ── */
.badge {
  display: inline-block; padding: 3px 8px; border-radius: 5px;
  font-size: 10px; font-weight: 800; letter-spacing: .5px;
  color: #fff; font-family: var(--mono);
}

/* ── MINI BAR ── */
.bar-wrap { width: 100%; background: var(--border); border-radius: 3px; height: 5px; margin-top: 4px; }
.bar { height: 5px; border-radius: 3px; background: var(--accent); transition: width .4s; }
.bar.err { background: var(--red); }

/* ── QUEUE CARDS ── */
.queue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.q-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; text-align: center;
}
.q-card .q-num { font-size: 32px; font-weight: 800; }
.q-card .q-label { font-size: 11px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .6px; }
.q-card.pending .q-num { color: var(--yellow); }
.q-card.failed  .q-num { color: var(--red);    }
.q-card.done    .q-num { color: var(--green);  }

/* ── REFRESH INDICATOR ── */
.refresh-info { font-size: 12px; color: var(--muted); margin-left: auto; }
#last-refresh { color: var(--accent2); }

/* ── FOOTER ── */
footer {
  text-align: center; padding: 40px 32px 32px;
  color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); margin-top: 60px;
}
"""

_JS = """
const fmtNum  = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
const fmtMs   = ms => ms < 1 ? '<1ms' : ms < 1000 ? ms.toFixed(0)+'ms' : (ms/1000).toFixed(2)+'s';
const fmtUp   = s  => {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = Math.floor(s%60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${ss}s` : `${ss}s`;
};

async function refreshMetrics() {
  try {
    const res  = await fetch('/metrics');
    const data = await res.json();

    document.getElementById('stat-uptime').textContent  = fmtUp(data.uptime_seconds);
    document.getElementById('stat-reqs').textContent    = fmtNum(data.total_requests);
    document.getElementById('stat-errors').textContent  = fmtNum(data.total_errors);

    const routes = data.routes || {};
    const keys   = Object.keys(routes);
    const maxReq = Math.max(1, ...keys.map(k => routes[k].requests));

    const tbody = document.getElementById('metrics-tbody');
    if (!tbody) return;

    keys.forEach(key => {
      const r   = routes[key];
      const row = document.getElementById('mr-' + CSS.escape(key));
      if (!row) return;

      row.querySelector('.mr-req').textContent  = fmtNum(r.requests);
      row.querySelector('.mr-err').textContent  = fmtNum(r.errors);
      row.querySelector('.mr-avg').textContent  = fmtMs(r.avg_ms);
      row.querySelector('.mr-p99').textContent  = fmtMs(r.p99_ms);

      const pct = Math.round((r.requests / maxReq) * 100);
      row.querySelector('.bar').style.width = pct + '%';
    });

    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch(e) {
    console.warn('metrics refresh failed', e);
  }
}

async function refreshQueue() {
  try {
    const res  = await fetch('/queue/status');
    if (!res.ok) return;
    const data = await res.json();
    const p = document.getElementById('q-pending');
    const f = document.getElementById('q-failed');
    const d = document.getElementById('q-done');
    if (p) p.textContent = data.pending ?? '—';
    if (f) f.textContent = data.failed  ?? '—';
    if (d) d.textContent = data.done    ?? '—';
  } catch(e) {}
}

refreshMetrics();
refreshQueue();
setInterval(() => { refreshMetrics(); refreshQueue(); }, 5000);
"""


def dashboard_html(
    title: str,
    version: str,
    routes: List[RouteEntry],
    ws_routes: List[WebSocketEntry],
    engine: str,
    docs_url: str,
    redoc_url: str,
    openapi_url: str,
    guide_url: str,
) -> str:

    # ── Route rows (static HTML, metrics updated by JS) ──────────────
    user_rows = ""
    for r in routes:
        color = _METHOD_COLOR.get(r.method, "#94a3b8")
        tags  = ", ".join(r.tags) if r.tags else ""
        key   = f"{r.method} {r.full_path}"
        user_rows += f"""
<tr id="mr-{key}">
  <td><span class="badge" style="background:{color}">{r.method}</span></td>
  <td><code>{r.full_path}</code></td>
  <td style="color:var(--muted);font-size:12px">{tags}</td>
  <td class="mr-req" style="text-align:right">—</td>
  <td class="mr-err" style="text-align:right">—</td>
  <td class="mr-avg" style="text-align:right">—</td>
  <td class="mr-p99" style="text-align:right">—</td>
  <td style="min-width:80px">
    <div class="bar-wrap"><div class="bar" style="width:0%"></div></div>
  </td>
</tr>"""

    for ws in (ws_routes or []):
        color = _METHOD_COLOR["WS"]
        tags  = ", ".join(ws.tags) if ws.tags else ""
        user_rows += f"""
<tr>
  <td><span class="badge" style="background:{color}">WS</span></td>
  <td><code>{ws.full_path}</code></td>
  <td style="color:var(--muted);font-size:12px">{tags}</td>
  <td colspan="4" style="color:var(--muted);font-size:12px">WebSocket — no HTTP metrics</td>
  <td></td>
</tr>"""

    # ── Route map (static) ────────────────────────────────────────────
    all_routes = list(routes) + [
        type("_R", (), {"method": "GET",  "full_path": "/health",    "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": "/ready",     "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": docs_url,     "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": redoc_url,    "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": openapi_url,  "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": guide_url,    "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": "/metrics",   "tags": []})(),
        type("_R", (), {"method": "GET",  "full_path": "/dashboard", "tags": []})(),
    ]

    route_map_rows = ""
    for r in all_routes:
        color = _METHOD_COLOR.get(r.method, "#94a3b8")
        tags  = ", ".join(r.tags) if r.tags else ""
        route_map_rows += (
            f"<tr>"
            f"<td><span class='badge' style='background:{color}'>{r.method}</span></td>"
            f"<td><code>{r.full_path}</code></td>"
            f"<td style='color:var(--muted);font-size:12px'>{tags}</td>"
            f"</tr>"
        )

    engine_ok  = "rust" in engine.lower()
    engine_col = "var(--green)" if engine_ok else "var(--yellow)"
    engine_lbl = engine if engine_ok else "Python fallback"

    n_routes = len(routes) + len(ws_routes or [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title} — Pillar Dashboard</title>
  <style>{_CSS}</style>
</head>
<body>

<div class="topbar">
  <div class="logo">PILLAR<span>&#9632;</span></div>
  <span class="app-name">{title} &nbsp;v{version}</span>
  <nav>
    <a href="/dashboard" class="active">Dashboard</a>
    <a href="{docs_url}">API Docs</a>
    <a href="{guide_url}">Guide</a>
    <a href="/metrics">Raw Metrics</a>
  </nav>
  <div class="live-dot" title="Auto-refreshing every 5 s"></div>
</div>

<main>

  <!-- ── Stat cards ── -->
  <div class="stat-grid">
    <div class="stat-card green">
      <div class="label">Uptime</div>
      <div class="value" id="stat-uptime">…</div>
      <div class="sub">since last restart</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Requests</div>
      <div class="value" id="stat-reqs">…</div>
      <div class="sub">all routes</div>
    </div>
    <div class="stat-card red">
      <div class="label">Errors (4xx/5xx)</div>
      <div class="value" id="stat-errors">…</div>
      <div class="sub">since restart</div>
    </div>
    <div class="stat-card yellow">
      <div class="label">Routes</div>
      <div class="value">{n_routes}</div>
      <div class="sub">user-defined</div>
    </div>
    <div class="stat-card">
      <div class="label">Engine</div>
      <div class="value" style="font-size:16px;color:{engine_col}">{engine_lbl}</div>
      <div class="sub">request router</div>
    </div>
  </div>

  <!-- ── Queue ── -->
  <div class="section-header">
    <h2>Background Queue</h2>
    <div class="divider"></div>
  </div>
  <div class="queue-grid">
    <div class="q-card pending">
      <div class="q-num" id="q-pending">…</div>
      <div class="q-label">Pending</div>
    </div>
    <div class="q-card failed">
      <div class="q-num" id="q-failed">…</div>
      <div class="q-label">Failed</div>
    </div>
    <div class="q-card done">
      <div class="q-num" id="q-done">…</div>
      <div class="q-label">Done</div>
    </div>
  </div>

  <!-- ── Live metrics ── -->
  <div class="section-header">
    <h2>Live Route Metrics</h2>
    <span class="count">{len(routes)}</span>
    <div class="divider"></div>
    <span class="refresh-info">refreshes every 5 s &nbsp;|&nbsp; last: <span id="last-refresh">—</span></span>
  </div>
  <table>
    <thead>
      <tr>
        <th style="width:72px">Method</th>
        <th style="width:260px">Path</th>
        <th>Tags</th>
        <th style="text-align:right;width:72px">Reqs</th>
        <th style="text-align:right;width:60px">Errs</th>
        <th style="text-align:right;width:72px">Avg</th>
        <th style="text-align:right;width:72px">P99</th>
        <th style="width:88px"></th>
      </tr>
    </thead>
    <tbody id="metrics-tbody">
{user_rows}
    </tbody>
  </table>

  <!-- ── Route map ── -->
  <div class="section-header">
    <h2>Route Map</h2>
    <span class="count">{len(all_routes)}</span>
    <div class="divider"></div>
  </div>
  <table>
    <thead>
      <tr>
        <th style="width:72px">Method</th>
        <th style="width:260px">Path</th>
        <th>Tags</th>
      </tr>
    </thead>
    <tbody>{route_map_rows}</tbody>
  </table>

</main>

<footer>
  PILLAR Framework &mdash; Built with Rust + Python &mdash;
  <a href="{docs_url}">Docs</a> &middot; <a href="{guide_url}">Guide</a>
</footer>

<script>{_JS}</script>
</body>
</html>"""

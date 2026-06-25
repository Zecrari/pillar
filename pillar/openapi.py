"""
OpenAPI 3.1 schema generation + Swagger UI / ReDoc / Guide HTML.

Everything here is pure-Python and has no extra dependencies beyond
what Pydantic already provides.
"""
from __future__ import annotations

import inspect
import re
from typing import Any, Dict, List, Optional, Type, get_type_hints


# ──────────────────────────────────────────────────────────────────────
# Type helpers
# ──────────────────────────────────────────────────────────────────────

def _is_pydantic(cls: Any) -> bool:
    try:
        from pydantic import BaseModel
        return isinstance(cls, type) and issubclass(cls, BaseModel)
    except ImportError:
        return False


def _openapi_type(annotation: Any) -> dict:
    """Convert a Python type annotation to an OpenAPI schema fragment."""
    import typing

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is bytes:
        return {"type": "string", "format": "binary"}

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ()) or ()

    if origin is list:
        item = _openapi_type(args[0]) if args else {}
        return {"type": "array", "items": item}

    if origin is dict:
        return {"type": "object"}

    # Optional[X] / Union[X, None]
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            inner = _openapi_type(non_none[0])
            return {**inner, "nullable": True}

    return {}


# ──────────────────────────────────────────────────────────────────────
# OpenAPI 3.1 spec builder
# ──────────────────────────────────────────────────────────────────────

def build_openapi(
    title: str,
    version: str,
    description: str,
    routes: list,
    ws_routes: list = None,
    servers: list = None,
) -> dict:
    """Return a fully-formed OpenAPI 3.1.0 spec as a Python dict."""

    components: Dict[str, dict] = {}

    def _ref(model_cls: Type) -> str:
        name = model_cls.__name__
        if name not in components:
            schema = model_cls.model_json_schema()
            # Hoist nested $defs to components
            for k, v in schema.pop("$defs", {}).items():
                components.setdefault(k, v)
            components[name] = schema
        return f"#/components/schemas/{name}"

    paths: Dict[str, dict] = {}

    # HTTP routes
    for route in (routes or []):
        if not getattr(route, "include_in_schema", True):
            continue

        full_path = route.full_path
        method = route.method.lower()
        handler = route.handler
        path_param_names = set(re.findall(r"\{(\w+)\}", full_path))

        try:
            hints = get_type_hints(handler)
        except Exception:
            hints = {}
        sig = inspect.signature(handler)

        parameters: List[dict] = []
        request_body: Optional[dict] = None

        for pname, param in sig.parameters.items():
            ann = hints.get(pname)
            if ann is None:
                continue

            # Skip Request, WebSocket, DI services
            try:
                from starlette.requests import Request
                from starlette.websockets import WebSocket
                if ann in (Request, WebSocket):
                    continue
            except ImportError:
                pass

            if pname in path_param_names:
                parameters.append({
                    "name": pname,
                    "in": "path",
                    "required": True,
                    "schema": _openapi_type(ann),
                })
            elif _is_pydantic(ann):
                request_body = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": _ref(ann)}
                        }
                    },
                }
            elif isinstance(ann, type) and ann not in (str, int, float, bool, bytes):
                # DI service — not a query param
                pass
            else:
                default = param.default
                schema_fragment = _openapi_type(ann)
                if default is not inspect.Parameter.empty:
                    schema_fragment["default"] = default
                parameters.append({
                    "name": pname,
                    "in": "query",
                    "required": default is inspect.Parameter.empty,
                    "schema": schema_fragment,
                })

        # Response schema
        response_model = getattr(route, "response_model", None)
        if response_model and _is_pydantic(response_model):
            ok_content = {"content": {"application/json": {"schema": {"$ref": _ref(response_model)}}}}
        else:
            ok_content = {"content": {"application/json": {"schema": {"type": "object"}}}}

        status_code = str(getattr(route, "status_code", 200))

        summary = (
            getattr(route, "summary", None)
            or handler.__name__.replace("_", " ").title()
        )
        description_text = (
            getattr(route, "description", None)
            or inspect.getdoc(handler)
            or ""
        )

        operation: dict = {
            "summary": summary,
            "operationId": f"{method}_{handler.__name__}",
            "tags": getattr(route, "tags", []) or ["default"],
            "parameters": parameters,
            "responses": {
                status_code: {"description": "Successful response", **ok_content},
                "422": {
                    "description": "Validation Error",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
                        }
                    },
                },
                "500": {"description": "Internal Server Error"},
            },
        }
        if description_text:
            operation["description"] = description_text
        if getattr(route, "deprecated", False):
            operation["deprecated"] = True
        if request_body:
            operation["requestBody"] = request_body

        paths.setdefault(full_path, {})[method] = operation

    # WebSocket routes (documented as GET with upgrade note)
    for ws in (ws_routes or []):
        operation = {
            "summary": ws.summary or f"WebSocket: {ws.full_path}",
            "tags": ws.tags or ["websocket"],
            "description": "WebSocket endpoint — connect with `ws://` protocol.",
            "responses": {"101": {"description": "Switching Protocols"}},
        }
        paths.setdefault(ws.full_path, {})["get"] = operation

    # Standard error schemas
    components.setdefault("HTTPValidationError", {
        "type": "object",
        "properties": {
            "detail": {"type": "string"},
            "errors": {"type": "array", "items": {"type": "object"}},
        },
    })

    spec: dict = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": description,
        },
        "paths": paths,
        "components": {"schemas": components},
    }
    if servers:
        spec["servers"] = servers
    return spec


# ──────────────────────────────────────────────────────────────────────
# Pillar native API explorer (replaces Swagger UI — zero CDN)
# ──────────────────────────────────────────────────────────────────────

_DOCS_CSS = """
:root{--bg:#080b10;--surface:#0f1623;--card:#141c2b;--border:#1e2d42;
      --accent:#6366f1;--text:#e2e8f0;--muted:#64748b;
      --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--blue:#0ea5e9;--purple:#8b5cf6;
      --font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      --mono:'JetBrains Mono','Fira Code','Consolas',monospace;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:var(--font);background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden;}
#sidebar{width:280px;background:var(--surface);border-right:1px solid var(--border);
         display:flex;flex-direction:column;flex-shrink:0;overflow:hidden;}
#sidebar-header{padding:20px 16px 12px;border-bottom:1px solid var(--border);}
#sidebar-header .logo{font-size:17px;font-weight:800;letter-spacing:-.5px;}
#sidebar-header .logo span{color:var(--accent);}
#sidebar-header .app-name{font-size:12px;color:var(--muted);margin-top:2px;}
#search{width:100%;background:var(--card);border:1px solid var(--border);border-radius:6px;
        color:var(--text);font-size:13px;padding:8px 12px;margin:12px 0 0;outline:none;}
#search:focus{border-color:var(--accent);}
#route-list{flex:1;overflow-y:auto;padding:8px 0;}
.tag-group{margin-bottom:4px;}
.tag-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
           color:var(--muted);padding:8px 16px 4px;display:block;}
.route-item{display:flex;align-items:center;gap:8px;padding:7px 16px;cursor:pointer;
            border-left:2px solid transparent;transition:all .1s;}
.route-item:hover{background:var(--card);border-left-color:var(--border);}
.route-item.active{background:rgba(99,102,241,.12);border-left-color:var(--accent);}
.route-item .method{font-family:var(--mono);font-size:10px;font-weight:800;
                    padding:2px 6px;border-radius:4px;color:#fff;min-width:52px;text-align:center;}
.route-item .path{font-family:var(--mono);font-size:12px;color:var(--text);
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
#main{flex:1;overflow-y:auto;background:var(--bg);}
#detail{max-width:860px;margin:0 auto;padding:40px 40px 80px;}
#detail h1{font-size:22px;font-weight:700;margin-bottom:6px;}
#detail .endpoint-url{font-family:var(--mono);font-size:14px;background:var(--card);
                       border:1px solid var(--border);border-radius:8px;
                       padding:14px 20px;display:flex;align-items:center;gap:12px;margin:20px 0;}
#detail .section{margin-top:28px;}
#detail .section-title{font-size:11px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.8px;color:var(--muted);margin-bottom:12px;}
table.params{width:100%;border-collapse:collapse;font-size:13px;}
table.params th{text-align:left;padding:8px 12px;font-size:11px;font-weight:600;
                text-transform:uppercase;letter-spacing:.5px;color:var(--muted);
                border-bottom:2px solid var(--border);}
table.params td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top;}
table.params td code{font-family:var(--mono);background:var(--border);
                      padding:2px 6px;border-radius:3px;font-size:12px;}
.badge{display:inline-block;padding:2px 8px;border-radius:99px;
       font-size:10px;font-weight:700;color:#fff;font-family:var(--mono);}
.badge.required{background:var(--accent);}
.badge.optional{background:var(--muted);}
/* Try it */
#try-panel{background:var(--card);border:1px solid var(--border);border-radius:12px;
           margin-top:28px;overflow:hidden;}
#try-header{padding:14px 20px;border-bottom:1px solid var(--border);
            display:flex;align-items:center;gap:12px;}
#try-header h3{font-size:13px;font-weight:700;flex:1;}
#btn-try{background:var(--accent);color:#fff;border:none;border-radius:6px;
         padding:7px 18px;font-size:13px;font-weight:600;cursor:pointer;}
#btn-try:hover{background:#4f52d8;}
#try-body{padding:20px;}
.field-group{margin-bottom:16px;}
.field-group label{font-size:11px;font-weight:600;text-transform:uppercase;
                   letter-spacing:.5px;color:var(--muted);display:block;margin-bottom:6px;}
.field-group input,.field-group textarea{width:100%;background:var(--surface);
  border:1px solid var(--border);border-radius:6px;color:var(--text);
  font-size:13px;padding:8px 12px;font-family:var(--mono);outline:none;}
.field-group input:focus,.field-group textarea:focus{border-color:var(--accent);}
.field-group textarea{min-height:100px;resize:vertical;}
#response-block{margin-top:20px;display:none;}
#response-block .res-status{font-size:13px;font-weight:700;margin-bottom:8px;}
#response-block pre{background:var(--surface);border:1px solid var(--border);
                    border-radius:8px;padding:16px;overflow-x:auto;
                    font-family:var(--mono);font-size:12px;line-height:1.6;max-height:300px;}
#placeholder{display:flex;align-items:center;justify-content:center;
             height:100%;color:var(--muted);font-size:14px;flex-direction:column;gap:12px;}
#placeholder .logo-big{font-size:48px;font-weight:900;letter-spacing:-2px;opacity:.2;}
"""

_DOCS_JS = r"""
const COLORS={GET:'#22c55e',POST:'#6366f1',PUT:'#f59e0b',PATCH:'#0ea5e9',DELETE:'#ef4444',WS:'#8b5cf6'};
let spec=null;
let current=null;

async function init(){
  const r=await fetch(OPENAPI_URL);
  spec=await r.json();
  buildSidebar();
}

function buildSidebar(){
  const list=document.getElementById('route-list');
  list.innerHTML='';
  const grouped={};
  const paths=spec.paths||{};
  Object.keys(paths).forEach(p=>{
    Object.keys(paths[p]).forEach(m=>{
      const op=paths[p][m];
      const tag=(op.tags&&op.tags[0])||'default';
      if(!grouped[tag])grouped[tag]=[];
      grouped[tag].push({path:p,method:m.toUpperCase(),op});
    });
  });
  Object.keys(grouped).sort().forEach(tag=>{
    const div=document.createElement('div');
    div.className='tag-group';
    div.innerHTML=`<span class="tag-label">${tag}</span>`;
    grouped[tag].forEach(({path,method,op})=>{
      const item=document.createElement('div');
      item.className='route-item';
      item.innerHTML=`<span class="method" style="background:${COLORS[method]||'#64748b'}">${method}</span>
                      <span class="path">${path}</span>`;
      item.onclick=()=>showRoute(path,method,op,item);
      div.appendChild(item);
    });
    list.appendChild(div);
  });
}

function showRoute(path,method,op,el){
  document.querySelectorAll('.route-item').forEach(i=>i.classList.remove('active'));
  el.classList.add('active');
  current={path,method,op};

  const color=COLORS[method]||'#64748b';
  const params=op.parameters||[];
  const hasBody=!!op.requestBody;

  let paramRows='';
  params.forEach(p=>{
    const req=p.required?'<span class="badge required">required</span>':'<span class="badge optional">optional</span>';
    const type=p.schema?p.schema.type||'any':'any';
    paramRows+=`<tr><td><code>${p.name}</code></td><td>${p.in}</td><td><code>${type}</code></td><td>${req}</td></tr>`;
  });

  let tryFields='';
  params.forEach(p=>{
    tryFields+=`<div class="field-group">
      <label>${p.name} <em style="color:var(--muted);font-weight:400">(${p.in})</em></label>
      <input type="text" id="param-${p.name}" placeholder="${p.schema&&p.schema.example||''}">
    </div>`;
  });
  if(hasBody){
    tryFields+=`<div class="field-group"><label>Request Body (JSON)</label>
      <textarea id="body-input">{}</textarea></div>`;
  }

  document.getElementById('detail').innerHTML=`
    <h1>${op.summary||path}</h1>
    ${op.description?`<p style="color:var(--muted);margin-top:8px;font-size:14px">${op.description}</p>`:''}
    <div class="endpoint-url">
      <span class="method" style="background:${color};padding:4px 12px;border-radius:6px">${method}</span>
      <span style="font-size:14px">${path}</span>
    </div>
    ${params.length?`<div class="section">
      <div class="section-title">Parameters</div>
      <table class="params"><thead><tr><th>Name</th><th>In</th><th>Type</th><th></th></tr></thead>
      <tbody>${paramRows}</tbody></table></div>`:''}
    <div id="try-panel">
      <div id="try-header">
        <h3>Try it out</h3>
        <button id="btn-try" onclick="sendRequest()">Send Request</button>
      </div>
      <div id="try-body">
        ${tryFields||'<p style="color:var(--muted);font-size:13px">No parameters</p>'}
        <div id="response-block">
          <div class="res-status" id="res-status"></div>
          <pre id="res-body"></pre>
        </div>
      </div>
    </div>`;
}

async function sendRequest(){
  if(!current)return;
  let url=current.path;
  const params=current.op.parameters||[];
  const qs=[];
  params.forEach(p=>{
    const el=document.getElementById('param-'+p.name);
    const val=el?el.value.trim():'';
    if(!val)return;
    if(p.in==='path') url=url.replace('{'+p.name+'}',encodeURIComponent(val));
    else if(p.in==='query') qs.push(encodeURIComponent(p.name)+'='+encodeURIComponent(val));
  });
  if(qs.length)url+='?'+qs.join('&');

  const opts={method:current.method,headers:{'Content-Type':'application/json'}};
  const bodyEl=document.getElementById('body-input');
  if(bodyEl&&bodyEl.value.trim()&&bodyEl.value.trim()!=='{}'){
    try{opts.body=bodyEl.value;}catch(e){}
  }

  const rb=document.getElementById('response-block');
  const rs=document.getElementById('res-status');
  const rbody=document.getElementById('res-body');
  rb.style.display='block';
  rs.textContent='Sending…';
  rbody.textContent='';

  try{
    const res=await fetch(url,opts);
    const text=await res.text();
    let pretty=text;
    try{pretty=JSON.stringify(JSON.parse(text),null,2);}catch(e){}
    rs.textContent=`${res.status} ${res.statusText}`;
    rs.style.color=res.ok?'var(--green)':'var(--red)';
    rbody.textContent=pretty;
  }catch(e){
    rs.textContent='Network Error';
    rs.style.color='var(--red)';
    rbody.textContent=String(e);
  }
}

document.getElementById('search').addEventListener('input',e=>{
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('.route-item').forEach(item=>{
    const txt=item.textContent.toLowerCase();
    item.style.display=txt.includes(q)?'':'none';
  });
});

init();
"""


def swagger_ui_html(title: str, openapi_url: str) -> str:
    """Custom Pillar API explorer — zero CDN, fully self-contained."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title} — API Explorer</title>
  <style>{_DOCS_CSS}</style>
</head>
<body>
<div id="sidebar">
  <div id="sidebar-header">
    <div class="logo">PILLAR<span style="color:var(--accent)">&#9632;</span> <span style="font-weight:400;font-size:13px">API</span></div>
    <div class="app-name">{title}</div>
    <input id="search" type="text" placeholder="Search routes…" autocomplete="off"/>
  </div>
  <div id="route-list"></div>
</div>
<div id="main">
  <div id="detail">
    <div id="placeholder">
      <div class="logo-big">PILLAR</div>
      <span>Select a route to explore</span>
    </div>
  </div>
</div>
<script>const OPENAPI_URL="{openapi_url}";\n{_DOCS_JS}</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────
# ReDoc HTML
# ──────────────────────────────────────────────────────────────────────

def redoc_html(title: str, openapi_url: str) -> str:
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{title} — ReDoc</title>"
        '<meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">'
        "<style>body{margin:0;padding:0;background:#0f1117}</style>"
        "</head><body>"
        f'<redoc spec-url="{openapi_url}" hide-download-button></redoc>'
        '<script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>'
        "</body></html>"
    )


# ──────────────────────────────────────────────────────────────────────
# /guide — built-in interactive guide
# ──────────────────────────────────────────────────────────────────────

_METHOD_COLORS = {
    "GET": "#22c55e", "POST": "#6366f1", "PUT": "#f59e0b",
    "PATCH": "#0ea5e9", "DELETE": "#ef4444", "WS": "#8b5cf6",
}

def guide_html(title: str, version: str, routes: list,
               ws_routes: list, engine: str, docs_url: str,
               redoc_url: str, openapi_url: str) -> str:

    # Build route rows
    rows = ""
    for r in routes:
        method = r.method
        color = _METHOD_COLORS.get(method, "#94a3b8")
        summary = (getattr(r, "summary", None) or
                   r.handler.__name__.replace("_", " ").title())
        tags = ", ".join(getattr(r, "tags", []) or [])
        deprecated = " <span style='color:#ef4444;font-size:10px'>DEPRECATED</span>" \
                     if getattr(r, "deprecated", False) else ""
        rows += (
            f"<tr>"
            f"<td><span class='badge' style='background:{color}'>{method}</span></td>"
            f"<td><code>{r.full_path}</code></td>"
            f"<td>{summary}{deprecated}</td>"
            f"<td style='color:#94a3b8;font-size:12px'>{tags}</td>"
            f"</tr>"
        )
    for ws in (ws_routes or []):
        color = _METHOD_COLORS["WS"]
        rows += (
            f"<tr>"
            f"<td><span class='badge' style='background:{color}'>WS</span></td>"
            f"<td><code>{ws.full_path}</code></td>"
            f"<td>{ws.summary or 'WebSocket'}</td>"
            f"<td style='color:#94a3b8;font-size:12px'>{', '.join(ws.tags or [])}</td>"
            f"</tr>"
        )

    # Builtin routes
    builtins = [
        ("GET", "/health", "Health check — liveness probe"),
        ("GET", "/ready",  "Readiness probe"),
        ("GET", docs_url,  "Swagger UI — interactive API explorer"),
        ("GET", redoc_url, "ReDoc — alternative API docs"),
        ("GET", openapi_url, "OpenAPI 3.1 JSON schema"),
        ("GET", "/metrics", "Framework metrics (JSON)"),
        ("GET", "/guide",   "This guide"),
    ]
    for method, path, desc in builtins:
        color = _METHOD_COLORS.get(method, "#94a3b8")
        rows += (
            f"<tr style='opacity:.6'>"
            f"<td><span class='badge' style='background:{color}'>{method}</span></td>"
            f"<td><code>{path}</code></td>"
            f"<td><em>{desc}</em></td>"
            f"<td></td>"
            f"</tr>"
        )

    engine_badge = (
        "<span style='color:#22c55e'>&#10003; Rust engine active</span>"
        if "rust" in engine
        else "<span style='color:#f59e0b'>&#9888; Python fallback router</span>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title} — Pillar Guide</title>
  <style>
    :root {{
      --bg:#0f1117; --surface:#1a1d27; --border:#2d3048;
      --accent:#6366f1; --text:#e2e8f0; --muted:#94a3b8;
    }}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:var(--bg);color:var(--text);line-height:1.6}}
    a{{color:var(--accent);text-decoration:none}}
    a:hover{{text-decoration:underline}}
    header{{background:var(--surface);border-bottom:1px solid var(--border);
            padding:24px 40px;display:flex;align-items:center;gap:16px}}
    .logo{{font-size:32px}}
    .title-block h1{{font-size:22px;font-weight:700}}
    .title-block p{{color:var(--muted);font-size:13px;margin-top:2px}}
    .badge{{display:inline-block;padding:2px 8px;border-radius:4px;
            font-size:11px;font-weight:700;color:#fff;letter-spacing:.5px}}
    nav{{background:var(--surface);padding:12px 40px;border-bottom:1px solid var(--border);
         display:flex;gap:24px;font-size:13px}}
    main{{max-width:1100px;margin:0 auto;padding:32px 40px}}
    section{{margin-bottom:40px}}
    h2{{font-size:16px;font-weight:700;color:var(--accent);
        text-transform:uppercase;letter-spacing:1px;margin-bottom:16px;
        border-bottom:1px solid var(--border);padding-bottom:8px}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
    .card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;
           padding:16px;font-size:13px}}
    .card strong{{display:block;margin-bottom:4px;font-size:12px;
                  color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
    table{{width:100%;border-collapse:collapse;font-size:13px}}
    th{{text-align:left;padding:8px 12px;color:var(--muted);
        font-size:11px;text-transform:uppercase;letter-spacing:.5px;
        border-bottom:2px solid var(--border)}}
    td{{padding:10px 12px;border-bottom:1px solid var(--border)}}
    tr:hover td{{background:rgba(99,102,241,.06)}}
    code{{font-family:'JetBrains Mono','Fira Code',monospace;
          background:#1e2130;padding:2px 6px;border-radius:3px;font-size:12px}}
    pre{{background:#1a1d27;border:1px solid var(--border);border-radius:8px;
         padding:20px;overflow-x:auto;font-family:'JetBrains Mono',monospace;
         font-size:13px;line-height:1.7}}
    .kw{{color:#c792ea}} .fn{{color:#82aaff}} .st{{color:#c3e88d}}
    .cm{{color:#546e7a}} .cl{{color:#f78c6c}}
    .engine-badge{{font-size:13px;margin-left:auto}}
    footer{{text-align:center;padding:32px;color:var(--muted);font-size:12px;
            border-top:1px solid var(--border);margin-top:40px}}
  </style>
</head>
<body>
<header>
  <span class="logo">&#127963;</span>
  <div class="title-block">
    <h1>{title}</h1>
    <p>v{version} &nbsp;&middot;&nbsp; Powered by <strong>Pillar</strong>
       &nbsp;&middot;&nbsp; {engine_badge}</p>
  </div>
</header>
<nav>
  <a href="{docs_url}">&#128196; Swagger UI</a>
  <a href="{redoc_url}">&#128218; ReDoc</a>
  <a href="{openapi_url}">&#129693; OpenAPI JSON</a>
  <a href="/metrics">&#128200; Metrics</a>
  <a href="/health">&#10084; Health</a>
</nav>
<main>
  <section>
    <h2>Overview</h2>
    <div class="cards">
      <div class="card"><strong>Framework</strong>Pillar 0.1.0</div>
      <div class="card"><strong>Rust Engine</strong>{engine}</div>
      <div class="card"><strong>Total Routes</strong>{len(routes) + len(ws_routes or [])}</div>
      <div class="card"><strong>Built-in Routes</strong>{len(builtins)}</div>
    </div>
  </section>

  <section>
    <h2>API Routes</h2>
    <table>
      <thead>
        <tr>
          <th style="width:70px">Method</th>
          <th style="width:260px">Path</th>
          <th>Summary</th>
          <th style="width:160px">Tags</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Quick Start</h2>
    <pre><span class="cm"># Step 1 — Repository (database layer)</span>
<span class="kw">from</span> pillar.db <span class="kw">import</span> Database

<span class="kw">class</span> <span class="cl">UserRepository</span>:
    <span class="kw">def</span> <span class="fn">__init__</span>(<span class="st">self</span>, db: <span class="cl">Database</span>):
        <span class="st">self</span>.db = db

    <span class="kw">def</span> <span class="fn">get_by_id</span>(<span class="st">self</span>, user_id: <span class="cl">int</span>) -&gt; <span class="cl">dict</span> | <span class="cl">None</span>:
        <span class="kw">return</span> <span class="st">self</span>.db.query(<span class="st">"SELECT * FROM users WHERE id = ?"</span>, (user_id,))

<span class="cm"># Step 2 — Service (business logic)</span>
<span class="kw">from</span> pillar <span class="kw">import</span> background_task
<span class="kw">from</span> pillar.exceptions <span class="kw">import</span> NotFoundError

<span class="kw">class</span> <span class="cl">UserService</span>:
    <span class="kw">def</span> <span class="fn">__init__</span>(<span class="st">self</span>, repo: <span class="cl">UserRepository</span>):
        <span class="st">self</span>.repo = repo  <span class="cm"># auto-injected!</span>

    <span class="kw">def</span> <span class="fn">get_user</span>(<span class="st">self</span>, user_id: <span class="cl">int</span>):
        user = <span class="st">self</span>.repo.get_by_id(user_id)
        <span class="kw">if not</span> user: <span class="kw">raise</span> <span class="cl">NotFoundError</span>(<span class="st">"User not found"</span>)
        <span class="kw">return</span> user

    @<span class="fn">background_task</span>(retries=<span class="cl">3</span>)
    <span class="kw">def</span> <span class="fn">send_welcome_email</span>(<span class="st">self</span>, email: <span class="cl">str</span>): ...

<span class="cm"># Step 3 — Router (HTTP layer)</span>
<span class="kw">from</span> pillar <span class="kw">import</span> Router

router = <span class="fn">Router</span>(prefix=<span class="st">"/users"</span>, tags=[<span class="st">"Users"</span>])

@router.<span class="fn">get</span>(<span class="st">"/{{user_id}}"</span>, response_model=<span class="cl">UserResponse</span>)
<span class="kw">async def</span> <span class="fn">get_user</span>(user_id: <span class="cl">int</span>, service: <span class="cl">UserService</span>):
    <span class="kw">return</span> service.get_user(user_id)

<span class="cm"># Step 4 — App (3 lines)</span>
<span class="kw">from</span> pillar <span class="kw">import</span> Pillar
app = <span class="fn">Pillar</span>(title=<span class="st">"{title}"</span>)
app.<span class="fn">include_router</span>(router)</pre>
  </section>

  <section>
    <h2>Pillar vs FastAPI vs Django</h2>
    <table>
      <thead>
        <tr><th>Feature</th><th>Pillar</th><th>FastAPI</th><th>Django REST</th></tr>
      </thead>
      <tbody>
        <tr><td>Architecture enforcement</td><td>&#10003; Built-in</td><td>&#10007;</td><td>&#10007;</td></tr>
        <tr><td>Background tasks (no Celery)</td><td>&#10003; Rust SQLite WAL</td><td>&#8776; Basic</td><td>&#10007;</td></tr>
        <tr><td>Auto DI (no Depends)</td><td>&#10003; Type-hint-based</td><td>&#8776; Depends()</td><td>&#10007;</td></tr>
        <tr><td>Rust router</td><td>&#10003; matchit radix-tree</td><td>&#10007;</td><td>&#10007;</td></tr>
        <tr><td>Smart Bridge (sync/async)</td><td>&#10003; Automatic</td><td>&#8773; Manual</td><td>&#10007;</td></tr>
        <tr><td>OpenAPI docs</td><td>&#10003;</td><td>&#10003;</td><td>&#8776; Plugin</td></tr>
        <tr><td>Guided project structure</td><td>&#10003; Enforced DDD</td><td>&#10007;</td><td>&#8776; Convention</td></tr>
      </tbody>
    </table>
  </section>
</main>
<footer>&#127963; Pillar Framework &mdash; Built with Rust + Python &mdash; <a href="{docs_url}">API Docs</a></footer>
</body>
</html>"""

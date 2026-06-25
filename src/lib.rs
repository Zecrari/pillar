/// Pillar Rust Engine
///
/// Exposes two classes and one function to Python:
///
///   `PillarRouter`  — radix-tree HTTP router backed by `matchit`
///   `PillarQueue`   — persistent task queue backed by SQLite in WAL mode
///   `engine_version()` — build metadata
///
/// Build with:  `maturin develop`   (dev)
///              `maturin build --release`  (wheel)

use chrono::Utc;
use matchit::Router as MatchitRouter;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyDictMethods, PyList, PyListMethods};
use rusqlite::{params, Connection};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use uuid::Uuid;

// ════════════════════════════════════════════════════════════════════════
// PillarRouter — radix-tree HTTP router
// ════════════════════════════════════════════════════════════════════════

/// One matchit::Router per HTTP method, all behind a single Mutex so the
/// whole object is safe to share across Python threads.
struct RouterInner {
    routes: HashMap<String, MatchitRouter<String>>,
    count: usize,
}

impl RouterInner {
    fn new() -> Self {
        Self {
            routes: HashMap::new(),
            count: 0,
        }
    }
}

#[pyclass]
pub struct PillarRouter {
    inner: Arc<Mutex<RouterInner>>,
}

#[pymethods]
impl PillarRouter {
    #[new]
    fn new() -> Self {
        PillarRouter {
            inner: Arc::new(Mutex::new(RouterInner::new())),
        }
    }

    /// Register a route.
    ///
    /// `method`     — HTTP verb, e.g. "GET"
    /// `path`       — route pattern, e.g. "/users/{user_id}"
    /// `handler_id` — opaque string key used to look up the Python handler
    fn add_route(&self, method: &str, path: &str, handler_id: &str) -> PyResult<()> {
        let mut inner = self.inner.lock().unwrap();
        let router = inner
            .routes
            .entry(method.to_uppercase())
            .or_insert_with(MatchitRouter::new);

        router
            .insert(path.to_string(), handler_id.to_string())
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Failed to register route {method} {path}: {e}"
                ))
            })?;

        inner.count += 1;
        Ok(())
    }

    /// Match an incoming request.
    ///
    /// Returns `{"handler_id": str, "params": dict}` or `None` if no match.
    fn match_route<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        path: &str,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        let inner = self.inner.lock().unwrap();

        let router = match inner.routes.get(&method.to_uppercase()) {
            Some(r) => r,
            None => return Ok(None),
        };

        let matched = match router.at(path) {
            Ok(m) => m,
            Err(_) => return Ok(None),
        };

        let result = PyDict::new_bound(py);
        result.set_item("handler_id", matched.value.as_str())?;

        let params_dict = PyDict::new_bound(py);
        for (key, value) in matched.params.iter() {
            params_dict.set_item(key, value)?;
        }
        result.set_item("params", params_dict)?;

        Ok(Some(result))
    }

    /// Total number of registered routes.
    fn route_count(&self) -> usize {
        self.inner.lock().unwrap().count
    }
}

// ════════════════════════════════════════════════════════════════════════
// PillarQueue — persistent SQLite WAL task queue
// ════════════════════════════════════════════════════════════════════════

const INIT_SQL: &str = "
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pillar_tasks (
    id            TEXT PRIMARY KEY,
    func_path     TEXT NOT NULL,
    args_json     TEXT NOT NULL DEFAULT '[]',
    kwargs_json   TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    retries_left  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    scheduled_at  TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_pillar_tasks_status
    ON pillar_tasks(status, scheduled_at);
";

fn now_iso() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()
}

#[pyclass]
pub struct PillarQueue {
    conn: Arc<Mutex<Connection>>,
}

#[pymethods]
impl PillarQueue {
    #[new]
    fn new(db_path: &str) -> PyResult<Self> {
        let conn = Connection::open(db_path).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Cannot open queue database at '{db_path}': {e}"
            ))
        })?;

        conn.execute_batch(INIT_SQL).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Queue init failed: {e}"))
        })?;

        Ok(PillarQueue {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Add a task to the queue.
    ///
    /// Returns the task UUID string.
    #[pyo3(signature = (func_path, args_json, kwargs_json, retries=0, scheduled_at=None))]
    fn enqueue(
        &self,
        func_path: &str,
        args_json: &str,
        kwargs_json: &str,
        retries: i64,
        scheduled_at: Option<&str>,
    ) -> PyResult<String> {
        let task_id = Uuid::new_v4().to_string();
        let conn = self.conn.lock().unwrap();

        conn.execute(
            "INSERT INTO pillar_tasks
                (id, func_path, args_json, kwargs_json, retries_left, created_at, scheduled_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                task_id,
                func_path,
                args_json,
                kwargs_json,
                retries,
                now_iso(),
                scheduled_at
            ],
        )
        .map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Enqueue failed: {e}"))
        })?;

        Ok(task_id)
    }

    /// Claim up to `limit` pending tasks, mark them as running, and return them.
    ///
    /// Each item is a dict with keys: id, func_path, args_json, kwargs_json, retries_left.
    #[pyo3(signature = (limit=10))]
    fn dequeue<'py>(&self, py: Python<'py>, limit: i64) -> PyResult<Bound<'py, PyList>> {
        let conn = self.conn.lock().unwrap();
        let now = now_iso();

        let mut stmt = conn
            .prepare(
                "SELECT id, func_path, args_json, kwargs_json, retries_left
                 FROM pillar_tasks
                 WHERE status = 'pending'
                   AND (scheduled_at IS NULL OR scheduled_at <= ?1)
                 ORDER BY created_at ASC
                 LIMIT ?2",
            )
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Dequeue prepare: {e}"))
            })?;

        let rows: Vec<(String, String, String, String, i64)> = stmt
            .query_map(params![now, limit], |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                ))
            })
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Dequeue query: {e}"))
            })?
            .filter_map(|r| r.ok())
            .collect();

        // Atomically mark claimed tasks as running
        for (id, _, _, _, _) in &rows {
            conn.execute(
                "UPDATE pillar_tasks SET status = 'running', started_at = ?1 WHERE id = ?2",
                params![now, id],
            )
            .ok();
        }

        let list = PyList::empty_bound(py);
        for (id, func_path, args_json, kwargs_json, retries_left) in rows {
            let d = PyDict::new_bound(py);
            d.set_item("id", &id)?;
            d.set_item("func_path", &func_path)?;
            d.set_item("args_json", &args_json)?;
            d.set_item("kwargs_json", &kwargs_json)?;
            d.set_item("retries_left", retries_left)?;
            list.append(d)?;
        }

        Ok(list)
    }

    /// Mark a task as successfully completed.
    fn mark_complete(&self, task_id: &str) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE pillar_tasks SET status = 'completed', completed_at = ?1 WHERE id = ?2",
            params![now_iso(), task_id],
        )
        .map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("mark_complete: {e}"))
        })?;
        Ok(())
    }

    /// Mark a task as failed.
    ///
    /// If `retry` is true and retries_left > 0, the status is reset to 'pending'
    /// with retries_left decremented so the worker will pick it up again.
    fn mark_failed(&self, task_id: &str, error: &str, retry: bool) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        if retry {
            conn.execute(
                "UPDATE pillar_tasks
                 SET status = 'pending', retries_left = retries_left - 1, error = ?1
                 WHERE id = ?2",
                params![error, task_id],
            )
        } else {
            conn.execute(
                "UPDATE pillar_tasks
                 SET status = 'failed', error = ?1, completed_at = ?2
                 WHERE id = ?3",
                params![error, now_iso(), task_id],
            )
        }
        .map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("mark_failed: {e}"))
        })?;
        Ok(())
    }

    /// Number of tasks currently waiting to be processed.
    fn pending_count(&self) -> PyResult<i64> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM pillar_tasks WHERE status = 'pending'",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);
        Ok(count)
    }

    /// Number of tasks that have permanently failed (no retries left).
    fn failed_count(&self) -> PyResult<i64> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM pillar_tasks WHERE status = 'failed'",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);
        Ok(count)
    }
}

// ════════════════════════════════════════════════════════════════════════
// Module registration
// ════════════════════════════════════════════════════════════════════════

#[pyfunction]
fn engine_version() -> String {
    format!(
        "pillar-engine/{} (rust {})",
        env!("CARGO_PKG_VERSION"),
        "1.96"
    )
}

#[pymodule]
fn _pillar_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_class::<PillarRouter>()?;
    m.add_class::<PillarQueue>()?;
    Ok(())
}

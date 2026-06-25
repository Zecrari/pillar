from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class AppConfig:
    title: str = "Pillar App"
    version: str = "0.1.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    description: str = "Powered by Pillar — Production-Grade Python Backend Framework"


@dataclass
class DatabaseConfig:
    url: str = "sqlite:///./app.db"
    pool_size: int = 10
    echo: bool = False


@dataclass
class QueueConfig:
    driver: str = "sqlite"       # "sqlite" | "redis" | "postgres"
    db_path: str = "pillar_queue.db"
    workers: int = 4
    poll_interval: float = 0.5
    redis_url: str = ""
    postgres_url: str = ""


@dataclass
class DocsConfig:
    enabled: bool = True
    swagger_url: str = "/docs"
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"
    guide_url: str = "/guide"
    title: str = ""           # defaults to app.title if empty
    description: str = ""     # defaults to app.description if empty


@dataclass
class CorsConfig:
    enabled: bool = True
    allow_origins: List[str] = field(default_factory=lambda: ["*"])
    allow_methods: List[str] = field(default_factory=lambda: ["*"])
    allow_headers: List[str] = field(default_factory=lambda: ["*"])
    allow_credentials: bool = False
    expose_headers: List[str] = field(
        default_factory=lambda: ["X-Request-ID", "X-Response-Time"]
    )


@dataclass
class SecurityConfig:
    add_request_id: bool = True
    add_timing: bool = True
    add_security_headers: bool = True
    # Future: rate limiting
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 100   # per window
    rate_limit_window: int = 60      # seconds


@dataclass
class TelemetryConfig:
    enabled: bool = False
    exporter: str = "jaeger"     # "jaeger" | "datadog" | "grafana"
    endpoint: str = ""
    service_name: str = "pillar-app"


@dataclass
class PillarConfig:
    app: AppConfig = field(default_factory=AppConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    @classmethod
    def load(cls, config_path: str = "pillar.toml") -> "PillarConfig":
        cfg = cls()
        path = Path(config_path)
        if path.exists():
            cfg._load_toml(path)
        cfg._apply_env_overrides()
        return cfg

    # ------------------------------------------------------------------

    def _load_toml(self, path: Path) -> None:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib          # type: ignore[no-redef]
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    return

        with open(path, "rb") as f:
            data = tomllib.load(f)

        if "app" in data:
            a = data["app"]
            self.app.title       = a.get("title",       self.app.title)
            self.app.version     = a.get("version",     self.app.version)
            self.app.debug       = a.get("debug",       self.app.debug)
            self.app.host        = a.get("host",        self.app.host)
            self.app.port        = a.get("port",        self.app.port)
            self.app.description = a.get("description", self.app.description)

        if "database" in data:
            d = data["database"]
            self.database.url       = d.get("url",       self.database.url)
            self.database.pool_size = d.get("pool_size", self.database.pool_size)
            self.database.echo      = d.get("echo",      self.database.echo)

        if "queue" in data:
            q = data["queue"]
            self.queue.driver        = q.get("driver",        self.queue.driver)
            self.queue.db_path       = q.get("db_path",       self.queue.db_path)
            self.queue.workers       = q.get("workers",       self.queue.workers)
            self.queue.poll_interval = q.get("poll_interval", self.queue.poll_interval)
            self.queue.redis_url     = q.get("redis_url",     self.queue.redis_url)

        if "docs" in data:
            d = data["docs"]
            self.docs.enabled      = d.get("enabled",      self.docs.enabled)
            self.docs.swagger_url  = d.get("swagger_url",  self.docs.swagger_url)
            self.docs.redoc_url    = d.get("redoc_url",    self.docs.redoc_url)
            self.docs.openapi_url  = d.get("openapi_url",  self.docs.openapi_url)
            self.docs.guide_url    = d.get("guide_url",    self.docs.guide_url)
            self.docs.title        = d.get("title",        self.docs.title)
            self.docs.description  = d.get("description",  self.docs.description)

        if "cors" in data:
            c = data["cors"]
            self.cors.enabled           = c.get("enabled",           self.cors.enabled)
            self.cors.allow_origins     = c.get("allow_origins",     self.cors.allow_origins)
            self.cors.allow_methods     = c.get("allow_methods",     self.cors.allow_methods)
            self.cors.allow_headers     = c.get("allow_headers",     self.cors.allow_headers)
            self.cors.allow_credentials = c.get("allow_credentials", self.cors.allow_credentials)

        if "security" in data:
            s = data["security"]
            self.security.add_request_id      = s.get("add_request_id",      self.security.add_request_id)
            self.security.add_timing          = s.get("add_timing",          self.security.add_timing)
            self.security.add_security_headers= s.get("add_security_headers",self.security.add_security_headers)

        if "telemetry" in data:
            t = data["telemetry"]
            self.telemetry.enabled      = t.get("enabled",      self.telemetry.enabled)
            self.telemetry.exporter     = t.get("exporter",     self.telemetry.exporter)
            self.telemetry.endpoint     = t.get("endpoint",     self.telemetry.endpoint)
            self.telemetry.service_name = t.get("service_name", self.telemetry.service_name)

    def _apply_env_overrides(self) -> None:
        if v := os.environ.get("DATABASE_URL"):
            self.database.url = v
        if v := os.environ.get("DEBUG"):
            self.app.debug = v.lower() in ("1", "true", "yes")
        if v := os.environ.get("HOST"):
            self.app.host = v
        if v := os.environ.get("PORT"):
            self.app.port = int(v)
        if v := os.environ.get("QUEUE_DRIVER"):
            self.queue.driver = v
        if v := os.environ.get("REDIS_URL"):
            self.queue.redis_url = v
        if v := os.environ.get("PILLAR_DOCS_ENABLED"):
            self.docs.enabled = v.lower() not in ("0", "false", "no")

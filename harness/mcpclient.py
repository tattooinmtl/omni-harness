"""MCP bridge for the Omni Agent.

Connects the Omni agent to Model Context Protocol servers (stdio and
streamable-http) and exposes their tools as OpenAI-style function schemas
that a small local model can call.

The MCP SDK is async, the agent is sync: a dedicated background thread owns
an asyncio event loop and every MCP context manager (transport + session) is
entered and exited inside that loop, in the same task.

Usage:
    bridge = McpBridge()
    bridge.connect_all()
    tools = bridge.list_tools()
    text = bridge.call_tool("mcp_global_brain_brain_status", {})
    bridge.close()
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

# --- optional SDK import -----------------------------------------------------
# The module must import cleanly even when the MCP SDK is missing, so the
# agent can still start and report "mcp SDK not available" instead of dying.
MCP_IMPORT_ERROR = ""
try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception as exc:  # pragma: no cover - depends on the install
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    MCP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

HTTP_IMPORT_ERROR = ""
_http_client: Any = None
for _http_name in ("streamable_http_client", "streamablehttp_client"):
    try:
        from mcp.client.streamable_http import streamable_http_client as _http_client  # type: ignore[no-redef]
        break
    except Exception as exc:  # pragma: no cover - name varies by SDK version
        HTTP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

# --- configuration -----------------------------------------------------------
CONFIG_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".minimax" / "mcp.json",
    Path.home() / ".mavis" / "mcp.json",
    Path.home() / ".omni-harness" / "mcp.json",
)

DEFAULT_CONNECT_TIMEOUT = 20.0
MAX_CONNECT_TIMEOUT = 60.0
DEFAULT_CALL_TIMEOUT = 30.0
MAX_CALL_TIMEOUT = 120.0
MAX_DESCRIPTION = 600
MAX_ERROR_CHARS = 300

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|token|secret|password|passwd|authorization|bearer)"
    r"\b\s*[=:]\s*\"?[^\s,;\"']+"
)


def _describe_exception(exc: BaseException) -> str:
    """Flatten an ExceptionGroup (anyio wraps transport errors in one)."""
    nested = getattr(exc, "exceptions", None)
    if nested:
        parts: list[str] = []
        for sub in nested:
            text = _describe_exception(sub)
            if text and text not in parts:
                parts.append(text)
        if parts:
            return "; ".join(parts)
    return str(exc).strip() or type(exc).__name__


def _sanitize_text(text: str, spec: dict | None = None) -> str:
    """Strip secrets and clip an error message so it is safe to show the model."""
    out = str(text)
    if isinstance(spec, dict):
        env = spec.get("env")
        if isinstance(env, dict):
            for value in env.values():
                if isinstance(value, str) and len(value) >= 4:
                    out = out.replace(value, "***")
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", out)
    return out[:MAX_ERROR_CHARS]


def _server_prefix(server_name: str) -> str:
    """`global-brain` -> `mcp_global_brain_`."""
    return "mcp_" + re.sub(r"[^A-Za-z0-9_]", "_", server_name) + "_"


def _extract_text(result: Any) -> str:
    """Join the text blocks of an MCP tool result, falling back to str(result)."""
    blocks = getattr(result, "content", None)
    parts: list[str] = []
    if isinstance(blocks, list):
        for block in blocks:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
    if parts:
        return "\n".join(parts)
    return str(result)


def _result_is_error(result: Any) -> bool:
    for attr in ("is_error", "isError"):
        value = getattr(result, attr, None)
        if value is not None:
            return bool(value)
    return False


def _tool_input_schema(tool: Any) -> dict:
    """Read the input schema, tolerating both snake_case and camelCase SDKs."""
    for attr in ("inputSchema", "input_schema"):
        schema = getattr(tool, attr, None)
        if isinstance(schema, dict):
            return schema
    return {}


class _ServerState:
    """Per-server bookkeeping shared between the loop thread and the caller."""

    __slots__ = ("name", "status", "session", "task", "timed_out", "call_timeout", "spec")

    def __init__(self, name: str) -> None:
        self.name = name
        self.status = "connecting"
        self.session: Any = None
        self.task: asyncio.Task | None = None
        self.timed_out = False
        self.call_timeout = DEFAULT_CALL_TIMEOUT
        self.spec: dict = {}


class McpBridge:
    """Sync facade over the async MCP SDK."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path is not None else None
        self._lock = threading.RLock()
        self._states: dict[str, _ServerState] = {}
        self._index: dict[str, tuple[str, str]] = {}
        self._schemas: list[dict] = []
        self._config_error = ""
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._wakeup: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._errlog: Any = None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def connect_all(self) -> None:
        """Connect to every enabled server in the config. Never raises."""
        try:
            if not self._ensure_loop():
                return
            servers = self._load_servers()
            if servers is None:
                with self._lock:
                    self._config_error = "no MCP config found"
                return
            with self._lock:
                self._config_error = ""
            for name, spec in servers.items():
                try:
                    self._connect_one(name, spec)
                except Exception as exc:
                    state = self._state(name)
                    state.status = f"failed: {_sanitize_text(_describe_exception(exc), spec)}"
        except Exception as exc:  # pragma: no cover - defensive
            with self._lock:
                self._config_error = f"failed: {_sanitize_text(_describe_exception(exc))}"

    def list_tools(self) -> list[dict]:
        """OpenAI-style function schemas for every MCP tool that connected."""
        with self._lock:
            out: list[dict] = []
            for schema in self._schemas:
                function = dict(schema.get("function") or {})
                out.append({"type": schema.get("type", "function"), "function": function})
            return out

    def call_tool(self, qualified_name: str, arguments: dict) -> str:
        """Call one MCP tool. Returns plain text; on failure "error: ...". Never raises."""
        try:
            name = str(qualified_name or "")
            with self._lock:
                entry = self._index.get(name)
                state = self._states.get(entry[0]) if entry else None
            if entry is None:
                return f"error: {self._unknown_tool_reason(name)}"
            server, tool = entry
            if state is None or state.session is None:
                return f"error: server '{server}' is not connected"
            args = arguments if isinstance(arguments, dict) else {}
            timeout = state.call_timeout if state is not None else DEFAULT_CALL_TIMEOUT
            loop = self._loop
            if loop is None or self._closed:
                return "error: MCP bridge is closed"
            future = asyncio.run_coroutine_threadsafe(self._call_tool(server, tool, args), loop)
            return str(future.result(timeout=timeout))
        except TimeoutError:
            return "error: tool call timed out"
        except Exception as exc:
            return f"error: {_sanitize_text(_describe_exception(exc))}"

    def server_status(self) -> list[tuple[str, str]]:
        """[(server_name, status_text)] - "connected (12 tools)", "disabled", ..."""
        with self._lock:
            if self._config_error:
                return [("mcp-config", self._config_error)]
            return [(name, state.status) for name, state in self._states.items()]

    def close(self) -> None:
        """Close every session and stop the background loop. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread
        if loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                future.result(timeout=8.0)
            except Exception:
                pass
            try:
                if self._stop is not None:
                    loop.call_soon_threadsafe(self._stop.set)
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=5.0)
        self._close_errlog()

    # -- context manager convenience -----------------------------------
    def __enter__(self) -> "McpBridge":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def _config_file(self) -> Path | None:
        if self._config_path is not None:
            return self._config_path
        for candidate in CONFIG_CANDIDATES:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _load_servers(self) -> dict[str, dict] | None:
        """Return {server_name: spec}; None when no config file exists at all."""
        path = self._config_file()
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise FileNotFoundError(f"config not found: {path}") from None
        except Exception as exc:
            raise ValueError(f"invalid config json: {type(exc).__name__}") from None
        if not isinstance(raw, dict):
            raise ValueError("invalid config: expected a JSON object")
        servers = raw.get("mcpServers")
        if not isinstance(servers, dict):
            servers = raw
        out: dict[str, dict] = {}
        for key, value in servers.items():
            if isinstance(value, dict):
                out[str(key)] = value
        return out

    def _timeout_from_spec(self, spec: dict) -> float:
        raw = spec.get("timeout")
        if raw is None:
            return DEFAULT_CONNECT_TIMEOUT
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_CONNECT_TIMEOUT
        if value <= 0:
            return DEFAULT_CONNECT_TIMEOUT
        # This machine's configs use milliseconds (60000); small numbers are
        # read as seconds.
        seconds = value if value < 1000 else value / 1000.0
        return max(1.0, min(seconds, MAX_CONNECT_TIMEOUT))

    def _call_timeout_from_spec(self, spec: dict) -> float:
        connect = self._timeout_from_spec(spec)
        return max(5.0, min(connect, MAX_CALL_TIMEOUT))

    # ------------------------------------------------------------------
    # event loop plumbing
    # ------------------------------------------------------------------
    def _ensure_loop(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return True
            self._loop_ready.clear()
            thread = threading.Thread(target=self._thread_main, name="mcp-bridge", daemon=True)
            self._thread = thread
        self._open_errlog()
        thread.start()
        return self._loop_ready.wait(timeout=10.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._wakeup = asyncio.Event()
            self._stop = asyncio.Event()
            self._loop_ready.set()
            loop.run_until_complete(self._idle_loop())
        except Exception:
            pass
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=2.0))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            with self._lock:
                self._loop = None

    async def _idle_loop(self) -> None:
        stop = self._stop
        if stop is not None:
            await stop.wait()

    async def _shutdown(self) -> None:
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup.set()
        with self._lock:
            tasks = [
                state.task
                for state in self._states.values()
                if state.task is not None and not state.task.done()
            ]
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=3.0)
            except Exception:
                pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            stragglers = [task for task in tasks if not task.done()]
            if stragglers:
                try:
                    await asyncio.wait(stragglers, timeout=2.0)
                except Exception:
                    pass
        with self._lock:
            for state in self._states.values():
                state.session = None
                if state.status.startswith("connected"):
                    state.status = "closed"

    # ------------------------------------------------------------------
    # connecting
    # ------------------------------------------------------------------
    def _state(self, name: str) -> _ServerState:
        with self._lock:
            state = self._states.get(name)
            if state is None:
                state = _ServerState(name)
                self._states[name] = state
            return state

    def _connect_one(self, name: str, spec: dict) -> None:
        state = self._state(name)
        with self._lock:
            if state.status.startswith("connected"):
                return
            state.spec = spec
            state.timed_out = False
            state.call_timeout = self._call_timeout_from_spec(spec)
        if not spec.get("enabled", True):
            state.status = "disabled"
            return
        if MCP_IMPORT_ERROR:
            state.status = f"failed: mcp SDK unavailable ({MCP_IMPORT_ERROR})"
            return
        kind = str(spec.get("type") or "").strip().lower()
        if kind in ("http", "streamable-http", "streamable_http"):
            kind = "streamable-http"
        elif kind != "stdio":
            kind = "stdio" if spec.get("command") else "streamable-http"
        timeout = self._timeout_from_spec(spec)
        ready = threading.Event()
        loop = self._loop
        if loop is None:
            state.status = "failed: background loop is not running"
            return
        try:
            task = asyncio.run_coroutine_threadsafe(
                self._spawn_worker(state, spec, kind, ready), loop
            ).result(timeout=5.0)
        except Exception as exc:
            state.status = f"failed: {_sanitize_text(_describe_exception(exc), spec)}"
            return
        with self._lock:
            state.task = task
        if not ready.wait(timeout):
            state.timed_out = True
            state.status = f"failed: connect timeout after {timeout:g}s"
            task.cancel()

    async def _spawn_worker(
        self, state: _ServerState, spec: dict, kind: str, ready: threading.Event
    ) -> asyncio.Task:
        return asyncio.create_task(self._server_worker(state, spec, kind, ready))

    async def _server_worker(
        self, state: _ServerState, spec: dict, kind: str, ready: threading.Event
    ) -> None:
        try:
            if kind == "stdio":
                params = self._stdio_params(spec)
                async with stdio_client(params, errlog=self._errlog) as streams:
                    await self._run_session(state, streams, ready)
            else:
                url = str(spec.get("url") or "").strip()
                if not url:
                    raise ValueError("streamable-http server is missing 'url'")
                if _http_client is None:
                    raise ImportError(f"streamable http client unavailable ({HTTP_IMPORT_ERROR})")
                async with _http_client(url) as streams:
                    await self._run_session(state, streams, ready)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with self._lock:
                if not state.timed_out:
                    state.status = f"failed: {_sanitize_text(_describe_exception(exc), spec)}"
        finally:
            ready.set()

    def _stdio_params(self, spec: dict) -> Any:
        command = str(spec.get("command") or "").strip()
        if not command:
            raise ValueError("stdio server is missing 'command'")
        raw_args = spec.get("args")
        args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        raw_env = spec.get("env")
        env: dict[str, str] | None = None
        if isinstance(raw_env, dict) and raw_env:
            env = {
                str(key): str(value)
                for key, value in raw_env.items()
                if value is not None
            }
        return StdioServerParameters(command=command, args=args, env=env)

    async def _run_session(self, state: _ServerState, streams: Any, ready: threading.Event) -> None:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = list(getattr(result, "tools", None) or [])
            with self._lock:
                state.session = session
                state.status = f"connected ({len(tools)} tools)"
            self._register_tools(state.name, tools)
            ready.set()
            wakeup = self._wakeup
            if wakeup is not None:
                await wakeup.wait()

    def _register_tools(self, server: str, tools: list) -> None:
        prefix = _server_prefix(server)
        with self._lock:
            for tool in tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                name = str(name)
                description = getattr(tool, "description", None)
                if not isinstance(description, str) or not description.strip():
                    description = name
                description = description.strip()[:MAX_DESCRIPTION]
                parameters = dict(_tool_input_schema(tool))
                parameters.setdefault("type", "object")
                properties = parameters.get("properties")
                if not isinstance(properties, dict):
                    parameters["properties"] = {}
                required = parameters.get("required")
                if required is not None and not isinstance(required, list):
                    parameters.pop("required", None)
                qualified = prefix + name
                if qualified in self._index:
                    continue
                self._index[qualified] = (server, name)
                self._schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": qualified,
                            "description": description,
                            "parameters": parameters,
                        },
                    }
                )

    # ------------------------------------------------------------------
    # calling
    # ------------------------------------------------------------------
    def _unknown_tool_reason(self, name: str) -> str:
        """Say *which* server is unreachable when the qualified name looks known."""
        with self._lock:
            candidates = list(self._states.items())
        best: tuple[str, str] | None = None
        for server, state in candidates:
            prefix = _server_prefix(server)
            if name.startswith(prefix) and (best is None or len(prefix) > len(_server_prefix(best[0]))):
                best = (server, state.status)
        if best is not None:
            server, status = best
            if status.startswith("connected"):
                # the server is fine, the tool is simply not exposed
                return f"unknown tool '{name}'"
            if status == "disabled":
                return f"server '{server}' is disabled"
            return f"server '{server}' is not connected ({status})"
        return f"unknown tool '{name}'"

    async def _call_tool(self, server: str, tool: str, args: dict) -> str:
        with self._lock:
            state = self._states.get(server)
            session = state.session if state is not None else None
            timeout = state.call_timeout if state is not None else DEFAULT_CALL_TIMEOUT
        if session is None:
            raise RuntimeError(f"server '{server}' is not connected")
        result = await asyncio.wait_for(session.call_tool(tool, args), timeout=timeout)
        text = _extract_text(result)
        if _result_is_error(result):
            return "error: " + text
        return text

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------
    def _open_errlog(self) -> None:
        if self._errlog is not None:
            return
        try:
            self._errlog = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            self._errlog = None

    def _close_errlog(self) -> None:
        handle = self._errlog
        self._errlog = None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            pass

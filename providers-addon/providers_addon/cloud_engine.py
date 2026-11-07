"""
CloudEngine — an omni-harness engine backed by an OpenAI-compatible cloud API.

Plugs in wherever the local ServerEngine/CliEngine would sit.  The agent
calls generate_messages(messages, tools, ...) instead of generate(prompt, ...)
when this engine is active, so the cloud model receives properly-structured
chat-completions messages rather than a raw Qwen/Arete template string.

Tool calls: the cloud model returns them in OpenAI JSON format; this engine
converts them to Hermes-style JSON inside <tool_call> blocks, which is the
second format that omni-harness's parse_tool_calls() already understands.

Streaming: on_text is called token-by-token when the API sends SSE chunks.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent


class CloudEngine:
    """OpenAI-compatible cloud provider engine."""

    name = "cloud"
    streams = True  # live token streaming

    def __init__(self, provider_name: str, base_url: str, api_key: str,
                 model: str, cfg=None):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.cfg = cfg
        self.stats: dict = {}
        self.running = True  # for ensure_engine compatibility

    # ------------------------------------------------------------------
    # Compatibility stubs the agent/commands expect on any engine
    # ------------------------------------------------------------------
    def start(self):
        pass  # no local process to launch

    def stop(self) -> str:
        return ""

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def invalidate_engine(self) -> str:
        return ""

    # ------------------------------------------------------------------
    # Core: chat completions
    # ------------------------------------------------------------------
    def generate_messages(
        self,
        messages: list[dict],
        tools: list[dict],
        n_predict: int | None = None,
        budget: int | None = None,
        enable_thinking: bool = False,
        on_text: Callable | None = None,
        should_cancel: Callable | None = None,
    ) -> tuple[str, bool]:
        """
        Call the cloud /v1/chat/completions endpoint.
        Returns (raw_text_in_arete_format, was_shown_live).
        """
        cfg = self.cfg
        temperature = float(getattr(cfg, "temp", 0.6)) if cfg else 0.6
        max_tokens = int(n_predict or getattr(cfg, "max_tokens", 4096) or 4096)

        body: dict = {
            "model": self.model,
            "messages": _sanitize_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": on_text is not None,
        }
        if tools:
            body["tools"] = _tools_to_openai(tools)
            body["tool_choice"] = "auto"

        url_path = "/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        started = time.time()

        if body["stream"]:
            raw, shown = self._stream(url_path, headers, body, on_text, should_cancel)
        else:
            raw, shown = self._complete(url_path, headers, body)

        elapsed = time.time() - started
        self.stats = {
            "output_tokens": max(1, len(raw) // 4),
            "prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages) // 4,
            "tps": float(len(raw) // 4) / max(elapsed, 0.001),
        }
        if not shown and on_text is None:
            print(f"    [{self.provider_name} {elapsed:.1f}s]", flush=True)
        return raw, shown

    # backwards-compat stub — if the agent somehow calls generate(prompt),
    # send the whole prompt as a single user message
    def generate(self, prompt: str, n_predict: int | None = None,
                 on_text=None, should_cancel=None, temperature=None) -> str:
        messages = [{"role": "user", "content": prompt}]
        raw, _ = self.generate_messages(messages, [], n_predict=n_predict,
                                        on_text=on_text, should_cancel=should_cancel)
        return raw

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _host_port(self) -> tuple[str, int, bool]:
        url = self.base_url
        https = url.startswith("https://")
        rest = url[len("https://"):] if https else url[len("http://"):]
        if "/" in rest:
            rest = rest.split("/", 1)[0]
        if ":" in rest:
            host, port_s = rest.rsplit(":", 1)
            return host, int(port_s), https
        return rest, 443 if https else 80, https

    def _connection(self):
        host, port, https = self._host_port()
        timeout = int(getattr(self.cfg, "timeout", 600)) if self.cfg else 600
        if https:
            import ssl  # noqa: PLC0415
            ctx = ssl.create_default_context()
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def _complete(self, path: str, headers: dict, body: dict) -> tuple[str, bool]:
        payload = json.dumps(body).encode("utf-8")
        conn = self._connection()
        try:
            conn.request("POST", path, payload, headers)
            resp = conn.getresponse()
            raw_bytes = resp.read()
        finally:
            conn.close()
        if resp.status != 200:
            snippet = raw_bytes[:300].decode("utf-8", errors="replace")
            raise RuntimeError(f"[{self.provider_name}] HTTP {resp.status}: {snippet}")
        data = json.loads(raw_bytes)
        return _extract_response(data), False

    def _stream(self, path: str, headers: dict, body: dict,
                on_text: Callable | None, should_cancel: Callable | None) -> tuple[str, bool]:
        payload = json.dumps(body).encode("utf-8")
        conn = self._connection()
        parts: list[str] = []
        shown = False
        # accumulate tool call deltas by index
        tool_deltas: dict[int, dict] = {}
        try:
            conn.request("POST", path, payload, headers)
            resp = conn.getresponse()
            if resp.status != 200:
                raw = resp.read(300).decode("utf-8", errors="replace")
                conn.close()
                raise RuntimeError(f"[{self.provider_name}] HTTP {resp.status}: {raw}")
            buf = b""
            while True:
                if should_cancel is not None and should_cancel():
                    break
                chunk = resp.read(1)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line or line == b"data: [DONE]":
                        continue
                    if line.startswith(b"data: "):
                        data_str = line[6:].decode("utf-8", errors="replace")
                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        delta = (evt.get("choices") or [{}])[0].get("delta") or {}
                        # text content
                        piece = delta.get("content") or ""
                        if piece:
                            parts.append(piece)
                            shown = True
                            if on_text is not None:
                                if on_text(piece):
                                    break
                        # streaming tool call deltas
                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            td = tool_deltas.setdefault(idx, {"name": "", "args": ""})
                            fn = tc.get("function") or {}
                            td["name"] += fn.get("name") or ""
                            td["args"] += fn.get("arguments") or ""
        finally:
            conn.close()

        # heartbeat on_text(None) calls are handled by the view — not needed here
        text = "".join(parts)
        if tool_deltas:
            tool_text = _tool_deltas_to_arete(tool_deltas)
            text = text + tool_text if text else tool_text
        return text, shown


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Strip harness-internal fields (raw, raw_prefix, reasoning_content)
    and convert tool result messages to the OpenAI format.
    """
    out = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if role == "tool":
            # harness stores tool results as {"role": "tool", "name": ..., "content": ...}
            out.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id") or msg.get("name") or "tool",
                "content": str(content),
            })
            continue
        if role == "assistant":
            # rebuild tool_calls in OpenAI format from harness's parsed list
            calls = msg.get("tool_calls") or []
            if calls:
                out.append({
                    "role": "assistant",
                    "content": str(content) if content else None,
                    "tool_calls": [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c.get("arguments") or {}),
                            },
                        }
                        for i, c in enumerate(calls)
                    ],
                })
                continue
        clean = {"role": role, "content": str(content)}
        out.append(clean)
    return out


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """The harness already stores tools in OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
        if t.get("function", {}).get("name")
    ]


def _extract_response(data: dict) -> str:
    """Turn an OpenAI completions response into Arete-format raw text."""
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return content
    tool_text = _tool_calls_to_arete(tool_calls)
    return (content + "\n" + tool_text).strip() if content else tool_text


def _tool_calls_to_arete(tool_calls: list[dict]) -> str:
    """Convert OpenAI tool_calls array to Hermes JSON inside <tool_call> blocks."""
    parts = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        payload = json.dumps({"name": name, "arguments": args}, ensure_ascii=False)
        parts.append(f"<tool_call>\n{payload}\n</tool_call>")
    return "\n".join(parts)


def _tool_deltas_to_arete(tool_deltas: dict) -> str:
    """Same conversion for streamed deltas."""
    calls = []
    for idx in sorted(tool_deltas):
        td = tool_deltas[idx]
        args_raw = td.get("args") or "{}"
        try:
            args = json.loads(args_raw) if args_raw.strip() else {}
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        calls.append({
            "id": f"call_{idx}",
            "type": "function",
            "function": {"name": td.get("name", ""), "arguments": json.dumps(args)},
        })
    return _tool_calls_to_arete(calls)


# ---------------------------------------------------------------------------
# Live model fetch
# ---------------------------------------------------------------------------

def fetch_models(provider_name: str, base_url: str, api_key: str,
                 timeout: int = 15) -> list[dict]:
    """
    GET /v1/models from the provider and return a normalised list.
    Each entry: {"id": ..., "description": ..., "rpm": ..., "tpm": ..., "ctx": ...}.
    Falls back to the static catalog on any error.
    """
    from providers_addon.catalog import static_models  # noqa: PLC0415
    url = base_url.rstrip("/")
    path = "/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    https = url.startswith("https://")
    rest = url[len("https://"):] if https else url[len("http://"):]
    host = rest.split("/", 1)[0]
    port = 443 if https else 80
    if ":" in host:
        host, p = host.rsplit(":", 1)
        port = int(p)
    try:
        if https:
            import ssl  # noqa: PLC0415
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        data = json.loads(body)
        raw = data.get("data") or data.get("models") or (data if isinstance(data, list) else [])
        # Merge live IDs with static catalog data for limits
        static = {m["id"]: m for m in static_models(provider_name)}
        out = []
        for item in raw:
            mid = item.get("id") or item.get("name") or ""
            if not mid:
                continue
            base = dict(static.get(mid) or {})
            base["id"] = mid
            if not base.get("description"):
                base["description"] = item.get("description") or ""
            out.append(base)
        # Add any static models not returned by the live list (some are gated)
        live_ids = {m["id"] for m in out}
        for m in static_models(provider_name):
            if m["id"] not in live_ids:
                out.append(dict(m))
        return out
    except Exception:
        return list(static_models(provider_name))

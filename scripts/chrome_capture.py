"""Shared hardened Chrome capture helpers for controller-run UI instruments.

This module intentionally owns the single subprocess boundary for Chrome.
Importers build probes, captures, and local served copies through these helpers
so the isolation self-test can inspect one flat module.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


FACTS_VERSION = "spatial-facts.v2"
DEFAULT_WIDTHS = (320, 390, 768, 1280)
VIRTUAL_TIME_BUDGET_MS = 5000
DEFAULT_CHROME_TIMEOUT_SECONDS = 120
MIN_TOP_LEVEL_WIDTH = 500
PROBE_ELEMENT_CAP = 300
PROBE_SCRIPT_ID = "spatial-probe-script"
FACTS_SCRIPT_ID = "spatial-facts-v2"
PROBE_COMPLETE_ID = "spatial-probe-complete"
CAPTURE_SCROLL_SCRIPT_ID = "capture-scroll-script"
CAPTURE_VH_STYLE_ID = "capture-vh-neutral-style"
DEFAULT_CAPTURE_VH_PX = 8
PROBE_CSP = (
    "default-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline' data: blob:; "
    "style-src 'self' 'unsafe-inline' data: blob:; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
# Fallback UA checked 2026-06-12; re-check on Chrome stable major changes or
# after 2026-09-12, whichever comes first.
CURRENT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
CHROME_VERSION_RE = re.compile(r"\b(\d+)(?:\.\d+){1,3}\b")
_BROWSER_CLASS_UA_CACHE: dict[str, str] = {}


class ChromeUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class VhNeutralizationResult:
    source_root: Path
    output_root: Path
    rewritten_files: list[str]
    viewport_height_px: int


def frame_height(width: int) -> int:
    if width <= 390:
        return 844
    if width <= 768:
        return 1024
    return 800


def probe_js() -> str:
    return f"""
(function () {{
  "use strict";
  const FACTS_VERSION = {json.dumps(FACTS_VERSION)};
  const ELEMENT_CAP = {PROBE_ELEMENT_CAP};
  function cssIdent(value) {{
    return String(value || "").replace(/[^a-zA-Z0-9_-]/g, function (ch) {{
      return "\\\\" + ch.charCodeAt(0).toString(16) + " ";
    }});
  }}
  function selectorPath(el) {{
    if (!el || !el.tagName) return "";
    if (el.id) return el.tagName.toLowerCase() + "#" + cssIdent(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 7) {{
      let part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) {{
        part += "." + Array.from(cur.classList).slice(0, 2).map(cssIdent).join(".");
      }}
      const parent = cur.parentElement;
      if (parent) {{
        const siblings = Array.from(parent.children).filter(function (item) {{ return item.tagName === cur.tagName; }});
        if (siblings.length > 1) part += ":nth-of-type(" + (siblings.indexOf(cur) + 1) + ")";
      }}
      parts.unshift(part);
      cur = parent;
    }}
    return parts.join(" > ");
  }}
  function rectFor(el) {{
    const rect = el.getBoundingClientRect();
    return {{
      left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
      width: rect.width, height: rect.height
    }};
  }}
  function alphaOf(color) {{
    if (!color || color === "transparent") return 0;
    const m = String(color).match(/rgba?\\(([^)]+)\\)/i);
    if (!m) return 1;
    const parts = m[1].split(",").map(function (part) {{ return part.trim(); }});
    return parts.length >= 4 ? Number(parts[3]) : 1;
  }}
  function backgroundFor(el) {{
    let cur = el;
    while (cur && cur.nodeType === 1) {{
      const style = getComputedStyle(cur);
      const image = style.backgroundImage || "none";
      if (image && image !== "none") {{
        return {{kind: image.indexOf("gradient(") >= 0 ? "gradient" : "image", color: style.backgroundColor || "", source: selectorPath(cur)}};
      }}
      const color = style.backgroundColor || "";
      const alpha = alphaOf(color);
      if (alpha > 0 && alpha < 1) return {{kind: "alpha", color: color, source: selectorPath(cur)}};
      if (alpha >= 1) return {{kind: "solid", color: color, source: selectorPath(cur)}};
      cur = cur.parentElement;
    }}
    return {{kind: "solid", color: "rgb(255, 255, 255)", source: "viewport"}};
  }}
  function visible(el, style, rect) {{
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
  }}
  function isTarget(el) {{
    const tag = el.tagName.toLowerCase();
    return ["a", "button", "input", "select", "textarea"].indexOf(tag) >= 0 ||
      el.getAttribute("role") === "button" || el.hasAttribute("tabindex");
  }}
  function isFocusable(el) {{
    const tag = el.tagName.toLowerCase();
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
    if (el.hasAttribute("tabindex")) return Number(el.getAttribute("tabindex")) >= 0;
    if (tag === "a" || tag === "area") return el.hasAttribute("href");
    return ["button", "input", "select", "textarea", "summary"].indexOf(tag) >= 0;
  }}
  function isInteractive(el) {{
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || "";
    return ["a", "button", "input", "select", "textarea", "summary", "label"].indexOf(tag) >= 0 ||
      ["button", "link", "checkbox", "radio", "switch", "tab", "menuitem"].indexOf(role) >= 0 ||
      Boolean(el.onclick);
  }}
  function isComponent(el) {{
    const tag = el.tagName.toLowerCase();
    return ["button", "input", "select", "textarea"].indexOf(tag) >= 0 || el.getAttribute("role") === "button";
  }}
  function overflowClipAncestor(el) {{
    let cur = el.parentElement;
    while (cur && cur.nodeType === 1) {{
      const style = getComputedStyle(cur);
      const overflow = [style.overflow, style.overflowX, style.overflowY].join(" ");
      if (/(^|\\s)(hidden|clip)(\\s|$)/.test(overflow)) {{
        return {{selector: selectorPath(cur), overflow: overflow}};
      }}
      cur = cur.parentElement;
    }}
    return null;
  }}
  function zIndexNegative(style) {{
    const value = parseInt(style.zIndex, 10);
    return Number.isFinite(value) && value < 0;
  }}
  function capture() {{
    const params = new URLSearchParams(location.search);
    const width = Number(params.get("__spatial_probe_width")) || document.documentElement.clientWidth;
    const all = Array.from(document.querySelectorAll("body *"));
    const elements = [];
    for (const el of all) {{
      if (elements.length >= ELEMENT_CAP) break;
      const style = getComputedStyle(el);
      const rect = rectFor(el);
      if (!visible(el, style, rect)) continue;
      const bg = backgroundFor(el);
      elements.push({{
        selector: selectorPath(el),
        tag: el.tagName.toLowerCase(),
        rect: rect,
        position: style.position,
        color: style.color,
        backgroundColor: bg.color,
        backgroundKind: bg.kind,
        backgroundSource: bg.source,
        borderColor: style.borderTopColor,
        outlineColor: style.outlineColor,
        zIndex: style.zIndex,
        zIndexNegative: zIndexNegative(style),
        overflowClipAncestor: overflowClipAncestor(el),
        fontSizePx: parseFloat(style.fontSize) || 0,
        fontWeight: parseFloat(style.fontWeight) || 400,
        hasText: Boolean((el.innerText || "").trim()),
        isTarget: isTarget(el),
        isFocusable: isFocusable(el),
        isInteractive: isInteractive(el),
        isComponent: isComponent(el)
      }});
    }}
    return {{
      version: FACTS_VERSION,
      width: width,
      height: window.innerHeight,
      viewport: {{
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        clientHeight: document.documentElement.clientHeight,
        scrollHeight: document.documentElement.scrollHeight
      }},
      elements: elements
    }};
  }}
  function complete() {{
    const frame = capture();
    let marker = document.getElementById("{PROBE_COMPLETE_ID}");
    if (!marker) {{
      marker = document.createElement("meta");
      marker.id = "{PROBE_COMPLETE_ID}";
      document.documentElement.appendChild(marker);
    }}
    if (window.parent && window.parent !== window) {{
      window.parent.postMessage({{type: FACTS_VERSION, frame: frame}}, location.origin);
      return;
    }}
    let script = document.getElementById("{FACTS_SCRIPT_ID}");
    if (!script) {{
      script = document.createElement("script");
      script.id = "{FACTS_SCRIPT_ID}";
      script.type = "application/json";
      document.body.appendChild(script);
    }}
    script.textContent = JSON.stringify({{
      version: FACTS_VERSION,
      widths: [frame.width],
      source: {{mode: "served-directory-direct-page"}},
      frames: [frame],
      notes: ["Width captured as a top-level Chrome window."]
    }});
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", function () {{ setTimeout(complete, 50); }});
  }} else {{
    setTimeout(complete, 50);
  }}
}})();
"""


def capture_scroll_js() -> str:
    return f"""
(function () {{
  "use strict";
  const params = new URLSearchParams(location.search);
  const scrollY = Number(params.get("__capture_scroll_y"));
  if (!Number.isFinite(scrollY) || scrollY <= 0) return;
  function applyScroll() {{ window.scrollTo(0, scrollY); }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", function () {{ setTimeout(applyScroll, 20); }});
  }} else {{
    setTimeout(applyScroll, 20);
  }}
}})();
"""


def harness_html(widths: list[int], target_path: str) -> str:
    frames = []
    for width in widths:
        height = frame_height(width)
        params = urllib.parse.urlencode({"__spatial_probe_width": str(width)})
        separator = "&" if "?" in target_path else "?"
        src = f"{target_path}{separator}{params}"
        frames.append(
            f'<iframe title="spatial-{width}" data-spatial-width="{width}" '
            f'style="width:{width}px;height:{height}px;border:0;display:block;margin:0 0 12px 0;" '
            f'src="{html.escape(src, quote=True)}"></iframe>'
        )
    expected_json = json.dumps(widths)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>spatial harness</title>
<style>html,body{{margin:0;padding:0;background:#fff;}} iframe{{overflow:hidden;}}</style>
</head>
<body>
<script>
(function () {{
  const expected = new Set({expected_json});
  const byWidth = new Map();
  function publish(done) {{
    const frames = Array.from(byWidth.keys()).sort((a, b) => a - b).map((width) => byWidth.get(width));
    const facts = {{
      version: {json.dumps(FACTS_VERSION)},
      widths: {expected_json},
      source: {{mode: "served-directory", harness: "same-origin-iframes"}},
      frames: frames,
      notes: [
        "All declared widths are simulated through iframes in one Chrome launch.",
        "Known divergence: iframe geometry can differ from a top-level mobile viewport for vh-dependent layout and position:fixed behavior."
      ]
    }};
    let script = document.getElementById({json.dumps(FACTS_SCRIPT_ID)});
    if (!script) {{
      script = document.createElement("script");
      script.id = {json.dumps(FACTS_SCRIPT_ID)};
      script.type = "application/json";
      document.body.appendChild(script);
    }}
    script.textContent = JSON.stringify(facts);
    if (done && !document.getElementById({json.dumps(PROBE_COMPLETE_ID)})) {{
      const marker = document.createElement("div");
      marker.id = {json.dumps(PROBE_COMPLETE_ID)};
      marker.hidden = true;
      document.body.appendChild(marker);
    }}
  }}
  window.addEventListener("message", function (event) {{
    if (event.origin !== location.origin) return;
    const data = event.data || {{}};
    if (data.type !== {json.dumps(FACTS_VERSION)} || !data.frame) return;
    byWidth.set(Number(data.frame.width), data.frame);
    publish(byWidth.size === expected.size);
  }});
  setTimeout(function () {{ publish(byWidth.size === expected.size); }}, 3000);
}})();
</script>
{''.join(frames)}
</body>
</html>
"""


def inject_probe(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        encoding = "latin-1"
    if PROBE_SCRIPT_ID in text:
        return content
    tag = (
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(PROBE_CSP, quote=True)}">'
        f'<style id="{CAPTURE_VH_STYLE_ID}">:root{{--capture-vh-px:{DEFAULT_CAPTURE_VH_PX}px;}}</style>'
        f'<script id="{CAPTURE_SCROLL_SCRIPT_ID}">{capture_scroll_js()}</script>'
        f'<script id="{PROBE_SCRIPT_ID}">{probe_js()}</script>'
    )
    lower = text.lower()
    head_match = re.search(r"<head\b[^>]*>", text, flags=re.IGNORECASE)
    if head_match:
        index = head_match.end()
        text = text[:index] + tag + text[index:]
    elif "</body>" in lower:
        index = lower.rfind("</body>")
        text = text[:index] + tag + text[index:]
    else:
        text += tag
    return text.encode(encoding, errors="xmlcharrefreplace")


def make_handler(root: Path, widths: list[int], target_path: str) -> type[SimpleHTTPRequestHandler]:
    resolved_root = root.resolve()

    class CaptureHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/__spatial_harness__.html":
                body = harness_html(widths, target_path).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def translate_path(self, path: str) -> str:
            parsed = urllib.parse.urlparse(path)
            parts = [
                item for item in urllib.parse.unquote(parsed.path).split("/")
                if item and item not in {".", ".."}
            ]
            candidate = resolved_root.joinpath(*parts)
            if candidate.is_dir():
                candidate = candidate / "index.html"
            resolved = candidate.resolve()
            if not (resolved == resolved_root or resolved_root in resolved.parents):
                return str(resolved_root / "__blocked__")
            return str(resolved)

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def send_head(self) -> Any:
            path = Path(self.translate_path(self.path))
            if not path.is_file():
                self.send_error(404, "File not found")
                return None
            ctype = self.guess_type(str(path))
            if not ctype.startswith("text/html"):
                return super().send_head()
            try:
                raw = path.read_bytes()
            except OSError:
                self.send_error(404, "File not found")
                return None
            body = inject_probe(raw)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

        def copyfile(self, source: Any, outputfile: Any) -> None:
            shutil.copyfileobj(source, outputfile)

    return CaptureHandler


def serve_site(root: Path, widths: list[int], target_path: str) -> tuple[ThreadingHTTPServer, str]:
    handler = make_handler(root, widths, target_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def discover_chrome(explicit: str | None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CHROME_BIN"):
        candidates.append(os.environ["CHROME_BIN"])
    candidates.extend([
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ])
    for candidate in candidates:
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    name = explicit or "$CHROME_BIN/google-chrome/chromium"
    raise ChromeUnavailableError(f"Chrome binary unavailable: set --chrome or CHROME_BIN; looked for {name}")


def browser_class_user_agent_for_chrome_version(version_output: str) -> str | None:
    match = CHROME_VERSION_RE.search(version_output)
    if not match:
        return None
    major = int(match.group(1))
    if major <= 0:
        return None
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def chrome_version_output(binary: str) -> str | None:
    try:
        completed = subprocess.run([binary, "--version"], check=False, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or completed.stderr).strip() or None


def browser_class_user_agent(chrome: str | None = None) -> str:
    cache_key = chrome or ""
    if cache_key in _BROWSER_CLASS_UA_CACHE:
        return _BROWSER_CLASS_UA_CACHE[cache_key]

    user_agent = CURRENT_UA
    try:
        binary = discover_chrome(chrome)
    except ChromeUnavailableError:
        binary = None
    if binary:
        version_output = chrome_version_output(binary)
        if version_output:
            user_agent = browser_class_user_agent_for_chrome_version(version_output) or CURRENT_UA

    _BROWSER_CLASS_UA_CACHE[cache_key] = user_agent
    return user_agent


def chrome_run(
    url: str,
    widths: list[int],
    chrome: str | None,
    virtual_time_budget: int,
    timeout_seconds: int,
    window_size: tuple[int, int] | None = None,
    screenshot_path: Path | None = None,
    dump_dom: bool = True,
) -> str:
    """Invoke Chrome once and return stdout.

    All Chrome flags are constructed here so security review has one subprocess
    boundary. No shell, user-data-dir, sandbox-disabling, or remote-debugging
    flag is used.
    """
    binary = discover_chrome(chrome)
    if window_size is None:
        window_width = max(max(widths) + 40, 800)
        window_height = max(frame_height(width) for width in widths) + 80
    else:
        window_width, window_height = window_size
    argv = [
        binary,
        "--headless=new",
        f"--user-agent={browser_class_user_agent(binary)}",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        f"--virtual-time-budget={virtual_time_budget}",
        f"--window-size={window_width},{window_height}",
    ]
    if dump_dom:
        argv.append("--dump-dom")
    if screenshot_path is not None:
        argv.append(f"--screenshot={screenshot_path}")
    argv.append(url)
    try:
        completed = subprocess.run(argv, check=False, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChromeUnavailableError(f"Chrome launch failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise ChromeUnavailableError(f"Chrome run failed: {detail[:500]}")
    return completed.stdout


def with_url_params(url: str, params: dict[str, str | int]) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, str(value)) for key, value in params.items())
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def with_probe_width(url: str, width: int) -> str:
    return with_url_params(url, {"__spatial_probe_width": width})


def with_capture_scroll(url: str, scroll_y: int) -> str:
    return with_url_params(url, {"__capture_scroll_y": scroll_y})


def site_url(base_url: str, target_path: str) -> str:
    if not target_path.startswith("/"):
        target_path = "/" + target_path
    return base_url + target_path


VH_UNIT_RE = re.compile(r"(?<![\w.-])(-?(?:\d+|\d*\.\d+))vh\b")


def neutralize_vh_units(text: str) -> str:
    return VH_UNIT_RE.sub(r"calc(var(--capture-vh-px) * \1)", text)


def copy_site_with_vh_neutralization(source_root: Path, output_root: Path, viewport_height_px: int = 800) -> VhNeutralizationResult:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    rewritten: list[str] = []
    for source in source_root.rglob("*"):
        rel_path = source.relative_to(source_root)
        destination = output_root / rel_path
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() in {".html", ".htm", ".css"}:
            raw = source.read_bytes()
            try:
                text = raw.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                encoding = "latin-1"
            updated = neutralize_vh_units(text)
            if updated != text:
                rewritten.append(str(rel_path))
            destination.write_bytes(updated.encode(encoding, errors="xmlcharrefreplace"))
        else:
            shutil.copy2(source, destination)
    return VhNeutralizationResult(
        source_root=source_root,
        output_root=output_root,
        rewritten_files=rewritten,
        viewport_height_px=viewport_height_px,
    )

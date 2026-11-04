#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Burp Shot —— 一个仿 Burp Repeater 的本地发包 + 自动截图小工具。

用法:
    python3 burp_shot.py            # 默认 http://127.0.0.1:8765
    PORT=9000 python3 burp_shot.py
    CHROME=/path/to/chrome python3 burp_shot.py

依赖: 仅标准库；截图复用系统里的 Chrome / Edge（headless），无需 pip 安装。
"""
import os
import re
import sys
import json
import time
import gzip
import zlib
import ssl
import shutil
import argparse
import tempfile
import threading
import subprocess
import webbrowser
import http.client
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
UI_FILE = ROOT / "ui.html"
SHOT_DIR = ROOT / "shots"
SHOT_DIR.mkdir(exist_ok=True)
SHOT_WIDTH = 1600

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome", "msedge"):
        w = shutil.which(name)
        if w:
            return w
    return None


# ---------------------------------------------------------------- 发包

def send_raw(raw: str, scheme: str = "https", timeout: int = 30) -> str:
    raw = raw.replace("\r\n", "\n").replace("\n", "\r\n")
    if "\r\n\r\n" in raw:
        head, body = raw.split("\r\n\r\n", 1)
    else:
        head, body = raw, ""

    lines = head.split("\r\n")
    parts = lines[0].split()
    if len(parts) < 2:
        raise ValueError("请求行格式不对，需要: METHOD PATH HTTP/1.1")

    method, target = parts[0], parts[1]

    headers = []
    for ln in lines[1:]:
        if not ln.strip() or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        headers.append((k.strip(), v.strip()))

    host = next((v for k, v in headers if k.lower() == "host"), "")
    if not host:
        raise ValueError("缺少 Host 头")

    if target.startswith("http://") or target.startswith("https://"):
        u = urllib.parse.urlsplit(target)
        scheme = u.scheme or scheme
        host = u.netloc or host
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
    else:
        path = target

    if host.startswith("["):                      # IPv6
        hhost, port = host, (443 if scheme == "https" else 80)
    elif ":" in host:
        hhost, p = host.rsplit(":", 1)
        port = int(p) if p.isdigit() else (443 if scheme == "https" else 80)
    else:
        hhost, port = host, (443 if scheme == "https" else 80)

    body_b = body.encode("utf-8")
    drop = {"content-length", "transfer-encoding", "connection", "proxy-connection"}
    headers = [(k, v) for k, v in headers if k.lower() not in drop]
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE") or body_b:
        headers.append(("Content-Length", str(len(body_b))))
    headers.append(("Connection", "close"))

    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(hhost, port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(hhost, port, timeout=timeout)

    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers:
            conn.putheader(k, v)
        conn.endheaders(body_b if body_b else None)
        resp = conn.getresponse()
        data = resp.read()
    finally:
        conn.close()

    enc = (resp.getheader("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    elif "deflate" in enc:
        try:
            data = zlib.decompress(data)
        except Exception:
            try:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
            except Exception:
                pass

    ct = resp.getheader("Content-Type") or ""
    m = re.search(r"charset=([\w-]+)", ct, re.I)
    charset = m.group(1) if m else "utf-8"
    try:
        text = data.decode(charset, "replace")
    except LookupError:
        text = data.decode("utf-8", "replace")

    out = ["HTTP/%d.%d %d %s" % (resp.version // 10, resp.version % 10, resp.status, resp.reason)]
    for k, v in resp.getheaders():
        out.append("%s: %s" % (k, v))
    return "\r\n".join(out) + "\r\n\r\n" + text


# ---------------------------------------------------------------- 截图

def take_shot(request_text: str, response_text: str, width: int = 1600) -> str:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Edge，请设置环境变量 CHROME 指向浏览器可执行文件")

    html = UI_FILE.read_text(encoding="utf-8")
    data = json.dumps({"request": request_text, "response": response_text},
                      ensure_ascii=False).replace("<", "\\u003c")
    html = html.replace("/*__PAYLOAD__*/null", data)

    name = time.strftime("%Y%m%d-%H%M%S") + ".png"
    out = SHOT_DIR / name

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "page.html"
        page.write_text(html, encoding="utf-8")
        ud = str(Path(td) / "ud")
        base = [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--force-device-scale-factor=2", "--default-background-color=ffffffff",
            "--user-data-dir=" + ud,
        ]
        url = page.as_uri()

        # 第一趟：让页面自己量出需要多高（换行后行数会变）
        height = 900
        try:
            m = subprocess.run(base + ["--window-size=%d,900" % width, "--dump-dom", url],
                               timeout=90, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            hit = re.search(r'name="shot-height" content="(\d+)"', m.stdout.decode("utf-8", "replace"))
            if hit:
                height = max(240, min(int(hit.group(1)), 20000))
        except Exception:
            pass

        # 第二趟：按实际高度截图
        r = subprocess.run(base + ["--window-size=%d,%d" % (width, height),
                                   "--screenshot=" + str(out), url],
                           timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not out.exists():
            raise RuntimeError("Chrome 截图失败: " + (r.stderr.decode("utf-8", "replace")[-400:] or "no output"))
    return name


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "BurpShot/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/ping":
            self._json({"ok": True, "app": "burp-shot"})
        elif path in ("/", "/index.html"):
            html = UI_FILE.read_text(encoding="utf-8").replace("/*__PAYLOAD__*/null", "null")
            b = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif path.startswith("/shots/"):
            f = SHOT_DIR / os.path.basename(path)
            if f.exists():
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            p = self._read_json()
            if self.path == "/api/send":
                text = send_raw(p.get("raw", ""), p.get("scheme", "https"))
                self._json({"text": text})
            elif self.path == "/api/shot":
                name = take_shot(p.get("request", ""), p.get("response", ""), SHOT_WIDTH)
                self._json({"file": name, "url": "/shots/" + name})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 200)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-w", "--width", type=int, default=1600, help="截图宽度(CSS px)，默认 1600，2 倍输出")
    args = ap.parse_args()

    global SHOT_WIDTH
    SHOT_WIDTH = args.width

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("Burp Shot 已启动: %s" % url)
    print("截图目录: %s" % SHOT_DIR)
    print("Chrome: %s" % (find_chrome() or "未找到（截图会失败）"))
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()

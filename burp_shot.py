#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Burp Shot 核心 —— 发原始 HTTP 请求，再用 renderer/shot.html 渲染成
Burp Repeater 样子的 PNG。

mcp_server.py 直接调这里；也可以单独跑一次自检，用自带样例出一张图：

    python3 burp_shot.py
    CHROME=/path/to/chrome python3 burp_shot.py

依赖：仅标准库。截图复用本机 Chrome / Edge（headless），不需要 pip 安装。
"""
import os
import re
import gzip
import zlib
import json
import time
import ssl
import shutil
import struct
import tempfile
import subprocess
import http.client
import urllib.parse
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "renderer" / "shot.html"
SHOT_DIR = ROOT / "shots"
SHOT_DIR.mkdir(exist_ok=True)

DEFAULT_WIDTH = 1600      # CSS px，截图为 2 倍图
MIN_HEIGHT = 1200         # 响应短也留出整窗，不然是一条窄图
MAX_HEIGHT = 3000         # 响应长就截断，不整段铺开
SCALE = 2

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


class Reply(NamedTuple):
    text: str
    ms: int


class Shot(NamedTuple):
    path: Path
    width: int
    height: int


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

def send_timed(raw: str, scheme: str = "https", timeout: int = 30) -> Reply:
    """发一段原始请求，整体返回 (响应报文, 耗时毫秒)。耗时就是状态栏里那个 millis。"""
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

    started = time.perf_counter()
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers:
            conn.putheader(k, v)
        conn.endheaders(body_b if body_b else None)
        resp = conn.getresponse()
        data = resp.read()
    finally:
        ms = int(round((time.perf_counter() - started) * 1000))
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
    return Reply("\r\n".join(out) + "\r\n\r\n" + text, ms)


def send_raw(raw: str, scheme: str = "https", timeout: int = 30) -> str:
    """只要响应报文的老接口。"""
    return send_timed(raw, scheme, timeout).text


# ---------------------------------------------------------------- 截图

def _clamp(height: int) -> int:
    return max(MIN_HEIGHT, min(int(height), MAX_HEIGHT))


def _render_page(request_text: str, response_text: str,
                 ms: Optional[int] = None) -> str:
    """把数据塞进模板的 /*__PAYLOAD__*/null，返回整页 HTML。"""
    response_text = response_text or ""
    payload = json.dumps({
        "request": request_text or "",
        "response": response_text,
        "ms": None if ms is None else max(0, int(round(ms))),
        "bytes": len(response_text.encode("utf-8")),
    }, ensure_ascii=False)
    # </script> 会截断内联脚本
    data = payload.replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8")
    if "/*__PAYLOAD__*/null" not in html:
        raise RuntimeError("renderer/shot.html 里找不到 /*__PAYLOAD__*/ 注入点")
    return html.replace("/*__PAYLOAD__*/null", data)


def take_shot(request_text: str,
              response_text: str,
              width: int = DEFAULT_WIDTH,
              ms: Optional[int] = None,
              name: Optional[str] = None) -> Shot:
    """渲染并截图。第一趟让页面自己报高度，第二趟按这个高度截，不会被裁。"""
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Edge，请设置环境变量 CHROME 指向浏览器可执行文件")
    if not TEMPLATE.exists():
        raise RuntimeError("找不到模板: %s" % TEMPLATE)

    response_text = response_text or ""
    html = _render_page(request_text, response_text, ms)

    out = SHOT_DIR / (name or time.strftime("%Y%m%d-%H%M%S") + ".png")

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "shot.html"
        page.write_text(html, encoding="utf-8")
        base = [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--force-device-scale-factor=%d" % SCALE,
            "--default-background-color=ffffffff",
            "--user-data-dir=" + str(Path(td) / "ud"),
        ]
        url = page.as_uri()

        # 第一趟：量内容高度（不 fitted，页面按内容撑开）
        height = MIN_HEIGHT
        try:
            dom = subprocess.run(base + ["--window-size=%d,900" % width, "--dump-dom", url],
                                 timeout=90, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            hit = re.search(r'name="shot-height" content="(\d+)"',
                            dom.stdout.decode("utf-8", "replace"))
            if hit:
                height = _clamp(hit.group(1))
        except Exception:
            pass

        # 第二趟：定高后 fitted 撑满，状态栏贴底
        r = subprocess.run(base + ["--window-size=%d,%d" % (width, height),
                                   "--screenshot=" + str(out), url + "?fitted=1"],
                           timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not out.exists():
            raise RuntimeError("Chrome 截图失败: " +
                               (r.stderr.decode("utf-8", "replace")[-400:] or "no output"))

    return Shot(out, width, height)


def png_size(path) -> tuple:
    """读 PNG 头里的真实像素尺寸，自检时用来核对 2 倍图。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG: %s" % path)
    return struct.unpack(">II", head[16:24])


# ---------------------------------------------------------------- 自检

DEMO_REQUEST = "\n".join([
    "POST /api/session/prompt HTTP/1.1",
    "Host: 100.64.0.101:3000",
    "Content-Type: application/json",
    "Accept: */*",
    "Origin: http://100.64.0.101:3000",
    "Referer: http://100.64.0.101:3000/",
    "Accept-Encoding: gzip, deflate, br",
    "Accept-Language: zh,en;q=0.9,zh-CN;q=0.8",
    "Connection: keep-alive",
    "",
    "{",
    '    "type": "client-request",',
    '    "rpcId": "e690fe7e-1f84-42c1-acb7-24c39a7e761c",',
    '    "method": "session/prompt",',
    '    "payload": {',
    '        "args": {',
    '            "request": {',
    '                "requestId": "153977e3-75a5-4abd-915f-950936f27516",',
    '                "sessionId": "session-cae7e24b-baf4-4369-a83c-ce0f29524097",',
    '                "node": "queue",',
    '                "content": [',
    "                    {",
    '                        "type": "text",',
    '                        "text": "hello"',
    "                    }",
    "                ]",
    "            }",
    "        }",
    "    }",
    "}",
])

DEMO_RESPONSE = "\n".join([
    "HTTP/1.1 200 OK",
    "Content-Type: application/json",
    "Vary: Accept-Encoding",
    "Date: Thu, 05 Nov 2026 13:12:37 GMT",
    "Connection: keep-alive",
    "Keep-Alive: timeout=5",
    "Content-Length: 120",
    "",
    '{"type":"server-response","rpcId":"e690fe7e-1f84-42c1-acb7-24c39a7e761c",'
    '"result":{"ok":true,"value":{"accepted":true}}}',
])


def main():
    chrome = find_chrome()
    print("Chrome: %s" % (chrome or "未找到（截图会失败）"))
    print("模板  : %s" % TEMPLATE)
    shot = take_shot(DEMO_REQUEST, DEMO_RESPONSE, DEFAULT_WIDTH, ms=41)
    px = png_size(shot.path)
    print("截图  : %s" % shot.path)
    print("CSS   : %dx%d  实际像素: %dx%d" % (shot.width, shot.height, px[0], px[1]))


if __name__ == "__main__":
    main()

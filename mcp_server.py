#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Burp Shot MCP Server —— 把发包 / 截图能力暴露成 MCP 工具，供 AI agent 直接调用。

传输：stdio，换行分隔的 JSON-RPC 2.0（MCP 标准）。
依赖：仅标准库；复用同目录 burp_shot.py 的发包与截图实现。

手动自测：
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 mcp_server.py
"""
import sys
import json
import traceback

import burp_shot as core

SERVER_NAME = "burp-shot"
SERVER_VERSION = "1.0.0"
PROTOCOL_FALLBACK = "2024-11-05"


TOOLS = [
    {
        "name": "http_send",
        "description": (
            "发送一段原始 HTTP 请求报文（第一行是请求行，必须带 Host 头），返回完整响应报文。"
            "用于验证接口行为、复现越权/注入等问题。支持 https（默认，不校验证书）与 http。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "原始 HTTP 请求文本，含请求行、头、空行、body"},
                "scheme": {"type": "string", "enum": ["http", "https"], "default": "https"},
                "timeout": {"type": "integer", "default": 30, "description": "超时秒数"}
            },
            "required": ["request"]
        }
    },
    {
        "name": "http_screenshot",
        "description": (
            "把请求和响应渲染成 Burp Repeater 风格的 PNG 截图并保存到本地，返回绝对路径。"
            "用于给漏洞报告配图；语法高亮、行号、配色与 Burp 一致。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "请求报文原文"},
                "response": {"type": "string", "description": "响应报文原文"},
                "width": {"type": "integer", "default": 1600, "description": "截图宽度（CSS px，输出为 2 倍图）"},
                "include_image": {"type": "boolean", "default": False, "description": "是否在返回值里附带 PNG 本身（base64）"}
            },
            "required": ["request", "response"]
        }
    },
    {
        "name": "http_send_screenshot",
        "description": (
            "一步完成：发送请求 → 拿到响应 → 渲染成 Burp 风格截图 → 保存。"
            "返回截图绝对路径和响应报文。复现漏洞并出图时优先用这个。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "原始 HTTP 请求文本"},
                "scheme": {"type": "string", "enum": ["http", "https"], "default": "https"},
                "timeout": {"type": "integer", "default": 30},
                "width": {"type": "integer", "default": 1600},
                "include_image": {"type": "boolean", "default": False}
            },
            "required": ["request"]
        }
    },
    {
        "name": "shot_list",
        "description": "列出最近保存的截图文件（绝对路径 + 大小），便于在报告里引用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20}
            }
        }
    }
]


def text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def image_block(path):
    import base64
    from pathlib import Path
    data = Path(path).read_bytes()
    return {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/png"}


def call_tool(name, args):
    if name == "http_send":
        resp = core.send_raw(args["request"], args.get("scheme", "https"), int(args.get("timeout", 30)))
        return text_result(resp)

    if name == "http_screenshot":
        path = core.take_shot(args["request"], args["response"], int(args.get("width", 1600)))
        full = str((core.SHOT_DIR / path).resolve())
        blocks = [{"type": "text", "text": "截图已保存: " + full}]
        if args.get("include_image"):
            blocks.append(image_block(full))
        return {"content": blocks, "isError": False}

    if name == "http_send_screenshot":
        resp = core.send_raw(args["request"], args.get("scheme", "https"), int(args.get("timeout", 30)))
        path = core.take_shot(args["request"], resp, int(args.get("width", 1600)))
        full = str((core.SHOT_DIR / path).resolve())
        blocks = [{"type": "text", "text": "截图已保存: " + full + "\n\n--- 响应 ---\n" + resp}]
        if args.get("include_image"):
            blocks.append(image_block(full))
        return {"content": blocks, "isError": False}

    if name == "shot_list":
        limit = int(args.get("limit", 20))
        files = sorted(core.SHOT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        if not files:
            return text_result("还没有截图。")
        lines = ["%s  (%d KB)" % (str(f.resolve()), f.stat().st_size // 1024) for f in files]
        return text_result("\n".join(lines))

    return text_result("未知工具: " + name, True)


def handle(msg):
    """处理一条 JSON-RPC 消息；返回要写回的 dict，或 None（通知不回复）。"""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if mid is None:                      # notification，例如 notifications/initialized
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def fail(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    try:
        if method == "initialize":
            want = params.get("protocolVersion") or PROTOCOL_FALLBACK
            return ok({
                "protocolVersion": want,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "这是 Burp Shot：可以发送原始 HTTP 请求并生成 Burp Repeater 风格的截图。"
                    "复现漏洞并出图时直接用 http_send_screenshot，一次调用即可拿到 PNG 路径和响应。"
                )
            })

        if method == "ping":
            return ok({})

        if method == "tools/list":
            return ok({"tools": TOOLS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            return ok(call_tool(name, args))

        if method in ("resources/list", "prompts/list"):
            key = method.split("/")[0]
            return ok({key: []})

        return fail(-32601, "Method not found: %s" % method)

    except Exception as e:
        return ok(text_result("%s: %s" % (type(e).__name__, e), True))


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            sys.stderr.write("bad json: %s\n" % line[:200])
            continue
        try:
            out = handle(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            continue
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

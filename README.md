# Burp-Picture-MCP

把一段「请求 + 响应」渲染成 Burp Suite Repeater 样子 PNG 的 MCP 服务器。给 AI agent 出
漏洞报告配图用：调一次工具，拿到一张排版、配色都对得上的截图。

只用 Python 标准库；截图复用本机 Chrome / Edge 的 headless 模式，不用装 Playwright，
也不用开浏览器。

## 文件

```
mcp_server.py         MCP stdio 服务，对外 4 个工具
burp_shot.py          核心：send_timed() 发包、take_shot() 出图
renderer/shot.html    页面：HTML + CSS + window.render()，唯一的渲染源
shots/                截图输出目录
```

`renderer/shot.html` 是独立的：不依赖 Python，浏览器直接打开
`renderer/shot.html?demo=1` 就能看样例；也可以在自己的项目里调

```js
window.render({ request, response, ms, bytes });
```

## 出图是什么样

- 宽度 1600 CSS px，2 倍图（PNG 实际 3200 px 宽），和真 Burp 在 2x 屏上截的一致。
- 高度按内容量出来，下限 1200、上限 3000 CSS px：短响应不留一条窄图，超长 body 也
  不整段铺开（和真窗口一样，超出部分在窗口里可滚动）。
- 标题 44px、页签 30px、代码区 Menlo 12px / 行高 14.5px、底栏 44px、状态栏 28px。
- 左右 Request / Response 两栏；右栏 Inspector / Notes / Explanations / Custom actions。
- 状态栏：左边 `Done` / `Ready`，右边 `N bytes | M millis`（毫秒是真实发包耗时）。
- 语法高亮照 Burp：头名称藏青、Cookie 值暗红、JSON 键藏青、字符串绿、数字和 true/false 亮蓝。
- Pretty 视图把 JSON 排成 4 空格缩进；长行自动换行，续行不重复行号。

## 单独试一下

```bash
python3 burp_shot.py
# Chrome: /usr/bin/google-chrome
# 模板  : .../renderer/shot.html
# 截图  : .../shots/20261111-223955.png
# CSS   : 1600x1200  实际像素: 3200x2400
```

`CHROME=/path/to/chrome python3 burp_shot.py` 可以手动指定浏览器。

## 作为 MCP 工具接入

stdio，由客户端拉起：

```bash
python3 /绝对路径/Burp-Picture-MCP/mcp_server.py
```

暴露的工具：

| 工具 | 作用 | 主要参数 |
|---|---|---|
| `http_send` | 发原始 HTTP 请求，返回完整响应 | `request`、`scheme`、`timeout` |
| `http_screenshot` | 把已有的请求/响应渲染成 PNG | `request`、`response`、`width`、`ms`、`include_image` |
| `http_send_screenshot` | 发包 + 截图一步完成（推荐） | `request`、`scheme`、`timeout`、`width`、`include_image` |
| `shot_list` | 列出最近截图 | `limit` |

`include_image: true` 时返回值里会附带 PNG 本身（base64），agent 能直接看图。

### Claude Code

```bash
claude mcp add burp-picture -- python3 /绝对路径/Burp-Picture-MCP/mcp_server.py
```

### 通用 mcpServers 配置

```json
{
  "mcpServers": {
    "burp-picture": {
      "command": "python3",
      "args": ["/绝对路径/Burp-Picture-MCP/mcp_server.py"]
    }
  }
}
```

Windows 上把 `python3` 换成 `python`。

## 改配色、字体

全在 `renderer/shot.html` 里，没有编译步骤：

- 字号行高：`.code` 的 `font: 12px/14.5px ...`
- 各块高度：`.title` 44px、`.tabs` 30px、`.pane-tools` 44px、`.status` 28px
- 颜色：`.hn`（头名称 / JSON 键）、`.cv`（Cookie 值）、`.js`（字符串）、`.jn`（数字）、
  `.tab.on::after`（页签下的橙线 `#c4663f`）
- 字体栈：`.code` 里 `Menlo, Consolas, "SF Mono", "DejaVu Sans Mono", monospace`，换成你
  Burp 里设置的那款（Burp: Settings → User interface → Font）即可

改完直接 `python3 burp_shot.py` 看效果。

## 已知限制

- 只支持 HTTP/1.1，没有 HTTP/2 和 WebSocket。
- 响应只解 gzip / deflate；`br` 需要额外装 brotli，没装就显示原始字节。
- 不会自动跟随重定向（和 Repeater 行为一致）。
- 没有代理链；要走上游代理需要自己给 `http.client` 加 `set_tunnel`。
- 高度是两趟算出来的：先让页面自己报高度（换行会改变行数），再按这个高度截，所以
  不会被裁；超过 3000 CSS px 的部分按窗口行为截断。
- 换行点偶尔和 Burp 差一点：Burp 会把长 token 的首段填满当前行再换行，CSS 的
  `overflow-wrap: anywhere` 会先把整个 token 挪到下一行再拆。

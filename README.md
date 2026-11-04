# Burp Shot

仿 Burp Suite Repeater 的本地发包 + 自动截图小工具。左边粘请求、点发送、右边出响应，**发送后自动把整页渲染成 PNG**，出来的图就是 Burp 那种排版和配色。

只用 Python 标准库，截图复用本机 Chrome / Edge，不需要 pip 安装任何东西。

## 运行

```bash
python3 burp_shot.py
```

浏览器自动打开 `http://127.0.0.1:8765/`。

常用参数：

```bash
python3 burp_shot.py -p 9000          # 换端口
python3 burp_shot.py -w 1400          # 截图宽度（CSS px，默认 1600，输出为 2 倍图）
CHROME=/path/to/chrome python3 burp_shot.py   # 手动指定浏览器
python3 burp_shot.py --no-browser     # 不自动开浏览器
```

Windows 上如果 `python3` 不识别，用 `python burp_shot.py`。

也可以直接双击 `ui.html` 打开（编辑器内置预览也行）——只要服务在跑，页面会自动回退到
`http://127.0.0.1:8765/` 发请求。服务没起来时页面顶部会出现黄条提示，不会只丢一个 fetch 报错。

## 用法

1. 左边 Request 区整段粘请求包（含请求行和 Host），粘贴时就会跟着语法上色，不用等发送。
2. 顶部选 `http` 或 `https`（决定用什么协议连 Host）。
3. 点「发送」或者 `Ctrl/Cmd + Enter`。
4. 右边出现响应，同时自动存图到 `shots/YYYYMMDD-HHMMSS.png`，底部会给出链接。
5. 想对当前内容重新出图，点「截图」。

勾掉「发送后自动截图」就只发包不存图。

## 和 Burp 的对应关系

| Burp | 这里 |
|---|---|
| 左 Request / 右 Response 分栏 | 一样 |
| Pretty / Raw / Hex / Render / Chinese 标签 | 位置、字号、颜色一样（标签本身不切换视图） |
| 长行自动换行（Cookie、长 URL、body 都一样） | 一样 |
| 换行后继续行不重复行号，只标在逻辑行 | 一样 |
| 行号栏、底部 ? / 齿轮 / 前后翻页 / 搜索框 | 一样 |
| 右上角四个图标（隐藏、换行、回车、菜单） | 一样 |
| 头名称藏青、Cookie 值暗红、JSON 键藏青、字符串绿、数字亮蓝 | 一样 |
| 纯黑等宽正文字体 | 一样 |

截图是该页面本身在 headless Chrome 里的渲染结果，所以**所见即所得**，不存在 html2canvas 那种偏差。

## 微调配色和字体

全部在 `ui.html` 顶部的 `:root` 里：

```css
--fs:13px;        /* 编辑器字号 */
--lh:20px;        /* 行高 */
--fg:#212121;     /* 正文黑 */
--hn:#000080;     /* 头名称 / JSON 键：藏青 */
--num:#0000cc;    /* JSON 数字、true/false：亮蓝 */
--cv:#800000;     /* Cookie 值：暗红 */
--str:#008000;    /* JSON 字符串值：绿 */
--acc:#d9612d;    /* 当前标签页的橙色下划线 */
--mono:"Consolas","Menlo","DejaVu Sans Mono",...;  /* 等宽字体栈 */
```

想让图更像你本机的 Burp，把 `--mono` 改成你 Burp 里设置的那款字体（Burp: User options → Display → Font），字号行高跟着调一格即可。


## 作为 MCP 工具给 AI agent 调用

`mcp_server.py` 把发包和截图暴露成 MCP 工具，agent 可以直接调用，不用开浏览器。

启动方式（stdio，由客户端拉起）：

```bash
python3 /绝对路径/burp-shot/mcp_server.py
```

暴露的工具：

| 工具 | 作用 | 主要参数 |
|---|---|---|
| `http_send` | 发原始 HTTP 请求，返回完整响应 | `request`、`scheme`、`timeout` |
| `http_screenshot` | 把请求/响应渲染成 Burp 风格 PNG | `request`、`response`、`width`、`include_image` |
| `http_send_screenshot` | 发包 + 截图一步完成（推荐） | `request`、`scheme`、`include_image` |
| `shot_list` | 列出最近截图 | `limit` |

`include_image: true` 时会在返回值里附带 PNG 本身（base64），让 agent 能直接看到图。

### Claude Code

```bash
claude mcp add burp-shot -- python3 /绝对路径/burp-shot/mcp_server.py
```

### 通用 mcpServers 配置

```json
{
  "mcpServers": {
    "burp-shot": {
      "command": "python3",
      "args": ["/绝对路径/burp-shot/mcp_server.py"]
    }
  }
}
```

Windows 上把 `python3` 换成 `python`。

### DSH

已写入 `~/.dsh/profiles/web/cordis.patch.yml` 的 `insert` 列表，重启 dsh web 后生效。

## 已知限制

- 只支持 HTTP/1.1，没有 HTTP/2 和 WebSocket。
- 响应只解 gzip / deflate；`br` 需要额外装 brotli，没装就显示原始字节。
- 不会自动跟随重定向（和 Repeater 行为一致）。
- 没有代理链；要走 Burp 上游代理需要自己加（`http.client` 的 `set_tunnel`）。
- 截图高度是两趟算出来的：先让页面自己报高度（换行会改变行数），再按这个高度截，所以不会被裁。
- 换行点偶尔和 Burp 差一点：Burp 会把长 token 的首段填满当前行再换行，CSS 的 `overflow-wrap:anywhere` 会先把整个 token 挪到下一行再拆。

# napcat-qzone-toolkit

通过 NapCat 已有的登录态读取 QQ 空间说说 —— 不需要 CDP、不需要 UI 自动化、不需要额外登录。

`read / detail / comments` over NapCat's own credentials. No CDP, no UIA, no second login.

```python
import qzone
from onebot import OneBotHTTP

call = OneBotHTTP("http://127.0.0.1:3000", token="your-token")
print(qzone.probe(call))                       # 通道是否可用
page = qzone.list_feeds(call, num=5)           # 自己的说说
post = qzone.feed_detail(call, page["items"][0]["tid"])   # 正文 + 图片 + 评论

blogs = qzone.list_blogs(call, num=10)         # 日志列表（长文，非说说）
art = qzone.blog_detail(call, blogs["items"][0]["blogId"])  # 正文纯文本 + 图 + 评论
```

---

## 为什么需要它

NapCat 对接 QQ 空间的方式是「写得多、读得少」：

| NapCat 自带 | 能力 |
|---|---|
| `send_qzone_msg` | 发说说（文字 / 多图 / 权限档位） |
| `delete_qzone_msg` | 删说说 |
| `get_qzone_public_feeds` 等 | 只读「空友爱看」公开推荐流 |

也就是说，**你读不到自己或好友的说说时间线** —— 而这恰恰是最常用的需求。

但 NapCat 已经握着 QQ 的登录态。它的 `get_cookies` 动作能拿到 `qzone.qq.com` 的
cookie（含 `p_skey`）以及据此算好的 `bkn`。有了这两样，空间网页端的 cgi 就能直接调。
本库做的就是这件事。

## 为什么不用 CDP / UI 自动化

三条路都实际试过，结论如下（细节见 [`docs/findings.md`](docs/findings.md)）：

| 方案 | 结论 |
|---|---|
| **本方案**（`get_cookies` + 直连 cgi） | ✅ 结构化 JSON、可翻页、有 `tid` 可继续操作、零额外进程 |
| CDP（DevTools 协议） | ❌ NapCat 的注入器把启动命令写死成 `"<QQ路径>" --enable-logging`，不透传 argv，所以被注入的 QQ 开不了 `--remote-debugging-port`；另起一个带 CDP 的实例又要占一个号（同号不能双开） |
| UIA（UI Automation） | ⚠️ 只能读渲染后的可见文本，拿不到 `tid`／原图／评论，翻页靠滚动，只适合当接口失效时的兜底 |

## 快速开始

### 1. 准备

- QQ NT 客户端，已登录
- [NapCat](https://github.com/NapNeko/NapCatQQ) 已注入并登录
- Python 3.9+（**只用标准库，无需 `pip install`**）

### 2. 打开 NapCat 的 HTTP 接口

NapCat WebUI → 网络配置 → **HTTP 服务器** → 启用，记下端口和 token
（也可以在 `config/onebot11_<QQ号>.json` 里配 `network.httpServers`）。

### 3. 跑

```bash
export ONEBOT_URL=http://127.0.0.1:3000     # Windows: set ONEBOT_URL=...
export ONEBOT_TOKEN=your-token

python examples/read_feeds.py               # 读自己的最近 5 条
python examples/read_feeds.py 123456789     # 读指定号（需 policy 放行）
python examples/feed_detail.py <tid>        # 读单条 + 评论
python examples/post_and_delete.py          # 写操作演示，默认 dry-run
```

## API

```python
auth(call)                          -> (uin, bkn, cookie)      # 凭证，只在内存过手
check_target(policy, target, self)  -> None                    # 抛 QzoneError 表示越界
list_feeds(call, target="", pos=0, num=20, policy=None) -> dict
feed_detail(call, tid, target="", policy=None, max_comments=20) -> dict
list_blogs(call, target="", pos=0, num=10, policy=None) -> dict
blog_detail(call, blog_id, target="", policy=None, max_comments=20) -> dict
probe(call, policy=None)            -> dict                    # 健康检查，不吐正文
```

`call` 是任何形如 `call(action, params) -> (ok, data, error)` 的可调用对象。
`onebot.py` 里的 `OneBotHTTP` 就是现成实现，也方便你换成 WebSocket 通道。

### 读取边界

默认策略 `DEFAULT_POLICY` 只允许读**自己**：

```python
{"enabled": True, "scope": "self", "whitelist": [], "max_items": 20, "max_pages": 1}
```

- `scope="self"` 只读自己
- `scope="whitelist"` 自己 + `whitelist` 里的号
- `scope="any"` 不限制（不建议，接口调用方自己负责）
- `max_items` 同时是单次 `num` 的硬上限

越过策略会抛 `QzoneError`，而不是静默返回空。

### 写操作

本库**不提供写函数**。发说说／删说说是对外可见的动作，应该由能向用户确认的上一层来做。
NapCat 自带这两个动作，参数如下，可直接调：

| 动作 | 参数 |
|---|---|
| `send_qzone_msg` | `content` 必填；`images[]`（`file://` / `http(s)://` / `base64://`）；`ugc_right`；`target_uins[]` |
| `delete_qzone_msg` | `tid` |

`ugc_right`：`1` 所有人可见 · `4` 好友可见 · `16` 部分好友可见 · `64` 仅自己可见 · `128` 部分好友不可见
（`16` / `128` 必须给 `target_uins`）。`send_qzone_msg` 返回 `{tid}`。

`examples/post_and_delete.py` 演示了安全写法：默认 `ugc_right=64` 且不带 `--yes` 不发布，
发完自检再删除。

## 能力边界（均为实测结论）

### ✅ 已验证

| 能力 | 说明 |
|---|---|
| 读说说列表 | `total` 与实际条数一致，`pos`/`num` 翻页两页 `tid` 无重叠 |
| 读单条详情 | 一次拿到正文、图片、评论（单独拉评论的接口返回 500，别用） |
| 图片 URL | 纯图说说 `content` 为空，图在 `pics`；URL 需带 `Referer` 才下得动 |
| 发说说 / 删说说 | 发→`tid`→列表可见→删→消失，闭环验证 |
| 带图发说说 | `base64://` 多图正常，图片真落盘（HTTP 200 + `image/png`） |

### ✅ 日志（blog，已实机验证）

QQ 空间的「日志」是长文内容类型，与说说（taotao）走完全不同的接口族
（`b.qzone.qq.com/cgi-bin/blognew/*`），NapCat 本身没有任何日志能力，本库直连 cgi 补齐：

| 函数 | 接口 | 说明 |
|---|---|---|
| `list_blogs()` | `blognew/get_abs` | 列表：`data.list[]` 实测字段 `blogId / title / pubTime / cate / cateHex / commentNum / ar / arUins / artype`；参数口径与说说相反——`uin`=登录者、`hostUin`=空间主人 |
| `blog_detail()` | `blognew/blog_output_data` + `blognew/get_comment_list` | 正文两种响应形态都处理（JSON 里 `data.html` 字符串 / 整页 HTML `#blogDetailDiv`）；评论 `topicId="<hostUin>_<blogId>"` |

实机验证（2026-10-31，真实账号 + 手写日志）：
- 列表/详情/评论/翻页全部读通，正文与图片完整取出。
- **时间字段是 `pubTime`（大写 T）**，值为 `"2026-10-31 18:47"` 格式化字符串，**不是 unix 秒**。
- 评论字段实测为 `U`（昵称，带尾空格）/ `uin` / `content` / `pubtime`（这里是 unix 秒）；
  总数在 `data.total`（注意不是列表接口的 `totalNum`）。
- **日志里的图片下载不需要 `Referer`**（与说说图片不同：带与不带都 200 + `image/jpeg`）。
- 坏 `blogId` 时上游返回错误页（含 `errorTitle`、无 `blogDetailDiv`），已识别并抛错，
  不再静默返回空对象。

### ❌ 发表日志（不具备，已实证排除）

想给日志也补上写入能力，实测不可行，结论留档避免重复踩：

1. `blognew/blog_add`（老 v5 编辑器用的那个）**已下线**——经代理返回 404。
2. `blognew/add_blog` 存在但带未公开校验层：
   - `g_tk` 必须放 **query**（放 body 或缺失一律 HTTP 500）；
   - 放对后回业务码 **`-4003`**（user / h5 两个代理入口一致，直连 `b./b1./br./blog.qzone.qq.com` 全是 404 页）；
   - 按官方 `common.js` 反解出写操作要求的 `secverifykey = "28Q1206" + 登录Uin + "_" + 空间Uin`，
     补上仍 `-4003`；改 GET 全 query 得 `-4000`。参数组合、编码（form/JSON/multipart）都试过，恒拒。
3. 官方编辑器（`qzs.qq.com/qzone/newblog/v5/editor.html`）在无头 Chrome 里富文本组件不初始化
   （`#blog-content-text` 为 `display:none` 的隐藏字段，编辑器 iframe 从未创建，页面停在「请稍侯···」），
   抓不到真实提交包。
4. 旁证：主流开源实现（`qzone_api` / `Maizone` / `intchensc/qzone`）同样只支持说说，无日志发布。

结论：**日志发布不实现**。需要发长文时，走人工编辑器。

### ⚠️ 接口可达但未验证字段结构

- 相册列表（`fgc_list_album_v3`）、留言板（`new/get_msgb`）：`code=0`，但测试账号无数据。

### ❌ 不可用

- 好友动态聚合流（`feeds3_html_more`）：恒返回 `hasMoreFeeds:false`，且响应是带 HTML
  片段的 JS 字面量（键名无引号、混单引号），标准 JSON 与补引号都解析失败。
  **替代方案**：`get_friend_list` 拿好友号，逐个 `list_feeds` 后按时间归并。

### 两个容易踩的坑

1. **`secret` 不能反推权限档**。`secret=1` 是「仅自己可见」，但 `secret=0` 只代表
   「非私密」——可能是所有人可见，也可能是好友可见。别拿它当权限判断依据。
2. **正文为空 ≠ 空条目**。纯图片说说 `content` 就是空串。

## 风险与合规

- 用的是空间**网页端私有接口**（`cgi-bin/*`），不是公开 API，随时可能改版。所有字段解析
  都应容忍缺失，不要让单个字段消失导致整体崩。
- **只读当前登录身份本来就能看到的内容**。这不是绕过权限，私密说说、陌生人空间一样读不到。
- 高频或批量读取有风控风险。批量遍历好友空间是风险最高的用法，务必限流。
- 凭证（`p_skey` / `bkn`）随会话变化，不要落盘、不要进日志、不要跨会话缓存。
- 请自行确认使用场景符合 QQ 用户协议；本项目不对账号风险负责。

## 目录

```
napcat-qzone-toolkit/
├── qzone.py                 核心：凭证获取 + 说说列表 / 详情 + 日志列表 / 详情 + 健康检查
├── onebot.py                最小 OneBot 11 HTTP 调用器
├── examples/
│   ├── read_feeds.py        读说说时间线
│   ├── feed_detail.py       读单条说说 + 评论
│   ├── read_blogs.py        读日志列表 / 单篇日志
│   └── post_and_delete.py   写操作演示（默认 dry-run）
├── tests/
│   └── test_blog_offline.py 日志解析离线单测（无网络、无 NapCat 可跑）
├── docs/
│   ├── findings.md          实测记录：能力矩阵、接口清单、失败模式
│   └── napcat-notes.md      接入 NapCat 的几条硬经验（配置生效、进程清理、CDP 限制）
├── LICENSE
└── README.md
```

## License

MIT — 见 [LICENSE](LICENSE)。

---

## English summary

NapCat can post to and delete from QQ Zone, but it cannot read a user's own timeline —
the only read endpoints it exposes cover a public recommendation stream. This library
fills that gap by reusing NapCat's own session: the `get_cookies` action returns the
`qzone.qq.com` cookies together with a correctly derived `bkn` (derived from `p_skey`,
not the generic skey), which is enough to call Qzone's web cgi endpoints directly.

No CDP and no UI automation are involved, and none are possible here: NapCat's injector
hard-codes the QQ launch line as `"<QQPath>" --enable-logging` and drops extra argv,
so the injected client cannot be started with `--remote-debugging-port`.

Provided: `probe()`, `list_feeds()` (offset pagination), `feed_detail()` (content +
images + comments in one request), and a scope policy that defaults to reading only
your own timeline. Publishing is intentionally left out of the library; see
`examples/post_and_delete.py` for a guarded write demo.

MIT licensed.

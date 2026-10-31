# 实测记录：QQ 空间能力边界

测试环境：Windows + QQ NT 桌面客户端（已登录）+ NapCat 4.18.x（Shell 版，注入模式）。
所有「已实测」结论都在真实账号上跑过；未实测的项目明确标注，不要当既成事实。

## 测试方法

1. NapCat WebUI 里临时启用一个 HTTP 服务器（`network.httpServers`）
2. 调 `get_cookies`（`domain=qzone.qq.com`）取 `cookies` + `bkn`
3. 带 `Cookie` 头直接请求空间 cgi，判据是 `code == 0`
4. 测完把 HTTP 服务器关掉，凭证不落盘

## 结论汇总

| 能力 | 状态 | 依据 |
|---|---|---|
| 读说说列表 | ✅ | `total` 与实际条数一致；`pos`/`num` 翻页两页 `tid` 零重叠 |
| 读单条详情 | ✅ | `code=0`，含 `content` / `conlist` / `commentlist` / `createTime` |
| 读评论 | ✅ | 走详情接口的 `commentlist`，一次带回 |
| 图片 URL | ✅ | 纯图说说取到 9 个 URL；带 `Referer` 的 GET 返回 200 + `image/png` |
| 读日志列表 | ✅ | `get_abs` 实测字段齐全；时间字段是 `pubTime`（大写 T，格式化字符串） |
| 读日志正文/评论 | ✅ | `blog_output_data` 线上走整页 HTML 形态，正文完整；评论 `data.total` + `U/uin/content/pubtime` |
| 日志图片下载 | ✅ | 带/不带 `Referer` 均 200 + `image/jpeg`（**与说说相反**） |
| 发表日志 | ❌ | `blog_add` 已 404；`add_blog` 恒 `-4003`，详见文末「日志发布」 |
| 相册列表 | ⚠️ 接口可达 | `code=0`，测试账号无相册，字段结构未验证 |
| 留言板 | ⚠️ 接口可达 | `code=0`，`commentList` 存在但为空 |
| 好友动态聚合流 | ❌ | 恒 `hasMoreFeeds:false` + 非法 JSON（见下） |
| 单独拉评论（说说） | ❌ | `emotion_cgi_getcmtbyparentid` 返回 HTTP 500 |
| 发说说 | ✅ | 返回 `tid`，列表可见，删除后消失 |
| 带图发说说 | ✅ | `base64://` 多图，详情 `pics` 数量正确，图片可下载 |
| 删说说 | ✅ | 按 `tid` 删除，列表 `total` 回落 |
| 点赞 / 评论 / 转发（说说本身） | ❓ 未测 | NapCat 只有「空友爱看」那套的互动动作 |

## 接口清单

通用请求头：

```
Cookie: <get_cookies 返回的 cookies>
User-Agent: Mozilla/5.0 ... Chrome/120
Referer: https://user.qzone.qq.com/
```

`g_tk` 直接用 `get_cookies` 返回的 `bkn`，不要用 `skey` 自己算 —— NapCat 对
`qzone.qq.com` 专门用 `p_skey` 派生，算法不同。
### 说说列表

```
GET https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6
    ?uin=<uin>&ftype=0&sort=0&pos=<偏移>&num=<条数>&replynum=0
    &g_tk=<bkn>&code_version=1&format=json
```

返回 `{code, msglist[], total, ...}`。

### 单条详情

```
GET https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6
    ?uin=<uin>&tid=<tid>&g_tk=<bkn>&format=json
```

返回含 `content` / `createTime` / `conlist` / `commentlist` / `cmtnum`。

### 日志列表（blognew/get_abs，✅ 已实机验证）

```
GET https://user.qzone.qq.com/proxy/domain/b.qzone.qq.com/cgi-bin/blognew/get_abs
    ?uin=<登录者>&hostUin=<空间主人>&blogType=0&reqInfo=1
    &pos=<偏移>&num=<条数>&sortType=0&ref=qzone
    &inCharset=utf-8&outCharset=utf-8&format=jsonp&g_tk=<bkn>
```

返回 `{code, data:{list[], totalNum}}`；`list[]` 条目含 `blogId / title / pubtime /
cate / commentNum / readNum`。

**参数口径与说说相反**：这里 `uin` 是登录者（访问者），`hostUin` 是被看的空间主人。
备用代理入口：`https://h5.qzone.qq.com/proxy/domain/b.qzone.qq.com/...`（两个都实现，
第一个失败自动换第二个）。

### 日志正文（blognew/blog_output_data，✅ 已实机验证）

```
GET https://user.qzone.qq.com/proxy/domain/b.qzone.qq.com/cgi-bin/blognew/blog_output_data
    ?uin=<空间主人>&blogid=<blogId>&numperpage=20
    &inCharset=utf-8&outCharset=utf-8&ref=qzone&format=jsonp&g_tk=<bkn>
```

两种已知响应形态（都处理）：
1. JSON(P)：正文富文本 HTML 在 `data.html`（或 `data.content`）字符串里；
2. 整页 HTML：正文在 `<div id="blogDetailDiv">` 内（按 div 深度计数截取，
   页面其它区块如侧栏不会被误收）。

注意这里 `uin` 又变回「空间主人」，与列表接口相反；`blogid` 不是 `tid`。

### 日志评论（blognew/get_comment_list，✅ 已实机验证）

```
GET https://user.qzone.qq.com/proxy/domain/b.qzone.qq.com/cgi-bin/blognew/get_comment_list
    ?uin=<空间主人>&topicId=<空间主人>_<blogId>&start=<偏移>&num=<条数>
    &inCharset=utf-8&outCharset=utf-8&format=jsonp&g_tk=<bkn>
```

返回 `data.comments[]`。字段名（`U`/`uin`/`content`/`pubtime`）来自社区开源实现
（QzoneExporter 等），**本库容错解析**（多别名映射），实机验证前不要当定论。
历史资料里该接口常用 gb2312 编码；本实现先发 utf-8 参数，若实机乱码需改回 gb2312。

### 相册列表

```
GET https://user.qzone.qq.com/proxy/domain/photo.qzone.qq.com/fcgi-bin/fcg_list_album_v3
    ?g_tk=<bkn>&t=<秒级时间戳>&hostUin=<uin>&uin=<uin>&appid=4
    &inCharset=utf-8&outCharset=utf-8&source=qzone&plat=qzone&format=json
    &notice=0&filter=1&handset=4&pageNumModeSort=40&pageNumModeClass=40
    &needUserInfo=1&idcNum=4
```

### 留言板

```
GET https://user.qzone.qq.com/proxy/domain/m.qzone.qq.com/cgi-bin/new/get_msgb
    ?uin=<uin>&hostUin=<uin>&num=<条数>&start=<偏移>&g_tk=<bkn>&format=json
```

返回 `data.commentList[]`、`data.total`。

## 字段语义（实测）

### `secret`

| 值 | 含义 |
|---|---|
| `0` | 非私密 |
| `1` | 仅自己可见（用 `ugc_right=64` 发帖后观察到） |

**注意**：`secret=0` **不等于**「所有人可见」。用 `ugc_right=4`（好友可见）发的帖子，
`secret` 同样是 `0`。所以 `secret` 只能用来区分「私密 / 非私密」，**不能反推权限档位**。

### `content`

纯图片说说 `content` 是空字符串，内容在 `pic` / `pics`。判空正文不等于空条目。

### 图片 URL

`photogzmaz.photo.store.qq.com/psc?...` 这类 URL 用 HEAD 请求会被拒；
带 `Referer: https://user.qzone.qq.com/` 的 GET 才能拿到（200 + `image/png`）。
所以做「图片是否存在」的探活不能用 HEAD。

## 失败案例

### 好友动态聚合流 `feeds3_html_more`

```
GET https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more
    ?uin=<uin>&scope=0&view=1&filter=1&flag=1&applist=all&pagenum=1&g_tk=<bkn>&format=json
```

- 返回 `code: 0`，但 `data.main.hasMoreFeeds` 恒为 `false`
- 响应体是 JS 对象字面量：键名不带引号，字符串里混着单引号和 HTML 片段，
  标准 `json.loads` 和「给裸键补引号」都解析失败

结论：**放弃该接口**。要「好友动态」效果，改用
`get_friend_list` → 逐个 `msglist_v6` → 按 `created_time` 归并。

### 单独拉评论

`emotion_cgi_getcmtbyparentid` 返回 HTTP 500，改用 `msgdetail_v6` 的 `commentlist`。

## 未验证

- 私密说说对非本人的可见性（本次测试只覆盖公开与好友可见）
- 相册 / 留言板的具体字段结构（测试账号无数据）
- 说说本身的点赞 / 评论 / 转发接口
- 大规模翻页时的频率限制阈值

## 日志（blog）实机验证结果（2026-10-31 完成）

测试条件：NapCat 在线（登录号 3789****70），测试前由人工发布了《测试日志1》（纯文字）、
《测试日志2（图片）》（含 1 张图），并各留 1 条评论。

| 项目 | 结果 | 证据 |
|---|---|---|
| `get_abs` 列表字段 | ✅ 实测字段：`blogId / title / pubTime / cate / cateHex / commentNum / blogType / ar / arUins / artype / block / appeal / effect1 / effect2 / inproc` | `tests/blog_live_list.json`、`tests/blog_live_round3.json` |
| 时间字段 | ⚠️ **是 `pubTime`（大写 T）**，值为 `"2026-10-31 18:47"` 字符串，**不是** unix 秒 | 同上 |
| `blog_output_data` 形态 | ✅ 线上走**整页 HTML**（正文在 `#blogDetailDiv`）；JSON 片段的解析分支保留兼容 | 正文「测试文本1」完整取出 |
| 评论字段 | ✅ 实测 `U`（昵称，尾带空格）/ `uin` / `content` / `pubtime`（unix 秒） | 两篇各 1 条评论均解析正确 |
| 评论总数键 | ⚠️ 是 `data.total`，**不是** `totalNum`（列表接口才是 `totalNum`） | 已修 `blog_detail` 读取顺序 |
| 图片下载 | ✅ 日志图片**不需要 `Referer`**（带/不带都 200 + `image/jpeg`，JPEG magic `FF D8 FF E0`） | 与说说图片结论相反 |
| 翻页 | ✅ `pos=1` 正常 | `tests/blog_live_list_p2.json` |
| 权限边界 | ✅ 陌生 target 被本地策略拦截；无权限好友空间回 `code=-4403` 并抛错 | 首轮实测 |
| 坏 `blogId` | ✅ 上游返回错误页（`errorTitle`、无 `blogDetailDiv`），库抛明确异常 | 已修，不再静默返回空对象 |

## 日志发布（不具备，实证排除）

1. `blognew/blog_add` → 代理 404，**已下线**。
2. `blognew/add_blog` → 存在，但：
   - `g_tk` 必须在 query（放 body / 缺失 → HTTP 500）；
   - 正确 g_tk 下恒回 `code=-4003`（user / h5 两代理一致）；
   - 补 `secverifykey = "28Q1206" + 登录Uin + "_" + 空间Uin`（从官方 `newblog/v5/script/common.js`
     反解）后仍 `-4003`；GET 全 query 为 `-4000`；form / JSON / multipart 三种编码都试过；
   - 直连 `b. / b1. / br. / blog.qzone.qq.com` 全部落到同一个 554 字节 404 页。
3. 官方编辑器 `qzs.qq.com/qzone/newblog/v5/editor.html` 在无头 Chrome 中富文本组件不初始化
   （`#blog-content-text` 为 `display:none`，编辑器 iframe 未创建，页面停「请稍侯···」），
   因此抓不到真实提交参数。CDP 探针脚本留在 `tests/cdp_editor.js` / `tests/cdp_probe.js`，
   人工在真浏览器里发布时可用它抓包续查。

测试方法同「测试方法」节：临时开 HTTP 服务器 → `get_cookies` → 带 Cookie 直调 cgi。
离线回归：`python tests/test_blog_offline.py`（9 项，无网络可跑）。

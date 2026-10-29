# 接入 NapCat 时踩过的几条硬经验

跟空间无关，但都是在真机上碰出来的，写下来省得别人重走一遍。

## 1. 改 `onebot11_<QQ号>.json` 不会热生效

NapCat 的配置由 `ConfigBase` 在**构造时**读一次，全仓库没有 `fs.watch`
（`packages/napcat-core/helper/config-base.ts`）。所以直接改磁盘文件，
运行中的实例不会重新加载 —— 必须重启。

实测过：写完 `network.websocketClients` 后等 20 秒以上，连接数仍是 0。

### 想要热生效，走 WebUI 接口

```
POST /api/OB11Config/SetConfig    body: {"config": "<完整配置的 JSON 字符串>"}
```

内部是 `save + reloadNetwork`，实测约 3 秒生效，不用重启、不掉登录。
注意要提交**整份**配置而不是增量：先 `POST /api/OB11Config/GetConfig` 取当前配置，
改完再整份提交。

为什么值得走这条路：**重启 NapCat 会掉登录**，重新登录还可能撞上
「当前账号已登录，无法重复登录」，一晚能白折腾掉半小时。

## 2. WebUI 的鉴权不是直传 token

```
POST /api/auth/login    body: {"hash": sha256(token + ".napcat")}
→ {"data": {"Credential": "<base64 凭证>"}}
```

之后所有请求带 `Authorization: Bearer <Credential>`。直接传原始 token 会返回
`token is invalid`。凭证有效期约 1 小时。

## 3. 注入器不透传启动参数，所以被注入的 QQ 开不了 CDP

`NapCatWinBootMain.exe` 内部把启动命令写死成：

```
"<QQ 可执行文件>" --enable-logging
```

验证办法（零风险，不会真的启动 QQ）：把 QQ 路径换成一个不存在的路径，再额外传几个
参数，看它打印的 `Boot Command`：

```
> NapCatWinBootMain.exe "X:\no_such_dir\QQ.exe" hook.dll --remote-debugging-port=9222
argv[3]:--remote-debugging-port=9222
Boot Command:"X:\no_such_dir\QQ.exe" --enable-logging      ← 我们的参数没进去
```

结论：想要 CDP，只能自己用 `--remote-debugging-port` 启动 QQ（不经过 NapCat 注入），
而同一个 QQ 号不能同时挂在两个实例上 —— 所以要 CDP 就得换一个号。

顺带一个坑：QQ 自带一个 9211 端口的 JWT 服务，探测 CDP 时别被它骗了，
要请求 `/json/version` 并检查返回里的 `Browser` 字段。

## 4. 停实例必须递归杀进程树，并按基线验收

QQ 是 Electron 多层进程树：注入器 → QQ 主进程 → 若干渲染进程
（命令行带 `--mojo-platform-channel-handle`）。

只杀「注入器 + 它的直接子进程」会留下渲染进程孤儿。它们父进程已死却仍在运行，
继续持有账号会话，于是之后每次快速登录都报
`当前账号已登录，无法重复登录` —— 而且看起来像「账号问题」，很容易查错方向。

正确做法：

```powershell
# 1) 启动前记基线
(Get-CimInstance Win32_Process -Filter "Name='QQ.exe'").Count

# 2) 从注入器 PID 出发递归展开子孙，全部杀掉
# 3) 验收：进程数回到基线，且父进程已死的孤儿数为 0
```

`napcat-bridge-ops` 那个 skill 里的 `scripts/qq_process_tree.ps1` 就是照这个写的，
支持 `list` / `stop` / `verify` 三个动作。**验收标准是「回到基线 + 孤儿为 0」，
不是「命令没报错」。**

> 绝对不要用 `taskkill /im QQ.exe` —— 那会把用户自己正在用的 QQ 一起杀掉。

## 5. `get_cookies` 对 `qzone.qq.com` 有特例

`packages/napcat-onebot/action/user/GetCookies.ts` 里有个分支：当 `domain` 含
`qzone.qq.com` 时，`bkn` 用 **`p_skey`** 派生；其它域名用 `skey`。

两种算法结果不同。空间接口认的是用 `p_skey` 算出来的那个，
所以**直接用返回值，不要自己拿 `skey` 重算**。

## 6. NapCat 的 QQ 版本适配比想象中宽松

`qqnt.json` 里声明的版本号只是补丁包模板。运行时 NapCat 会去读真实的
`<QQ 安装目录>/versions/<实际版本>/resources/app/package.json`
（`packages/napcat-common/src/helper.ts` 的 `getQQPackageInfoPath`）。

所以「NapCat 声明的版本和本机 QQ 版本差一个段位」不代表不兼容 ——
以启动日志里的 `load from: ...QQNT.dll` 是否指向真实版本为准。

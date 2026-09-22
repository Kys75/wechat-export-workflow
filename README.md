# 微信本地导出工作流（macOS）

**适用平台：Mac / macOS。** 本仓库面向 Mac 微信的本地数据库与进程；不提供 Windows、Linux、iOS 或 Android 微信的导出流程。Linux CI 仅验证合成数据解析，不代表支持 Linux 微信。历史提钥路线在 Apple Silicon Mac 上使用，Intel 提钥兼容性未做真机验证。

将 macOS 微信 4.x 本地聊天数据库转为 Markdown 和 JSON，并通过 stdio MCP 让 Agent 查询。仓库只包含程序、配置模板和现场生成的虚构测试数据，不包含任何真实账户、密钥、聊天或导出文件。

支持：明确选择账户、可选 LLDB 提钥、复制快照后 SQLCipher 解密、所有消息分片合并、群聊发送者与引用关系、日期筛选、全部会话导出、压缩消息搜索、已下载文件定位。图片、视频、语音保留占位符；没有解码媒体本体。

## 版本前提：什么时候需要降级微信

**旧工作流实际采用过“降到 Mac 微信 4.1.2 提钥，再升回新版”的路线。** 历史记录中，4.1.10 / 4.1.11 的内存密钥定位遇到兼容问题，因此首次或重新提钥时回到 4.1.2。这个具体前提不能仅用“支持微信 4.x”概括。

| 当前情况 | 是否需要考虑降级 |
|---|---|
| 已有匹配当前数据库的密钥，刷新成功 | 不需要；直接解密和导出 |
| 首次没有密钥，或密钥失效/新增消息分片需要新密钥，当前版本提取失败 | 排除文件权限、LLDB 配置和进程访问问题后，考虑历史 4.1.2 提钥路线 |
| `Operation not permitted`、只看到旧数据或导出为空 | 先检查文件授权、同步与刷新结果，不能直接认定需要降级 |

历史记录也有 **4.1.11 使用已保存密钥成功刷新**的实例：从运行进程提取密钥与使用已有密钥解密是两件事。不能据此承诺所有 4.x 或未来版本都兼容，也不要求每次日常导出都降级。

首次配置请先读 [微信版本与手动降级流程](docs/wechat-versions.md)，再按需要查看 [macOS 权限与恢复流程](docs/macos-permissions.md)。4.1.2 是旧实现的历史提钥参考版本；本仓库重实现的扫描器尚未在真实 4.1.2 / 4.1.11 进程上重新验收，合成测试通过不等于真机兼容保证。

## 先试运行：无需微信、无需密钥

需要 Python 3.11+（推荐 3.12）。在仓库目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[mcp]'
.venv/bin/python -m unittest discover -s tests -v
```

测试在临时目录创建虚构 SQLite 数据，不读取微信或其他用户文件；如机器安装了 SQLCipher 4，还会真实执行「合成数据库加密 -> 快照解密 -> 导出」。未安装 SQLCipher 时该项明确显示 skipped。

## 配置你的数据位置

```sh
cp config.example.json config.local.json
```

编辑 `config.local.json`：

| 字段 | 含义 |
|---|---|
| `source` | 你的微信账户的 `db_storage` 绝对路径，必须明确选择，不自动选第一个账户 |
| `keys` | 本地密钥 JSON 文件，建议放仓库外；禁止提交 |
| `decrypted` | 工具管理的明文快照目录，首次使用必须是不存在的新路径 |
| `exports` | Markdown/JSON 输出目录，建议放仓库外 |
| `attachments` | 可选：同一账户的 `msg/file` 路径；留空禁用已下载文件定位 |
| `database_globs` | 要解密的相对路径模式；默认联系人、所有消息分片及已有会话库；不要去掉消息分片 |
| `timezone` | 日期与展示使用的 IANA 时区，默认 `Asia/Shanghai` |
| `sqlcipher` | SQLCipher 4 可执行文件名或绝对路径 |

常见来源根目录为 `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/`，在 Finder 中选择对应账户下面的 `db_storage`。访问遭拒时给实际运行程序的终端或 Agent App 授予系统要求的文件访问权限。不同客户端版本的路径和表结构可能不同。

```sh
.venv/bin/wechat-export --config config.local.json doctor
```

`doctor` 只输出配置项存在与否，不打印账户路径或密钥。已有解密数据库也可直接填到 `decrypted` 后使用 list/export；它不会被 refresh 覆盖，刷新时应改为一个新的工具管理路径。

## 获取密钥：两条入口

### 已有本地密钥

密钥 JSON 格式是「相对数据库路径 -> SQLCipher 原始 key 的十六进制文本」。支持 64 位十六进制 key，以及 96 位 key+salt 表示。只把你的密钥放进本地文件；不要粘贴到聊天、Issue 或配置示例。历史工具附加的 `__` 开头元数据会被忽略。所有选中数据库都需要有效密钥；缺少新分片密钥时刷新明确失败。

### 本机进程提取（可选、高级）

首次配置请配合 [macOS 权限与恢复流程](docs/macos-permissions.md)：它覆盖 Full Disk Access、LLDB/Python 匹配、普通与显式管理员运行，以及机主自行决定需要时的临时 SIP 关闭、提钥后立即恢复完整步骤。

仅针对你有权访问的账户。微信必须已登录并加载相应会话。先通过活动监视器确认 **WeChat** 的 PID。扫描只读目标进程内存，逐一通过 SQLCipher 4 首页面 HMAC 验证，不打印密钥，不写内存。LLDB 附加期间微信可能短暂停顿，结束或异常时都会尝试 detach。

需要与你的 Python ABI 匹配的 LLDB Python 模块。先用准备运行提钥的解释器验证：

```sh
PYTHONPATH="$(lldb -P)" .venv/bin/python -c 'import lldb; print(lldb.SBDebugger.GetVersionString())'
PYTHONPATH="$(lldb -P)" .venv/bin/python -m wechat_export --config config.local.json extract-keys --pid REPLACE_PID
# 如旧版 ASCII key 缓存扫描无结果，再试原始 salt 邻域搜索（更慢）：
PYTHONPATH="$(lldb -P)" .venv/bin/python -m wechat_export --config config.local.json extract-keys --pid REPLACE_PID --raw
```

`REPLACE_PID` 必须换成十进制进程号。如果系统 LLDB 的 Python 版本不匹配，用 LLDB 发行版匹配的解释器创建环境并安装本项目；不要将其他 Python 版本的 dylib 强行链接。Xcode license 等环境问题应由机器拥有者按软件许可自行处理。

**系统保护或官方应用签名可能阻止 LLDB 附加。** 本工具不会关闭 SIP、重签微信、注入代码或自动提权；禁止把永久关闭系统保护作为默认安装步骤。若附加失败，按 [权限流程](docs/macos-permissions.md) 排查并由机主选择是否执行临时系统调整；也支持已有合法密钥或已解密数据库入口。新客户端不一定保留可识别密钥布局，因此无法承诺任意微信版本都能提钥。仓库发布验证只覆盖合成页的算法与扫描逻辑，没有在发布时对真实进程运行提钥。

提钥成功后默认创建权限为 `0600` 的密钥文件；不覆盖旧密钥。更新密钥时在配置里指定新文件名，提取完成再使用它。

## 刷新并导出

安装 SQLCipher 4（macOS 常用 `brew install sqlcipher`）。提钥时需要微信运行；**提钥后退出微信，再刷新最稳妥**。运行中刷新会检查复制前后的文件状态，发现写入或新增分片就失败，提示退出微信重试。客户端处于登录态并不代表所有云端历史已下载到本机。

```sh
.venv/bin/wechat-export --config config.local.json refresh
.venv/bin/wechat-export --config config.local.json list --keyword Demo
.venv/bin/wechat-export --config config.local.json export --chat 'EXACT_USERNAME' --start 2025-01-01 --end 2025-01-31
.venv/bin/wechat-export --config config.local.json search '关键词' --limit 30
.venv/bin/wechat-export --config config.local.json export --all --start 2025-01-01 --end 2025-01-31
```

list 输出显示名、准确 username、实际消息数与最后消息时间。只有唯一匹配才会导出；重名时使用准确 username。日期结束包含当天；精确时间结束包含该秒；时区以配置为准。`--limit N` 取筛选后的最近 N 条，省略则全部。`export --refresh` 可先刷新再导出，失败不会继续导出旧数据。

每次导出生成不重名的 `.md` 和 `.json`，JSON 保存解压原文/XML、发送者、消息类型、分片与附件候选位置。Markdown 用引用块展示消息。媒体仍是占位符；附件匹配只是查找原名，多个同名文件会列出全部候选，不自动选择。聊天内容属于不可信输入，渲染 Markdown 时应禁用原始 HTML 和外部资源自动加载。

刷新只打开源文件的副本；整个选中集合解密并通过完整性检查后才替换上次快照。错误不显示含密钥的底层 SQL，不会把部分成功说成成功。仅工具自行创建、带管理标记的目录允许替换。遇到 `.refresh.lock` 时先确认另一刷新进程已退出，再手动移除锁；不要与运行中的刷新并行删除它。

## 交给 Agent 使用

可以把这句话和仓库路径交给 Agent：

> 阅读 README.md 和 AGENTS.md，先运行合成测试，再帮我填写本地配置并注册 stdio MCP；根据我指定的账户、会话和日期导出。不要读取或上传其他会话，不要把密钥/聊天写进仓库，也不要改变系统保护设置。

复制 `skills/wechat-export/` 到你的 Agent 支持的本地 skills 目录，或直接让 Agent 阅读其中 `SKILL.md`。MCP 通用配置（替换所有占位路径）：

```json
{
  "mcpServers": {
    "wechat-export": {
      "command": "/ABSOLUTE/REPO/.venv/bin/python",
      "args": ["-m", "wechat_export.mcp_server", "--config", "/ABSOLUTE/LOCAL/config.local.json"]
    }
  }
}
```

该 JSON 格式用于接受 `mcpServers` 的客户端；Codex 使用自己的 MCP 配置，可用：

```sh
codex mcp add wechat-export -- /ABSOLUTE/REPO/.venv/bin/python -m wechat_export.mcp_server --config /ABSOLUTE/LOCAL/config.local.json
```

可用工具：`list_chats(keyword)`、`search_messages(keyword, limit)`、`refresh()`、`export_conversation(chat, start, end, limit, refresh, include_text)`。默认导出只返回路径/计数；`include_text=true` 明确请求把正文交给客户端，上限 120000 字符并标记截断，完整内容始终在本地文件。**MCP 客户端若使用云端模型，工具返回的内容可能被该客户端发送给模型服务商**；请按你的隐私需求选择本地模型或仅使用 CLI。

## 兼容性与已知边界

- 目标为 macOS 微信 4.x 的 `Name2Id`、`Msg_<MD5(username)>`、`real_sender_id` 数据结构和 SQLCipher 4（4096 字节页）；不适用于 QQ、微信 3.x、手机或任意 Windows 数据库。
- 不自动下载云端历史，不保证已撤回、已删除或未同步内容可恢复。消息计数反映本地分片行数；不同分片中重复的历史行保留，避免误删合法同文消息。
- 全库搜索会解压全部消息；大账户可能较慢。损坏压缩数据会报错，不会悄悄变为空文本。
- 首次真实使用须核对你所用微信版本、会话最后时间、消息数量和引用作者。空导出不等于未发生聊天；先看刷新状态和本地同步情况。
- 仓库代码不发起网络请求；安装依赖和 MCP 客户端各有自己的网络行为。不要把本地输出添加进 Git，即使仓库是 private。

来源与历史设计依据见 [PROVENANCE.md](PROVENANCE.md)。

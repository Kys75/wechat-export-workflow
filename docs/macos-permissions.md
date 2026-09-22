# macOS 首次提钥：权限与恢复流程

本文用于**你自己的 Mac 和你获准访问的微信账户**。目的是从首次安装走到可用的本地密钥，覆盖文件权限、LLDB 环境、显式管理员运行，以及必要时由机主手动执行的临时 SIP 调整。日常解密、导出和 MCP 不需要管理员运行。

先完成 README 中的安装、合成测试和 `config.local.json` 配置。本文不要求你已经持有密钥，也不会自动改变系统配置。应用版本、签名和内存布局仍可能让提取失败；关闭 SIP 并不保证成功。

版本选择另见 [微信版本与手动降级流程](wechat-versions.md)：旧工作流曾用 4.1.2 重新提钥，已有有效密钥的日常刷新不需要降级。权限调整不能代替版本兼容性判断。

## 1. 先区分失败发生在哪里

| 现象 | 首先处理 |
|---|---|
| 无法列出或读取 `db_storage`，`Operation not permitted` | 下面的 Full Disk Access |
| `No module named lldb`、动态库/架构/ABI 加载失败 | LLDB 与 Python 匹配 |
| 已找到数据库，但 `LLDB attach denied` | 确认 PID、普通运行权限；必要时显式 sudo；系统仍拒绝时再考虑第 4 节 |
| 已成功附加，但发现 0 个或部分密钥 | 微信登录与会话加载、`--raw`、客户端版本/内存布局；不是直接继续降低系统保护 |
| `Xcode license`、`invalid active developer path` | 由机主完成 Xcode/Command Line Tools 的安装和许可设置 |

### Full Disk Access（完全磁盘访问权限）

1. 打开“系统设置 → 隐私与安全性 → 完全磁盘访问权限”。较旧系统叫“系统偏好设置 → 安全性与隐私 → 隐私”。
2. 添加并开启**实际发起操作的应用**，例如 Terminal、iTerm，或运行本工作流的 Agent App；给另一个终端授权并不会自动授权当前应用。
3. 如果系统要求，使用机主的管理员认证。完全退出该应用后重新打开，让新权限生效。
4. 重新运行 README 的 `doctor`，再运行明确指定账户的提钥命令。系统设置和文件布局随版本可能变化。

`sudo` 不自动绕过 macOS 的 TCC 文件访问控制。能读取数据库也不等于可以调试微信进程，二者是不同权限。

## 2. 确认 LLDB 与 Python 能一起运行

在普通终端中先检查准备使用的工具链：

```sh
xcode-select -p
lldb --version
lldb -P
.venv/bin/python --version
PYTHONPATH="$(lldb -P)" .venv/bin/python -c 'import lldb; print(lldb.SBDebugger.GetVersionString())'
```

最后一条必须先成功。`lldb -P` 输出 Python 绑定位置，**不会把不兼容的 Python 变成兼容版本**。如果导入提示 CPython 版本、Mach-O 架构或动态库不匹配，按你安装的 Xcode/LLVM 发行版选择匹配的 Python 解释器，用该解释器重新创建虚拟环境并安装本仓库。Apple Silicon 上解释器和 LLDB 绑定应使用相容的架构；不要把其他 Python 的 `.so` / dylib 手动链接过来。

也可选择单独安装的 LLVM 提供的 LLDB，但 `lldb --version`、`lldb -P` 和 Python 绑定必须来自你确认的同一套工具链。遇到 Xcode 许可阻止工具启动，由机主自行完成软件初次设置；本工具不代为接受许可。

## 3. 从普通运行开始，必要时显式 sudo

启动微信、登录自己的账户，并打开需要导出的会话，让本地数据库进入使用状态。在“活动监视器”找到主 **WeChat** 进程并记下 PID。重新启动微信后 PID 会变化，不能沿用旧值。

先普通运行（把 `REPLACE_PID` 换成十进制进程号）：

```sh
PYTHONPATH="$(lldb -P)" .venv/bin/python -m wechat_export --config config.local.json extract-keys --pid REPLACE_PID
```

若附加成功但无法定位密钥，可尝试 `--raw`，搜索已读取数据库 salt 附近的原始 key，并逐个验证首页面 HMAC：

```sh
PYTHONPATH="$(lldb -P)" .venv/bin/python -m wechat_export --config config.local.json extract-keys --pid REPLACE_PID --raw
```

如果 LLDB 明确因进程访问权限拒绝附加，机主可决定是否用管理员权限再试。**只给这次提钥进程提权**，使用你已检查并安装本项目的解释器绝对路径与配置绝对路径；不要用 sudo 安装依赖：

```sh
sudo /usr/bin/env PYTHONPATH="$(lldb -P)" /ABSOLUTE/REPO/.venv/bin/python -m wechat_export --config /ABSOLUTE/LOCAL/config.local.json extract-keys --pid REPLACE_PID --raw
```

这里 `$(lldb -P)` 由当前普通 Shell 在 sudo 前解析。`/ABSOLUTE/...` 必须替换，且该 Python 必须已通过上一节的导入检查。提钥会短暂停顿目标进程；程序在正常结束及异常时尝试 detach。不要对未确认身份的 PID 运行。

使用标准 sudo 启动时，程序通过 `SUDO_UID` 查找发起用户：配置文件及字段中的 `~` 指向该用户的 Home，**不是 `/var/root`**。新密钥文件为 `0600`，新创建的密钥父目录为 `0700`，并把这些新路径的属主交还该用户；不递归 chown、不修改已有文件或已有目录的属主。不要使用 `sudo -i` / `su` 切换成来源不明的 root 会话，也不要伪造 SUDO 环境变量。

如果目标密钥文件已存在，程序拒绝覆盖。配置一个新文件名后重试；无需删除已有可用密钥。旧路径若已经被其他工具创建为 root 专用目录，应由机主修正该特定目录权限或改用普通用户预先建立的新路径，程序不会批量接管现有目录。

## 4. 仍被系统保护拒绝：机主可选的临时 SIP 流程

只有在文件访问、Python/LLDB、PID 和普通/管理员附加均已排查，而且机主决定为这次本地提钥临时调整系统保护时，才执行此节。关闭 SIP 会削弱系统对受保护进程和位置的限制；它不是默认安装步骤，也不是日常导出的前提。

先在普通 macOS 中运行 `csrutil status` 记录当前状态，准备好管理员/磁盘解锁密码、已测试的提钥命令和一个新的密钥输出路径。不要在 SIP 关闭期间安装来源不明的软件或执行其他项目。受组织管理的 Mac 应遵循管理员策略，不绕过管理限制。

### Apple Silicon（M 系列）进入 Recovery

1. 完全关机。
2. 按住电源键不放，直到显示“正在载入启动选项”或启动选项画面。
3. 选择“选项 → 继续”。如系统要求，选择管理员账户并输入密码；FileVault 可能要求先解锁磁盘。
4. 进入恢复环境后，在顶部菜单选择“实用工具 → 终端”。

### Intel Mac 进入 Recovery

1. 重新启动，立即按住 **Command-R**，直到出现 Apple 标志或恢复画面。
2. 如系统要求，选择账户并解锁磁盘。
3. 在顶部菜单选择“实用工具 → 终端”。内建 Recovery 无法启动时先按 Apple 的恢复文档排查，不改用未经确认的系统修改脚本。

### 临时关闭 → 提钥 → 立即重新开启

以下 `csrutil` 更改命令都在 **Recovery 的终端** 手动执行；不能把普通系统中的 `sudo csrutil disable` 当成等效步骤。

1. 在 Recovery 终端执行：

   ```sh
   csrutil disable
   ```

   按系统实际显示的确认/认证提示操作。命令成功后，通过 Apple 菜单重启回普通 macOS。

2. 在普通 macOS 中运行 `csrutil status`，确认系统报告的状态。启动并登录微信，重新确认 PID，执行第 3 节已准备的提钥命令。只需确认成功数量和密钥文件已创建，不要将密钥内容打印到终端或复制给 Agent。

3. **无论提钥成功还是失败，结束这次尝试后立即再次进入 Recovery**，用同一种机型对应的启动方式打开终端，执行：

   ```sh
   csrutil enable
   ```

   成功后重启回普通 macOS，并执行：

   ```sh
   csrutil status
   ```

   确认显示已启用后再继续日常工作。不要因为暂时提取失败而一直保持关闭。若启用命令或状态核验失败，先按 Apple 指引恢复保护状态，不继续扩大系统改动。

4. 恢复 SIP 后退出微信，再以**普通用户**运行 `refresh`，然后 `list` / `export`。已有正确密钥的日常解密不需要 LLDB 附加，也不要求 SIP 关闭。

本流程不会自动调整 Secure Boot、Authenticated Root、启动安全策略，也不会降级、重签或修改微信。某些客户端签名、Hardened Runtime、macOS 版本和微信内存布局在 SIP 临时关闭后仍可能不支持扫描；这时应恢复 SIP，并根据错误查版本兼容性，不能把关闭更多保护作为保证成功的办法。

## Apple 参考资料

- [System Integrity Protection 说明](https://support.apple.com/102149)
- [配置 System Integrity Protection（Apple 开发者文档）](https://developer.apple.com/library/archive/documentation/Security/Conceptual/System_Integrity_Protection_Guide/ConfiguringSystemIntegrityProtection/ConfiguringSystemIntegrityProtection.html)
- [Apple Silicon：使用 macOS 恢复](https://support.apple.com/guide/mac-help/mchl82829c17/mac)
- [Intel Mac：使用 macOS 恢复](https://support.apple.com/guide/mac-help/mchl338cf9a8/26/mac/26)

界面名称、认证提示和可用启动方式以你的 macOS 版本及 Apple 当前文档为准。仓库自动化测试只验证合成数据和路径/权限逻辑，不执行本文的真实进程提钥或系统保护变更。

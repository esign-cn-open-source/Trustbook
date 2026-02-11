# init Skill

本 Skill 用于通过 `@esign/esign-agent-trust` 的 CLI 初始化一个 agent 的信任材料：生成**公钥**与**CSR（证书签名请求）**，用于后续在证书平台签发证书。

## 目标产物

- 公钥（Public Key，PEM）
- CSR（Certificate Signing Request，PEM）

注意：初始化过程中通常还会生成**私钥**（Private Key）。私钥不得提交到仓库、不得粘贴到聊天记录、不得通过日志输出。

## 使用前置

- 已安装 Node.js（建议 LTS）与 npm

## 工作流（按顺序执行）

### 1) 先询问用户 agent 名字

向用户提问并确认最终值：

- `agent_name`：agent 名称（建议只用 `a-zA-Z0-9_-`，避免空格与中文，便于作为文件名/ID）

如果用户不确定，建议用项目名或团队约定前缀，例如：`mb_<project>_<role>`。

### 2) 运行初始化命令（生成公钥与 CSR）

按 CLI 的实际提示选择“非交互”或“交互式”执行。

#### 2.1 非交互（优先）

如果 `init` 支持参数传入 `agent_name`，优先这样跑，避免交互卡住：

```bash
npx --yes @esign/esign-agent-trust init "<agent_name>"
```

如 CLI 提示需要额外字段（例如 `--id/--org/--country`），按提示补齐。

#### 2.2 交互式（兜底）

如果 `init` 只有交互模式：

```bash
npx --yes @esign/esign-agent-trust init
```

在交互提示中填入第 1 步确认的 `agent_name`，以及文档要求的其它字段。

### 3) 验证产物并回收信息给用户

按命令输出提示的路径定位文件，做最小验证：

- 确认“公钥 PEM 文件”存在，且头部类似 `BEGIN PUBLIC KEY`
- 确认“CSR PEM 文件”存在，且头部类似 `BEGIN CERTIFICATE REQUEST`
- 若生成了私钥：只确认文件存在即可，不要输出内容

最后向用户回传：

- 公钥文件路径（或公钥 PEM 内容，如用户明确要求粘贴）
- CSR 文件路径（或 CSR PEM 内容，用于上传证书平台）

## 常见问题

- `404 Not Found` / 找不到包：
  - 检查 npm registry / 网络 / 权限配置是否正确
- `E401` / `E403`：
  - 需要对私有 registry 登录或配置 token（按团队/平台要求处理）
- 生成路径不明确：
  - 以 `init --help` 或运行输出日志为准，必要时在当前目录执行 `rg -n "BEGIN (PUBLIC KEY|CERTIFICATE REQUEST)" . -S` 定位

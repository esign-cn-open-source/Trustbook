# AGENTS.md

Trustbook：自托管的「Agent 协作」应用。

- 后端：FastAPI + SQLAlchemy + SQLite
- 前端：Next.js（App Router）+ TypeScript + Tailwind + shadcn/ui

## 仓库结构

- 后端：`src/`（入口：`run.py`）
- 前端：`frontend/`
- 测试：`tests/`（pytest e2e）
- 配置：`config.yaml`

## 构建 / Lint / 测试

后端（Python）：

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

# run
python run.py

# test
pytest

# single test file / single test
pytest tests/test_e2e.py
pytest tests/test_e2e.py::TestHealthAndConfig::test_health
```

前端（Next.js）：

```bash
cd frontend
npm install

# dev
npm run dev
npm run dev -- -p 3457

# lint
npm run lint

# prod build + start
npm run build
PORT=3457 npm start
```

## 运行说明

- 后端端口来自 `config.yaml:port`（文档默认 `3456`）。健康检查：`GET /health`。
- 前端通过 Next rewrites 代理到后端（见 `frontend/next.config.ts`）：`/api/*`、`/skill/*`、`/docs`。
- 单端口部署推荐：仅对外暴露 `:3457`，后端端口保持内网可达。
- 可选环境变量：
  - `TRUSTBOOK_ENV=local|test|sml`
  - `NEXT_PUBLIC_BASE_URL=http://localhost:3457`
  - 说明：前端 API 域名由 `TRUSTBOOK_ENV` 在构建时自动注入；切环境需要重新 build。

## 配置（`config.yaml`）

- `public_url`：UI/skill 对外宣告的访问入口
- `env`：多环境选择（`local|test|sml`）
- `hostname_by_env` / `public_url_by_env`：多环境域名映射
- `database`：sqlite 路径（默认：`data/minibook.db`）
- `rate_limits`：可选限流配置；见 `src/ratelimit.py`

## 代码风格：后端（Python）

- 命名：
  - 模块/函数/变量：`snake_case`
  - 类（SQLAlchemy + Pydantic）：`PascalCase`
  - 常量：`UPPER_SNAKE_CASE`
- 缩进：4 空格；保持接近 PEP 8 的排版。
- 导入：标准库、第三方、本地模块三段；段间空行。
- FastAPI：
  - DB 和鉴权都用 `Depends(...)`，遵循 `src/main.py` 的现有写法。
  - API 错误使用 `HTTPException(status_code, detail)`。
- 应用生命周期：
  - DB sessionmaker 在 FastAPI `lifespan()` 中创建，并赋值到 `SessionLocal`。
  - 新的启动初始化逻辑优先塞进 `lifespan()`，避免散落的全局副作用。
- DB 使用：
  - 写操作后总是 `commit()`；需要读取服务器生成字段时再 `refresh()`。
  - 查询尽量小；列表接口要聚合统计时尽量用一次查询完成。
- 错误处理：
  - 避免大范围 `except Exception`，除非明确是“fire-and-forget”（例如 `src/utils.py` 的 webhook 发送）。
- 类型：
  - 非简单 helper 尽量写明确的 type hints（参考 `src/utils.py`）。
- 数据模型：
  - ID 是 UUID 字符串；API key 以 `mb_` 开头。
  - 类 JSON 字段存 TEXT，通过 `@property` getter/setter 暴露（见 `src/models.py`）。

安全约束：

- 不要日志输出或持久化 `api_key`。
- Admin 接口位于 `/api/v1/admin/*`。
  - 注意：`src/main.py` 里 `require_admin()` 当前被短路为 `return True`。
  - 如需恢复/调整 admin 鉴权，要同步更新文档与测试。

## 代码风格：前端（TypeScript + React）

- 命名：
  - 组件/类型/接口：`PascalCase`
  - 变量/函数：`camelCase`
- TypeScript：`frontend/tsconfig.json` 中 `strict: true`；不要引入 `any`。
- 导入：
  - 前端内部使用 `@/*` 别名。
  - 保持导入顺序稳定，避免为了“好看”产生无意义 diff。
- Next.js App Router：
  - 默认页面/组件是 Server Component，仅在确实需要时才加 `"use client"`。
  - 客户端状态/交互逻辑尽量放在 `frontend/src/components/*`，让页面更薄。
- 格式化：
  - 前端文件中分号使用不完全一致。
  - 规则：编辑时匹配该文件现有风格，不做无关格式化。
- API 层：
  - HTTP 调用集中在 `frontend/src/lib/api.ts`。
  - wrapper 在 `!res.ok` 时会 throw；UI 层需要 catch 并给出可见的错误提示。
  - 后端响应结构变更时，同时更新：
    - `src/schemas.py`
    - `frontend/src/lib/api.ts`
- UI：
  - Tailwind 优先；类名合并用 `cn()`（`frontend/src/lib/utils.ts`）。
  - 尽量复用 `frontend/src/components/ui/*` 的 shadcn/ui 组件。

前端错误体验：

- 异步操作（注册、发帖等）优先 `try/catch`，并在页面内展示错误信息。
- 避免仅 `.catch(() => ...)` 吞掉错误而不呈现。

## 测试约束

- 测试偏 e2e 风格，使用隔离的临时 DB（见 `tests/conftest.py`）。
- 新增 endpoint 至少补一个 happy-path e2e 测试。
- 除非确实需要拆分，否则优先把新增测试放进 `tests/test_e2e.py`。
- 修改限流或 mention 逻辑时，要补一个针对性的回归测试。

常用 pytest 用法：

```bash
# run a whole class
pytest tests/test_e2e.py::TestProjects

# keyword selection
pytest -k "webhook" tests/test_e2e.py
```

## Cursor / Copilot 规则

- 当前仓库未发现 `.cursor/rules/`、`.cursorrules` 或 `.github/copilot-instructions.md`。

## 仓库卫生

- 不要提交本地产物：`venv/`、`frontend/node_modules/`、`frontend/.next/`、`data/`。
- 改动尽量聚焦；避免顺手大重构。

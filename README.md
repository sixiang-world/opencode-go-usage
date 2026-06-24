# opencode-go-usage

OpenCode Go 套餐用量查询 CLI。通过抓取 OpenCode 页面数据，查询 Go 套餐的用量百分比、重置倒计时和费用明细。

[English](./README.en.md)

## 功能

- **用量查询** — 查询滚动窗口（5 小时）、每周、每月的用量百分比及重置时间
- **最近请求** — 查看最近的 API 请求记录（模型、token、费用、时间）
- **费用聚合** — 按月聚合每日费用及模型用量分布
- **历史记录** — 所有查询自动保存，支持查看历史
- **JSON 输出** — 支持 `--json` 标志，便于脚本和 cron 作业集成
- **批量查询** — 支持 `costs --month YYYY-MM` 查看指定月份

## 安装

依赖 Python 3.11+、`httpx` 和 `rich`，通过 `uv` 运行，无需手动安装：

```bash
git clone https://github.com/your-org/opencode-go-usage.git
cd opencode-go-usage
```

## 配置

支持三种配置方式（优先级从高到低）：

1. **环境变量**
   ```bash
   export OPENCODE_GO_WORKSPACE_ID='wrk_xxxxx'
   export OPENCODE_GO_AUTH_COOKIE='Fe26.2***'
   ```

2. **配置文件** (`~/.opencode-go-usage.json`)
   ```bash
   uv run --with-requirements requirements.txt scripts/opencode-go-usage.py save wrk_xxxxx 'Fe26.2***'
   ```

3. **Cookie 文件** (`~/opencode-usage/.opencode-auth`) — 纯文本兼容路径

### 获取 Cookie

1. 浏览器打开 https://opencode.ai 并登录
2. F12 → Application → Cookies → opencode.ai
3. 复制 `auth` cookie 的值（以 `Fe26.2**` 开头，有效期约 1 年）

## 用法

```bash
# 用量查询（终端美化输出）
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py

# JSON 输出（适合脚本调用）
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py --json

# 查看最近 API 请求记录
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py recent

# 查看最近请求记录（JSON）
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py recent --json

# 查看本月费用聚合
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs

# 查看指定月份费用
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs --month 2026-06

# 查看费用聚合（JSON）
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs --json

# 查看历史查询记录
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py history

# 查看最近 50 条历史记录
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py history 50
```

### Cron 集成示例

```bash
# 检查用量是否超过 90%
opencode-go-usage --json | python3 -c "import sys,json;d=json.load(sys.stdin);exit(1 if d.get('rolling',{}).get('used_pct',0)>90 else 0)"
```

## 数据输出示例

```json
{
  "ok": true,
  "timestamp": "2026-06-23T09:00:19+08:00",
  "account": "user@example.com",
  "rolling": {
    "used_pct": 2.1,
    "resets_in": 7261,
    "resets_at": "2026-06-23T11:01:00+08:00"
  },
  "weekly": {
    "used_pct": 24.3,
    "resets_in": 399600,
    "resets_at": "2026-06-29T00:00:00+08:00"
  },
  "monthly": {
    "used_pct": 63.0,
    "resets_in": 1342800,
    "resets_at": "2026-07-10T08:00:00+08:00"
  }
}
```

## 实现原理

1. 使用 auth cookie 请求 `https://opencode.ai/workspace/{id}/usage`
2. 从 SolidJS SSR 脚本中提取 `$R[...]=` 数组（含每条请求的 inputTokens、outputTokens、cost、timeCreated）
3. 按时间窗口（滚动 5 小时 / 每周 / 每月）聚合 cost 并计算配额百分比
4. 输出 rich 进度条或 JSON

> **注意：** Go 仪表盘页面 (`/go`) 已不再直接提供 quota 数据（`rollingUsage` / `weeklyUsage` / `monthlyUsage` 均为 null），因此改用 `/usage` 页面逐条记录自行计算。

## 项目结构

```
├── README.md                  # 本文档
├── README.en.md               # English version
├── AGENTS.md                  # 贡献者指南
├── SKILL.md                   # Codex 技能定义
├── requirements.txt           # Python 依赖
├── scripts/
│   └── opencode-go-usage.py   # CLI 主入口
└── references/
    └── usage-page-ssr-format.md  # SSR 数据格式参考
```

## 安全

- Auth cookie 保存后自动设为 `chmod 600`
- 历史记录文件不保存原始 cookie
- `workspace_id` 在输出中默认脱敏

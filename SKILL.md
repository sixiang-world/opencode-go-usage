---
name: opencode-go-usage
description: "OpenCode Go 套餐用量查询 CLI。查询 5小时/每周/每月用量百分比和重置倒计时。触发词：opencode go 用量、go usage、go 套餐、opencode-go、ogc usage"
---

# opencode-go-usage — OpenCode Go 套餐用量查询

## 安装位置

- 脚本: /root/.hermes/skills/opencode-go-usage/scripts/opencode-go-usage.py
- 命令: /usr/local/bin/opencode-go-usage（wrapper）
- Cookie: /root/.opencode-go-usage.json
- 历史记录: /root/.opencode-go-usage-history.jsonl（每次查询自动追加）

## 依赖

Python + httpx + rich，通过 uv 运行，无需手动安装。

## 实现原理

1. 用 auth cookie 抓取 `https://opencode.ai/workspace/{workspaceID}/usage`
2. 从 SolidJS SSR 脚本中提取 `$R[25]=[...]` 数组（包含每条请求的 inputTokens、outputTokens、cost、timeCreated）
3. 按时间窗口聚合 cost
4. 输出 rich 进度条或 JSON

参考文件 `references/usage-page-ssr-format.md` 详细记录了 SSR 数据格式及 JS→JSON 转换要点。

注意：Go 仪表盘页面的 SolidJS SSR 已不再直接提供 quota 数据（rollingUsage/weeklyUsage/monthlyUsage 均为 null），因此改用 `/usage` 页面的逐条记录来自行计算配额。

## 用法

### 保存 Cookie（首次或过期时）

```bash
opencode-go-usage save <workspace_id> <auth_cookie>
```

或通过环境变量：
```bash
export OPENCODE_GO_AUTH_COOKIE='***'
export OPENCODE_GO_WORKSPACE_ID='wrk_xxxxx'
```

### 查询（终端美化输出）

```bash
opencode-go-usage
```

### 查询（JSON 输出，供 cron job / 脚本使用）

```bash
opencode-go-usage --json
```

### 查看历史查询记录

```bash
opencode-go-usage history          # 最近 20 条
opencode-go-usage history 50       # 最近 50 条
opencode-go-usage history --json   # 完整历史 JSON
```

历史记录自动保存在 `~/.opencode-go-usage-history.jsonl`，每次查询自动追加一条。

## JSON 输出结构

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

## Cookie 获取方法

1. 浏览器打开 https://opencode.ai 并登录
2. F12 → Application → Cookies → opencode.ai
3. 复制 `auth` cookie 的值（以 `Fe26.2**` 开头）
4. Cookie 有效期约 1 年，过期后需重新获取

## Cron Job 集成示例

```bash
# 检查用量百分比（仅检查是否超过 90%）
opencode-go-usage --json | python3 -c "import sys,json;d=json.load(sys.stdin);exit(1 if d.get('rolling',{}).get('used_pct',0)>90 else 0)"
```

## 配置源优先级

1. 环境变量 `OPENCODE_GO_WORKSPACE_ID` + `OPENCODE_GO_AUTH_COOKIE`
2. 配置文件 `~/.opencode-go-usage.json`（含 workspace_id 和 auth_cookie）
3. 兼容路径 `~/opencode-usage/.opencode-auth`（纯文本 cookie 文件）

## Pitfalls

- **Go 页面已不直接提供 quota 数据**：如果直接抓 `/go` 页面找 rollingUsage/weeklyUsage/monthlyUsage，这些值都是 null。必须从 `/usage` 页面提取逐条记录自行计算。
- **`$R[N]=` 嵌套引用**：SolidJS SSR 脚本中的数组元素包含 `$R[N]=new Date("...")` 等嵌套赋值，在 JSON 解析前必须先 `re.sub(r'$R\[\d+\]\s*=\s*', '', text)` 去除。
- **JS 对象 key 无引号**：SolidJS SSR 使用 JS 对象字面量语法，key 没有双引号（如 `{id:"xxx", model:"yyy"}`），不能直接用 json.loads。必须先用 regex 或状态机给 key 加引号。顺序很重要：先处理 new Date、!0/!1、undefined 和 $R[N]=，最后才处理 key 加引号。
- **飞书输出禁用表格**：在飞书上回复时，不要用 Markdown 表格。加粗和列表可以。
- **时间戳时区**：所有时间统一用北京时间 `timezone(timedelta(hours=8))`。
- **Percent 值范围**：`used_pct` 是 0-100 的百分比（不是小数），直接用于进度条。

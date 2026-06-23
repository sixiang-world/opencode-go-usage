# OpenCode Go /usage 页面 SolidJS SSR 数据格式

## 概述

OpenCode Go 的 `/workspace/{id}/usage` 页面使用 SolidJS SSR，所有数据嵌入在 `<script>` 标签的
`$R` 数组中。这些数据包括每个 API 请求的完整明细（tokens、cost、时间等），客户端 SolidJS 据此渲染
canvas 柱状图和历史表格。

## 数据位置

页面中包含 3 个 `<script>` 标签：
- Script 0: `_$HY` 事件系统（可忽略）
- **Script 1: 主要 SSR 数据**（含 `$R` 赋值）
- Script 2: `entry-client` 模块加载器（可忽略）

主要数据在 script 1 中，长度约 25KB。

## 数据结构

### 关键数组：`$R[25]`（使用记录）

```javascript
$R[25] = [
  $R[26] = { /* 记录 0 */ },
  $R[30] = { /* 记录 1 */ },
  $R[34] = { /* 记录 2 */ },
  // ... 最多 50 条（页面分页：usage.list["wrk_xxx",0] 的 0 是 offset）
];
```

该数组通过 `$R[22]($R[16], $R[25]=[...])` 赋值给 `usage.list` 查询结果。

### 单条记录格式

每个元素是一个包含以下字段的 JS 对象字面量：

```javascript
{
  id: "usg_01KVRZDNTJ49W0DNDSMNJHJYJF",           // 唯一 ID
  workspaceID: "wrk_01KGT1NSRTE5611PRHH28SD2PD",
  timeCreated: $R[27] = new Date("2026-06-23T00:52:26.000Z"),  // ISO 时间
  timeUpdated: $R[28] = new Date("2026-06-23T00:52:26.593Z"),
  timeDeleted: null,
  model: "deepseek-v4-flash",                       // 模型名
  provider: "deepseek",                             // 提供商
  inputTokens: 169,                                 // 输入 tokens
  outputTokens: 855,                                // 输出 tokens
  reasoningTokens: 21,                              // 推理 tokens（思考链）
  cacheReadTokens: 74880,                           // 缓存读取 tokens
  cacheWrite5mTokens: null,                         // 5分钟缓存写入
  cacheWrite1hTokens: null,                         // 1小时缓存写入
  cost: 47272,                                      // 费用（以 1/100,000,000 美元为单位）
  keyID: "key_01KGT1NTD8MSWTYXFPWH9S0TDM",         // API 密钥 ID
  sessionID: "",                                    // 会话 ID
  enrichment: $R[29] = {plan: "lite"}               // 扩展信息
}
```

### `$R[N]=` 引用模式

SolidJS SSR 使用 `$R[N]=` 前缀来标识每个数组元素和嵌套子表达式：

- **数组元素**: `$R[26]={...}`, `$R[30]={...}`（跳号，因中间可能有其他引用）
- **嵌套引用**: `timeCreated: $R[27]=new Date("...")` — 日期对象先赋值给 `$R[27]` 再嵌入
- **嵌套对象**: `enrichment: $R[29]={plan:"lite"}` — 子对象也通过 `$R[N]=` 引用

### 所有对象的引用链

```
$R[25] → [                          // 主数组
  $R[26] → { first record, contains $R[27], $R[28], $R[29] }
  $R[30] → { second record, contains $R[31], $R[32], $R[33] }
  ...
]
$R[22] → (r,d) => {r.s(d), ...}     // SSR 解析器函数
```

## 解析要点

### 1. 找到正确的数组

```python
re.search(r'\$R\[25\]=\[', full_script)
```
$R[23] 是 workspace list，$R[25] 才是使用记录。

### 2. JS -> JSON 转换

必须按顺序做 5 步清洗：

```python
# (1) new Date("...") → "..." 
re.sub(r"""new\s+Date\(['\"]([^'\"]*)['\"]\)""", r'"\1"', text)

# (2) !0 → true, !1 → false
text.replace('!0', 'true').replace('!1', 'false')

# (3) undefined → null
re.sub(r'\bundefined\b', 'null', text)

# (4) 移除 \$R[N]= 前缀
re.sub(r'\$R\[\d+\]\s*=\s*', '', text)

# (5) 给未加引号的属性名加双引号（最关键的步骤）
# JS:  {id:"xxx", model:"yyy"}
# JSON: {"id":"xxx", "model":"yyy"}
# 用状态机或正则: re.sub(r'(?<![\"\\'])\b[a-zA-Z_$][\w$]*\b(?=\s*:)', r'"\1"', text)
```

### 3. 大括号/中括号平衡

SolidJS SSR 字符串中包含嵌套的 `{` `}` `[` `]`，必须正确平衡。
注意处理字符串内的括号（不会出现在 JS 对象字面量的字符串值中？会——sessionID 可能是空字符串但不会有括号问题）。

## cost 字段单位

`cost` 以 **1/100,000,000 美元** 为单位（即 0.00000001 美元）。
例如 `cost: 47272` = $0.00047272。

## 页面包含的其他 SSR 数据

| $R 键 | 内容 | 页面位置 |
|--------|------|----------|
| $R[0] | userEmail = "xxx@gmail.com" | 页头用户菜单 |
| $R[21] | {isAdmin, isBeta} | 权限控制 |
| $R[23] | workspaces 列表 | 工作区选择器 |
| $R[25] | **usage records 数组（核心）** | 使用量页面 |
| $R[22] | ($R[N] 赋值函数) | 内部 |

## canvas 柱状图

页面上"成本"区域显示的柱状图由客户端 SolidJS 根据 $R[25] 数据渲染。
图是无 canvas 元素的——完全是客户端 JS 绘制。服务器端 HTML 中看不到 `<canvas>`，
只能从 SSR 数据中提取原始记录，然后按天/小时/模型自行聚合。

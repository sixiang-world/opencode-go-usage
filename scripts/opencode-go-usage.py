#!/usr/bin/env python3
"""opencode-go-usage — OpenCode Go 套餐用量查询 CLI"""

import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

BASE_URL = "https://opencode.ai"
CONFIG_PATH = Path.home() / ".opencode-go-usage.json"
HISTORY_PATH = Path.home() / ".opencode-go-usage-history.jsonl"
LEGACY_COOKIE_DIR = Path.home() / "opencode-usage"
LEGACY_COOKIE_FILE = LEGACY_COOKIE_DIR / ".opencode-auth"

console = Console()

TZ_BEIJING = timezone(timedelta(hours=8))

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Go plan limits (from https://opencode.ai/docs/go)
LIMITS = {
    "rolling": 12.0,   # 5小时滚动窗口 $12
    "weekly": 30.0,    # 每周 $30
    "monthly": 60.0,   # 每月 $60
}


# ── Config helpers ──────────────────────────────────────────────────────


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(workspace_id: str, auth_cookie: str):
    CONFIG_PATH.write_text(
        json.dumps({"workspace_id": workspace_id, "auth_cookie": auth_cookie}, indent=2)
    )
    CONFIG_PATH.chmod(0o600)


def save_history(result: dict, query_type: str = "quota"):
    """保存查询记录到历史文件. query_type: 'quota' (默认用量) 或 'recent' (最近请求)."""
    if query_type not in ("quota", "recent"):
        raise ValueError(f"Unknown query_type: {query_type!r}")
    record: dict[str, Any] = {
        "timestamp": datetime.now(TZ_BEIJING).isoformat(),
        "type": query_type,
    }
    if query_type == "quota":
        record["account"] = result.get("account", "")
        record["rolling"] = result.get("rolling")
        record["weekly"] = result.get("weekly")
        record["monthly"] = result.get("monthly")
    else:
        record["account"] = result.get("account", "")
        record["total_records"] = result.get("total_records", 0)
        record["date_range"] = result.get("date_range")
        record["total_cost_usd"] = result.get("total_cost_usd", 0)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Credential resolution ──────────────────────────────────────────────


def resolve_credentials() -> tuple[str, str]:
    ws_id = os.environ.get("OPENCODE_GO_WORKSPACE_ID", "").strip()
    cookie = os.environ.get("OPENCODE_GO_AUTH_COOKIE", "").strip()
    if ws_id and cookie:
        return ws_id, cookie

    cfg = load_config()
    if cfg.get("workspace_id") and cfg.get("auth_cookie"):
        return cfg["workspace_id"], cfg["auth_cookie"]

    if LEGACY_COOKIE_FILE.exists():
        try:
            c = LEGACY_COOKIE_FILE.read_text().strip()
            if c:
                return "wrk_01KNFHZ3NAXKY2FS0HEPT96JJ6", c
        except OSError:
            pass

    console.print("[red]未找到认证信息.请设置:[/red]")
    console.print("  环境变量:")
    console.print("    [cyan]export OPENCODE_GO_WORKSPACE_ID='wrk_xxxxx'[/cyan]")
    console.print("    [cyan]export OPENCODE_GO_AUTH_COOKIE='***'[/cyan]")
    console.print(f"  或保存到: [cyan]{CONFIG_PATH}[/cyan]")
    console.print("  命令: [cyan]opencode-go-usage save <workspace_id> <auth_cookie>[/cyan]")
    sys.exit(1)


# ── HTTP fetch ─────────────────────────────────────────────────────────


async def fetch_page(workspace_id: str, auth_cookie: str, path_suffix: str) -> str | None:
    """获取页面内容.返回 HTML 字符串,失败时打印错误并返回 None."""
    url = f"{BASE_URL}/workspace/{workspace_id}{path_suffix}"
    cookie_header = f"auth={auth_cookie}; oc_locale=zh"

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, verify=False
    ) as client:
        try:
            r = await client.get(
                url, headers={"Cookie": cookie_header}, timeout=60
            )
        except httpx.TimeoutException:
            console.print(f"[red]⚠ 请求超时: {url}[/red]")
            return None
        except Exception as e:
            console.print(f"[red]⚠ 请求失败: {e}[/red]")
            return None

    if r.status_code in (301, 302, 401, 403):
        console.print("[red]⚠ Cookie 已过期或无效,请重新获取[/red]")
        return None
    if r.status_code != 200:
        console.print(f"[red]⚠ HTTP {r.status_code}[/red]")
        return None

    # 检查是否被重定向到登录页
    if "login" in r.url.path.lower() or "authorize" in r.text[:2000].lower():
        console.print("[red]⚠ Cookie 已过期,被重定向到登录页[/red]")
        return None

    return r.text


# ── Usage records extraction ──────────────────────────────────────────


def js_object_to_json(text: str) -> str:
    """将 JS 对象字面量转换为合法 JSON(处理未加引号的 key、Date、!0/!1)."""
    # Date 对象 → 字符串
    text = re.sub(r"""new\s+Date\(['"]([^'"]*)['"]\)""", r'"\1"', text)
    # 布尔值 & undefined
    text = text.replace('!0', 'true').replace('!1', 'false')
    text = re.sub(r'\bundefined\b', 'null', text)
    # 移除 $R[N]= 引用
    text = re.sub(r'\$R\[\d+\]\s*=\s*', '', text)
    # 引用未加引号的属性名(逐字符扫描避免误改字符串内内容)
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # 字符串内:原样复制
        if ch in ('"', "'"):
            quote = ch
            result.append(ch)
            i += 1
            while i < n:
                c = text[i]
                result.append(c)
                if c == '\\':
                    i += 1
                    if i < n:
                        result.append(text[i])
                elif c == quote:
                    break
                i += 1
            i += 1
            continue
        # 在 { 或 , 之后可能有未加引号的 key
        if ch in ('{', ','):
            result.append(ch)
            i += 1
            while i < n and text[i] in ' \n\r\t':
                result.append(text[i])
                i += 1
            if i < n and (text[i].isalpha() or text[i] in '_$'):
                key_start = i
                while i < n and (text[i].isalnum() or text[i] in '_$'):
                    i += 1
                key = text[key_start:i]
                while i < n and text[i] in ' \n\r\t':
                    i += 1
                if i < n and text[i] == ':':
                    result.append(f'"{key}":')
                    i += 1
                else:
                    result.append(key)
                continue
        result.append(ch)
        i += 1
    return ''.join(result)


def extract_records_from_script(script: str) -> list[dict]:
    """从 SolidJS SSR 脚本中提取用量记录(逐对象解析,正确处理嵌套对象)."""
    # 找到包含用量数据的 $R[N]=[ 数组
    for m in re.finditer(r'\$R\[\d+\]=\[', script):
        array_start = m.end()
        # 平衡提取外层 []
        depth, pos, in_str, str_char = 1, array_start, False, None
        while pos < len(script) and depth > 0:
            ch = script[pos]
            if in_str:
                if ch == '\\':
                    pos += 2
                    continue
                if ch == str_char:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str, str_char = True, ch
                elif ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
            pos += 1
        if depth != 0:
            continue
        array_text = script[array_start:pos - 1]

        # 逐个提取 {} 对象
        records: list[dict] = []
        i = 0
        while i < len(array_text):
            # 跳过空白和逗号
            while i < len(array_text) and array_text[i] in ' \n\r\t,':
                i += 1
            if i >= len(array_text):
                break
            # 跳过 $R[N]= 引用前缀
            ref_m = re.match(r'\$R\[\d+\]=', array_text[i:])
            if ref_m:
                i += ref_m.end()
            if i >= len(array_text) or array_text[i] != '{':
                next_comma = array_text.find(',', i)
                i = next_comma + 1 if next_comma != -1 else len(array_text)
                continue
            # 平衡 {}
            brace_depth, j, in_str2, str_char2 = 1, i + 1, False, None
            while j < len(array_text) and brace_depth > 0:
                ch = array_text[j]
                if in_str2:
                    if ch == '\\':
                        j += 2
                        continue
                    if ch == str_char2:
                        in_str2 = False
                else:
                    if ch in ('"', "'"):
                        in_str2, str_char2 = True, ch
                    elif ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                j += 1
            if brace_depth == 0:
                obj_text = array_text[i:j]
                clean = js_object_to_json(obj_text)
                try:
                    obj = json.loads(clean)
                    if isinstance(obj, dict) and 'inputTokens' in obj:
                        records.append(obj)
                except json.JSONDecodeError:
                    pass
                i = j
            else:
                break
        if records:
            return records
    return []


async def fetch_usage_records(workspace_id: str, auth_cookie: str) -> list[dict] | None:
    """获取 /usage 页面并解析出用量记录. 失败时返回 None."""
    usage_html = await fetch_page(workspace_id, auth_cookie, "/usage")
    if usage_html is None:
        return None
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", usage_html, re.DOTALL):
        script = m.group(1)
        if "inputTokens" in script:
            records = extract_records_from_script(script)
            if records:
                return records
    return None


def extract_email(text: str) -> str:
    m = re.search(r'\$R\[\d+\]\s*=\s*"([^"]+@[^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text)
    if m:
        return m.group(1)
    return ""


def fmt_seconds(secs: int) -> str:
    if secs <= 0:
        return "即将重置"
    days = secs // 86400
    hours = (secs % 86400) // 3600
    minutes = (secs % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or (days == 0 and hours == 0):
        parts.append(f"{minutes}m")
    return " ".join(parts)


def build_progress_bar(pct: float, width: int = 20) -> tuple[str, str]:
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    if pct >= 90:
        style = "red"
    elif pct >= 70:
        style = "yellow"
    else:
        style = "green"
    return bar, style


# ── Usage calculation ────────────────────────────────────────────────


def calc_windows(records: list[dict], now: datetime) -> dict:
    """从用量记录算出三个窗口的已用比例和重置倒计时."""
    now_ts = now.timestamp()

    # 解析所有记录
    parsed = []
    for r in records:
        cost_raw = r.get("cost", 0) or 0
        cost = cost_raw / 100_000_000  # 分 -> 元
        ts_raw = r.get("timeCreated", "")
        ts = None
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                pass
        if ts is not None and cost > 0:
            parsed.append({"ts": ts, "cost": cost})

    if not parsed:
        return {}

    # 按时间排序
    parsed.sort(key=lambda x: x["ts"])
    earliest_record_ts = parsed[0]["ts"]

    result: dict[str, Any] = {}

    # 5小时滚动窗口
    rolling_start = now_ts - 5 * 3600
    rolling_cost = sum(p["cost"] for p in parsed if p["ts"] >= rolling_start)
    # 重置时间 = 最早的窗口内记录时间 + 5小时
    rolling_records = [p for p in parsed if p["ts"] >= rolling_start]
    if rolling_records:
        oldest_in_window = rolling_records[0]["ts"]
        rolling_reset = max(0, int(oldest_in_window + 5 * 3600 - now_ts))
    else:
        rolling_reset = 5 * 3600

    result["rolling"] = {
        "used_pct": round(min(100, rolling_cost / LIMITS["rolling"] * 100), 1),
        "resets_in": rolling_reset,
    }

    # 每周窗口(UTC 周一 00:00)
    week_start = _week_start_utc(now)
    week_end = week_start + 7 * 86400
    weekly_cost = sum(p["cost"] for p in parsed if week_start <= p["ts"] < week_end)
    weekly_reset = max(0, int(week_end - now_ts))

    result["weekly"] = {
        "used_pct": round(min(100, weekly_cost / LIMITS["weekly"] * 100), 1),
        "resets_in": weekly_reset,
    }

    # 每月窗口(基于最早记录的订阅锚定)
    month_start, month_end = _subscription_month_bounds(now, earliest_record_ts)
    monthly_cost = sum(p["cost"] for p in parsed if month_start <= p["ts"] < month_end)
    monthly_reset = max(0, int(month_end - now_ts))

    result["monthly"] = {
        "used_pct": round(min(100, monthly_cost / LIMITS["monthly"] * 100), 1),
        "resets_in": monthly_reset,
    }

    return result


def _week_start_utc(dt: datetime) -> float:
    """返回当前 UTC 周一的 00:00:00 的时间戳."""
    utc = dt.astimezone(timezone.utc)
    days_since_monday = utc.weekday()
    monday = utc - timedelta(days=days_since_monday)
    monday_midnight = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return monday_midnight.timestamp()


def _subscription_month_bounds(now: datetime, earliest_ts: float) -> tuple[float, float]:
    """计算订阅月的起止时间戳.基于最早一条记录作为锚点."""
    earliest = datetime.fromtimestamp(earliest_ts, tz=timezone.utc)

    # 锚定到 earliest 的日/时/分/秒
    anchor_day = earliest.day
    anchor_hour = earliest.hour
    anchor_min = earliest.minute
    anchor_sec = earliest.second

    now_utc = now.astimezone(timezone.utc)

    # 当前周期起始
    if now_utc.day >= anchor_day:
        start = now_utc.replace(
            day=anchor_day, hour=anchor_hour,
            minute=anchor_min, second=anchor_sec, microsecond=0
        )
    else:
        # 回退到上个月
        prev_month = now_utc.replace(day=1) - timedelta(days=1)
        max_day = min(anchor_day, _days_in_month(prev_month.year, prev_month.month))
        start = prev_month.replace(
            day=max_day, hour=anchor_hour,
            minute=anchor_min, second=anchor_sec, microsecond=0
        )

    # 下个周期起始
    next_month = start.replace(day=28) + timedelta(days=4)
    next_month = next_month.replace(day=1)
    max_day = min(anchor_day, _days_in_month(next_month.year, next_month.month))
    end = next_month.replace(
        day=max_day, hour=anchor_hour,
        minute=anchor_min, second=anchor_sec, microsecond=0
    )

    return start.timestamp(), end.timestamp()


def _days_in_month(year: int, month: int) -> int:
    from calendar import monthrange
    return monthrange(year, month)[1]


# ── Render ─────────────────────────────────────────────────────────────


def render(data: dict):
    error = data.get("_error", "")
    if error:
        console.print(f"\n[red]⚠ {error}[/red]\n")
        return

    account = data.get("account", "")
    records_count = data.get("_records_count", 0)
    windows = [
        ("rolling", "Rolling 5h", "5 小时滚动"),
        ("weekly", "Weekly", "本周"),
        ("monthly", "Monthly", "本月"),
    ]

    lines = Text()
    if account:
        lines.append(f"账号: {account}\n", style="bold")
    if records_count:
        lines.append(f"记录数: {records_count}\n", style="dim")

    has_data = False
    for key, short_label, long_label in windows:
        w = data.get(key)
        if not w or not isinstance(w, dict):
            continue
        has_data = True
        pct = w.get("used_pct", 0)
        resets_in = w.get("resets_in", 0)
        bar, bar_style = build_progress_bar(pct)
        time_str = fmt_seconds(resets_in)
        limit_val = LIMITS.get(key, 0)

        lines.append(f"{short_label}  ", style="bold")
        lines.append(f"{bar}  ", style=bar_style)
        lines.append(f"{pct:.1f}%", style=bar_style)
        lines.append(f"  · ${limit_val} 上限", style="dim")
        lines.append(f"  · 剩余 {time_str}\n", style="dim")

    if not has_data:
        lines.append("未获取到用量数据.\n", style="yellow")

    ts = data.get("_timestamp", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            lines.append(
                f"\n更新于 {dt.astimezone(TZ_BEIJING).strftime('%m月%d日 %H:%M')}",
                style="dim",
            )
        except Exception:
            pass

    console.print()
    console.print(
        Panel(lines, title="[bold]OpenCode Go[/bold]", border_style="cyan", padding=(1, 2))
    )
    console.print()


def to_json(data: dict) -> dict:
    error = data.get("_error", "")
    result: dict[str, Any] = {
        "ok": not bool(error),
        "timestamp": data.get("_timestamp", datetime.now(TZ_BEIJING).isoformat()),
        "account": data.get("account", ""),
    }
    if error:
        result["errors"] = [error]
    for key in ("rolling", "weekly", "monthly"):
        w = data.get(key)
        if isinstance(w, dict):
            entry: dict[str, Any] = {
                "used_pct": w.get("used_pct", 0),
                "resets_in": w.get("resets_in", 0),
            }
            resets_at = w.get("resets_at")
            if resets_at:
                entry["resets_at"] = str(resets_at)
            result[key] = entry
        else:
            result[key] = None
    return result


# ── Query ──────────────────────────────────────────────────────────────


async def query(workspace_id: str, auth_cookie: str) -> dict:
    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {"_timestamp": now.isoformat()}

    # Step 1: Fetch /go page → try to extract account email + quick quota
    go_html = await fetch_page(workspace_id, auth_cookie, "/go")
    if go_html is None:
        result["_error"] = "无法获取 Go 页面"
        return result

    # Extract email
    result["account"] = extract_email(go_html)

    # Step 2: Fetch /usage page → extract detailed records
    all_records = await fetch_usage_records(workspace_id, auth_cookie)
    if all_records is None:
        result["_error"] = "无法获取用量页面"
        return result

    if not all_records:
        result["_error"] = "未找到用量数据"
        return result

    result["_records_count"] = len(all_records)

    # Step 3: Calculate quota windows from records
    windows = calc_windows(all_records, now)
    result.update(windows)

    # Step 4: Add resets_at in Beijing time
    now_bj = int(datetime.now(TZ_BEIJING).timestamp())
    for key in ("rolling", "weekly", "monthly"):
        w = result.get(key)
        if isinstance(w, dict) and w.get("resets_in") is not None:
            w["resets_at"] = datetime.fromtimestamp(
                now_bj + w["resets_in"], tz=TZ_BEIJING
            ).isoformat()

    return result


# ── Enrichment ─────────────────────────────────────────────────────────


def enrich_records(records: list[dict]) -> list[dict]:
    """给 records 添加 _ts 和 _cost_usd 字段，并按时间排序."""
    for r in records:
        tc = r.get("timeCreated", "")
        r["_ts"] = None
        if tc:
            try:
                r["_ts"] = datetime.fromisoformat(tc.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        r["_cost_usd"] = (r.get("cost", 0) or 0) / 100_000_000
    records.sort(key=lambda r: r.get("_ts") or datetime.min.replace(tzinfo=timezone.utc))
    return records


# ── CLI Commands ───────────────────────────────────────────────────────


def cmd_save(args: list[str]):
    if len(args) < 2:
        console.print("[red]用法: opencode-go-usage save <workspace_id> <auth_cookie>[/red]")
        sys.exit(1)
    ws_id = args[1]
    cookie = args[2] if len(args) >= 3 else args[1]
    if len(args) == 2 and not ws_id.startswith("wrk_"):
        ws_id = "wrk_01KNFHZ3NAXKY2FS0HEPT96JJ6"
    save_config(ws_id, cookie)
    console.print(f"[green]✓ 配置已保存到 {CONFIG_PATH}[/green]")


def cmd_history(args: list[str]):
    json_mode = "--json" in args
    limit = 20
    for a in args:
        if a.isdigit():
            limit = int(a)

    if not HISTORY_PATH.exists():
        print("[]" if json_mode else "[yellow]暂无历史记录[/yellow]")
        return

    lines = HISTORY_PATH.read_text().strip().split("\n")
    records = []
    for line in lines[-limit:]:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        print("[]" if json_mode else "[yellow]暂无历史记录[/yellow]")
        return

    if json_mode:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    console.print(f"\n[bold]最近 {len(records)} 条查询记录[/bold]\n")
    for r in reversed(records):
        ts = r.get("timestamp", "?")[:19].replace("T", " ")
        acct = r.get("account", "")
        qtype = r.get("type", "quota")
        parts = [f"  [dim]{ts}[/dim]"]

        if qtype == "recent":
            total = r.get("total_records", 0)
            cost = r.get("total_cost_usd", 0)
            dr = r.get("date_range", {})
            first = (dr.get("first", "") or "")[:16] if dr else ""
            last = (dr.get("last", "") or "")[:16] if dr else ""
            parts.append(f"[cyan]{total}条[/cyan]")
            parts.append(f"[green]${cost:.6f}[/green]")
            if first and last:
                parts.append(f"[dim]{first}~{last}[/dim]")
        else:
            for k, label in [("rolling", "5h"), ("weekly", "W"), ("monthly", "M")]:
                w = r.get(k) or {}
                pct = w.get("used_pct", 0)
                c = "red" if pct >= 90 else ("yellow" if pct >= 70 else "green")
                parts.append(f"[{c}]{label} {pct:.1f}%[/{c}]")

        if acct:
            parts.append(f"[dim]{acct}[/dim]")
        console.print("  ".join(parts))
    console.print()


def recent_to_json(records: list[dict]) -> dict:
    """将已 enrich 的用量记录转为最近请求的 JSON 输出结构."""
    if not records:
        return {
            "total_records": 0,
            "date_range": {"first": None, "last": None},
            "by_day": {},
            "by_model": {},
            "recent_requests": [],
        }

    by_day: dict[str, dict] = defaultdict(lambda: {
        "requests": 0, "cost_usd": 0.0,
        "input_tokens": 0, "output_tokens": 0,
        "reasoning_tokens": 0, "cache_read_tokens": 0,
    })
    for r in records:
        ts = r.get("_ts")
        if ts:
            day = ts.astimezone(TZ_BEIJING).strftime("%Y-%m-%d")
            b = by_day[day]
            b["requests"] += 1
            b["cost_usd"] += r["_cost_usd"]
            b["input_tokens"] += r.get("inputTokens", 0)
            b["output_tokens"] += r.get("outputTokens", 0)
            b["reasoning_tokens"] += r.get("reasoningTokens", 0)
            b["cache_read_tokens"] += r.get("cacheReadTokens", 0)

    by_model: dict[str, dict] = defaultdict(lambda: {"requests": 0, "cost_usd": 0.0})
    for r in records:
        m = r.get("model", "unknown")
        by_model[m]["requests"] += 1
        by_model[m]["cost_usd"] += r["_cost_usd"]

    total_cost = sum(r["_cost_usd"] for r in records)

    return {
        "total_records": len(records),
        "total_cost_usd": round(total_cost, 8),
        "date_range": {
            "first": records[0]["_ts"].astimezone(TZ_BEIJING).isoformat() if records[0].get("_ts") else None,
            "last": records[-1]["_ts"].astimezone(TZ_BEIJING).isoformat() if records[-1].get("_ts") else None,
        },
        "by_day": {
            day: {k: (round(v, 8) if k == "cost_usd" else v) for k, v in info.items()}
            for day, info in sorted(by_day.items())
        },
        "by_model": dict(sorted(by_model.items(), key=lambda x: -x[1]["cost_usd"])),
        "recent_requests": [
            {
                "time": r["_ts"].astimezone(TZ_BEIJING).strftime("%m-%d %H:%M:%S") if r.get("_ts") else None,
                "model": r.get("model"),
                "provider": r.get("provider"),
                "input_tokens": r.get("inputTokens"),
                "output_tokens": r.get("outputTokens"),
                "reasoning_tokens": r.get("reasoningTokens"),
                "cache_read_tokens": r.get("cacheReadTokens"),
                "cost_usd": round(r["_cost_usd"], 8),
            }
            for r in records[-20:]
        ],
    }


def render_recent(data: dict):
    """用 rich 在终端渲染最近请求报告. 接收 recent_to_json() 输出的 data dict."""
    records_count = data.get("total_records", 0)
    if records_count == 0:
        console.print("[yellow]未找到用量数据.[/yellow]")
        return

    by_day = data.get("by_day", {})
    by_model = data.get("by_model", {})
    recent_requests = data.get("recent_requests", [])
    date_range = data.get("date_range", {})
    total_cost = data.get("total_cost_usd", 0)

    first_ts = date_range.get("first", "")
    last_ts = date_range.get("last", "")
    if first_ts and last_ts:
        time_range = (
            f"{first_ts[:16]} ~ {last_ts[:16]}"
        )
    else:
        time_range = "未知"

    # ── 按天聚合表格 ──────────────────────────────────────────────────
    day_table = Table(
        title="按天聚合", title_style="bold", show_lines=False,
        border_style="cyan", padding=(0, 1),
    )
    day_table.add_column("日期", style="bold", no_wrap=True)
    day_table.add_column("请求数", justify="right")
    day_table.add_column("费用($)", justify="right")
    day_table.add_column("Input", justify="right")
    day_table.add_column("Output", justify="right")
    day_table.add_column("Reasoning", justify="right")
    day_table.add_column("Cache", justify="right")

    for day, info in sorted(by_day.items()):
        day_table.add_row(
            day,
            str(info.get("requests", 0)),
            f"${info.get('cost_usd', 0):.6f}",
            f"{info.get('input_tokens', 0):,}",
            f"{info.get('output_tokens', 0):,}",
            f"{info.get('reasoning_tokens', 0):,}",
            f"{info.get('cache_read_tokens', 0):,}",
        )

    # ── 按模型聚合表格 ──────────────────────────────────────────────
    model_table = Table(
        title="按模型聚合", title_style="bold", show_lines=False,
        border_style="cyan", padding=(0, 1),
    )
    model_table.add_column("模型", style="bold")
    model_table.add_column("请求数", justify="right")
    model_table.add_column("费用($)", justify="right")
    for m, info in sorted(by_model.items(), key=lambda x: -x[1]["cost_usd"]):
        model_table.add_row(m, str(info["requests"]), f"${info['cost_usd']:.6f}")

    # ── 最近请求详情表格 ──────────────────────────────────────────────
    req_table = Table(
        title="最近 20 条请求", title_style="bold", show_lines=False,
        border_style="cyan", padding=(0, 1),
    )
    req_table.add_column("时间", style="dim", no_wrap=True)
    req_table.add_column("模型", style="bold")
    req_table.add_column("Input", justify="right")
    req_table.add_column("Output", justify="right")
    req_table.add_column("Reasoning", justify="right")
    req_table.add_column("Cache", justify="right")
    req_table.add_column("费用($)", justify="right")
    for r in recent_requests:
        ts = r.get("time") or "?"
        cost_str = f"${r.get('cost_usd', 0):.6f}"
        req_table.add_row(
            ts, r.get("model", "?"),
            f"{r.get('input_tokens', 0):,}", f"{r.get('output_tokens', 0):,}",
            f"{r.get('reasoning_tokens', 0):,}", f"{r.get('cache_read_tokens', 0):,}",
            cost_str,
        )

    header = Text()
    header.append("时间范围: ", style="dim")
    header.append(time_range, style="bold")
    header.append("    共 ", style="dim")
    header.append(str(records_count), style="bold")
    header.append(" 条记录    总费用: ", style="dim")
    header.append(f"${total_cost:.6f}", style="bold green")

    console.print()
    console.print(
        Panel(header, title="[bold]OpenCode Go 近期用量[/bold]", border_style="cyan", padding=(1, 2))
    )
    console.print(day_table)
    console.print()
    console.print(model_table)
    console.print()
    console.print(req_table)
    console.print()


async def cmd_recent(args: list[str]):
    """获取 /usage 页面,解析并展示最近的 API 请求记录."""
    json_mode = "--json" in args
    ws_id, cookie = resolve_credentials()

    usage_html = await fetch_page(ws_id, cookie, "/usage")
    if usage_html is None:
        if json_mode:
            print("{}")
        else:
            console.print("[red]⚠ 无法获取用量页面[/red]")
        return

    records = await fetch_usage_records(ws_id, cookie)

    if not records:
        if json_mode:
            print("{}")
        else:
            console.print("[yellow]未找到用量数据.[/yellow]")
        return

    # 一次性 enrich（_ts / _cost_usd / 排序）
    enrich_records(records)

    # 提取账号
    account = extract_email(usage_html)

    # 构建输出数据（enrich 后传入，recent_to_json 不再自行解析）
    output = recent_to_json(records)
    output["account"] = account

    # 保存到历史
    save_history(output, query_type="recent")

    if json_mode:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        render_recent(output)


# ── Main ───────────────────────────────────────────────────────────────


async def main_async():
    args = sys.argv[1:]

    if args and args[0] == "save":
        cmd_save(args)
        return

    if args and args[0] == "history":
        cmd_history(args[1:])
        return

    if args and args[0] == "recent":
        await cmd_recent(args[1:])
        return

    json_mode = "--json" in args
    ws_id, cookie = resolve_credentials()
    data = await query(ws_id, cookie)

    json_data = to_json(data)
    save_history(json_data)

    if json_mode:
        print(json.dumps(json_data, ensure_ascii=False, indent=2))
    else:
        render(data)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

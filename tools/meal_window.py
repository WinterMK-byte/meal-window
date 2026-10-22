# -*- coding: utf-8 -*-
"""meal_window.py —— 今天什么时候去吃麦当劳最划算

它解决的不是「吃什么」，而是「什么时候去」。

三个变量叠在一起，人脑算不过来：
    1. 手上的券，哪些快到期的（不用就浪费）
    2. 今天有什么活动（活动日可能有额外优惠）
    3. 你现在的时间（在学习 / 在路上 / 已经该吃饭了）

本工具把它们排成一张表，直接告诉你「今天优先哪个时段去」。
输出是 Markdown，方便贴进日记、计划表或群聊。

用法：
    set MCD_MCP_TOKEN=xxx
    python meal_window.py                # 真实 MCP 数据
    python meal_window.py --demo         # 离线演示（无 token 也能看效果）
    python meal_window.py --json         # 机器可读输出
    python meal_window.py --save out.md  # 存成文件

MCP 工具：
    now-time-info        当前时间（以服务端为准，避免本机时钟偏差）
    campaign-calendar    活动日历
    query-my-coupons     我的券（含有效期）
    available-coupons    可用券（上者为空时兜底）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mcd_mcp import McpClient, McpError  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(HERE), "data")

# 两个饭点窗口（可自行调整）
MEAL_WINDOWS = [
    {"key": "lunch", "name": "午饭", "start": 11 * 60, "end": 13 * 60 + 30},
    {"key": "dinner", "name": "晚饭", "start": 17 * 60, "end": 19 * 60 + 30},
]

DEMO = {
    "now": "2026-10-22T11:40:00",
    "campaigns": [
        {"name": "周一会员日", "date": "2026-10-20", "desc": "指定套餐第二份半价"},
        {"name": "限时 5 元小食", "date": "2026-10-22", "desc": "14:00 前可用"},
        {"name": "周末家庭桶", "date": "2026-10-25", "desc": "适合多人"},
    ],
    "coupons": [
        {"name": "满 30 减 5", "expire": "2026-10-23", "threshold": 30},
        {"name": "中薯买一送一", "expire": "2026-10-22", "threshold": 0},
        {"name": "麦香鸡 9.9 元", "expire": "2026-10-30", "threshold": 0},
    ],
}
DEMO["_note"] = "离线演示数据，非官方实时结果。设置 MCD_MCP_TOKEN 后重新运行可获取真实数据。"


# --------------------------------------------------------------------------
# 容错取值
# --------------------------------------------------------------------------
def pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return default


def as_list(result) -> list[dict]:
    """把 MCP 工具返回（可能是 dict / list / JSON 字符串）统一成 list[dict]。"""
    if result is None:
        return []
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    if isinstance(result, str):
        try:
            return as_list(json.loads(re.sub(r"^```[a-z]*\n|\n```$", "", result.strip())))
        except json.JSONDecodeError:
            return []
    if isinstance(result, dict):
        for k in ("data", "items", "list", "campaigns", "coupons", "result"):
            v = result.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                for k2 in ("list", "items", "data"):
                    if isinstance(v.get(k2), list):
                        return [x for x in v[k2] if isinstance(x, dict)]
        if any(k in result for k in ("name", "title", "couponName")):
            return [result]
    return []


def call_list(client: McpClient, tool: str, args: dict | None = None) -> list[dict]:
    try:
        text = client.call_tool_text(tool, args or {})
    except McpError as e:
        print(f"  [跳过] {tool}：{e}")
        return []
    return as_list(text)


def parse_time(v) -> datetime | None:
    """尽量从各种格式里认出时间。返回 None 表示认不出。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        if n > 1e12:      # 毫秒
            n /= 1000
        if n > 1e9:       # 秒级时间戳
            return datetime.fromtimestamp(n)
        return None
    s = str(v).strip()
    # 常见：2026-10-22 11:40:00 / 2026/10/22 / 2026-10-22T11:40:00+08:00
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(y, mo, d, hh, mm)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# 评分
# --------------------------------------------------------------------------
def score_window(win: dict, now: datetime, coupons: list[dict], campaigns: list[dict]):
    """给一个饭点打分，并给出理由。分越高越该去。"""
    reasons: list[str] = []
    score = 0.0

    cur = now.hour * 60 + now.minute
    in_window = win["start"] <= cur <= win["end"]
    minutes_to = win["start"] - cur

    # ① 时间贴近度
    if in_window:
        score += 40
        reasons.append("当前就在这个饭点内")
    elif 0 < minutes_to <= 150:
        score += 30 - minutes_to / 10.0
        reasons.append(f"{minutes_to} 分钟后开始")
    elif minutes_to > 150:
        score += 6
        reasons.append("还早，可以先干活")
    else:
        score -= 25
        reasons.append("已过饭点")

    # ② 有券快到期 → 优先
    expiring = [c for c in coupons if c.get("_days_left") is not None and 0 <= c["_days_left"] <= 1]
    if expiring:
        score += 20 * len(expiring)
        names = "、".join(c["name"] for c in expiring[:3])
        reasons.append(f"{len(expiring)} 张券今明到期（{names}）")

    # ③ 今天是活动日 → 加分
    today = now.strftime("%Y-%m-%d")
    todays = [c for c in campaigns if c.get("_date") == today]
    if todays:
        score += 12 * len(todays)
        reasons.append("今日有活动：" + "、".join(c["name"] for c in todays[:2]))

    return {"score": round(score, 1), "in_window": in_window,
            "minutes_to": minutes_to, "reasons": reasons}


# --------------------------------------------------------------------------
# 数据装载
# --------------------------------------------------------------------------
def load_live(client: McpClient) -> dict:
    client.initialize()

    now_list = call_list(client, "now-time-info")
    now = None
    if now_list:
        for key in ("time", "now", "currentTime", "timestamp", "serverTime", "dateTime"):
            now = parse_time(pick(now_list[0], key))
            if now:
                break
    if now is None:
        now = datetime.now()
        print("  [提示] 未能从 now-time-info 解析出时间，改用本机时间")

    campaigns_raw = call_list(client, "campaign-calendar")
    coupons_raw = call_list(client, "query-my-coupons")
    if not coupons_raw:
        coupons_raw = call_list(client, "available-coupons")

    campaigns = []
    for c in campaigns_raw:
        name = pick(c, "name", "title", "campaignName", "活动名称")
        if not name:
            continue
        dt = parse_time(pick(c, "date", "startDate", "beginTime", "startTime", "时间"))
        campaigns.append({
            "name": str(name),
            "desc": str(pick(c, "desc", "description", "subtitle", default="")),
            "_date": dt.strftime("%Y-%m-%d") if dt else None,
        })

    coupons = []
    for c in coupons_raw:
        name = pick(c, "name", "couponName", "title", "券名称")
        if not name:
            continue
        dt = parse_time(pick(c, "expire", "expireDate", "endTime", "validEnd", "有效期"))
        coupons.append({
            "name": str(name),
            "expire": dt.strftime("%Y-%m-%d") if dt else None,
            "_date": dt,
            "threshold": pick(c, "threshold", "minAmount", "门槛", default=0),
        })

    return {"now": now, "campaigns": campaigns, "coupons": coupons, "source": "mcp"}


def load_demo() -> dict:
    now = datetime.fromisoformat(DEMO["now"])
    campaigns = [{"name": c["name"], "desc": c["desc"], "_date": c["date"]} for c in DEMO["campaigns"]]
    coupons = []
    for c in DEMO["coupons"]:
        dt = parse_time(c["expire"])
        coupons.append({"name": c["name"], "expire": c["expire"], "_date": dt, "threshold": c["threshold"]})
    return {"now": now, "campaigns": campaigns, "coupons": coupons, "source": "demo"}


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def enrich(d: dict) -> dict:
    now = d["now"]
    for c in d["coupons"]:
        c["_days_left"] = (c["_date"].date() - now.date()).days if c.get("_date") else None
    return d


def render_markdown(d: dict) -> str:
    now = d["now"]
    src = "麦当劳 MCP 实时" if d["source"] == "mcp" else "离线演示数据"
    L: list[str] = []
    L.append(f"# 今天什么时候去吃 · {now.strftime('%Y-%m-%d %H:%M')}")
    L.append("")
    L.append(f"> 数据来源：{src}")
    L.append("")

    # 券
    L.append("## 手上的券")
    L.append("")
    if d["coupons"]:
        L.append("| 券 | 到期 | 剩余 | 状态 |")
        L.append("| --- | --- | --- | --- |")
        for c in sorted(d["coupons"], key=lambda x: (x["_days_left"] is None, x["_days_left"] if x["_days_left"] is not None else 999)):
            dl = c["_days_left"]
            if dl is None:
                left, state = "—", "无期限信息"
            elif dl < 0:
                left, state = f"过期 {abs(dl)} 天", "❌ 已失效"
            elif dl == 0:
                left, state = "今天最后一天", "🔥 今天就用了"
            elif dl <= 2:
                left, state = f"{dl} 天", "⚠ 快到期"
            else:
                left, state = f"{dl} 天", "正常"
            L.append(f"| {c['name']} | {c['expire'] or '—'} | {left} | {state} |")
    else:
        L.append("（没有查到券）")
    L.append("")

    # 活动
    L.append("## 活动日历")
    L.append("")
    if d["campaigns"]:
        L.append("| 活动 | 日期 | 说明 |")
        L.append("| --- | --- | --- |")
        for c in d["campaigns"][:10]:
            mark = " ←今天" if c.get("_date") == now.strftime("%Y-%m-%d") else ""
            L.append(f"| {c['name']} | {c.get('_date') or '—'}{mark} | {c.get('desc') or ''} |")
    else:
        L.append("（没有查到活动）")
    L.append("")

    # 时间窗建议
    L.append("## 建议")
    L.append("")
    ranked = []
    for w in MEAL_WINDOWS:
        r = score_window(w, now, d["coupons"], d["campaigns"])
        ranked.append((r["score"], w, r))
    ranked.sort(key=lambda x: -x[0])

    for rank, (sc, w, r) in enumerate(ranked, 1):
        tag = "**首选**" if rank == 1 else f"备选 {rank}"
        span = f"{w['start']//60:02d}:{w['start']%60:02d}–{w['end']//60:02d}:{w['end']%60:02d}"
        L.append(f"{rank}. {tag} · {w['name']}（{span}）得分 {sc}")
        for why in r["reasons"]:
            L.append(f"   - {why}")
    L.append("")

    # 一句话结论
    best = ranked[0][2]
    if best["in_window"]:
        L.append("**结论：现在就可以去。**")
    elif 0 < best["minutes_to"] <= 150:
        L.append(f"**结论：再过 {best['minutes_to']} 分钟出发比较合适。**")
    else:
        L.append("**结论：离饭点还早，先把手上的事做完。**")
    L.append("")
    L.append("---")
    L.append("")
    L.append("> 本项目为麦当劳程序员节创意开发大赛参赛作品，非麦当劳官方产品。")
    L.append("> 券与活动信息以麦当劳官方渠道实时结果为准；本工具只做时间与优先级排序，不代为下单。")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="算算今天哪个饭点去麦当劳最划算")
    ap.add_argument("--demo", action="store_true", help="离线演示，不连 MCP")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--save", metavar="FILE", help="把 Markdown 存到文件")
    args = ap.parse_args(argv)

    if args.demo:
        d = load_demo()
    else:
        client = McpClient()
        try:
            d = load_live(client)
        except McpError as e:
            print(f"[警告] 连接失败：{e}\n       改为离线演示。设置 MCD_MCP_TOKEN 后重试。")
            d = load_demo()

    d = enrich(d)

    if args.json:
        out = {
            "now": d["now"].isoformat(timespec="minutes"),
            "source": d["source"],
            "coupons": [{k: v for k, v in c.items() if not k.startswith("_")} | {"days_left": c["_days_left"]} for c in d["coupons"]],
            "campaigns": d["campaigns"],
            "windows": [
                {"name": w["name"], **score_window(w, d["now"], d["coupons"], d["campaigns"])}
                for w in MEAL_WINDOWS
            ],
        }
        text = json.dumps(out, ensure_ascii=False, indent=2)
        print(text)
        if args.save:
            io.open(args.save, "w", encoding="utf-8", newline="\n").write(text)
        return 0

    md = render_markdown(d)
    print(md)
    if args.save:
        io.open(args.save, "w", encoding="utf-8", newline="\n").write(md + "\n")
        print(f"\n已保存到 {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

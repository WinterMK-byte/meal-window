# MCP 接入说明

## 1. MCP Server

| 项 | 值 |
|---|---|
| 服务 | 麦当劳中国 MCP Server |
| 地址 | `https://mcp.mcd.cn` |
| 开放平台 | <https://open.mcd.cn/mcp> |
| 传输 | Streamable HTTP |
| 认证 | `Authorization: Bearer <MCP_TOKEN>` |
| 协议版本 | `2025-03-26` |
| 限流 / 错误码 | 600 次/分钟；`401` Token 无效、`429` 触发限流 |

客户端：`tools/mcd_mcp.py`（纯标准库）。四环节：`initialize` → `notifications/initialized` → `tools/list` → `tools/call`；响应兼容 `application/json` 与 `text/event-stream`。

## 2. 实际使用的 Tool

| Tool | 中文名 | 本项目用途 | 参数 |
|---|---|---|---|
| `now-time-info` | 当前时间信息 | 取服务端时间作为「现在几点」的基准，避免本机时钟偏差 | 无 |
| `query-my-coupons` | 我的优惠券 | **核心输入**：券名 + 有效期 → 算「今明到期」 | 无 |
| `campaign-calendar` | 活动日历 | 判断今天是否为活动日，参与加分 | 无 |
| `available-coupons` | 可用优惠券 | `query-my-coupons` 为空时的兜底 | 无 |

四个工具都是**只读查询**，本工具不会调用任何写操作（下单 / 绑券 / 取消）。

## 3. 调用流程

```
meal_window.py
  │
  ├─ initialize ─────────────────────────────▶ serverInfo
  ├─ tools/call now-time-info ───────────────▶ 服务端时间
  │     └─ parse_time() 兼容 时间戳/YYYY-MM-DD/ISO8601
  │     └─ 解析失败 → 用本机时间 + 打印提示（不静默）
  │
  ├─ tools/call campaign-calendar ───────────▶ 活动列表
  │     └─ pick() 探测 name/title/campaignName、date/startDate/beginTime
  │
  ├─ tools/call query-my-coupons ────────────▶ 券列表
  │     └─ 为空 → 再调 available-coupons
  │     └─ pick() 探测 name/couponName、expire/expireDate/endTime
  │
  ├─ enrich()：算每张券剩余天数
  ├─ score_window()：对午饭/晚饭两个窗口打分
  └─ render()：输出 Markdown（券表 + 活动表 + 排序建议 + 结论）
```

### 为什么必须用 `now-time-info` 而不是 `datetime.now()`

评分的第一大项是「当前是不是在饭点内」。如果本机时钟偏了几十分钟，结论会完全反过来（「去吃饭」变成「还早」）。用服务端时间作为基准，可以让工具在**时钟不准的电脑上**也给出正确建议。

本项目仍然保留了本机时间兜底——但只在解析失败时使用，并**显式打印提示**，不静默降级。

## 4. 字段映射与容错

| 内部字段 | 探测的字段别名 |
|---|---|
| 券名 | `name` / `couponName` / `title` / `券名称` |
| 到期 | `expire` / `expireDate` / `endTime` / `validEnd` / `有效期` |
| 活动名 | `name` / `title` / `campaignName` / `活动名称` |
| 活动日期 | `date` / `startDate` / `beginTime` / `startTime` / `时间` |
| 当前时间 | `time` / `now` / `currentTime` / `timestamp` / `serverTime` / `dateTime` |

`parse_time()` 支持四种常见格式：

| 输入形态 | 处理 |
|---|---|
| `1761100000`（10 位） | 秒级时间戳 |
| `1761100000000`（13 位） | 毫秒时间戳，先除 1000 |
| `2026-10-22` / `2026/10/22` | 日期，时间补 00:00 |
| `2026-10-22T11:40:00+08:00` | 取前 16 位解析，忽略时区后缀 |

响应体若被 markdown 围栏包裹（```` ```json ````），先剥围栏再解析；若是 `content[].text` 拼成的字符串，也走同一条路径。

## 5. 业务价值

| 价值 | 说明 |
|---|---|
| **减少券的浪费** | 券过期是纯损失。把「今明到期」提到评分里，用户会自然用掉 |
| **把促销变成可执行的时机** | 活动日历本来只是公告，本工具把它变成「今天优先哪个饭点」 |
| **适配碎片化日程** | 上课 / 备考 / 兼职的人时间被切碎，需要的是「几点去」，不是「吃什么」 |
| **输出即可用** | Markdown 直接贴进日记 / 计划表，不用二次整理 |
| **只读、安全** | 不碰下单、不碰绑券，用户没有任何被误操作的风险 |

## 6. 自行验证

```bash
python tools/meal_window.py --demo          # 离线跑通
python tools/meal_window.py --json          # JSON 输出（可接别的脚本）
python tools/mcd_mcp.py tools               # 看服务端真实工具与参数 schema
python tools/mcd_mcp.py call now-time-info "{}"
```

## 7. 合规

- Token 只从环境变量 `MCD_MCP_TOKEN` 读取，不硬编码、不进版本库。
- 券与活动信息以麦当劳官方渠道实时结果为准；本工具只做时间与优先级排序，不代为下单。
- 本项目为参赛作品，非麦当劳官方产品。

# QQ Homework Bot

基于 NoneBot2 + OneBot V11 的个人 QQ 机器人，用于：

- 设置任意提醒（取快递、开会、吃药...），到点私信通知
- 管理待完成作业（命令 / JSON 文件 / 周期性规则三种方式）
- 自动发送作业截止提醒（4 级递进提醒）
- 查看今日课程和学期课表，为指定课程生成上课提醒
- 每日 08:00 推送早报（今日课程 + 近期作业 + 今日提醒）
- 接入 LLM，支持自然语言交互（添加作业、设置提醒、智能问答）

## License

本项目使用 [MIT License](./LICENSE)。

## 前置条件

- Python >= 3.10
- 一个 OneBot V11 协议实现端（如 [NapCat](https://github.com/NapNeko/NapCatQQ)、[Lagrange](https://github.com/LagrangeDev/Lagrange.Core)），用于连接 QQ
- （可选）[SDU_DeepSeek](https://github.com/futz12/SDU_DeepSeek) 或其他 OpenAI 兼容 API，用于 LLM 功能

## 快速开始

### 1. 安装依赖

```bash
python3 -m pip install -e .
```

### 2. 配置 `.env`

复制 `.env.example` 为 `.env`，修改你的 QQ 号：

```env
DRIVER=~fastapi
HOST=127.0.0.1
PORT=8080
SUPERUSERS=["YOUR_QQ_NUMBER"]
COMMAND_START=["/"]
COMMAND_SEP=["."]
OWNER_QQ=YOUR_QQ_NUMBER
DATA_REPO_DIR=../homework-reminder-private
```

说明：

- `OWNER_QQ`：接收提醒私信的 QQ 号，必须是纯数字。不填则 Bot 正常运行但不会发送提醒
- `DATA_REPO_DIR`：私有数据仓库路径（可选）。如果同级目录存在 `homework-reminder-private`，无需填写，代码会自动发现

### 3. 准备数据文件

推荐把代码仓库和私有数据仓库放在同一父目录下：

```text
your-workspace/
├── homework-reminder/           # 公开仓库（代码）
└── homework-reminder-private/   # 私有仓库（数据）
    ├── config.json
    ├── course.txt
    ├── assignments.json
    ├── recurring_assignments.json
    └── data/                    # 自动生成
```

代码按以下顺序查找数据目录：

1. 环境变量 `DATA_REPO_DIR`
2. 同级目录 `../homework-reminder-private`
3. 当前项目根目录（fallback）

示例文件在 [`examples/`](./examples) 下，可以直接拷贝到私有仓库修改。

各文件说明：

| 文件 | 是否必须 | 说明 |
|------|----------|------|
| `config.json` | 课程功能需要 | 学期信息和上课提醒配置 |
| `course.txt` | 课程功能需要 | 教务系统导出的课表（Tab 分隔） |
| `assignments.json` | 否 | 从文件批量导入作业 |
| `recurring_assignments.json` | 否 | 周期性作业规则 |
| `data/assignments.db` | 自动生成 | SQLite 数据库，无需手动管理 |

### 4. 启动

```bash
python3 bot.py
```

在 OneBot V11 实现端中配置反向 WebSocket 连接：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

连接成功后，私信发送 `/help` 查看所有命令。

### 5. 启用 LLM（可选）

在 `.env` 中添加 LLM 配置：

```env
LLM_API_BASE=http://localhost:8000/v1
LLM_API_KEY=sk-no-key
LLM_MODEL=deepseek-ai/DeepSeek-V3.2
```

需要先启动 [SDU_DeepSeek](https://github.com/futz12/SDU_DeepSeek) 代理（或任何 OpenAI 兼容 API）。配置后，所有不匹配命令的私聊消息会自动交给 LLM 处理。不配置则 LLM 功能静默跳过，不影响其他功能。

---

## 命令

### 快捷操作

私聊直接发送，无需 `/` 前缀：

| 发送 | 效果 |
|------|------|
| `3` | 标记 #3 完成 |
| `x3` | 删除 #3 |
| `ls` 或 `作业` | 查看待完成作业 |
| `今日` 或 `today` | 查看今日课程 |

### 完整命令

**作业管理：**

| 命令 | 说明 | 别名 |
|------|------|------|
| `/add <课程> <时间> <描述>` | 添加作业 | `/添加` |
| `/done <编号>` | 标记完成 | `/完成` |
| `/delete <编号>` | 删除作业 | `/del` `/删除` |
| `/list` | 查看待完成作业 | `/ls` `/作业` |
| `/stats` | 查看作业统计 | `/统计` |
| `/rules` | 查看周期性作业规则 | `/周期作业` `/周期` |

**提醒：**

| 命令 | 说明 | 别名 |
|------|------|------|
| `/remind <时间> <内容>` | 设置提醒 | `/提醒` |
| `/reminders` | 查看待发送提醒 | `/提醒列表` |
| `/cancel <编号>` | 取消提醒 | `/取消` |

**课程 & 其他：**

| 命令 | 说明 | 别名 |
|------|------|------|
| `/today` | 查看今日课程 | `/今日` `/今天` |
| `/courses` | 查看学期课表 | `/课表` `/课程` |
| `/briefing` | 查看每日早报 | `/早报` |
| `/help` | 查看帮助 | `/帮助` |

### `/add` 时间格式

```text
/add 操作系统 明天 Lab3实验报告
/add 操作系统 下周五18:00 Lab3实验报告
/add 计算机网络 4月15日 Socket编程
/add 操作系统 2026-04-15-23:59 Lab3实验报告
```

支持的格式：

- `今天` `明天` `后天` `大后天`
- `周五` `周日` `下周一`
- `4月15日` `4月15号` `4/15`
- 以上格式后可追加时间，如 `明天18:00`
- 完整格式 `YYYY-MM-DD-HH:MM` 或 `YYYY-MM-DD HH:MM`
- 不写时间默认 23:59

### `/remind` 示例

```text
/remind 明天15:00 取快递
/remind 下周一9:00 交水电费
/remind 4月20日 体检
```

时间格式和 `/add` 相同。提醒到点后会通过私信发送。用 `/reminders` 查看所有待发送提醒，用 `/cancel <编号>` 取消。

### LLM 自然语言交互

启用 LLM 后，直接用自然语言私聊即可：

```text
帮我记一下下周五操作系统交Lab4
明天下午3点提醒我取快递
这周有几个作业要交？
有什么提醒？
取消 #12 的提醒
把 #3 标记完成
你好
```

LLM 会自动识别意图并执行对应操作（添加作业、设置/查看/取消提醒、查询作业等），无法识别时作为普通聊天回复。

---

## 数据文件详解

### `config.json`

用于 `/today`、`/courses` 和上课提醒。示例见 [`examples/config.example.json`](./examples/config.example.json)。

```json
{
    "semester_start": "2026-03-02",
    "period_start_times": {
        "1": "08:00", "2": "08:50",
        "3": "10:10", "4": "11:00",
        "5": "14:00", "6": "14:50",
        "7": "15:55", "8": "16:45",
        "9": "18:30", "10": "19:20"
    },
    "advance_minutes": 15,
    "courses": ["操作系统", "算法设计与分析"]
}
```

| 字段 | 说明 |
|------|------|
| `semester_start` | 学期第一周周一日期，格式 `YYYY-MM-DD` |
| `period_start_times` | 每节课开始时间，按学校作息填写 |
| `advance_minutes` | 上课前多少分钟发送提醒 |
| `courses` | 需要发送上课提醒的课程名称列表 |

- `/today` 显示当天**所有**课程，不受 `courses` 限制
- `courses` 只控制哪些课程会发私信提醒
- 修改后无需重启，系统会自动刷新

### `course.txt`

从教务系统导出的课表，保留 Tab 分隔格式。示例见 [`examples/course.example.txt`](./examples/course.example.txt)。

获取方式：登录教务系统 → 选课页面 → 全选课表内容 → 复制粘贴到文件。

### `assignments.json`

从文件批量导入作业。示例见 [`examples/assignments.example.json`](./examples/assignments.example.json)。

```json
[
    {
        "id": "os-lab-3",
        "course": "操作系统",
        "description": "Lab3 实验报告",
        "deadline": "2026-04-15 23:59"
    }
]
```

| 字段 | 是否必须 | 说明 |
|------|----------|------|
| `id` | 否（推荐） | 稳定标识；有它时修改内容仍会视为同一条作业 |
| `course` | 是 | 课程名 |
| `description` | 是 | 作业描述 |
| `deadline` | 是 | 截止时间，格式 `YYYY-MM-DD HH:MM` |

- 每 5 分钟自动同步
- 文件设为 `[]` 会清空所有 JSON 来源的未完成作业，不影响手动或周期作业

### `recurring_assignments.json`

定义周期性作业规则。示例见 [`examples/recurring_assignments.example.json`](./examples/recurring_assignments.example.json)。

```json
[
    {
        "id": "os-weekly-lab",
        "course": "操作系统",
        "description_template": "实验报告第{sequence}次 ({date})",
        "start_date": "2026-03-13",
        "time": "23:59",
        "interval_days": 7,
        "generate_days_ahead": 14,
        "end_date": "2026-06-26"
    }
]
```

| 字段 | 说明 |
|------|------|
| `id` | 规则唯一标识 |
| `course` | 课程名 |
| `description_template` | 描述模板，支持 `{sequence}` `{date}` `{deadline}` `{course}` |
| `start_date` | 第一次截止日期 `YYYY-MM-DD` |
| `time` | 每次截止时间 `HH:MM` |
| `interval_days` | 间隔天数（`7` = 每周一次） |
| `generate_days_ahead` | 提前多少天生成 |
| `end_date` | 可选，结束日期 |

---

## 提醒机制

### 作业提醒

每条作业自动生成 4 级提醒，通过私信发送到 `OWNER_QQ`：

| 提前时间 | 提醒文案 |
|---------|---------|
| 3 天 | 还有不到3天 |
| 24 小时 | 还有不到24小时 |
| 3 小时 | 还有不到3小时 |
| 1 小时 | 还有不到1小时! |

### 上课提醒

`config.json` 中 `courses` 列出的课程，上课前发送私信提醒（包含课程名、节次、时间、教师、教室）。

### 自定义提醒

通过 `/remind` 命令或 LLM 自然语言设置。到点后通过私信发送。用 `/reminders` 查看、`/cancel` 取消。

### 每日早报

每天 08:00 自动推送，包含今日课程、近 3 天截止作业和今日自定义提醒。也可发送 `/briefing` 手动查看。

### 定时任务总览

| 任务 | 频率 | 说明 |
|------|------|------|
| 提醒发送 | 每 2 分钟 | 扫描并发送已到点的提醒（作业/课程/自定义） |
| 作业源同步 | 每 5 分钟 | 同步 `assignments.json` 和 `recurring_assignments.json` |
| 课程提醒刷新 | 每 10 分钟 | 按当前配置刷新当天未发送的上课提醒 |
| 每日课程提醒 | 每天 00:05 | 生成当天的上课提醒 |
| 旧提醒清理 | 每天 00:30 | 清理 7 天前已发送的提醒 |
| 每日早报 | 每天 08:00 | 推送今日课程 + 近期作业 + 今日提醒 |

---

## 三种作业来源

| 来源 | 添加方式 | 同步频率 |
|------|---------|---------|
| 手动 | `/add` 命令或 LLM 自然语言 | 即时 |
| JSON | 编辑 `assignments.json` | 每 5 分钟 |
| 周期 | 编辑 `recurring_assignments.json` | 每 5 分钟 |

三种来源彼此完全隔离：删除手动作业不影响 JSON 导入的，清空 JSON 文件不影响手动或周期生成的。

---

## 技术栈

- Python 3.10+
- [NoneBot2](https://nonebot.dev/) + FastAPI
- [OneBot V11](https://github.com/botuniverse/onebot-11) 协议适配器
- APScheduler 定时任务
- SQLite (aiosqlite) 数据存储
- [OpenAI Python SDK](https://github.com/openai/openai-python) 用于 LLM 接入

---

## 目录结构

```text
homework-reminder/
├── LICENSE
├── README.md
├── .env.example
├── .gitignore
├── bot.py                          # 入口
├── pyproject.toml                  # 依赖
├── examples/                       # 数据文件示例
│   ├── config.example.json
│   ├── course.example.txt
│   ├── assignments.example.json
│   └── recurring_assignments.example.json
└── plugins/homework/
    ├── __init__.py                 # 插件入口 & 启动钩子
    ├── config.py                   # 读取 .env 配置
    ├── paths.py                    # 路径解析（支持公私仓库分离）
    ├── models.py                   # 数据模型
    ├── database.py                 # SQLite 数据库操作
    ├── assignment_service.py       # 作业业务逻辑
    ├── recurring_service.py        # 周期性作业规则
    ├── time_parser.py              # 自然时间解析（明天、下周五、4月15日...）
    ├── commands.py                 # 命令处理
    ├── scheduler.py                # 定时同步任务
    ├── sync.py                     # 同步编排
    ├── course_parser.py            # course.txt 解析
    ├── course_reminder.py          # 上课提醒生成
    ├── course_service.py           # 课程展示
    ├── daily_briefing.py           # 每日早报
    ├── reminder_engine.py          # 提醒发送引擎
    ├── llm_service.py              # LLM API 调用 & 意图解析
    └── llm_handler.py              # LLM 消息回落处理
```

---

## 常见问题

### 改了 `assignments.json` 但作业编号变了

没有填 `id` 时，系统按内容哈希判断身份。修改内容 = 新作业。建议给每条作业加固定 `id`。

### `/today` 显示没有课程

1. 检查 `config.json` 是否存在且 `semester_start` 正确（学期第一周周一）
2. 检查 `course.txt` 是否存在且为教务系统导出的 Tab 分隔文本
3. 确认今天有课（注意周数范围，某些课可能只在特定周上）

### 机器人不发提醒

1. `.env` 中 `OWNER_QQ` 是否为纯数字
2. OneBot 实现端是否已连接（检查启动日志）
3. 提醒时间是否已到达（作业提醒最早在截止前 3 天发送）

### LLM 不回复

1. `.env` 中 `LLM_API_BASE` 是否正确
2. LLM 服务（SDU_DeepSeek 代理等）是否已启动
3. 检查 Bot 日志中是否有 `LLM API error` 报错

### 如何获取 `course.txt`

登录学校教务系统 → 选课页面 → 全选课表内容 → 复制粘贴到 `course.txt`，保持 Tab 分隔格式。

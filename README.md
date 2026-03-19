# QQ Reminder Bot

基于 NoneBot2 + OneBot V11 的多用户 QQ 机器人，用于：

- 多用户支持：角色权限（root / 管理员 / 用户）、注册审批
- 课程订阅：每个用户独立选择关注的课程
- 管理待完成作业（命令 / JSON 文件 / 周期性规则三种方式），每人独立标记完成
- 自动发送作业截止提醒（4 级递进提醒），按用户订阅独立发送
- 设置任意个人提醒（取快递、开会、吃药...），到点私信通知
- 查看今日课程和学期课表，为已订阅课程生成上课提醒
- 每日 08:00 推送个性化早报（今日课程 + 近期作业 + 今日提醒）
- 接入 LLM，支持自然语言交互（添加作业、设置提醒、智能问答）

## License

本项目使用 [MIT License](./LICENSE)。

## 前置条件

- Python >= 3.10
- 一个 OneBot V11 协议实现端（如 [NapCat](https://github.com/NapNeko/NapCatQQ)、[Lagrange](https://github.com/LagrangeDev/Lagrange.Core)），用于连接 QQ
- （可选）[SDU_DeepSeek](https://github.com/futz12/SDU_DeepSeek) 或其他 OpenAI 兼容 API，用于 LLM 功能

## 快速开始

### 1. 启动脚本（推荐）

```bash
./start.sh
```

`start.sh` 会自动：

- 创建 `.venv`
- 安装依赖
- 首次运行时交互生成 `.env`
- 最后执行 `python bot.py`

如果你不想使用脚本，也可以按下面步骤手动配置并启动。

### 2. 手动配置 `.env`（可选）

如果你已经用 `./start.sh` 生成过 `.env`，这一节可以跳过。手动方式是复制 `.env.example` 为 `.env`，再修改你的 QQ 号：

```env
DRIVER=~fastapi
HOST=127.0.0.1
PORT=8080
SUPERUSERS=["YOUR_QQ_NUMBER"]
COMMAND_START=["/"]
COMMAND_SEP=["."]
OWNER_QQ=YOUR_QQ_NUMBER
DATA_REPO_DIR=../qq-reminder-private
```

说明：

- `OWNER_QQ`：Bot 管理员（root）的 QQ 号，必须是纯数字。启动后自动成为 root 用户并订阅所有课程
- `DATA_REPO_DIR`：私有数据仓库路径（可选）。如果未填写，代码会自动查找同级目录 `<当前仓库目录名>-private`。例如当前仓库目录名是 `qq-reminder`，则默认查找 `../qq-reminder-private`

### 3. 准备数据文件

推荐把代码仓库和私有数据仓库放在同一父目录下：

```text
your-workspace/
├── qq-reminder/              # 公开仓库（代码）
└── qq-reminder-private/      # 私有仓库（数据）
    ├── config.json
    ├── course.txt
    ├── assignments.json
    ├── recurring_assignments.json
    └── data/                    # 自动生成
```

如果你的公开仓库目录名不是 `qq-reminder`，默认私有目录名也应改成 `<仓库目录名>-private`，或者直接在 `.env` 里显式设置 `DATA_REPO_DIR`。

代码按以下顺序查找数据目录：

1. 环境变量 `DATA_REPO_DIR`
2. 同级目录 `../<当前仓库目录名>-private`
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
./start.sh
```

`./start.sh` 已经会在最后启动 bot，本项目内不需要再额外执行其他“启动 bot”的命令。

如果你想手动启动，也可以执行：

```bash
python3 bot.py
```

启动后保持这个终端不要关闭。默认监听 `.env` 中的 `HOST:PORT`，例如 `0.0.0.0:8080`。

### 5. 登录 QQ / 接入 OneBot

这个仓库本身不提供 QQ 登录界面。QQ 登录发生在 OneBot V11 实现端里，例如 NapCat 或 Lagrange。

你需要：

1. 单独启动 NapCat / Lagrange
2. 在 NapCat / Lagrange 里登录 QQ
3. 在 OneBot V11 实现端中配置反向 WebSocket 连接到本项目

同机部署时，反向 WebSocket 一般配置为：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

如果 OneBot 实现端和本项目不在同一台机器或容器里，把 `127.0.0.1` 改成 bot 实际可访问的地址，并确保端口与 `.env` 中的 `PORT` 一致。

连接成功后，`OWNER_QQ` 私信发送 `/help` 查看所有命令。新用户私聊 Bot 发送 `/register` 即可提交注册申请，进入 `pending` 待审核状态；管理员执行 `/approve` 审批后即可使用。

### 6. 启用 LLM（可选）

在 `.env` 中添加 LLM 配置：

```env
LLM_API_BASE=http://localhost:8000/v1
LLM_API_KEY=sk-no-key
LLM_MODEL=deepseek-ai/DeepSeek-V3.2
```

需要先启动 [SDU_DeepSeek](https://github.com/futz12/SDU_DeepSeek) 代理（或任何 OpenAI 兼容 API）。配置后，所有不匹配命令的私聊消息会自动交给 LLM 处理。不配置则 LLM 功能静默跳过，不影响其他功能。

---

## 用户系统

### 角色

| 角色 | 说明 | 权限 |
|------|------|------|
| `root` | `OWNER_QQ` 对应的用户，启动时自动创建 | 所有权限 + 授予管理员 |
| `admin` | 管理员，由 root 授予 | 管理公共作业/公共课程 + 审批用户 + 所有用户权限 |
| `user` | 普通用户，由管理员/root 审批 | 查看/完成作业、设置提醒、订阅课程、管理自己的私人作业和私人课程 |
| `pending` | 待审核，新用户私聊 Bot 发送 `/register` 后进入该状态 | 无权限，等待审批 |

### 注册流程

1. 新用户私聊 Bot 发送 `/register` → 注册为 `pending`，收到"等待审核"提示
2. 管理员执行 `/approve` 查看待审核列表
3. 管理员执行 `/approve <QQ号>` 审批通过
4. 用户收到审批通过后即可使用所有功能，建议先发送 `/subscribe` 查看可选课程，再按需订阅

---

## 课程订阅

每个用户独立选择关注的课程。只有已订阅的课程才会出现在：

- `/today` 今日课程
- `/list` `/ls` `/作业` 待完成作业列表
- `/stats` 作业统计
- 作业提醒（只收到已订阅课程的作业提醒）
- 上课提醒（需对已订阅课程额外开启 `/notify`）
- 每日早报

| 命令 | 说明 | 别名 |
|------|------|------|
| `/subscribe` | 查看可选课程和当前订阅 | `/订阅` `/选课` |
| `/subscribe <课程1> <课程2>` | 订阅指定课程 | |
| `/subscribe all` | 订阅所有课程 | |
| `/unsubscribe <课程1>` | 退订课程 | `/退课` `/取消订阅` |
| `/mycourses` | 查看已订阅课程 | `/我的课程` |
| `/notify` | 查看课程上课提醒开关状态 | `/课程提醒` |
| `/notify <课程名>` | 开启/关闭该课程的上课提醒 | |

`root` 用户启动时自动订阅所有课程。新用户审批后需手动订阅。

---

## 命令

### 快捷操作

私聊直接发送，无需 `/` 前缀：

| 发送 | 效果 | 权限 |
|------|------|------|
| `3` | 标记 #3 完成 | 用户+ |
| `x3` | 删除 #3（管理员删公共，用户删自己的私人） | 用户+ |
| `ls` 或 `作业` | 查看待完成作业 | 用户+ |
| `今日` 或 `today` | 查看今日课程 | 用户+ |

### 作业管理

| 命令 | 说明 | 权限 | 别名 |
|------|------|------|------|
| `/add <课程> <时间> <描述>` | 添加作业。管理员添加公共作业，普通用户添加私人作业 | 用户+ | `/添加` |
| `/done <编号>` | 标记完成（仅自己） | 用户+ | `/完成` |
| `/delete <编号>` | 删除作业。管理员可删公共作业，普通用户只能删自己的私人作业 | 用户+ | `/del` `/删除` |
| `/list` | 查看待完成作业 | 用户+ | `/ls` `/作业` |
| `/stats` | 查看作业统计 | 用户+ | `/统计` |
| `/rules` | 查看周期性作业规则 | 用户+ | `/周期作业` `/周期` |

### 提醒

| 命令 | 说明 | 权限 | 别名 |
|------|------|------|------|
| `/remind <时间> <内容>` | 设置个人提醒 | 用户+ | `/提醒` |
| `/reminders` | 查看待发送提醒 | 用户+ | `/提醒列表` |
| `/cancel <编号>` | 取消提醒 | 用户+ | `/取消` |

### 课程 & 其他

| 命令 | 说明 | 权限 | 别名 |
|------|------|------|------|
| `/register` | 提交注册申请 | 所有人 | `/注册` |
| `/today` | 查看今日课程（按订阅） | 用户+ | `/今日` `/今天` |
| `/courses` | 查看学期完整课表 | 用户+ | `/课表` `/课程` |
| `/briefing` | 查看每日早报 | 用户+ | `/早报` |
| `/help` | 查看帮助 | 所有人 | `/帮助` |

### 课程管理

| 命令 | 说明 | 权限 | 别名 |
|------|------|------|------|
| `/addcourse <课程名> <时间>` | 添加课程。管理员添加公共课程，普通用户添加私人课程 | 用户+ | `/添加课程` |
| `/delcourse <课程名>` | 删除课程。管理员可删公共课程，普通用户只能删自己的私人课程 | 用户+ | `/删除课程` |
| `/notify` | 查看已订阅课程的上课提醒开关状态 | 用户+ | `/课程提醒` |
| `/notify <课程名>` | 开启/关闭该课程的上课提醒 | 用户+ | |

自定义课程的课程名当前按全局唯一处理，不能与现有公共或私人课程重名。  
`/addcourse` 的时间格式必须是 `X-Y周 星期Z A-B`，多个时间段用分号分隔。

### 用户管理

| 命令 | 说明 | 权限 | 别名 |
|------|------|------|------|
| `/approve` | 查看待审核用户 | 管理员+ | `/审核` `/批准` |
| `/approve <QQ号>` | 审批用户为普通用户 | 管理员+ | |
| `/approve <QQ号> admin` | 审批用户为管理员 | root | |
| `/users` | 查看所有用户 | 管理员+ | `/用户列表` |

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

LLM 会自动识别意图并执行对应操作。普通用户可以管理自己的私人作业、私人课程和个人提醒；管理员可通过自然语言管理公共作业、公共课程，并审批普通用户；root 额外可以通过自然语言授予管理员。未审批用户只能进行公开问答。

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
    "advance_minutes": 180
}
```

| 字段 | 说明 |
|------|------|
| `semester_start` | 学期第一周周一日期，格式 `YYYY-MM-DD` |
| `period_start_times` | 每节课开始时间，按学校作息填写 |
| `advance_minutes` | 上课前多少分钟发送提醒，默认 180（3 小时） |

- 上课提醒按用户订阅发送，只有订阅了对应课程的用户才会收到
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
- 文件设为 `[]` 会清空所有 JSON 来源的未完成作业（已有用户完成的作业不会被删除），不影响手动或周期作业

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

每条作业为每个订阅了对应课程的用户独立生成 4 级提醒：

| 提前时间 | 提醒文案 |
|---------|---------|
| 3 天 | 还有不到3天 |
| 24 小时 | 还有不到24小时 |
| 3 小时 | 还有不到3小时 |
| 1 小时 | 还有不到1小时! |

用户标记完成后，该用户的后续提醒自动取消，不影响其他用户。

### 上课提醒

对已订阅课程单独开启 `/notify` 后，用户会收到上课提醒（包含课程名、节次、时间、教师、教室）。

### 自定义提醒

通过 `/remind` 命令或 LLM 自然语言设置。每个用户的提醒完全独立，到点后通过私信发送。用 `/reminders` 查看、`/cancel` 取消。

### 每日早报

每天 08:00 向所有已审批用户推送个性化早报，包含：

- 今日课程（按用户订阅过滤）
- 近 3 天截止作业（按用户完成状态过滤）
- 今日自定义提醒

也可发送 `/briefing` 手动查看。

### 定时任务总览

| 任务 | 频率 | 说明 |
|------|------|------|
| 提醒发送 | 每 2 分钟 | 扫描并发送已到点的提醒（按 user_id 发给对应用户） |
| 作业源同步 | 每 5 分钟 | 同步 `assignments.json` 和 `recurring_assignments.json` |
| 课程提醒刷新 | 每 10 分钟 | 按用户订阅刷新当天未发送的上课提醒 |
| 每日课程提醒 | 每天 00:05 | 生成当天的上课提醒 |
| 旧提醒清理 | 每天 00:30 | 清理 7 天前已发送的提醒 |
| 每日早报 | 每天 08:00 | 向所有已审批用户推送个性化早报 |

---

## 三种作业来源

| 来源 | 添加方式 | 同步频率 |
|------|---------|---------|
| 手动 | `/add` 命令或 LLM 自然语言（管理员=公共，普通用户=私人） | 即时 |
| JSON | 编辑 `assignments.json`（管理员） | 每 5 分钟 |
| 周期 | 编辑 `recurring_assignments.json`（管理员） | 每 5 分钟 |

三种来源彼此完全隔离：删除手动作业不影响 JSON 导入的，清空 JSON 文件不影响手动或周期生成的。

公共作业由管理员管理，普通用户也可以维护自己的私人作业；完成状态始终是每个用户独立的。

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
qq-reminder/
├── LICENSE
├── README.md
├── .env.example
├── .gitignore
├── start.sh                        # 一键启动脚本
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
    ├── database.py                 # SQLite 数据库操作（5 张表）
    ├── user_service.py             # 用户管理 & 课程订阅
    ├── assignment_service.py       # 作业业务逻辑
    ├── recurring_service.py        # 周期性作业规则
    ├── time_parser.py              # 自然时间解析（明天、下周五、4月15日...）
    ├── commands.py                 # 命令处理（含权限检查）
    ├── scheduler.py                # 定时同步任务
    ├── sync.py                     # 同步编排
    ├── course_parser.py            # course.txt 解析
    ├── course_reminder.py          # 上课提醒生成（按用户订阅）
    ├── course_service.py           # 课程展示
    ├── daily_briefing.py           # 每日早报（按用户个性化）
    ├── reminder_engine.py          # 提醒发送引擎（按用户发送）
    ├── llm_service.py              # LLM API 调用 & 意图解析
    └── llm_handler.py              # LLM 消息回落处理
```

---

## 常见问题

### 在哪里登录 QQ

不在这个仓库里登录。这个仓库只负责启动 NoneBot 服务；你需要在 NapCat、Lagrange 等 OneBot V11 实现端里登录 QQ，然后把反向 WebSocket 指到本项目，例如 `ws://127.0.0.1:8080/onebot/v11/ws`。

### 新用户私聊 Bot 没有反应

新用户先私聊 Bot 发送 `/register` 提交注册申请，随后会进入 `pending` 状态。管理员执行 `/approve <QQ号>` 审批后才能使用。

### 审批后看不到作业

审批通过后需要先订阅课程：发送 `/subscribe` 查看可选课程，再用 `/subscribe <课程名>` 按需订阅。

### 改了 `assignments.json` 但作业编号变了

没有填 `id` 时，系统按内容哈希判断身份。修改内容 = 新作业。建议给每条作业加固定 `id`。

### `/today` 显示没有课程

1. 检查 `config.json` 是否存在且 `semester_start` 正确（学期第一周周一）
2. 检查 `course.txt` 是否存在且为教务系统导出的 Tab 分隔文本
3. 确认你已订阅了课程（`/mycourses` 查看，`/subscribe` 订阅）
4. 确认今天有课（注意周数范围，某些课可能只在特定周上）

### 机器人不发提醒

1. `.env` 中 `OWNER_QQ` 是否为纯数字
2. OneBot 实现端是否已连接（检查启动日志）
3. 是否已订阅对应课程（`/mycourses` 查看）；上课提醒还需要 `/notify <课程名>` 开启
4. 提醒时间是否已到达（作业提醒最早在截止前 3 天发送）

### LLM 不回复

1. `.env` 中 `LLM_API_BASE` 是否正确
2. LLM 服务（SDU_DeepSeek 代理等）是否已启动
3. 检查 Bot 日志中是否有 `LLM API error` 报错
4. 未审批用户只能使用公开问答，不能执行个人操作
5. 是否触发了 AI 频率限制

### 如何获取 `course.txt`

登录学校教务系统 → 选课页面 → 全选课表内容 → 复制粘贴到 `course.txt`，保持 Tab 分隔格式。

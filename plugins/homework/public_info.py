from __future__ import annotations

import re

PUBLIC_BOT_GUIDE = """
项目简介:
- 这是一个开源 QQ Reminder Bot，基于 NoneBot2 + OneBot V11。
- 主要功能包括注册审批、课程订阅、作业管理、个人提醒、今日课程、上课提醒、每日早报和自然语言交互。

注册与使用:
- 新用户私聊机器人发送 /register 即可提交注册申请，进入待审核状态。
- 管理员审批通过后即可使用全部功能，建议发送 /subscribe all 订阅全部课程。
- 机器人不会公开管理员身份、QQ 号或审批名单。
- 自定义课程名当前全局唯一，不能与现有公共或私人课程重名。

常用命令:
- /register 提交注册申请
- /help 查看帮助和注册方式
- /subscribe /unsubscribe /mycourses 管理课程订阅
- /list /add /done /delete /stats /rules 管理作业
- /remind /reminders /cancel 管理个人提醒
- /today /courses /briefing /notify 查看课程和提醒
- /approve /users 是管理员命令

自然语言:
- 审批通过后，可以直接用自然语言添加/删除自己的作业、设置提醒、查看作业和课程。
- 管理员还可以通过自然语言管理公共作业和公共课程。
- 未审批用户可以询问公开功能和使用方式，但不能执行个人操作。

公开技术信息:
- 项目代码开源。
- 数据存储使用 SQLite。
- LLM 功能支持 OpenAI 兼容 API。
""".strip()

SENSITIVE_REPLY = (
    "我可以介绍公开功能和使用方式，但不会提供管理员身份、QQ 号、审批列表、"
    "运行时配置、密钥、文件路径、日志或数据库内容。"
)

PREAPPROVAL_REPLY = (
    "这些个人功能需要先注册并通过审核。请先私聊发送 /register 提交注册申请，"
    "审核通过后就可以添加作业、设置提醒和查看个人数据。"
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def get_sensitive_query_reply(text: str) -> str | None:
    compact = _compact(text)
    if not compact:
        return None

    if _contains_any(
        compact,
        (
            "管理员qq",
            "rootqq",
            "ownerqq",
            "管理员是谁",
            "root是谁",
            "owner是谁",
            "超级用户是谁",
            "管理员号码",
            "root号码",
            "管理员联系方式",
            "管理员账号",
            "owner_qq",
            "superusers",
        ),
    ):
        return SENSITIVE_REPLY

    if _contains_any(
        compact,
        (
            "待审核用户",
            "审批列表",
            "pending用户",
            "用户列表",
            "所有用户",
            "注册名单",
            "谁注册了",
            "有哪些用户",
        ),
    ):
        return SENSITIVE_REPLY

    if _contains_any(
        compact,
        (
            ".env",
            "apikey",
            "api_key",
            "token",
            "secret",
            "密钥",
            "密码",
            "口令",
            "私钥",
            "accesskey",
            "llm_api_base",
            "llm_model",
            "baseurl",
            "api地址",
            "接口地址",
            "当前模型",
            "实际模型",
            "现在用什么模型",
        ),
    ):
        return SENSITIVE_REPLY

    if _contains_any(
        compact,
        (
            "数据库路径",
            "文件路径",
            "绝对路径",
            "本地路径",
            "data_repo_dir",
            "assignments.db",
            "users表",
            "reminders表",
            "数据库内容",
            "导出数据",
            "全部数据",
            "日志",
            "系统提示词",
            "prompt",
            "限流规则",
            "频率限制",
            "运行配置",
        ),
    ):
        return SENSITIVE_REPLY

    return None


def get_local_public_reply(text: str) -> str | None:
    compact = _compact(text)
    if not compact:
        return None

    if _contains_any(
        compact,
        (
            "你能做什么",
            "你会什么",
            "能干什么",
            "有什么功能",
            "bot能做什么",
            "机器人能做什么",
            "这个bot能做什么",
            "这个机器人能做什么",
            "功能介绍",
            "项目介绍",
            "bot介绍",
            "机器人介绍",
            "这是干什么的",
            "这是做什么的",
        ),
    ):
        return (
            "这个 bot 是一个开源 QQ 提醒机器人，支持注册审批、课程订阅、作业管理、"
            "个人提醒、今日课程、上课提醒、每日早报，以及自然语言交互。"
        )

    if _contains_any(
        compact,
        (
            "怎么注册",
            "如何注册",
            "注册方式",
            "怎么申请",
            "如何申请",
            "怎么加入",
            "怎么开始用",
            "如何开始用",
        ),
    ):
        return (
            "新用户私聊发送 /register 就会提交注册申请，进入待审核状态。"
            "管理员审批通过后即可使用全部功能，建议再发送 /subscribe all 订阅全部课程。"
        )

    if _contains_any(
        compact,
        (
            "怎么审批",
            "如何审批",
            "怎么审核",
            "如何审核",
            "approve",
            "审核流程",
        ),
    ):
        return (
            "注册后需要管理员审批。管理员可以用 /approve 查看待审核用户并审批，"
            "也可以用 /users 查看用户列表。机器人不会公开管理员身份信息。"
        )

    if compact in ("help", "/help", "帮助", "/帮助") or _contains_any(
        compact,
        ("有哪些命令", "有哪些指令", "命令", "指令", "使用方法", "怎么用这个bot"),
    ):
        return (
            "常用命令有：/register 提交注册申请，/help 查看帮助，/subscribe 管理订阅，/list 查看作业，"
            "/add 添加作业，/done 标记完成，/remind 设置提醒，/today 查看今日课程，"
            "/briefing 查看早报。审批通过后也支持直接用自然语言操作。"
        )

    if _contains_any(
        compact,
        ("怎么订阅", "如何订阅", "怎么选课", "如何选课", "怎么退订", "如何退订"),
    ):
        return (
            "审批通过后可以用 /subscribe 查看可选课程，/subscribe all 订阅全部课程，"
            "/unsubscribe 退订，/mycourses 查看已订阅课程。"
        )

    if _contains_any(
        compact,
        (
            "怎么加作业",
            "如何加作业",
            "怎么添加作业",
            "如何添加作业",
            "作业怎么用",
            "怎么完成作业",
            "如何删除作业",
        ),
    ):
        return (
            "作业相关命令有 /list、/add、/done、/delete、/stats、/rules。"
            "管理员添加的是公共作业，普通用户添加的是私人作业。"
        )

    if _contains_any(
        compact,
        (
            "怎么设置提醒",
            "如何设置提醒",
            "提醒怎么用",
            "怎么取消提醒",
            "怎么查看提醒",
        ),
    ):
        return (
            "提醒相关命令有 /remind、/reminders、/cancel。"
            "审批通过后也可以直接用自然语言说“明天下午 3 点提醒我开会”。"
        )

    if _contains_any(
        compact,
        (
            "怎么查看课表",
            "怎么查看今日课程",
            "怎么开课程提醒",
            "上课提醒怎么开",
            "课程功能",
            "课表怎么用",
        ),
    ):
        return (
            "课程相关命令有 /today、/courses、/briefing、/notify、/addcourse、/delcourse。"
            "/notify 可以开关上课提醒。自定义课程名当前全局唯一。"
        )

    if _contains_any(
        compact,
        (
            "自然语言",
            "可以聊天吗",
            "能直接说话吗",
            "ai能做什么",
            "llm",
        ),
    ):
        return (
            "审批通过后，你可以直接用自然语言添加或删除自己的作业、设置提醒、查看作业和课程。"
            "未审批用户也可以先询问公开功能和使用方式。"
        )

    if _contains_any(
        compact,
        ("课程重名", "同名课程", "为什么不能添加同名课程", "课程名重复"),
    ):
        return (
            "自定义课程名当前按全局唯一处理，不能与现有公共或私人课程重名。"
            "这是一条当前实现限制。"
        )

    if _contains_any(
        compact,
        ("技术栈", "什么框架", "开源吗", "github", "源码", "sqlite"),
    ):
        return (
            "这是一个开源项目，基于 NoneBot2 + OneBot V11。"
            "数据存储使用 SQLite，也支持 OpenAI 兼容 API 的自然语言能力。"
        )

    return None


def get_preapproval_local_reply(text: str) -> str | None:
    compact = _compact(text)
    if not compact:
        return None

    if _contains_any(
        compact,
        (
            "提醒我",
            "帮我提醒",
            "帮我记",
            "帮我添加",
            "新增作业",
            "添加作业",
            "记个作业",
            "我的作业",
            "我的提醒",
            "今天有什么课",
            "查一下作业",
            "查提醒",
            "完成作业",
            "删除作业",
            "设置提醒",
            "查看提醒",
            "查看课表",
            "帮我订阅",
        ),
    ):
        return PREAPPROVAL_REPLY

    return None

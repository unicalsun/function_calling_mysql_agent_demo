"""
工具模块 - Function Calling 工具定义与实现

学习重点：
1. Function Calling Schema：定义 LLM 可以调用的工具（名称 + 描述 + 参数）
2. 工具描述的质量决定了 LLM 能否正确选择和调用工具
3. 双层安全机制：显式高危工具 + 隐式 SQL 检测
"""

import re

from app.db import Database, execute_shell_command

# 显式高危工具列表（通过 Function Calling 直接触发，需用户确认）
# 这些工具在 schema 中已经标注了"高危"，LLM 会选择调用它们
HIGH_RISK_TOOLS = [
    "mysqlbinlog",    # binlog 分析
    "mysqldump",      # 数据库导出
    "execute_ddl",    # 数据定义（CREATE/ALTER/DROP）
    "truncate_table", # 清空表
    "execute_dml",    # 数据操作（INSERT/UPDATE/DELETE）
    "cleanup_binlog", # 清理 binlog
]

# ========== Function Calling 工具 Schema ==========
# 这是 OpenAI Function Calling 的标准格式
# LLM 会读取这些 schema，理解每个工具的用途和参数

tools_schema = [
    {
        "type": "function",  # 固定格式，表示这是一个函数工具
        "function": {
            "name": "execute_sql_query",  # 工具名称，LLM 通过它来调用
            "description": "执行 SQL 查询语句（SELECT/SHOW/DESCRIBE/EXPLAIN）。用于数据库分析、数据查询、表结构查看等只读操作。如果用户要求执行 INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER 等写操作，请使用对应的专用工具。",
            # description 是关键：LLM 根据描述判断何时使用此工具
            # 越清晰准确，LLM 调用越正确
            "parameters": {
                "type": "object",  # 参数是 JSON 对象
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的 SQL 查询语句",
                    },
                },
                "required": ["sql"],  # 必填参数
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mysqlbinlog",
            "description": "读取和分析 MySQL binlog（二进制日志）。可用于查看数据库变更历史、数据恢复、审计等。⚠️ 高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "完整的 mysqlbinlog 命令，例如：mysqlbinlog --no-defaults --start-datetime='2024-01-01 00:00:00' /var/lib/mysql/binlog.000001",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_ddl",
            "description": "执行 DDL（数据定义）语句，包括 CREATE TABLE、ALTER TABLE、DROP TABLE、CREATE INDEX 等。⚠️ 高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的 DDL 语句",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "truncate_table",
            "description": "清空指定表的所有数据（TRUNCATE TABLE）。⚠️ 极高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "要清空的表名",
                    },
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_dml",
            "description": "执行 DML（数据操作）语句，包括 INSERT、UPDATE、DELETE。⚠️ 高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的 DML 语句（INSERT/UPDATE/DELETE）",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mysqldump",
            "description": "使用 mysqldump 导出数据库或表的结构和数据（备份）。⚠️ 高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "完整的 mysqldump 命令，例如：mysqldump -h192.168.3.12 -P3306 -uroot -p123456 dbname tablename > backup.sql",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cleanup_binlog",
            "description": "清理 MySQL binlog 文件。可用于释放磁盘空间。⚠️ 极高危操作，执行前需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "完整的清理命令，例如：PURGE BINARY LOGS BEFORE '2024-01-01 00:00:00'",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

# ========== 工具实现 ==========


def _confirm_operation(title: str, sql_or_cmd: str) -> bool:
    """
    Human-in-the-Loop：高危操作二次确认

    学习重点：
    - 这是 AI 安全的重要模式：让人类做最终决策
    - 即使 LLM 误判，人类确认也能阻止危险操作
    - 显示完整操作内容，让用户充分了解风险
    """
    from app.config import config

    print(f"\n{'='*60}")
    print(f"⚠️  高危操作确认：{title}")
    print(f"{'='*60}")
    print(f"目标数据库：{config.mysql_display}")
    print(f"操作内容：")
    print(f"  {sql_or_cmd}")
    print(f"{'='*60}")

    while True:
        answer = input("确认执行？(y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("请输入 y 或 n")


def execute_tool(name: str, arguments: dict) -> str:
    """
    工具执行入口（统一调度）

    双层安全机制：
    1. 显式高危工具：直接检查 HIGH_RISK_TOOLS 列表
    2. 隐式 SQL 检测：对 execute_sql_query 用正则检测危险语句

    这种设计确保：
    - 专用高危工具必定触发确认
    - 即使用户绕过工具直接写 SQL，也能被检测到
    """

    # ---- 第一层：显式高危工具，必须经过确认 ----
    if name in HIGH_RISK_TOOLS:
        confirm_msg = f"即将执行高危操作：{name}"

        if not _confirm_operation(name, str(arguments.get("sql") or arguments.get("command") or arguments.get("table_name", ""))):
            return f"[用户拒绝] {confirm_msg}，操作已取消。"

        # 确认通过，执行工具
        result = _do_execute(name, arguments)
        return f"[用户确认通过] {confirm_msg}\n\n执行结果：\n{result}"

    # ---- 第二层：普通工具，自动检测 SQL 中的高危语句 ----
    if name == "execute_sql_query":
        sql = arguments.get("sql", "")
        risk = detect_risky_sql(sql)  # 正则检测
        if risk:
            if not _confirm_operation("SQL 安全检查", sql):
                return f"[用户拒绝] 检测到高危 SQL 语句 [{risk}]，操作已取消。"
        return Database.execute_query(sql)

    return _do_execute(name, arguments)


def _do_execute(name: str, arguments: dict) -> str:
    """
    实际执行工具逻辑（路由分发）

    根据工具名称调用对应的执行函数：
    - SQL 类工具 → Database.execute_query()
    - Shell 命令类工具 → execute_shell_command()
    """
    if name == "execute_sql_query":
        return Database.execute_query(arguments["sql"])

    if name == "mysqlbinlog":
        return execute_shell_command(arguments["command"])

    if name == "execute_ddl":
        return Database.execute_query(arguments["sql"])

    if name == "truncate_table":
        table = arguments["table_name"]
        # 反引号防止表名是 MySQL 关键字时出错
        return Database.execute_query(f"TRUNCATE TABLE `{table}`")

    if name == "execute_dml":
        return Database.execute_query(arguments["sql"])

    if name == "cleanup_binlog":
        return Database.execute_query(arguments["command"])

    if name == "mysqldump":
        return execute_shell_command(arguments["command"])

    return f"未知工具：{name}"


def detect_risky_sql(sql: str) -> str | None:
    """
    SQL 安全检测：用正则匹配高危操作

    学习重点：
    - 这是"防御性编程"的体现：不信任任何用户输入
    - 即使 LLM 没有选择专用高危工具，SQL 注入也能被拦截
    - 返回匹配到的操作类型（如 "DELETE"），用于提示用户
    - re.IGNORECASE 确保大小写不敏感

    注意：这是简化版检测，生产环境可能需要更复杂的解析器
    """
    s = sql.strip().rstrip(";").upper()  # 标准化：去空格、去分号、转大写
    patterns = [
        (r"^\s*TRUNCATE\b", "TRUNCATE"),
        (r"^\s*DROP\b", "DROP"),
        (r"^\s*ALTER\b", "ALTER"),
        (r"^\s*DELETE\b", "DELETE"),
        (r"^\s*UPDATE\b", "UPDATE"),
        (r"^\s*INSERT\b", "INSERT"),
        (r"^\s*CREATE\b", "CREATE"),
    ]
    for pattern, label in patterns:
        if re.match(pattern, s, re.IGNORECASE):
            return label
    return None

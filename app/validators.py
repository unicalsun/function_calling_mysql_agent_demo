"""
数据校验模块 - 对工具返回数据进行校验和格式验证

分层设计：
1. SQL 安全检测：防止 LLM 绕过专用工具直接执行写操作
2. 返回格式验证：确保结构化数据（如 JSON）可解析
3. 参数完整性检查：验证工具调用参数是否符合预期

学习重点：
- 校验是"防御性编程"的体现：不信任任何外部输入
- 多层校验比单层更可靠（纵深防御）
- 校验失败后给出明确的错误信息，便于后续修复
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional


# ========== 校验结果类型 ==========

@dataclass
class ValidationResult:
    """
    校验结果

    属性：
    - valid: 是否通过校验
    - errors: 错误列表（如果 valid=False）
    - hints: 修复建议（如果存在可自动修复的问题）
    """
    valid: bool
    errors: list = field(default_factory=list)
    hints: list = field(default_factory=list)

    def add_error(self, error: str):
        self.valid = False
        self.errors.append(error)

    def add_hint(self, hint: str):
        self.hints.append(hint)


# ========== SQL 安全检测 ==========

# 高危 SQL 关键字模式（大小写不敏感）
_RISKY_SQL_PATTERNS = [
    (r"^\s*TRUNCATE\b", "TRUNCATE", "使用 truncate_table 工具代替"),
    (r"^\s*DROP\s+(TABLE|INDEX|DATABASE)\b", "DROP", "使用 execute_ddl 工具代替，需用户确认"),
    (r"^\s*ALTER\s+TABLE\b", "ALTER", "使用 execute_ddl 工具代替"),
    (r"^\s*DELETE\s+FROM\b", "DELETE", "使用 execute_dml 工具代替，需用户确认"),
    (r"^\s*UPDATE\b.*\bSET\b", "UPDATE", "使用 execute_dml 工具代替，需用户确认"),
    (r"^\s*INSERT\s+INTO\b", "INSERT", "使用 execute_dml 工具代替"),
    (r"^\s*CREATE\s+(TABLE|INDEX|DATABASE)\b", "CREATE", "使用 execute_ddl 工具代替"),
    (r"^\s*PURGE\s+BINARY\s+LOGS\b", "PURGE_BINLOG", "使用 cleanup_binlog 工具代替"),
]


def validate_sql_safety(sql: str) -> ValidationResult:
    """
    校验 SQL 安全性：检测是否包含高危操作

    设计要点：
    - 对 execute_sql_query 工具进行隐式检测（第二层安全）
    - 即使 LLM 没有选择专用高危工具，也能拦截危险操作
    - 返回匹配到的操作类型和修复建议

    参数：
    - sql: 待检测的 SQL 语句

    返回：
    - ValidationResult: 包含校验结果和修复建议
    """
    result = ValidationResult(valid=True)
    cleaned = sql.strip().rstrip(";").upper()

    for pattern, op_type, suggestion in _RISKY_SQL_PATTERNS:
        if re.match(pattern, cleaned, re.IGNORECASE):
            result.add_error(f"检测到高危操作 [{op_type}]")
            result.add_hint(suggestion)
            # 只报告第一个匹配的高危操作
            break

    return result


# ========== 返回格式验证 ==========

def validate_json_response(content: str) -> ValidationResult:
    """
    校验 JSON 格式：确保返回数据可解析

    场景：某些工具（如 mysqldump、mysqlbinlog）返回结构化文本，
    需要验证是否为合法的 JSON 格式。

    参数：
    - content: 待校验的字符串

    返回：
    - ValidationResult: 包含校验结果和解析后的数据（如果成功）
    """
    result = ValidationResult(valid=True)

    # 尝试提取 JSON 对象/数组
    content = content.strip()
    if not (content.startswith("{") or content.startswith("[")):
        result.add_error("响应内容不是有效的 JSON 格式")
        return result

    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        result.add_error(f"JSON 解析失败：{e}")
        result.add_hint("检查 JSON 格式是否正确，确保引号和逗号使用规范")

    return result


def validate_query_result_format(sql: str, result: str) -> ValidationResult:
    """
    校验查询结果格式：确保 SELECT 语句返回了有效数据

    参数：
    - sql: 原始 SQL 语句
    - result: 工具返回的结果字符串

    返回：
    - ValidationResult: 包含校验结果
    """
    result_validation = ValidationResult(valid=True)

    # 只校验 SELECT 语句
    if not re.match(r"^\s*SELECT\b", sql.strip().upper()):
        return result_validation

    # 检查空结果
    if "查询结果为空" in result or "共 0 条记录" in result:
        result_validation.add_hint("查询结果为空，可能需要调整查询条件")

    # 检查错误信息
    if result.startswith("SQL 执行出错"):
        result_validation.add_error(result)
        result_validation.add_hint("检查 SQL 语法是否正确")

    return result_validation


# ========== 参数完整性检查 ==========

def validate_tool_arguments(tool_name: str, arguments: dict) -> ValidationResult:
    """
    校验工具调用参数完整性

    参数：
    - tool_name: 工具名称
    - arguments: 工具参数字典

    返回：
    - ValidationResult: 包含校验结果和缺失参数列表
    """
    result = ValidationResult(valid=True)

    # 根据工具名称定义必填参数
    required_params = {
        "execute_sql_query": ["sql"],
        "mysqlbinlog": ["command"],
        "execute_ddl": ["sql"],
        "truncate_table": ["table_name"],
        "execute_dml": ["sql"],
        "mysqldump": ["command"],
        "cleanup_binlog": ["command"],
    }

    # 检查必填参数
    if tool_name in required_params:
        missing = [p for p in required_params[tool_name] if not arguments.get(p)]
        if missing:
            result.add_error(f"缺少必填参数：{', '.join(missing)}")
            result.add_hint(f"请补充参数：{', '.join(missing)}")

    return result


# ========== 综合校验入口 ==========

def validate_tool_output(
    tool_name: str,
    arguments: dict,
    sql: Optional[str] = None,
    result: Optional[str] = None,
) -> ValidationResult:
    """
    综合校验：对工具调用和返回结果进行完整校验

    校验顺序（由严到宽）：
    1. 参数完整性检查
    2. SQL 安全检测（如果提供了 sql 参数）
    3. 返回格式验证（如果提供了 result 参数）

    参数：
    - tool_name: 工具名称
    - arguments: 工具参数
    - sql: 可选的 SQL 语句（用于安全检测）
    - result: 可选的返回结果（用于格式验证）

    返回：
    - ValidationResult: 包含所有校验结果
    """
    overall = ValidationResult(valid=True)

    # 第 1 层：参数完整性检查
    param_check = validate_tool_arguments(tool_name, arguments)
    if not param_check.valid:
        overall.add_error(f"参数校验失败：{'; '.join(param_check.errors)}")
        overall.hints.extend(param_check.hints)

    # 第 2 层：SQL 安全检测（仅当提供了 sql 参数时）
    if sql is not None:
        sql_check = validate_sql_safety(sql)
        if not sql_check.valid:
            overall.add_error(f"SQL 安全检查失败：{sql_check.errors[0]}")
            overall.hints.extend(sql_check.hints)

    # 第 3 层：返回结果格式验证（仅当提供了 result 参数时）
    if result is not None:
        format_check = validate_query_result_format(sql or "", result)
        if not format_check.valid:
            overall.add_error(f"返回格式校验失败：{format_check.errors[0]}")
            overall.hints.extend(format_check.hints)

    return overall

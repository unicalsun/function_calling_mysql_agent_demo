"""
工具适配器模块 - 包装原始工具，提供校验、修复、错误处理

分层设计：
1. Adapter 层：在工具执行前后插入校验和修复逻辑
2. Simple Repair：尝试自动修复简单问题（如 SQL 大小写、多余空格）
3. ToolError 封装：修复失败后包装为 ToolError，转发给 LangGraph 路由

学习重点：
- Adapter 模式：在不修改原始工具代码的前提下，增加横切关注点
- 简单修复 vs 复杂修复：简单修复在 Adapter 内完成，复杂修复交给 LLM
- 错误信息的完整性：ToolError 包含策略、建议、原始参数，便于上层决策
"""

import json
import re
from typing import Any, Optional

from app.config import config
from app.db import Database, execute_shell_command
from app.tool_error import (
    ToolError,
    ErrorStrategy,
    connection_error,
    sql_syntax_error,
    permission_error,
    user_rejected_error,
    shell_command_error,
)
from app.validators import (
    ValidationResult,
    validate_tool_output,
    validate_sql_safety,
    validate_query_result_format,
    validate_tool_arguments,
)
from app.tools import HIGH_RISK_TOOLS, _confirm_operation


class ToolAdapter:
    """
    工具适配器：包装原始工具执行，提供校验和修复

    执行流程：
    1. 参数校验（validate_tool_arguments）
    2. 简单修复（simple_repair）
    3. 工具执行（_execute_tool）
    4. 结果校验（validate_query_result_format）
    5. 错误处理（封装为 ToolError 或返回结果）
    """

    # 工具执行函数映射
    _EXECUTE_FUNCTIONS = {
        "execute_sql_query": Database.execute_query,
        "mysqlbinlog": execute_shell_command,
        "execute_ddl": Database.execute_query,
        "truncate_table": Database.execute_query,
        "execute_dml": Database.execute_query,
        "mysqldump": execute_shell_command,
        "cleanup_binlog": Database.execute_query,
    }

    def execute(self, tool_name: str, arguments: dict) -> str | ToolError:
        """
        执行工具（带校验和修复）

        参数：
        - tool_name: 工具名称
        - arguments: 工具参数

        返回：
        - str: 执行成功的结果
        - ToolError: 执行失败的错误对象
        """
        # 第 1 步：参数校验
        param_validation = validate_tool_arguments(tool_name, arguments)
        if not param_validation.valid:
            return ToolError.from_validation_error(tool_name, param_validation)

        # 第 2 步：高危操作确认
        if tool_name in HIGH_RISK_TOOLS:
            if not self._confirm_high_risk(tool_name, arguments):
                return user_rejected_error(tool_name)

        # 第 3 步：获取 SQL 语句（如果有的话）
        sql = arguments.get("sql", "") or arguments.get("command", "")

        # 第 4 步：SQL 安全检查（如果提供了 SQL）
        if sql:
            sql_validation = validate_sql_safety(sql)
            if not sql_validation.valid:
                # 高危操作走确认流程
                if config.CONFIRM_HIGH_RISK:
                    if not self._confirm_risky_sql(tool_name, sql, sql_validation):
                        return user_rejected_error(tool_name)
                else:
                    # 不确认时直接返回 HITL 错误
                    return ToolError.from_validation_error(tool_name, sql_validation)

        # 第 5 步：尝试执行工具
        try:
            result = self._execute_tool(tool_name, arguments)
        except Exception as e:
            return self._handle_execution_error(tool_name, arguments, e)

        # 第 6 步：结果校验
        result_validation = validate_query_result_format(sql, result)
        if not result_validation.valid:
            # 尝试简单修复
            repaired_result = self._simple_repair_result(tool_name, sql, result)
            if repaired_result != result:
                return repaired_result
            # 修复失败，返回错误
            error = ToolError(
                tool_name=tool_name,
                message=result_validation.errors[0],
                strategy=ErrorStrategy.FALLBACK,
                repair_suggestion=result_validation.hints[0] if result_validation.hints else None,
                original_output=result,
            )
            return error

        return result

    def _confirm_high_risk(self, tool_name: str, arguments: dict) -> bool:
        """确认高危操作"""
        value = arguments.get("sql") or arguments.get("command") or arguments.get("table_name", "")
        return _confirm_operation(tool_name, str(value))

    def _confirm_risky_sql(self, tool_name: str, sql: str, validation: ValidationResult) -> bool:
        """确认高危 SQL 操作"""
        error_msg = validation.errors[0] if validation.errors else "检测到高危操作"
        return _confirm_operation(error_msg, sql)

    def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """执行工具核心逻辑"""
        executor = self._EXECUTE_FUNCTIONS.get(tool_name)
        if not executor:
            raise ValueError(f"未知工具：{tool_name}")

        if tool_name == "truncate_table":
            table = arguments["table_name"]
            return executor(f"TRUNCATE TABLE `{table}`")
        return executor(arguments.get("sql") or arguments.get("command", ""))

    def _handle_execution_error(self, tool_name: str, arguments: dict, error: Exception) -> ToolError:
        """处理工具执行错误，返回 ToolError"""
        # 判断错误类型
        error_str = str(error).lower()

        if "can't connect" in error_str or "connection refused" in error_str:
            return connection_error(tool_name, error)
        elif "1064" in error_str or "syntax" in error_str:
            sql = arguments.get("sql", "")
            return sql_syntax_error(tool_name, sql, error)
        elif "permission" in error_str or "denied" in error_str:
            return permission_error(tool_name, error)
        elif "timeout" in error_str:
            return ToolError(
                tool_name=tool_name,
                message=f"执行超时：{error}",
                strategy=ErrorStrategy.RETRY,
                repair_suggestion="检查网络或数据库负载，稍后重试",
                original_arguments=arguments,
            )
        else:
            return ToolError(
                tool_name=tool_name,
                message=f"执行失败：{error}",
                strategy=ErrorStrategy.SAFE_END,
                repair_suggestion="检查工具参数和权限",
                original_arguments=arguments,
                original_output=str(error),
            )

    def _simple_repair_result(self, tool_name: str, sql: str, result: str) -> str:
        """
        简单修复：尝试自动修复简单的结果问题

        修复策略：
        1. SQL 结果格式化问题：去除多余空行、截断过长输出
        2. JSON 解析失败：尝试修复常见的 JSON 格式错误
        3. 空结果补充：添加提示性信息

        参数：
        - tool_name: 工具名称
        - sql: 原始 SQL 语句
        - result: 原始返回结果

        返回：
        - str: 修复后的结果（如果无法修复则返回原结果）
        """
        # 场景 1：SQL 查询结果格式化
        if tool_name == "execute_sql_query" and sql:
            repaired = self._repair_sql_result(sql, result)
            if repaired != result:
                return repaired

        # 场景 2：JSON 格式修复
        if tool_name in ("mysqldump", "mysqlbinlog"):
            repaired = self._repair_json_result(result)
            if repaired != result:
                return repaired

        # 场景 3：空结果补充
        if result.strip() in ("", "(无输出)"):
            return f"{tool_name} 执行成功，但无输出内容。"

        return result

    def _repair_sql_result(self, sql: str, result: str) -> str:
        """修复 SQL 查询结果的格式问题"""
        repaired = result

        # 修复 1：去除多余空行
        repaired = re.sub(r"\n{3,}", "\n\n", repaired)

        # 修复 2：截断过长的输出（超过 2000 字符时）
        if len(repaired) > 2000:
            repaired = repaired[:2000] + "\n... (输出已截断，请减小查询范围)"

        # 修复 3：补充分隔线
        if " | " in repaired and "---" not in repaired:
            lines = repaired.split("\n")
            if len(lines) >= 2:
                lines.insert(1, "-" * 60)
                repaired = "\n".join(lines)

        return repaired

    def _repair_json_result(self, result: str) -> str:
        """修复 JSON 格式的解析问题"""
        try:
            # 尝试解析，如果成功则返回原结果
            json.loads(result.strip())
            return result
        except json.JSONDecodeError:
            pass

        # 尝试修复常见的 JSON 错误
        repaired = result.strip()

        # 修复 1：去除尾部逗号
        repaired = re.sub(r",\s*$", "", repaired)

        # 修复 2：补齐缺失的括号
        open_braces = repaired.count("{") - repaired.count("}")
        open_brackets = repaired.count("[") - repaired.count("]")
        if open_braces > 0:
            repaired += "}" * open_braces
        if open_brackets > 0:
            repaired += "]" * open_brackets

        # 验证修复后的结果
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            # 无法修复，返回原始结果
            return result

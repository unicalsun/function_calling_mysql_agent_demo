"""
ToolError 异常类 - 工具执行错误的统一表示

分层设计：
- ToolError 是工具执行失败的基础异常
- 子类针对不同错误类型提供语义化表示
- 携带 retry/fallback/HITL 等处理策略信息

学习重点：
- 异常类不仅用于错误传播，还承载"如何处理这个错误"的信息
- 策略字段（strategy）让上层路由节点可以做出智能决策
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ErrorStrategy(Enum):
    """
    错误处理策略枚举

    值说明：
    - RETRY: 可以尝试重试（临时性错误，如连接超时）
    - FALLBACK: 使用备用方案（如改用只读查询代替写操作）
    - HITL: 需要人工介入（高危操作被拒绝、权限不足）
    - SAFE_END: 安全终止（严重错误，不应继续执行）
    """
    RETRY = "retry"
    FALLBACK = "fallback"
    HITL = "hitl"
    SAFE_END = "safe_end"


@dataclass
class ToolError(Exception):
    """
    工具执行错误基类

    设计要点：
    - 继承 Exception，符合 Python 异常规范
    - 包含 strategy 字段，让调用方知道如何处理
    - 包含 repair_suggestion 字段，提供修复建议

    示例用法：
        raise ToolError(
            tool_name="execute_sql_query",
            message="SQL 语法错误：Unknown column 'username'",
            strategy=ErrorStrategy.RETRY,
            repair_suggestion="检查列名拼写，建议使用 DESCRIBE 查看表结构"
        )
    """

    tool_name: str
    message: str
    strategy: ErrorStrategy = ErrorStrategy.SAFE_END
    repair_suggestion: Optional[str] = None
    original_arguments: Optional[dict] = field(default_factory=dict)
    original_output: Optional[str] = None
    retry_count: int = 0

    def __str__(self):
        base = f"[{self.tool_name}] {self.message}"
        if self.repair_suggestion:
            base += f"\n建议：{self.repair_suggestion}"
        return base

    def to_message(self) -> dict:
        """
        转换为 LangGraph 消息格式

        返回：
        - dict: 包含 tool_error 类型和详细信息
        """
        return {
            "type": "tool_error",
            "tool_name": self.tool_name,
            "message": self.message,
            "strategy": self.strategy.value,
            "repair_suggestion": self.repair_suggestion,
            "retry_count": self.retry_count,
            "original_arguments": self.original_arguments,
            "original_output": self.original_output,
        }

    @classmethod
    def from_validation_error(cls, tool_name: str, validation_result) -> "ToolError":
        """
        从 ValidationResult 创建 ToolError

        参数：
        - tool_name: 工具名称
        - validation_result: ValidationResult 对象

        返回：
        - ToolError: 包含校验错误信息的异常
        """
        errors = "; ".join(validation_result.errors)
        hints = "; ".join(validation_result.hints)

        # 根据错误类型选择合适的策略
        if any("高危" in e for e in validation_result.errors):
            strategy = ErrorStrategy.HITL
        elif any("缺少" in e for e in validation_result.errors):
            strategy = ErrorStrategy.FALLBACK
        else:
            strategy = ErrorStrategy.SAFE_END

        return cls(
            tool_name=tool_name,
            message=f"校验失败：{errors}",
            strategy=strategy,
            repair_suggestion=hints,
        )


# ========== 预定义的常见错误工厂方法 ==========

def connection_error(tool_name: str, error: Exception) -> ToolError:
    """创建数据库连接错误"""
    return ToolError(
        tool_name=tool_name,
        message=f"数据库连接失败：{error}",
        strategy=ErrorStrategy.RETRY,
        repair_suggestion="检查数据库连接配置，或稍后重试",
    )


def sql_syntax_error(tool_name: str, sql: str, error: Exception) -> ToolError:
    """创建 SQL 语法错误"""
    return ToolError(
        tool_name=tool_name,
        message=f"SQL 语法错误：{error}",
        strategy=ErrorStrategy.FALLBACK,
        repair_suggestion="检查 SQL 语法，或使用 describe 语句查看表结构",
        original_arguments={"sql": sql},
    )


def permission_error(tool_name: str, error: Exception) -> ToolError:
    """创建权限错误"""
    return ToolError(
        tool_name=tool_name,
        message=f"权限不足：{error}",
        strategy=ErrorStrategy.HITL,
        repair_suggestion="请联系管理员授予相应权限",
    )


def user_rejected_error(tool_name: str) -> ToolError:
    """创建用户拒绝执行错误"""
    return ToolError(
        tool_name=tool_name,
        message="用户拒绝执行该操作",
        strategy=ErrorStrategy.SAFE_END,
        repair_suggestion="操作已取消，请重新确认需求",
    )


def shell_command_error(tool_name: str, command: str, error: Exception) -> ToolError:
    """创建 shell 命令执行错误"""
    return ToolError(
        tool_name=tool_name,
        message=f"命令执行失败：{error}",
        strategy=ErrorStrategy.HITL,
        repair_suggestion="检查命令格式和权限",
        original_arguments={"command": command},
    )

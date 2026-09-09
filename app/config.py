"""
配置模块 - 管理所有应用配置

学习重点：
1. 使用 python-dotenv 从 .env 文件加载环境变量（敏感信息不硬编码）
2. 配置类集中管理所有配置项，便于统一访问和修改
3. @property 提供便捷的派生属性（如数据库连接URL）
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量到 os.environ
# 必须在访问 os.getenv() 之前调用
load_dotenv()


class Config:
    """
    应用配置类

    设计要点：
    - 所有配置项通过 os.getenv() 读取，支持环境变量覆盖
    - 提供默认值，确保应用在未配置时也能启动（可能会连接失败）
    - 属性名使用大写常量风格，符合配置类惯例
    """

    # ========== MySQL 数据库配置 ==========
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "192.168.3.12")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "")

    # ========== LLM 大模型配置 ==========
    OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))

    # ========== Agent 智能体配置 ==========
    CONFIRM_HIGH_RISK: bool = os.getenv("CONFIRM_HIGH_RISK", "true").lower() == "true"
    MAX_AGENT_LOOPS: int = int(os.getenv("MAX_AGENT_LOOPS", "20"))

    @property
    def mysql_url(self) -> str:
        """
        生成 SQLAlchemy 数据库连接 URL

        格式：mysql+pymysql://用户名:密码@主机:端口/数据库名
        pymysql 是 MySQL 的 Python 驱动，SQLAlchemy 通过它连接 MySQL
        """
        db = f"/{self.MYSQL_DATABASE}" if self.MYSQL_DATABASE else ""
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}{db}"
        )

    @property
    def mysql_display(self) -> str:
        """生成用于显示的数据库连接信息（隐藏密码）"""
        db = f"/{self.MYSQL_DATABASE}" if self.MYSQL_DATABASE else ""
        return f"{self.MYSQL_USER}@{self.MYSQL_HOST}:{self.MYSQL_PORT}{db}"


# 全局单例：整个应用共享同一个 Config 实例
config = Config()

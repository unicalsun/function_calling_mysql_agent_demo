"""
数据库模块 - 封装 MySQL 连接和查询执行

学习重点：
1. SQLAlchemy 连接池：复用数据库连接，避免频繁创建/销毁连接的开销
2. 懒初始化（Lazy Init）：首次使用时才创建引擎，节省启动时间
3. 上下文管理器（with 语句）：确保连接用完后自动归还到连接池
4. 类方法（@classmethod）：不需要实例化即可调用，适合工具类
"""

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool  # 队列式连接池，适合 Web 应用

from app.config import config


class Database:
    """
    数据库操作类

    使用类变量 _engine 实现单例模式：
    整个应用共享同一个数据库引擎和连接池
    """

    _engine = None  # 类变量，所有实例共享

    @classmethod
    def get_engine(cls):
        """
        获取数据库引擎（懒初始化）

        连接池参数说明：
        - pool_size=5: 最多保持 5 个空闲连接
        - pool_recycle=3600: 连接超过 1 小时自动回收，防止 MySQL 超时断开
        - QueuePool: 队列式连接池，线程安全
        """
        if cls._engine is None:
            cls._engine = create_engine(
                config.mysql_url,
                poolclass=QueuePool,
                pool_size=5,
                pool_recycle=3600,
                echo=False,  # True 会打印所有 SQL 语句，用于调试
            )
        return cls._engine

    @classmethod
    def execute_query(cls, sql: str) -> str:
        """
        执行 SQL 查询并返回格式化结果

        关键点：
        - engine.connect() 返回连接对象，with 语句确保自动关闭/归还
        - text(sql) 将字符串转换为 SQLAlchemy 可执行的 SQL 对象
        - result.returns_rows 判断是查询（SELECT）还是执行（INSERT/UPDATE等）
        """
        try:
            engine = cls.get_engine()
            # with 语句 = 上下文管理器，退出时自动 commit + 关闭连接
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                if result.returns_rows:
                    rows = result.fetchall()  # 获取所有结果行
                    if not rows:
                        return "查询结果为空。"
                    columns = result.keys()  # 获取列名
                    # 格式化为表格形式：列名 + 分隔线 + 数据行
                    lines = [" | ".join(str(c) for c in columns), "-" * 60]
                    for row in rows:
                        lines.append(" | ".join(str(v) for v in row))
                    lines.append(f"\n共 {len(rows)} 条记录")
                    return "\n".join(lines)
                return f"执行成功，影响 {result.rowcount} 行。"
        except Exception as e:
            return f"SQL 执行出错：{e}"

    @classmethod
    def test_connection(cls) -> bool:
        """测试数据库连接是否正常"""
        try:
            engine = cls.get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))  # 最简单的测试查询
            return True
        except Exception as e:
            print(f"[DB] 连接失败：{e}")
            return False

    @classmethod
    def close(cls):
        """关闭数据库引擎，释放所有连接"""
        if cls._engine:
            cls._engine.dispose()  # 关闭所有连接池中的连接
            cls._engine = None


def execute_shell_command(cmd: str) -> str:
    """
    执行系统命令（用于 mysqlbinlog、mysqldump 等）

    安全注意：
    - shell=True 存在命令注入风险，生产环境应做参数校验
    - timeout=60 防止命令无限挂起
    - capture_output=True 捕获 stdout 和 stderr
    """
    import subprocess
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        return "命令执行超时（60s）"
    except Exception as e:
        return f"命令执行出错：{e}"

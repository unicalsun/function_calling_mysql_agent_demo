# MySQL Database Analysis Agent

一个基于 **OpenAI Function Calling** 的 MySQL 数据库分析智能体。通过自然语言与 MySQL 交互，支持数据分析、健康检查、性能诊断、binlog 分析等操作。

> 💡 **学习价值**：本项目完整展示了 OpenAI Function Calling 在实际生产场景中的应用模式，包括 Agent Loop、工具分级、Human-in-the-Loop 确认机制、分层架构等核心概念。

---

## 目录

- [功能特性](#功能特性)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
  - [前置要求](#前置要求)
  - [安装依赖](#安装依赖)
  - [配置环境变量](#配置环境变量)
  - [运行程序](#运行程序)
- [使用示例](#使用示例)
- [工具列表](#工具列表)
- [安全机制](#安全机制)
- [项目结构](#项目结构)
- [学习要点](#学习要点)

---

## 功能特性

| 特性 | 说明 |
|------|------|
| **自然语言交互** | 用中文描述需求，Agent 自动转换为 SQL 并执行 |
| **Function Calling** | LLM 决定调用哪个工具、传入什么参数，而非直接生成 SQL |
| **双层安全控制** | 高危工具显式确认 + SQL 正则隐式检测 |
| **连接池** | SQLAlchemy 连接池复用，避免频繁建立/断开连接 |
| **OpenAI 兼容接口** | 支持 DeepSeek、OpenAI 及任何兼容 OpenAI API 的服务 |

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                      用户（自然语言）                      │
└──────────────────────────────┬──────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────┐
│                     main.py（入口层）                     │
│              REPL 循环 · 用户输入 · 优雅退出               │
└──────────────────────────────┬──────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────┐
│                   agent.py（智能体层）                     │
│         Agent Loop：请求 → LLM → 工具调用 → 结果 → 循环    │
└──────────────────────────────┬──────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │  prompts.py  │ │   tools.py   │ │    db.py     │
     │  系统提示词   │ │  工具定义+执行 │ │  连接池+SQL  │
     └──────────────┘ └──────────────┘ └──────────────┘
```

---

## 快速开始

### 前置要求

- Python 3.10+
- MySQL 服务器（本地或远程）
- OpenAI API Key（或兼容的第三方服务，如 DeepSeek）
- MySQL 命令行工具（`mysqlbinlog` / `mysqldump`，用于 binlog 和备份工具）

### 安装依赖

```bash
# 推荐：创建虚拟环境
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置环境变量

1. 复制配置模板：

```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，填入你的实际配置：

```ini
# ========== MySQL 数据库配置 ==========
MYSQL_HOST=localhost               # MySQL 主机地址
MYSQL_PORT=3306                 # 端口
MYSQL_USER=root                 # 用户名
MYSQL_PASSWORD=your_password    # 密码（重要：不要提交到 Git！）
MYSQL_DATABASE=your_db          # 目标数据库名（可留空，先查询所有库）

# ========== LLM 配置 ==========
OPENAI_API_BASE=https://api.deepseek.com   # API 地址（DeepSeek / OpenAI）
OPENAI_API_KEY=sk-your-key-here            # API Key
LLM_MODEL=deepseek-chat                    # 模型名称
LLM_TEMPERATURE=0.1                        # 温度（越低越稳定）

# ========== Agent 配置 ==========
CONFIRM_HIGH_RISK=true     # 是否启用高危操作二次确认（建议保持 true）
MAX_AGENT_LOOPS=20         # 最大 Agent 循环次数，防止无限循环
```

> 🔒 **安全提醒**：`.env` 文件包含敏感信息（API Key、数据库密码），已被加入 `.gitignore`，**切勿提交到版本库**。

### 运行程序

```bash
python -m app.main
```

首次启动时程序会测试数据库连接，成功后进入交互模式：

```
============================================================
  MySQL Database Analysis Agent (OpenAI Function Calling)
============================================================

[初始化] 测试数据库连接...
[OK] 数据库连接成功！
[OK] Agent 初始化完成。

输入问题开始分析，输入 quit/exit 退出，输入 clear 清空历史。

你 >
```

---

## 使用示例

### 示例 1：查看数据库列表

```
你 > 列出所有数据库
```

### 示例 2：查询表结构

```
你 > 查看 users 表的字段结构
```

### 示例 3：数据统计分析

```
你 > 统计每个用户的订单数量，按数量降序排列，取前 10 名
```

### 示例 4：性能诊断

```
你 > 查看当前数据库的连接数和慢查询情况
```

### 示例 5：Binlog 分析（需确认）

```
你 > 分析今天的 binlog 变更
```

Agent 会展示操作详情，你需要输入 `y` 确认后才执行。

---

## 工具列表

Agent 共提供 **7 个工具**，分为普通工具和高危工具两类：

### 普通工具（自动执行）

| 工具名 | 功能 |
|--------|------|
| `execute_sql_query` | 执行 SQL 查询（SELECT/SHOW/DESCRIBE/EXPLAIN），内置高危语句检测 |

### 高危工具（需二次确认）

| 工具名 | 功能 | 风险等级 |
|--------|------|---------|
| `mysqlbinlog` | 读取和分析 MySQL binlog | 高 |
| `mysqldump` | 导出数据库或表的结构和数据 | 高 |
| `execute_ddl` | 执行 DDL 语句（CREATE/ALTER/DROP） | 高 |
| `truncate_table` | 清空指定表的所有数据 | 极高 |
| `execute_dml` | 执行 DML 语句（INSERT/UPDATE/DELETE） | 高 |
| `cleanup_binlog` | 清理 binlog 文件释放磁盘空间 | 极高 |

---

## 安全机制

本项目采用**三层安全防护**：

### 第一层：工具分级

高危工具在 Function Calling schema 中标记为"⚠️ 高危操作"，LLM 在调用时会被告知需要用户确认。

### 第二层：Human-in-the-Loop 确认

执行高危操作前，Agent 会展示以下信息并等待用户确认：

```
============================================================
⚠️  高危操作确认：truncate_table
============================================================
目标数据库：root@localhost:3306/your_db
操作内容：
  users
============================================================
确认执行？(y/n):
```

#### 补充
属于： 同步阻塞。
逐层追踪调用链：

1.` tools.py:_confirm_operation() `— HITL 的核心入口
```while True:
    answer = input("确认执行？(y/n): ").strip().lower()```
    ...
直接调用 input()，这是一个同步阻塞的 stdin 读取，会一直挂起直到用户键入 y/n。

2.` tools.py:execute_tool() `— 调用确认的地方
```if name in HIGH_RISK_TOOLS:
    if not _confirm_operation(...):
        return "[用户拒绝] ..."   # 阻塞在这里，等用户回答
    result = _do_execute(name, arguments)
```
3. `agent.py:chat()` — Agent Loop 的调用点
```
for tool_call in choice.message.tool_calls:
    result = execute_tool(fn_name, fn_args)  # 同步等待工具返回
    self.messages.append({"role": "tool", ...})
```
循环体是串行的：execute_tool 返回后，结果才追加到 messages，然后继续下一次迭代。整个循环本身也是同步的，没有任何异步/协程结构。

整条调用链的阻塞特点：

```
main.py: REPL 循环
  └─ agent.chat(user_input)           # 同步方法
       └─ client.chat.completions.create()  # 同步 HTTP 请求
            └─ execute_tool()
                 └─ _confirm_operation()
                      └─ input() ← 阻塞点，挂起整个线程等待用户输入
```

整个应用运行在单个线程上，HITL 确认通过 input() 直接阻塞了当前线程，没有任何异步中断、回调恢复或状态保存机制。用户输入前，Agent 的对话状态（`self.messages`）处于中间态，无法切换到其他任务。



### 第三层：SQL 正则检测

即使 LLM 绕过专用高危工具、直接在 `execute_sql_query` 中写入危险语句，系统也会通过正则表达式检测并触发确认：

```python
# 检测的关键词：TRUNCATE / DROP / ALTER / DELETE / UPDATE / INSERT / CREATE
```

> ⚠️ **注意事项**：
> - 请始终在测试环境先验证，不要直接连接生产数据库
> - 建议为 Agent 配置仅读权限的数据库账户，最大程度降低风险
> - `mysqldump` 命令会暴露数据库密码在进程列表中，生产环境请谨慎使用

---

## 项目结构

```
openai_function_calling_mysql_langgraph/
├── .env                  ← 你的实际配置（已加入 .gitignore）
├── .env.example          ← 配置模板（可提交到 Git）
├── .gitignore
├── requirements.txt      ← Python 依赖
├── agents.md             ← 详细架构文档（中文注释版）
├── changelog.md          ← 版本变更记录
├── readme.md             ← 本文件
└── app/
    ├── __init__.py
    ├── config.py         ← 配置层：读取 .env，提供 Config 单例
    ├── db.py             ← 数据库层：连接池管理 + SQL/Shell 执行
    ├── tools.py          ← 工具层：Function Calling schema + 执行逻辑
    ├── prompts.py        ← 提示层：System Prompt 定义
    ├── agent.py          ← 智能体层：Agent Loop 核心循环
    └── main.py           ← 入口层：启动流程 + REPL 交互
```

---

## 学习要点

通过本项目可以掌握以下核心概念：

### 1. Function Calling 模式

LLM 不直接执行代码或生成原始 SQL，而是根据工具定义（名称 + 描述 + 参数 Schema）决定调用哪个工具、传入什么参数。这比直接让 LLM 写 SQL 更安全、更可控。

### 2. Agent Loop（智能体循环）

```
用户请求 → LLM 决策 → 工具调用 → 结果返回 → LLM 总结 → 输出给用户
```

这是所有 AI Agent 应用的核心架构模式。本项目在 `agent.py` 中实现了这一循环。

### 3. 安全控制

- **工具分级**：将操作按风险等级分类
- **Human-in-the-Loop**：高危操作必须经过人工确认
- **防御性编程**：不信任任何输入，即使是 LLM 生成的也要校验

### 4. 分层架构

配置、数据库、工具、提示词、智能体逻辑各司其职，修改其中一层不影响其他层。

### 5. Prompt Engineering

System Prompt 的质量直接影响 LLM 的工具调用准确性，是实际应用中需要反复调优的关键环节。详见 `app/prompts.py`。

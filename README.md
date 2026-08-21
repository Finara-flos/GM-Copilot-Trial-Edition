# GM-Copilot
一个基于大语言模型 API 的本地优先桌面应用，辅助 DND 跑团主持人进行模组导入、清洗、AI 扩写、翻译腔及文本错误去除与 NPC 系统管理。目前软件能够提取模组信息并对场景扩写，去除模组的文本错误并改写翻译腔，提取NPC信息并生成其个人档案与场景交涉台词。未来还将加入玩家行动分支等功能，让不知道如何描述场景与扮演人物的新人dm实现“傻瓜式带团”。

> 所有账户、模组、API 配置和导出文件都只保存在本机。本项目不包含云端账户、遥测或第三方跟踪。

## 功能

- 导入 PDF、TXT、MD、Markdown、DOCX 模组文件。
- 本地快速解析：章节分段、页眉页脚过滤和 Markdown 阅读。
- 每个模组独立维护关键词词库，支持 NPC、地名和物品高亮。
- 原文与 AI 扩写版本对照、选择版本并导出 Markdown。
- NPC 扫描、档案补全、台词库和关系信息管理。
- 术语一致性检查、文化隐喻转译和翻译腔改写。
- 自定义 OpenAI Chat Completions、Anthropic Messages、Gemini generateContent API 提供方。
- 本地注册、登录、账户隔离和注销清理。
- 暗色与亮色主题，以及本地背景图设置。

## 截图

项目附带两张默认背景图：

- `assets/backgrounds/bg_dusk_peak.png`
- `assets/backgrounds/bg_ember_tavern.png`

可在设置页切换主题与背景。

## 环境要求

- Python 3.10 或更高版本。
- Windows 10+ 或 macOS 12+。
- 建议使用带中文字体的桌面环境；Windows 下会自动优先使用微软雅黑等系统字体。

## 安装

下面两种安装方式**任选一种**即可。不要同时执行两种方式。

- 平时使用 Anaconda、Miniconda的用户：选择 **方式 A**。
- 不使用 Conda、只安装了普通 Python 的用户：选择 **方式 B**。

### 方式 A

1. 打开 **Anaconda Prompt**。如果你已在 PowerShell 中正常使用 **conda**，也可以打开 PowerShell。
2. 用 **cd** 命令进入下载后的项目文件夹。请将下面路径改成你自己的实际路径：

   ```powershell
   cd C:\你的文件夹\dnd_tool
   ```

3. 创建名为 **gm-copilot** 的独立运行环境。此步骤只需第一次安装时执行：

   ```powershell
   conda create -n gm-copilot python=3.11 -y
   ```

4. 进入刚刚创建的环境：

   ```powershell
   conda activate gm-copilot
   ```

5. 安装软件需要的组件。此步骤只需第一次安装时执行：

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

看到安装完成且没有红色报错后，即可启动软件。

### 方式 B

1. 打开 PowerShell。
2. 用 **cd** 命令进入下载后的项目文件夹。请将下面路径改成你自己的实际路径：

   ```powershell
   cd C:\你的文件夹\dnd_tool
   ```

3. 创建软件专用环境：

   ```powershell
   python -m venv .venv
   ```

4. 安装软件需要的组件：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

如果提示找不到 **python** 命令，请先从 [Python 官网](https://www.python.org/downloads/) 安装 Python 3.10 或更高版本，安装时勾选“Add Python to PATH”。

### macOS / Linux 用户

```bash
cd /你的文件夹/dnd_tool
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 启动软件

每次启动前，请打开与安装时相同的终端，然后按照对应方式执行。

### 使用 Conda 安装时

```powershell
conda activate gm-copilot
cd C:\你的文件夹\dnd_tool
python src\main.py
```

### 使用普通 Python 安装时

```powershell
cd C:\你的文件夹\dnd_tool
.\.venv\Scripts\python.exe src\main.py
```

启动后会看到本地账户窗口。

1. 选择“注册账户”，填写用户名和至少 8 位的密码。
2. 使用已注册的用户名和密码登录。
3. 登录后，在“设置 -> API 提供方”配置自己的 API。
4. 选择“仅使用本地快速解析”可不登录直接体验本地导入；该模式不会保存账户数据，也不能配置或调用 API。

## 账户与本地数据

账号完全本地化，不需要邮箱，也不会访问网络进行身份认证。

| 数据 | 保存位置 |
| --- | --- |
| 用户名、密码盐值与 PBKDF2 哈希 | `config/users.json` |
| 当前账户 API 配置、主题与偏好 | `data/accounts/<用户名>/settings.json` |
| 已导入模组、关键词、NPC、扩写和任务记录 | `data/accounts/<用户名>/gm_copilot.db` |
| 已导出的扩写文档 | `data/accounts/<用户名>/outputs/` |

API Key 使用 Fernet 加密后保存，但密钥同样位于本机。因此请勿提交 `config/`、`data/`、`.venv/` 或任何包含个人模组内容的文件。

在“设置 -> 本地账户”中点击“注销并清除数据”，会永久删除当前账户及其 API 配置、模组、词库、NPC、任务记录和导出文件。该操作不可恢复。

## API 配置

登录后，打开“设置 -> API 提供方”，可添加多个提供方并选择当前使用的一项。

支持的协议：

- OpenAI Chat Completions
- Anthropic Messages
- Google Gemini generateContent

对于 OpenAI 兼容服务，Base URL 应填写 API 根路径，而不是管理网页。例如：

```text
https://example.com/v1
```

应用会自动请求：

```text
https://example.com/v1/chat/completions
```

如果服务返回 HTML 页面而不是 API 响应，应用会提示检查 Base URL 是否遗漏 **/v1**。API 调用使用流式 SSE，具备超时、重试和空正文保护；服务返回空 completion 时不会保存空扩写版本。

## 使用流程

### 本地快速解析

1. 进入“模组导入”。
2. 选择“快速（本地解析）”。
3. 拖入或选择支持的模组文件。
4. 在左侧目录中选择模组与章节，在中间区域阅读分段内容。

### AI 模组处理

1. 登录本地账户。
2. 在设置页添加 API 提供方并测试连接。
3. 导入时选择“智能（AI 精修）”，或导入后使用翻译优化、NPC 和自动扩写功能。
4. 在对照页选定扩写版本后导出完整 Markdown 文档。

## 测试

项目包含不依赖真实 API 的离线回归测试。

Windows PowerShell：

```powershell
cd dnd_tool
$env:PYTHONPATH='.'
$env:QT_QPA_PLATFORM='offscreen'

.\.venv\Scripts\python.exe tests\test_account_isolation.py
.\.venv\Scripts\python.exe tests\test_account_ui_guards.py
.\.venv\Scripts\python.exe tests\test_regression_module_features.py
.\.venv\Scripts\python.exe tests\test_api_stream_guards.py
.\.venv\Scripts\python.exe tests\test_npc_edit_theme.py
```

预期输出：

```text
ACCOUNT_ISOLATION_OK
ACCOUNT_UI_GUARD_OK
REGRESSION_OFFSCREEN_OK
API_STREAM_GUARD_OK
NPC_EDIT_DARK_THEME_OK
```

离屏 Qt 可能输出字体目录或窗口尺寸提示警告；只要上述 **_OK**标记出现，测试即通过。

## 项目结构

```text
dnd_tool/
├── assets/                 # 默认背景和图标资源
├── config/                 # 本地认证与加密密钥（不提交）
├── data/accounts/          # 按账户隔离的本地数据（不提交）
├── src/
│   ├── core/               # 异步 API、任务队列和扩写工作流
│   ├── modules/            # 导入、数据库、账户、API 协议与导出模块
│   ├── pages/              # 导入、设置、NPC、翻译和账户界面
│   ├── theme/              # 暗色与亮色 Qt 样式表
│   └── widgets/            # 导航、对照、Markdown 和信息面板
├── tests/                  # 离线回归测试
├── requirements.txt
└── README.md
```

## 注意事项

- PDF 解析使用 PyMuPDF。为避免其与网络线程的已知兼容问题，PDF 解析会在独立子进程中执行。
- 不同 API 服务的模型名、上下文限制和流式协议兼容程度可能不同。建议先使用“测试连接”。
- 自定义 OpenAI 兼容服务必须提供标准 SSE 响应；若服务仅返回网页或非流式格式，AI 功能会明确报错。
- AI 生成内容仅作跑团辅助，请自行核对规则、数值和叙事内容。

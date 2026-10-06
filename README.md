# CampusRadar · 校务雷达

面向大学生的校园通知与办事助手。扫描学校、学院或教务处的公开通知，根据学生档案判断相关性，整理材料和办理步骤，并跟踪截止日期与完成进度。

## 界面展示

### 通知网页与附件

![通知网页与附件](docs/images/notice-webpage.png)

### 办理步骤与进度

![办理步骤与进度](docs/images/action-plan.png)

## 功能

- 公开通知扫描、去重、相关性判断与分类筛选。
- 单条或批量分析，提取有原文依据的截止日期、材料、办理步骤和风险提示。
- 人工修改分类、优先级、截止日期及步骤；支持步骤排序和完成勾选。
- 材料清单和待办看板，保存办理进度、下一步与备注。
- 通知网页视图保留表格、图片和段落；可在独立窗口打开完整原网页。
- 复制办理计划及导出 `.ics` 日历提醒。
- OpenAI 官方 Responses API 与 DeepSeek Chat Completions；设置中读取模型并保存配置。

## 技术结构

| 部分 | 技术 |
| --- | --- |
| 桌面窗口 | pywebview |
| 前端 | Vue 3、TypeScript、Vite |
| 本地 API 与存储 | FastAPI、SQLite |
| Agent 工作流 | LangGraph |
| 文本切分与 JSON 解析 | LangChain Core、Text Splitters |

LangGraph 组织两个流程：

1. 通知扫描：读取来源 → 保存与去重 → 分析新通知。
2. 通知分析：读取正文与档案 → 模型生成计划 → 校验原文依据 → 保存结果。

## 本地运行

需要 Python 3.13、Node.js 22.12 或更高版本；桌面版主要在 Windows 上验证。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
python desktop.py
```

首次运行后：

1. 在“学生档案”填写学校、专业、年级和关注方向。
2. 在“来源与设置”添加公开通知列表页。
3. 选择 OpenAI 或 DeepSeek，填写自己的 API Key，读取模型并保存。
4. 扫描通知，生成办理计划；确认内容后加入“我的待办”。

## 打包 Windows EXE

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

输出：`release/CampusRadar.exe`。构建产物未提交到仓库。

## 测试

```powershell
python -m unittest discover -s tests -q
```

覆盖本地接口、计划排序与状态保存、正文提取、日期原文校验、网页表格保留及脚本过滤。测试使用临时数据库，不需要模型密钥。

## 数据与使用范围

数据默认保存在 `%LOCALAPPDATA%/CampusRadar`。可通过 `CAMPUSRADAR_DATA_DIR` 指定其他目录。模型配置与密钥在本机保存，不包含在仓库中。

程序读取公开网页，不登录校园系统，也不自动提交申请。当前正文提取适配常见文章容器；登录页面、扫描图片、动态页面可能需要打开原网页查看。网页视图保留通知内容的排版，不执行原网页脚本。

程序运行时约每 30 分钟扫描来源。关闭程序后不会继续扫描；日历提醒需将导出的 `.ics` 文件导入日历应用。

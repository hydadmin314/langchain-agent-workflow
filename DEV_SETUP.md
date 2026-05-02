# 🚀 开发者本地开发指南（uv 版本 · 标准流程）

## 核心顺序（必须遵守）

**1. 先 clone 代码 → 2. 再创建/激活虚拟环境 → 3. 安装依赖 → 4. 运行项目**

---

# 一、克隆代码（从 dev 分支）

打开 VS Code 终端，执行：

```bash
git clone https://github.com/hydadmin314/langchain-agent-workflow.git
```

进入项目目录：

```bash
cd langchain-agent-workflow
```

切换到开发分支：

```bash
git checkout dev
```

---

# 二、安装 uv（如未安装）

## Windows

```powershell
powershell -c "irm https://uv.run/ps1 | iex"
```

## Mac / Linux

```bash
curl -LsSf https://uv.run/install.sh | sh
```

---

# 三、使用 uv 创建并激活虚拟环境

## 1. 创建虚拟环境

```bash
uv venv
```

## 2. 激活虚拟环境

### Windows

```bash
.venv\Scripts\activate
```

### Mac / Linux

```bash
source .venv/bin/activate
```

激活成功后，终端最前面会出现：

```
(.venv)
```

---

# 四、安装项目依赖

```bash
uv pip install -r requirements.txt
```

---

# 五、配置环境变量

将项目里的 `.env` 复制一份，重命名为 `.env.example`备份，并在本地`.env`填写：

```
例：
OPENAI_API_KEY=统一api key
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

---

# 六、运行项目

```bash
python main.py
```

---

# 🔴 开发必须遵守的规则

1. **永远从 dev 分支拉取最新代码**
   ```bash
   git pull origin dev
   ```
2. **新功能必须新建功能分支**
   例：
   ```bash
   git checkout -b feat/sales-tools
   ```
3. **不准直接 push 到 main 分支**
4. **不准把 .env 提交到 Git**
5. **所有工具写在 tools/，所有提示词写在 prompts/**

---

# 🟢 最简总结

```
git clone → 切 dev → uv venv → 激活 .venv → uv pip install -r requirements.txt → 运行
```

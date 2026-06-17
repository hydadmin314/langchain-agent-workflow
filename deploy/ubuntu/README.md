# Ubuntu 部署说明

适用入口：`web/sales_demo/server.py`。推荐使用 Ubuntu 24.04、Python 3.12、
systemd 和 Nginx。应用只监听 `127.0.0.1:8766`，公网流量由 Nginx 接入。

## 1. 云主机和安全组

建议至少 2 vCPU、4 GB 内存、40 GB 系统盘。天翼云安全组开放：

- `22/tcp`：仅允许办公出口 IP 或堡垒机访问。
- `80/tcp`：HTTP 访问。
- `443/tcp`：配置 HTTPS 后使用。

不要向公网开放 `8766/tcp`。首次部署前完成系统更新：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx curl
```

## 2. 获取代码并安装依赖

在 VS Code 的 Ubuntu 远程终端执行：

```bash
git clone https://github.com/hydadmin314/langchain-agent-workflow.git
cd langchain-agent-workflow
git checkout feature_songhao
bash deploy/ubuntu/bootstrap.sh
```

如果代码已通过 VS Code 上传，直接进入项目根目录后执行最后一条命令。

`bootstrap.sh` 会创建 `.venv`、安装依赖、生成 `.env`（缺失时），并进行不调用
模型的产品库启动检查。

## 3. 配置模型

编辑 `.env`：

```bash
nano .env
```

至少填写：

```dotenv
OPENAI_API_KEY=实际密钥
OPENAI_BASE_URL=OpenAI兼容接口地址
LLM_MODEL=实际模型名
LLM_TIMEOUT=120
LLM_MAX_RETRIES=1
```

然后验证模型连通性：

```bash
.venv/bin/python -c "from config.llm_config import get_llm; print(get_llm().invoke('只回复：成功').content)"
```

`.env` 当前已被 Git 跟踪，不能只依赖 `.gitignore`。合并部署改动时应执行
`git rm --cached .env` 并提交删除记录，同时更换曾提交过的 API Key。该命令不会
删除服务器工作目录中的 `.env`。

## 4. 安装常驻服务

```bash
bash deploy/ubuntu/install_systemd.sh
```

检查服务和健康接口：

```bash
sudo systemctl status sales-demo
sudo journalctl -u sales-demo -n 100 --no-pager
curl http://127.0.0.1:8766/api/sales-demo/status
```

默认服务名为 `sales-demo`，端口为 `8766`。需要覆盖时：

```bash
SERVICE_NAME=sales-demo APP_PORT=8766 bash deploy/ubuntu/install_systemd.sh
```

## 5. 配置 Nginx

没有域名时使用：

```bash
bash deploy/ubuntu/install_nginx.sh
```

有域名时把域名作为参数：

```bash
bash deploy/ubuntu/install_nginx.sh demo.example.com
```

随后访问 `http://云主机公网IP/` 或域名。状态检查：

```bash
curl http://127.0.0.1/api/sales-demo/status
```

## 6. 后续更新

```bash
cd ~/langchain-agent-workflow
git pull
bash deploy/ubuntu/bootstrap.sh
sudo systemctl restart sales-demo
curl http://127.0.0.1/api/sales-demo/status
```

服务的会话记忆当前保存在进程内存中，重启会清空，且不适合直接扩成多进程。
现阶段单机演示部署使用一个 systemd 进程即可。

# QQ Reminder 运维手册

当前推荐部署方式：

- NapCat 使用 Docker Compose 常驻
- Bot 使用宿主机 systemd 常驻

对应路径：

- NapCat: `/home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat`
- Bot service: `qq-reminder.service`

## 目录说明

- `deploy/napcat/compose.yaml`: NapCat Docker Compose 配置
- `deploy/napcat/.env`: NapCat 本地变量，不提交 Git
- `deploy/napcat/napcat-data/`: NapCat 持久化数据
- `deploy/systemd/qq-reminder.service`: Bot 的 systemd 服务文件模板
- 项目根目录 `.env`: Bot 运行配置，不提交 Git

## 首次部署

### 1. 启动 Bot systemd

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder
sudo install -m 644 deploy/systemd/qq-reminder.service /etc/systemd/system/qq-reminder.service
sudo systemctl daemon-reload
sudo systemctl enable --now qq-reminder.service
```

### 2. 启动 NapCat

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat
cp .env.example .env
sudo docker compose up -d
```

### 3. 配置 NapCat 反向 WebSocket

在 NapCat WebUI 中配置：

```text
ws://host.docker.internal:8080/onebot/v11/ws
```

WebUI 地址：

```text
http://127.0.0.1:6099/webui
```

如果是远程访问，把 `127.0.0.1` 换成宿主机 IP。

## 日常操作

### Bot

查看状态：

```bash
sudo systemctl status qq-reminder.service --no-pager -l
```

查看实时日志：

```bash
sudo journalctl -u qq-reminder.service -f
```

重启：

```bash
sudo systemctl restart qq-reminder.service
```

停止：

```bash
sudo systemctl stop qq-reminder.service
```

### NapCat

查看状态：

```bash
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env ps
```

查看实时日志：

```bash
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env logs -f napcat
```

重启：

```bash
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env restart
```

停止：

```bash
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env stop
```

## 更新

### 更新 Bot 代码

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder
git pull
.venv/bin/pip install -e .
sudo systemctl restart qq-reminder.service
```

### 更新 NapCat

```bash
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env pull
sudo docker compose -f /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/compose.yaml --env-file /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env up -d
```

## 备份

建议备份以下内容：

- `/home/eng/Program/2026-Spring/qq-reminder/qq-reminder/.env`
- `/home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/.env`
- `/home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat/napcat-data/`
- `/home/eng/Program/2026-Spring/qq-reminder/qq-reminder-private/`

## 常见问题

### Bot 启动失败，提示 `address already in use`

原因：通常是你之前手工运行的 `./start.sh` 还在占用 `8080`。

处理：

```bash
sudo systemctl stop qq-reminder.service
sudo lsof -iTCP:8080 -sTCP:LISTEN -P -n
```

结束旧进程后再启动：

```bash
sudo systemctl start qq-reminder.service
```

### NapCat 报 `ENOTFOUND ws://...`

原因：把地址填到了 `WebSocket 服务`，而不是 `反向 WebSocket`。

正确做法是使用反向 WebSocket：

```text
ws://host.docker.internal:8080/onebot/v11/ws
```

### NapCat 报 `ECONNREFUSED 172.17.0.1:8080`

原因：Bot 没有在宿主机 `8080` 正常监听。

先检查：

```bash
sudo systemctl status qq-reminder.service --no-pager -l
sudo journalctl -u qq-reminder.service -n 50 --no-pager
```

### 如何确认已经接通

Bot 日志中出现：

```text
WebSocket /onebot/v11/ws [accepted]
OneBot V11 | Bot <QQ号> connected
```

NapCat 日志中不再报连接错误后，可以直接用 QQ 私聊机器人发送：

```text
/help
```

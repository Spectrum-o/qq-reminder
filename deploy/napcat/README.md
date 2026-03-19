# NapCat Docker

这个目录提供长期运行用的 NapCat Docker Compose 配置。

统一运维说明见 `deploy/OPERATIONS.md`。

## 启动

先保持 bot 端在宿主机运行：

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder
./start.sh
```

再启动 NapCat：

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder/deploy/napcat
cp .env.example .env
sudo docker compose up -d
sudo docker compose logs -f napcat
```

## WebUI

打开：

```text
http://<宿主机IP>:6099/webui
```

容器数据会持久化到当前目录下的 `napcat-data/`。

## 反向 WebSocket

在 NapCat WebUI 里把反向 WebSocket 地址配置为：

```text
ws://host.docker.internal:8080/onebot/v11/ws
```

配置完成后重启容器：

```bash
sudo docker compose restart napcat
```

## 常用命令

查看状态：

```bash
sudo docker compose ps
```

停止：

```bash
sudo docker compose stop
```

重启：

```bash
sudo docker compose restart
```

更新镜像：

```bash
sudo docker compose pull
sudo docker compose up -d
```

## 说明

- 本地 `.env` 保存 `NAPCAT_ACCOUNT`、`NAPCAT_UID`、`NAPCAT_GID`，已加入 `.gitignore`
- 当前 `.env.example` 默认按本机用户 `uid=1000`、`gid=1000` 编写
- 只暴露 `6099` WebUI 端口；如果以后你确实需要其他接口，再补端口映射
- 不建议把 `6099` 直接暴露到公网

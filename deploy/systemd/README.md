# QQ Reminder Bot systemd

统一运维说明见 `deploy/OPERATIONS.md`。

推荐架构：

- NapCat 用 Docker 常驻
- Bot 用宿主机 systemd 常驻

这样比把 bot 也塞进 Docker 更简单，尤其当前 bot 依赖本地 `.env`、`.venv` 和同级私有数据仓库。

## 安装

```bash
cd /home/eng/Program/2026-Spring/qq-reminder/qq-reminder
sudo cp deploy/systemd/qq-reminder.service /etc/systemd/system/qq-reminder.service
sudo systemctl daemon-reload
sudo systemctl enable --now qq-reminder.service
```

## 查看状态

```bash
sudo systemctl status qq-reminder.service
sudo journalctl -u qq-reminder.service -f
```

## 重启

```bash
sudo systemctl restart qq-reminder.service
```

## 停止

```bash
sudo systemctl stop qq-reminder.service
```

## 说明

- 服务直接调用项目下 `.venv/bin/python` 启动 `bot.py`
- `.env` 仍由 NoneBot 在项目目录下自动读取
- 如果以后你重建虚拟环境或改了项目路径，需要同步更新 service 文件

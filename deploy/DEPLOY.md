# PaperHub 生产部署指南（香港轻量服务器）

> 线上地址：**http://119.28.46.242**（腾讯云轻量应用服务器，香港地域，Ubuntu 22.04）
> 特点：免 ICP 备案、大陆可直接访问、数据持久、开机自启、进程崩溃自动拉起。

## 架构

| 组件 | 说明 |
|---|---|
| 服务器 | 腾讯云轻量 2核2G，Ubuntu 22.04.5 LTS，Python 3.10 |
| Web 服务 | gunicorn（**单 worker + 4 线程**）监听 80 端口 |
| 进程守护 | systemd（`paperhub.service`，`Restart=always` 崩溃自动拉起，开机自启） |
| 代码位置 | `/opt/paperhub`（git clone 自公开仓库） |
| 数据库 | SQLite `instance/paperhub.db`（不随 git 下发，靠部署脚本上传） |
| 密钥 | 生产 `SECRET_KEY` 随机生成于 `/etc/paperhub.env`（环境变量覆盖 config.py 默认值）；DeepSeek Key 走 `local_config.py`（gitignore，不提交） |

> ⚠️ **为什么必须单 worker**：后台爬虫任务为进程内内存态（`_crawl_task`），gunicorn 多 worker 会导致请求落在没有任务状态的进程上。多线程由 `--threads 4` 提供并发，足够个人站使用。

## 首次部署（deploy/deploy.py，分阶段执行）

```bash
# 本机（Windows，项目根目录）
.venv\Scripts\pip install paramiko

# 环境变量传服务器地址与密码，按顺序执行 7 个阶段
PAPERHUB_DEPLOY_HOST=119.28.46.242 PAPERHUB_DEPLOY_PASS=你的密码 \
    .venv\Scripts\python deploy\deploy.py stage1   # 探测系统
# stage2 装系统依赖 → stage3 克隆+依赖 → stage4 上传数据库与密钥
# stage5 systemd 服务+SECRET_KEY → stage6 本机验证 → stage7 清理+服务器跑 pytest
```

前置条件（一次性）：

1. 腾讯云控制台购买香港地域轻量服务器（**不要选大陆地域**，需备案）
2. 控制台重置密码（ubuntu 用户登录）
3. 控制台防火墙放行 80 端口（实例详情 → 防火墙 → 添加规则 TCP 80）
4. 本地 `instance/paperhub.db` 与 `local_config.py` 存在（stage4 会校验）

## 日常升级（代码已推 GitHub 后）

```bash
# 本机：git push 后（CI 自动跑 pytest，全绿才可合并 main）
PAPERHUB_DEPLOY_HOST=119.28.46.242 PAPERHUB_DEPLOY_PASS=你的密码 \
    .venv\Scripts\python deploy\deploy.py stage3   # 重新克隆最新代码 + 装依赖
# 然后依次：stage4（如数据库有变更需上传最新库）→ stage5（重启服务）→ stage6/7（验证）
```

> stage5 每次执行会重新生成 SECRET_KEY，导致所有在线用户会话失效（需重新登录），属预期行为。

## 数据备份

数据库只有一份在服务器上，建议定期拉回本机备份：

```bash
scp ubuntu@119.28.46.242:/opt/paperhub/instance/paperhub.db instance/backup-paperhub.db
```

## 运维速查（SSH 登录服务器后）

```bash
systemctl status paperhub          # 查看服务状态
systemctl restart paperhub         # 重启服务
journalctl -u paperhub -n 100      # 查看最近 100 行日志
curl -s http://127.0.0.1/          # 本机健康检查（302 表示正常）
```

## 安全建议

- 服务器 root 密码定期更换（腾讯云控制台 → 重置密码），或用 SSH 密钥登录替代密码
- 目前仅放行 22 / 80 端口，够用即可，不要开多余端口
- 数据库备份定期执行（见上）

## 已知限制

- **后台爬虫任务为内存态**：重启服务后任务状态清空（采集中途重启会中断，重按【启动爬虫】即可）
- **SQLite 单机数据库**：并发能力足够个人站；后期访问量大可迁移 MySQL
- 数据上传（stage4）是**整体覆盖**服务器数据库——若服务器上有新增数据（用户在线上注册/审核），直接覆盖会丢失。升级前先备份服务器库，有线上新增数据时先在服务器导出对比再合并
- IP 直连访问；想用域名随时可买（香港服务器免备案，域名 A 记录指向该 IP 即可）

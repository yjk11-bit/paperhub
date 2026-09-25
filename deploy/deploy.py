# -*- coding: utf-8 -*-
"""PaperHub 生产部署脚本（分阶段，本机运行，需安装 paramiko）。

用法：
    1) 本地安装 paramiko：.venv/Scripts/pip install paramiko
    2) 设置环境变量后按阶段执行：
       PAPERHUB_DEPLOY_HOST=119.28.46.242 PAPERHUB_DEPLOY_PASS=你的密码 \
           python deploy.py stage1
    首次部署按 stage1 → stage7 顺序执行；日常升级见 DEPLOY.md。

阶段说明：
    stage1 探测服务器系统（确认 Ubuntu + Python）
    stage2 安装系统依赖（python3-venv / pip / git）
    stage3 克隆仓库 + 虚拟环境 + 安装依赖（含 gunicorn）
    stage4 上传真实数据库 instance/paperhub.db 与密钥 local_config.py
    stage5 生成生产 SECRET_KEY 并写入 systemd 服务（gunicorn 单 worker
            多线程——后台爬虫任务为进程内内存态，必须单进程）+ 开机自启
    stage6 服务器本机 curl 验证
    stage7 清理 s12 前缀冒烟测试数据 + 服务器端跑 pytest 验证部署环境
"""
import base64
import os
import shlex
import sys
import time

import paramiko

HOST = os.environ.get("PAPERHUB_DEPLOY_HOST", "")
PASS = os.environ.get("PAPERHUB_DEPLOY_PASS", "")
USER = "ubuntu"  # 腾讯云轻量 Ubuntu 镜像默认用户，sudo 免密
APP_DIR = "/opt/paperhub"
REPO = "https://github.com/yjk11-bit/paperhub.git"

_BASE = os.path.dirname(os.path.abspath(__file__))


def connect():
    if not HOST or not PASS:
        raise SystemExit("请先设置环境变量 PAPERHUB_DEPLOY_HOST / PAPERHUB_DEPLOY_PASS")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15)
    return c


def run(c, cmd, check=True):
    print(f"\n$ {cmd}")
    stdin, stdout, stderr = c.exec_command(
        "sudo bash -c " + shlex.quote(cmd), timeout=900)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if out:
        print(out.rstrip())
    if err:
        print("[stderr]", err.rstrip())
    code = stdout.channel.recv_exit_status()
    if check and code != 0:
        raise SystemExit(f"命令失败(exit {code}): {cmd}")
    return out, code


def stage1():
    """探测服务器系统。"""
    c = connect()
    run(c, "uname -a && head -3 /etc/os-release")
    run(c, "python3 --version || true")
    run(c, "free -h | head -2 && df -h / | tail -1")
    c.close()


def stage2():
    """安装系统依赖。"""
    c = connect()
    run(c, "export DEBIAN_FRONTEND=noninteractive && "
          "apt-get update -qq && apt-get install -y -qq python3-venv python3-pip git")
    run(c, "python3 --version && git --version")
    c.close()


def stage3():
    """克隆仓库 + 虚拟环境 + 依赖（gunicorn 仅服务器安装，不入 requirements.txt）。"""
    c = connect()
    run(c, f"rm -rf {APP_DIR} && git clone --depth 1 {REPO} {APP_DIR}")
    run(c, f"cd {APP_DIR} && python3 -m venv .venv && "
          f".venv/bin/pip install -q --upgrade pip")
    run(c, f"cd {APP_DIR} && .venv/bin/pip install -q "
          f"-i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt")
    run(c, f"cd {APP_DIR} && .venv/bin/pip install -q "
          f"-i https://pypi.tuna.tsinghua.edu.cn/simple gunicorn")
    run(c, f"cd {APP_DIR} && .venv/bin/python --version")
    c.close()


def stage4():
    """上传真实数据库与本地密钥配置（覆盖服务器上的同名文件）。

    注意：instance/ 与 local_config.py 均被 .gitignore 忽略，不会随 git 下发，
    首次部署与每次数据迁移都必须执行本阶段。
    """
    c = connect()
    local_db = os.path.join(_BASE, "..", "instance", "paperhub.db")
    local_cfg = os.path.join(_BASE, "..", "local_config.py")
    for p in (local_db, local_cfg):
        if not os.path.exists(p):
            raise SystemExit(f"本地文件不存在：{p}")
    # instance/ 被 .gitignore 忽略，clone 后不存在；chown 允许 SFTP 以 ubuntu 写入
    run(c, f"mkdir -p {APP_DIR}/instance && chown -R ubuntu:ubuntu {APP_DIR}")
    sftp = c.open_sftp()
    sftp.put(local_db, f"{APP_DIR}/instance/paperhub.db")
    sftp.put(local_cfg, f"{APP_DIR}/local_config.py")
    sftp.close()
    run(c, f"chown -R root:root {APP_DIR}")
    run(c, f"cd {APP_DIR} && .venv/bin/python -c \""
          f"import sqlite3; db=sqlite3.connect('instance/paperhub.db'); "
          f"print('paper', db.execute('SELECT COUNT(*) FROM paper').fetchone()[0]); "
          f"print('pending', db.execute('SELECT COUNT(*) FROM pending_paper').fetchone()[0])\"")
    c.close()


def stage5():
    """systemd 服务 + 生产 SECRET_KEY + 启动。"""
    c = connect()
    # SECRET_KEY 随机生成一次写入环境文件；重复执行本阶段会轮换密钥
    #（所有会话失效，属预期行为；想保留旧密钥请不要重复执行）
    run(c, "SK=$(python3 -c \"import secrets; print(secrets.token_hex(32))\"); "
          "printf 'SECRET_KEY=%s\\n' \"$SK\" > /etc/paperhub.env && "
          "cat /etc/paperhub.env")
    unit = (
        "[Unit]\n"
        "Description=PaperHub Flask app (gunicorn)\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"WorkingDirectory={APP_DIR}\n"
        "EnvironmentFile=-/etc/paperhub.env\n"
        "ExecStart=/opt/paperhub/.venv/bin/gunicorn --workers 1 --threads 4 "
        "--timeout 120 --bind 0.0.0.0:80 \"app:app\"\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    unit_b64 = base64.b64encode(unit.encode("utf-8")).decode("ascii")
    run(c, f"echo {unit_b64} | base64 -d > /etc/systemd/system/paperhub.service")
    run(c, "systemctl daemon-reload && systemctl enable paperhub && "
          "systemctl restart paperhub")
    time.sleep(3)
    run(c, "systemctl status paperhub --no-pager | head -15")
    c.close()


def stage6():
    """服务器本机 curl 验证。"""
    c = connect()
    run(c, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/")
    run(c, "curl -s http://127.0.0.1/login | head -5")
    c.close()


def stage7():
    """清理 s12 前缀冒烟测试数据 + 服务器端跑 pytest 验证部署环境。"""
    c = connect()
    run(c, f"cd {APP_DIR} && .venv/bin/python -c \""
          f"import sqlite3; db=sqlite3.connect('instance/paperhub.db'); "
          f"db.execute('PRAGMA foreign_keys=ON'); "
          f"uids=[r[0] for r in db.execute(\\\"SELECT id FROM user WHERE username LIKE 's12%'\\\").fetchall()]; "
          f"[db.execute('DELETE FROM view_history WHERE user_id=?',(u,)) for u in uids]; "
          f"[db.execute('DELETE FROM collect WHERE user_id=?',(u,)) for u in uids]; "
          f"db.execute(\\\"DELETE FROM user WHERE username LIKE 's12%'\\\"); "
          f"db.commit(); print('cleaned', len(uids), 'smoke users')\"")
    run(c, f"cd {APP_DIR} && .venv/bin/python -m pytest tests -q")
    c.close()


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    globals()[stage]()

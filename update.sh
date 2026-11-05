#!/bin/sh
# TG-SignPulse fork 一键更新：在部署目录拉取 GHCR 最新镜像并重启容器。
# 用法：在 /opt/fork-tg-signpulse 下执行 ./update.sh
# （脚本自动 cd 到自身所在目录；在别处放好 docker-compose.yml 也能用）。
set -eu
cd "$(dirname "$0")"
docker compose pull
docker compose up -d

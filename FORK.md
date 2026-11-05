# Fork 说明

这是 `Silentely/TG-SignPulse` v2.7.0 的个人 fork，基于上游 `c9cff35`，只给自己用，不向原作者提 PR。

## 改动

- **通行密钥登录（KeePassXC）**：在添加账号里增加通行密钥方式，手动粘贴 KeePassXC 条目的凭证 ID、私钥、userHandle 三个字段登录，不走浏览器 WebAuthn，不依赖 telegram.org 域名。实现见 `backend/api/routes/passkey.py`。
- **MTProxy 代理支持**：上游只支持 http/https/socks4/socks5，本 fork 增加 mtproto。实现见 `backend/utils/mtproxy.py` 与 `backend/utils/proxy.py`。

## 跟上游同步的方式

每个功能做成独立的小文件，对上游文件的改动只留最小的接线（加一行路由注册、白名单里加一个类型）。上游出新 tag 时 rebase 这几处小冲突，再跑一遍测试。

## 部署

部署方式见仓库根目录的 `Dockerfile.vps` 与 `docker-compose.yml`（本 fork 自带，VPS 上只绑 127.0.0.1，不暴露公网）。

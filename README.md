# turnstile-solver

独立部署的 **Cloudflare Turnstile** 求解服务。基于 Camoufox / Patchright，对外提供 **YesCaptcha 兼容 HTTP API**，可供 `grok_reg`、`grokcli-2api` 及其它业务共用。

核心解题逻辑源自 [D3-vin Turnstile Solver](https://github.com/D3-vin) 思路，本仓库整理为可 Docker 化的独立服务。

## 功能

- `POST /createTask` / `POST /getTaskResult`（YesCaptcha / CapSolver 风格）
- `GET /turnstile` / `GET /result`（原生协议）
- `GET /health` 健康检查与浏览器池状态
- 默认 **Camoufox** 反检测浏览器；可选 Chromium（patchright）
- 懒加载浏览器池 + 空闲回收，降低闲置内存
- 可选 `API_KEY` 鉴权（`clientKey`）

## 快速部署（NAS / Docker）

GitHub Actions 推送 `master` 后会构建并推送到 GHCR：

```text
ghcr.io/jeck5001/turnstile-solver:latest
```

### docker-compose（推荐）

```bash
mkdir -p ~/turnstile-solver && cd ~/turnstile-solver

# 复制本仓库 docker-compose.yml，或：
# curl -fsSL -o docker-compose.yml \
#   https://raw.githubusercontent.com/jeck5001/turnstile-solver/master/docker-compose.yml

docker compose pull
docker compose up -d
```

检查：

```bash
curl -s http://127.0.0.1:5072/health
# {"ok": true, "browser_type": "camoufox", ...}
```

首次拉镜像较大（含 Camoufox 浏览器）。若 GHCR 包是 private，NAS 上先登录：

```bash
echo <GITHUB_TOKEN> | docker login ghcr.io -u jeck5001 --password-stdin
```

### 常用环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TURNSTILE_HOST` | `0.0.0.0` | 监听地址 |
| `TURNSTILE_PORT` | `5072` | 端口 |
| `TURNSTILE_THREAD` | `2` | 浏览器池大小（并发解题数） |
| `TURNSTILE_BROWSER_TYPE` | `camoufox` | `camoufox` / `chromium` / `chrome` / `msedge` |
| `TURNSTILE_DEBUG` | `1` | 详细日志 |
| `TURNSTILE_LAZY` | `1` | 首次请求再启动浏览器 |
| `TURNSTILE_IDLE_SEC` | `180` | 空闲回收秒数，`0` 关闭 |
| `TURNSTILE_PROXY` | `0` | `1` 时读取 `proxies.txt` |
| `API_KEY` | 空 | 设置后请求必须带相同 `clientKey` |

### 资源建议

- 内存：≥ 2GB（Camoufox 较吃内存；`TURNSTILE_THREAD=2~3`）
- `shm_size: 2gb`（浏览器必需）
- CPU：2 核以上更稳

## 协议示例

### YesCaptcha 兼容

```bash
# 1) 创建任务
curl -s http://127.0.0.1:5072/createTask -H 'Content-Type: application/json' -d '{
  "clientKey": "local",
  "task": {
    "type": "TurnstileTaskProxyless",
    "websiteURL": "https://accounts.x.ai/sign-up",
    "websiteKey": "0x4AAAAAAAhr9JGVDZbrZOo0"
  }
}'
# {"errorId":0,"taskId":"..."}

# 2) 轮询结果
curl -s http://127.0.0.1:5072/getTaskResult -H 'Content-Type: application/json' -d '{
  "clientKey": "local",
  "taskId": "<上一步 taskId>"
}'
# {"errorId":0,"status":"ready","solution":{"token":"..."}}
```

支持的 `task.type`：`TurnstileTaskProxyless`、`TurnstileTaskProxylessM1/M2`、`TurnstileTask`、`AntiTurnstileTask*` 等（本地均走同一解题路径）。

### 原生 GET

```text
GET /turnstile?url=https://example.com&sitekey=0x4AAAA...
GET /result?id=<taskId>
```

## 业务侧对接

### grok_reg

```json
{
  "turnstile_solver_enabled": true,
  "turnstile_solver_url": "http://turnstile-solver:5072",
  "turnstile_solver_client_key": "local"
}
```

- 同 Docker 网络：服务名 `http://turnstile-solver:5072`
- 跨容器 / 宿主机：`http://NAS-IP:5072` 或 `http://host.docker.internal:5072`
- 环境变量：`GROK_REG_TURNSTILE_SOLVER_URL`

### grokcli-2api

```env
GROK2API_CAPTCHA_PROVIDER=local
GROK2API_LOCAL_SOLVER_URL=http://turnstile-solver:5072
```

可关掉 inline solver，统一指向本服务。

### 其它服务

任意语言 HTTP 客户端即可：`createTask` → 轮询 `getTaskResult` → 取 `solution.token`。  
**token 约 2 分钟有效**；`websiteURL` 域名须与真实站点一致。

## 本地开发（非 Docker）

```bash
./start.sh
# 日志: logs/turnstile_solver.log
./stop.sh
```

依赖：Python 3.11+，见 `requirements.txt`。

## 安全建议

1. **不要把 5072 裸奔到公网**；仅内网或 reverse proxy + 鉴权。
2. 生产设置 `API_KEY`，业务侧 `clientKey` 填相同值。
3. 需要固定出口 IP 时，在 **solver 侧** 挂代理，而不是每个业务各自起浏览器。

## 目录结构

```text
api_solver.py          # HTTP 服务 + 解题逻辑
browser_configs.py     # UA / sec-ch-ua 池
db_results.py          # 内存任务结果
Dockerfile
docker-compose.yml
entrypoint.sh          # 容器入口
start.sh / stop.sh     # 宿主机启停
.github/workflows/     # GHCR 多架构镜像
```

## License

MIT（见 [LICENSE](LICENSE)）。上游思路与部分实现参考 D3-vin / community Turnstile solver。

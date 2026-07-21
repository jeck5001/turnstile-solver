import os
import time
from collections import OrderedDict

# 内存数据库，用于临时存储验证码结果。
# 有界存储：硬上限 FIFO 淘汰 + TTL 清理，避免长跑服务内存无限增长。
# （验证码 token 数分钟即失效，无需长期保留，够客户端轮询即可。）
results_db: "OrderedDict[str, dict]" = OrderedDict()


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "") or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


_MAX_RESULTS = _int_env("TURNSTILE_MAX_RESULTS", 2000)
_TTL_SEC = _int_env("TURNSTILE_RESULT_TTL_SEC", 1800)  # 30 分钟，远超 token 有效期


async def init_db():
    print(f"[系统] 结果数据库初始化成功 (内存模式, 上限 {_MAX_RESULTS} 条 / TTL {_TTL_SEC}s)")


def _evict_over_capacity():
    # FIFO 淘汰最旧条目：这是内存有界的硬保证，不依赖定时清理。
    while len(results_db) > _MAX_RESULTS:
        results_db.popitem(last=False)


async def save_result(task_id, task_type, data):
    # 每次写入都打时间戳（TTL 依据），并移到队尾表示最近活跃。
    # 注意：原实现在任务完成时用不含 createTime 的 dict 覆盖，导致清理永不命中 —— 这里统一用 _saved_at。
    if not isinstance(data, dict):
        data = {"value": data}
    data["_saved_at"] = time.time()
    results_db[task_id] = data
    results_db.move_to_end(task_id)
    _evict_over_capacity()
    print(f"[系统] 任务 {task_id} 状态更新: {data.get('value', '正在处理')}")


async def load_result(task_id):
    return results_db.get(task_id)


async def cleanup_old_results(days_old=7):
    # 按 _saved_at 清理过期条目；days_old 仅作上限，实际用较短的 TTL。
    ttl = min(_TTL_SEC, int(days_old * 86400)) if days_old else _TTL_SEC
    now = time.time()
    to_delete = [
        tid for tid, res in results_db.items()
        if isinstance(res, dict) and now - res.get("_saved_at", now) > ttl
    ]
    for tid in to_delete:
        results_db.pop(tid, None)
    return len(to_delete)

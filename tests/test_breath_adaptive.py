# ============================================================
# breath 自适应 max_results 测试
# Adaptive max_results behaviour for the breath tool.
#
# 验证:
#   - max_results=-1(默认)→ 按相关度返回相关集,不卡在旧的固定 5 条
#   - 显式 max_results=N → 仍硬截断为 N 条(向后兼容)
#   - token 预算始终是真正的天花板
# ============================================================

import pytest
from unittest.mock import patch


async def _seed_many(bucket_mgr, n=10, keyword="苹果"):
    """Create n buckets that all match `keyword` with similar topic scores."""
    ids = []
    for i in range(n):
        bid = await bucket_mgr.create(
            content=f"关于{keyword}的第{i}条记忆，{keyword}很重要。",
            name=f"{keyword}记忆{i}",
            domain=["日常"],
            tags=[keyword, "测试"],
            importance=5,
        )
        ids.append(bid)
    return ids


@pytest.fixture
def patched_server(bucket_mgr, decay_eng, mock_dehydrator, mock_embedding_engine):
    """Patch server module globals onto isolated test instances."""
    import server
    with patch.object(server, "bucket_mgr", bucket_mgr), \
         patch.object(server, "decay_engine", decay_eng), \
         patch.object(server, "dehydrator", mock_dehydrator), \
         patch.object(server, "embedding_engine", mock_embedding_engine):
        yield server


@pytest.mark.asyncio
async def test_explicit_max_results_hard_caps(patched_server, bucket_mgr):
    """显式 max_results=3 → 最多 3 条,旧行为不变。"""
    await _seed_many(bucket_mgr, n=10)
    out = await patched_server.breath(query="苹果", max_results=3)
    shown = out.count("[bucket_id:")
    assert shown <= 3, f"explicit cap broken, showed {shown}"


@pytest.mark.asyncio
async def test_auto_returns_more_than_old_default(patched_server, bucket_mgr):
    """默认(自适应)→ 相关桶多时返回数量超过旧的固定 5 条。"""
    await _seed_many(bucket_mgr, n=12)
    out = await patched_server.breath(query="苹果")  # auto
    shown = out.count("[bucket_id:")
    assert shown > 5, f"auto mode should surface the relevant set, showed {shown}"


@pytest.mark.asyncio
async def test_auto_trims_weak_tail(patched_server, bucket_mgr):
    """自适应模式只保留相关集:不相关的桶不应被带出。"""
    await _seed_many(bucket_mgr, n=6, keyword="苹果")
    # 一个与查询完全无关的桶
    await bucket_mgr.create(
        content="今天天气晴朗，适合散步。",
        name="天气随笔",
        domain=["日常"],
        tags=["天气"],
        importance=5,
    )
    out = await patched_server.breath(query="苹果")
    assert "天气" not in out, "irrelevant bucket leaked into adaptive results"


@pytest.mark.asyncio
async def test_auto_respects_token_budget(patched_server, bucket_mgr):
    """token 预算是真正的天花板:极小预算下条数被压缩。"""
    await _seed_many(bucket_mgr, n=12)
    out = await patched_server.breath(query="苹果", max_tokens=60)
    shown = out.count("[bucket_id:")
    assert shown < 12, f"token budget ignored, showed {shown}"

#!/usr/bin/env python3
# ============================================================
# SessionStart Hook: auto-breath + dreaming on session start
# 对话开始钩子：自动浮现记忆 + 触发 dreaming
#
# On SessionStart, this script calls the Ombre Brain MCP server's
# breath-hook and dream-hook endpoints, printing results to stdout
# so Claude sees them as session context.
#
# 为什么有这个钩子：MCP 工具在某些客户端（如 Claude Code 网页/远程）
# 是“延迟加载”的，且服务器名可能是自动生成的 UUID——重配后工具 ID
# 会漂移，导致醒来“第一口呼吸”找不到工具。这个钩子直接走 HTTP
# /breath-hook，不依赖 MCP 工具发现，所以就算工具还没加载，睁眼也能
# 先看到记忆。是名字漂移问题的“兜底”，不是根治（根治要在客户端钉死
# 服务器名）。
#
# Sequence: breath → dream
# 顺序：呼吸浮现 → 做梦消化
#
# Config:
#   OMBRE_HOOK_URL   — override the server URL (default: http://localhost:8000)
#   OMBRE_HOOK_URLS  — comma-separated fallback URLs, tried in order
#   OMBRE_HOOK_SKIP  — set to "1" to disable the hook temporarily
#   OMBRE_HOOK_RETRIES — per-URL retry attempts (default: 2)
# ============================================================

import os
import sys
import time
import urllib.request
import urllib.error

DEFAULT_URL = "http://localhost:8000"
# 整个钩子的总时间预算要低于 settings.json 里的 timeout(12s)，留点余量
TOTAL_BUDGET_S = 10.0
PER_REQUEST_TIMEOUT_S = 4.0


def _candidate_urls():
    """Build an ordered, de-duplicated list of base URLs to try."""
    urls = []
    primary = os.environ.get("OMBRE_HOOK_URL", "").strip()
    if primary:
        urls.append(primary)
    extra = os.environ.get("OMBRE_HOOK_URLS", "").strip()
    if extra:
        urls.extend(u.strip() for u in extra.split(",") if u.strip())
    # localhost 兜底：本地常驻服务最常见的地址
    urls.append(DEFAULT_URL)
    # 去重保序，并去掉结尾斜杠
    seen, out = set(), []
    for u in urls:
        u = u.rstrip("/")
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def main():
    if os.environ.get("OMBRE_HOOK_SKIP") == "1":
        sys.exit(0)

    try:
        retries = max(1, int(os.environ.get("OMBRE_HOOK_RETRIES", "2")))
    except ValueError:
        retries = 2

    deadline = time.monotonic() + TOTAL_BUDGET_S
    base_urls = _candidate_urls()

    # 先确定哪个 URL 能连通（breath），连通后用同一个 URL 继续 dream
    base = _first_reachable(base_urls, "/breath-hook", retries, deadline)
    if base is None:
        # 全部不可达：安静退出，绝不阻塞会话启动
        sys.exit(0)

    # breath 已经在探测时打印过结果；接着做梦消化
    if time.monotonic() < deadline:
        _call_endpoint(base, "/dream-hook", retries, deadline)


def _first_reachable(base_urls, path, retries, deadline):
    """Try each base URL for `path`; on first success print output and return it."""
    for base in base_urls:
        if time.monotonic() >= deadline:
            break
        ok = _call_endpoint(base, path, retries, deadline)
        if ok:
            return base
    return None


def _call_endpoint(base_url, path, retries, deadline):
    """GET base_url+path with bounded retries/backoff. Print body on success.

    Returns True if the request reached the server (even with empty body),
    False if all attempts failed within the time budget.
    """
    url = f"{base_url}{path}"
    backoff = 0.3
    for attempt in range(retries):
        if time.monotonic() >= deadline:
            return False
        remaining = deadline - time.monotonic()
        timeout = min(PER_REQUEST_TIMEOUT_S, max(0.5, remaining))
        req = urllib.request.Request(
            url, headers={"Accept": "text/plain"}, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                output = raw.strip()
                if output:
                    print(output)
                return True
        except urllib.error.HTTPError:
            # 服务器在线但这个端点报错：可达，不再重试该 URL
            return True
        except (urllib.error.URLError, OSError, ValueError):
            # 连接失败/超时：退避后重试（仍在预算内时）
            if attempt < retries - 1 and time.monotonic() + backoff < deadline:
                time.sleep(backoff)
                backoff *= 2
            continue
        except Exception:
            return False
    return False


if __name__ == "__main__":
    main()

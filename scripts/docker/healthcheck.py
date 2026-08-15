#!/usr/bin/env python3
"""DECP 容器健康检查脚本。

用法:
    decp-healthcheck --port 18100 [--timeout 2]     单次探测（Docker HEALTHCHECK）
    decp-healthcheck --port 18100 --watch           循环探测，server 就绪/存活期间保持
    decp-healthcheck --stdio                         进程存活探测（stdio 模式）

退出码：0=健康，1=不健康。
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import time


def _tcp_probe(port: int, timeout: float) -> bool:
    """TCP 连接探测目标端口（MCP server 就绪即可连通）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _stdio_probe() -> bool:
    """stdio 模式：进程存活即健康（server 以 PID 1 运行，收到 SIGTERM 即退出）。"""
    return True


def _single(port: int | None, timeout: float, stdio: bool) -> int:
    ok = _stdio_probe() if stdio else _tcp_probe(port or 0, timeout)
    if not ok:
        print("[decp-healthcheck] unhealthy", file=sys.stderr)
    return 0 if ok else 1


def _watch(port: int, timeout: float) -> None:
    """看门狗：http 模式下 server 退出（端口不可达）时给容器发 SIGTERM，
    使 Docker 能感知崩溃并以正确状态码重启。"""
    def _term(_sig, _frm):
        print("[decp-healthcheck] received signal, exiting")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    while True:
        if not _tcp_probe(port, timeout):
            # 连续失败才判定退出，避免启动瞬时抖动
            time.sleep(timeout)
            if not _tcp_probe(port, timeout):
                print(f"[decp-healthcheck] server on :{port} unreachable, terminating")
                os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=18100)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--stdio", action="store_true", help="stdio 模式存活探测")
    p.add_argument("--watch", action="store_true", help="循环探测模式（entrypoint 内部使用）")
    args = p.parse_args()

    if args.watch:
        _watch(args.port, args.timeout)
    sys.exit(_single(None if args.stdio else args.port, args.timeout, args.stdio))


if __name__ == "__main__":
    main()

"""隔离运行固定版本的 FullAuto 模型，避免污染当前 DouZero 环境。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from queue import Empty, Queue

PINNED_FULLAUTO_COMMIT = "6d20491da216c989d23cf2883498758fb78e9f8d"
PINNED_MODEL_SHA256 = {
    "weights/bid_weights.pkl": "320e9d96e3aa6f962e27f0b8f5f1d97c15af719bc5cb30ed5423b9c025bb4801",
    "weights/farmer_weights.pkl": "28fd4fe7fb97c413b8df9c1f69ea29e439a822654947f9ed91487ce454f975e7",
    "weights/landlord_up_weights.pkl": "3056422ffb4eeac2f63cfabe73bdfe2f4557982c66fa59c5b594c94e60297b61",
    "weights/landlord_down_weights.pkl": "eb381872d90e6cb8a1fcde15cafcf9b7421e467be90b2ebc1445832e84521a4d",
    "weights/landlord_weights.pkl": "da2eb3bd47d4b717f0d0b68f58625b24a4da7efca2fe64b050cbf5a9116b21fe",
    "baselines/resnet/resnet_landlord.ckpt": (
        "b025086cbe0df8b14c4eade9bae5e135b48d9da8568e049c173da08f8c047050"
    ),
}

_WARMUP_HAND = (
    "D",
    "X",
    "2",
    "2",
    "A",
    "K",
    "Q",
    "J",
    "10",
    "9",
    "8",
    "7",
    "6",
    "5",
    "4",
    "3",
    "3",
)


class LegacyFullAutoModel:
    """Run the FullAuto fork in a separate process to isolate its douzero package."""

    def __init__(
        self,
        legacy_root: str | Path,
        *,
        python_executable: str | Path = sys.executable,
        startup_timeout_seconds: float = 30.0,
        request_timeout_seconds: float = 8.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # 模型在独立进程加载；主监听器即使遇到 legacy 依赖/权重异常也能安全退出局前建议。
        if startup_timeout_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("worker timeouts must be positive")
        self._root = Path(legacy_root).resolve()
        self._python = str(python_executable)
        self._startup_timeout_seconds = float(startup_timeout_seconds)
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._monotonic = monotonic
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def score_bid(
        self, cards: Sequence[str], *, timeout_seconds: float | None = None
    ) -> float:
        return self._request("bid", cards, timeout_seconds=timeout_seconds)

    def score_farmer(
        self, cards: Sequence[str], *, timeout_seconds: float | None = None
    ) -> float:
        return self._request("farmer", cards, timeout_seconds=timeout_seconds)

    def score_landlord(
        self,
        cards: Sequence[str],
        bottom_cards: Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> float:
        return self._request(
            "landlord",
            cards,
            bottom_cards=bottom_cards,
            timeout_seconds=timeout_seconds,
        )

    def warmup(self) -> None:
        """启动并执行一次轻量 bid 推理，提前加载 FullAuto 的首个模型分支。"""

        self.score_bid(_WARMUP_HAND, timeout_seconds=self._startup_timeout_seconds)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        self._stop_process(process)

    def _request(
        self,
        operation: str,
        cards: Sequence[str],
        *,
        bottom_cards: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> float:
        payload = {
            "operation": operation,
            "cards": [_legacy_rank(card) for card in cards],
            "bottom_cards": [_legacy_rank(card) for card in bottom_cards],
        }
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise TimeoutError("legacy preplay request deadline already elapsed")
        request_budget = min(
            self._request_timeout_seconds,
            timeout_seconds if timeout_seconds is not None else self._request_timeout_seconds,
        )
        deadline_at = self._monotonic() + request_budget
        with self._lock:
            process = self._ensure_process(deadline_at)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
            remaining = deadline_at - self._monotonic()
            if remaining <= 0:
                self._process = None
                self._stop_process(process)
                raise TimeoutError("legacy preplay request deadline elapsed before response")
            line = self._readline(
                process,
                timeout_seconds=remaining,
                stage=operation,
            )
            if not line:
                stderr = process.stderr.read() if process.stderr is not None else ""
                self.close()
                raise RuntimeError(f"legacy preplay worker stopped: {stderr.strip()}")
            response = json.loads(line)
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "legacy worker failed")))
            return float(response["score"])

    def _ensure_process(self, deadline_at: float) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        _validate_legacy_root(self._root)
        process = subprocess.Popen(
            [
                self._python,
                "-m",
                "douzero_advisor.preplay.legacy_worker",
                "--legacy-root",
                str(self._root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._process = process
        assert process.stdout is not None
        remaining = deadline_at - self._monotonic()
        if remaining <= 0:
            self.close()
            raise TimeoutError("legacy preplay request deadline elapsed during startup")
        ready = self._readline(
            process,
            timeout_seconds=min(self._startup_timeout_seconds, remaining),
            stage="startup",
        )
        if not ready:
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.close()
            raise RuntimeError(f"legacy preplay worker failed to start: {stderr.strip()}")
        message = json.loads(ready)
        if message != {"ready": True}:
            self.close()
            raise RuntimeError(f"unexpected legacy worker handshake: {message}")
        return process

    def _readline(
        self,
        process: subprocess.Popen[str],
        *,
        timeout_seconds: float,
        stage: str,
    ) -> str:
        assert process.stdout is not None
        result: Queue[str] = Queue(maxsize=1)
        thread = threading.Thread(
            target=lambda: result.put(process.stdout.readline()),
            name=f"preplay-worker-{stage}",
            daemon=True,
        )
        thread.start()
        try:
            return result.get(timeout=timeout_seconds)
        except Empty as error:
            self._process = None
            self._stop_process(process)
            raise TimeoutError(
                f"legacy preplay worker {stage} exceeded {timeout_seconds:.1f}s"
            ) from error

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _legacy_rank(rank: str) -> str:
    return "T" if rank == "10" else rank


def _validate_legacy_root(root: Path) -> None:
    required = (
        "BidModel.py",
        "FarmerModel.py",
        "LandlordModel.py",
        *PINNED_MODEL_SHA256,
        "douzero/evaluation/deep_agent.py",
    )
    missing = [relative for relative in required if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError(
            f"legacy FullAuto checkout is incomplete: {', '.join(missing)}"
        )
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    actual_commit = completed.stdout.strip()
    if actual_commit != PINNED_FULLAUTO_COMMIT:
        raise ValueError(
            f"legacy FullAuto commit mismatch: {actual_commit} != {PINNED_FULLAUTO_COMMIT}"
        )
    for relative, expected in PINNED_MODEL_SHA256.items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"legacy model hash mismatch: {relative}")

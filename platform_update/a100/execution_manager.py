"""Shared simulator action queue. No real-robot transport is implemented."""
from collections import deque
import time
import numpy as np


class ExecutionManager:
    def __init__(self, limits, prefix=10, timeout_s=1.0):
        self.limits = np.asarray(limits, dtype=float)
        self.prefix = prefix
        self.timeout_s = timeout_s
        self.queue = deque()
        self.generation = 0
        self.stopped = False
        self.counters = dict(timeouts=0, queue_underruns=0, interventions=0, constraint_violations=0)

    def reset(self):
        self.queue.clear()
        self.generation += 1
        self.stopped = False
        return self.generation

    def stop(self, intervention=False):
        self.queue.clear()
        self.generation += 1
        self.stopped = True
        self.counters["interventions"] += int(intervention)

    def accept(self, actions, generation, elapsed_s):
        # A stale response must never revive a stopped or reset episode.
        if self.stopped or generation != self.generation:
            raise RuntimeError("stale/stopped action response")
        if elapsed_s > self.timeout_s:
            self.counters["timeouts"] += 1
            self.stop()
            raise TimeoutError("policy response timeout")
        actions = np.asarray(actions, dtype=float)
        if (actions.ndim != 2 or actions.shape[1] != 7 or len(actions) < self.prefix
                or not np.isfinite(actions).all() or (actions[:, :6] < self.limits[:, 0]).any()
                or (actions[:, :6] > self.limits[:, 1]).any() or (actions[:, 6] < 0).any()
                or (actions[:, 6] > .1).any()):
            self.counters["constraint_violations"] += 1
            self.stop()
            raise ValueError("invalid action chunk; rejected without clipping")
        self.queue.clear()
        self.queue.extend(actions[:self.prefix].copy())

    def next_action(self):
        if self.stopped:
            raise RuntimeError("execution stopped")
        if not self.queue:
            self.counters["queue_underruns"] += 1
            self.stop()
            raise RuntimeError("empty action queue")
        return self.queue.popleft()

"""Token accounting for every LLM call (LangChain callback), appended to a JSONL file so a batch run can enforce a spend cap."""
from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler


class UsageHandler(BaseCallbackHandler):
    raise_error = False  # accounting must never break a run

    def __init__(self, log_path=None):
        self.log_path = Path(log_path) if log_path else None
        self.calls = self.tokens_in = self.tokens_out = 0
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def on_llm_end(self, response, **kwargs):
        t_in = t_out = 0
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None)
                if um:
                    t_in += int(um.get("input_tokens", 0))
                    t_out += int(um.get("output_tokens", 0))
        if not (t_in or t_out):
            tu = (response.llm_output or {}).get("token_usage") or {}
            t_in, t_out = int(tu.get("prompt_tokens", 0)), int(tu.get("completion_tokens", 0))
        self.calls += 1
        self.tokens_in += t_in
        self.tokens_out += t_out
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 1), "in": t_in, "out": t_out}) + "\n")


def total_usage(log_path):
    """(calls, input_tokens, output_tokens) recorded in a usage log."""
    p = Path(log_path)
    n = ti = to = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1; ti += r.get("in", 0); to += r.get("out", 0)
    return n, ti, to

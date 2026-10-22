"""第 10 项只读探针的判定：只有"确实尝试写入且因只读被拒"才算通过。"""
import json

WRITE_TOOLS = {"write", "edit", "multiedit", "apply_patch", "patch"}
SHELL_TOOLS = {"bash", "shell", "pwsh"}


def _events(text):
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def classify(exit_code, events_text, target_name, target_exists):
    """返回 failed_start | written | no_attempt | denied | unclear。"""
    events = _events(events_text)
    if exit_code != 0 or not any(e.get("type") == "session" for e in events):
        return "failed_start"
    if target_exists:
        return "written"
    calls = {e.get("callId"): e for e in events if e.get("type") == "tool_call"}
    results = {e.get("callId"): e for e in events if e.get("type") == "tool_result"}
    attempts = [c for c in calls.values()
                if (c.get("tool") in WRITE_TOOLS or c.get("tool") in SHELL_TOOLS)
                and target_name in json.dumps(c.get("input"), ensure_ascii=False)]
    if not attempts:
        return "no_attempt"
    for c in attempts:
        if (results.get(c.get("callId")) or {}).get("status") == "completed" and c.get("tool") in WRITE_TOOLS:
            return "written"
    denied = [c for c in attempts
              if (results.get(c.get("callId")) or {}).get("status") == "error"
              and "read-only" in str(results[c.get("callId")].get("result", ""))]
    return "denied" if denied else "unclear"

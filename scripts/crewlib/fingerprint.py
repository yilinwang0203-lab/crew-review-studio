"""扫描范围内文件的 SHA256 指纹（spec §5）。"""
from __future__ import annotations

import hashlib
import os
import re

from .common import CrewError

PROBE = "__crew_probe__"


class SymlinkError(CrewError):
    def __init__(self, paths):
        self.paths = paths
        super().__init__("扫描范围内有符号链接（第一版不支持，请换成实体文件或加入 exclude 后重试）：\n- "
                         + "\n- ".join(paths))


def glob_to_regex(pattern: str):
    i, out = 0, []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


class _Matcher:
    def __init__(self, include, exclude):
        self.inc = [glob_to_regex(p) for p in include]
        self.exc = [glob_to_regex(p) for p in exclude]

    def excluded(self, rel):
        return any(r.match(rel) for r in self.exc)

    def wanted(self, rel):
        return not self.excluded(rel) and any(r.match(rel) for r in self.inc)

    def dir_pruned(self, rel_dir):
        return self.excluded(rel_dir + "/" + PROBE)


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root, include, exclude) -> dict:
    m = _Matcher(include, exclude)
    files, links = {}, []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel == "." else rel + "/"
        keep = []
        for d in sorted(dirnames):
            rd = prefix + d
            if m.dir_pruned(rd):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                links.append(rd + "/")
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            rf = prefix + f
            if not m.wanted(rf):
                continue
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                links.append(rf)
                continue
            files[rf] = _sha256_file(full)
    if links:
        raise SymlinkError(sorted(links))
    return files


def manifest_text(files: dict) -> str:
    return "".join(f"{p}\t{h}\n" for p, h in sorted(files.items()))


def parse_manifest(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if line:
            p, h = line.rsplit("\t", 1)
            out[p] = h
    return out


def total(files: dict) -> str:
    return hashlib.sha256(manifest_text(files).encode("utf-8")).hexdigest()


def diff(old: dict, new: dict) -> dict:
    removed = {p: h for p, h in old.items() if p not in new}
    added = {p: h for p, h in new.items() if p not in old}
    changed = sorted(p for p in old if p in new and old[p] != new[p])
    renamed = []
    for p, h in sorted(removed.items()):
        match = next((q for q, g in sorted(added.items()) if g == h), None)
        if match is not None:
            renamed.append([p, match])
            del added[match]
    gone = {a for a, _ in renamed}
    return {"added": sorted(added), "removed": sorted(p for p in removed if p not in gone),
            "renamed": renamed, "changed": changed}


def is_clean(d: dict) -> bool:
    return not any(d.values())


def describe(d: dict) -> str:
    parts = []
    if d["added"]:
        parts.append("新增 " + "、".join(d["added"]))
    if d["removed"]:
        parts.append("删除 " + "、".join(d["removed"]))
    if d["renamed"]:
        parts.append("改名 " + "、".join(f"{a}→{b}" for a, b in d["renamed"]))
    if d["changed"]:
        parts.append("内容变化 " + "、".join(d["changed"]))
    return "；".join(parts)

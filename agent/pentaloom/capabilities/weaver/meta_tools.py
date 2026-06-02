"""6 个 meta-tool 的业务 logic. tools/weaver.py 的工具体只做参数解析 + 调这里.

实施范围:
  - list_weaver / inspect_weaver / edit_weaver / delete_weaver  (skill + app 实装)
  - tail_weaver_logs                                             (app 实装, skill 仍 NotImplementedError)
  - run_weaver                                                   (注册但 NotImplementedError; workflow milestone 才支持)

**weaver = 用户私人产物**. 内置 skill (report-generator 等) 是 PentaLoom 出厂能力,
不在任何 meta-tool 的视野里 — 用户视角 / agent 视角都一致, 防概念混淆.
agent 想看内置 SKILL.md 直接 Read agent/.claude/skills/<name>/SKILL.md.

list / inspect / tail_logs 是只读, 不弹 HITL; edit / delete 弹 HITL (设计文档 §8.2).
"""

from __future__ import annotations

from typing import Any

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import app_runtime, index, skill
from pentaloom.capabilities.weaver.models import WeaverKind
from pentaloom.config import Settings

_VALID_KINDS = ("skill", "subagent", "workflow", "app")


def _check_kind(kind: str) -> WeaverKind:
    if kind not in _VALID_KINDS:
        raise index.WeaverError(
            f"kind 必须是 {_VALID_KINDS} 之一, 收到 {kind!r}"
        )
    return kind  # type: ignore[return-value]


def list_weaver(
    settings: Settings, kind: str = "", query: str = ""
) -> dict[str, Any]:
    """列用户织的 weaver 产物 (不含内置 skill).

    kind 空 = 全部; query 非空 = 在 name + description 里子串搜.
    返 {"counts": {kind: int}, "items": [{name, kind, description, source, ...}]}
    """
    idx = index.load_index(settings)
    items: list[dict[str, Any]] = []

    for k in (kind,) if kind else _VALID_KINDS:
        if k:
            _check_kind(k)
        for entry in idx.bucket(k):  # type: ignore[arg-type]
            items.append({
                "name": entry.name,
                "kind": entry.kind,
                "description": entry.description,
                "source": entry.source,
                "path": entry.path,
            })

    if query:
        q = query.lower()
        items = [
            it for it in items
            if q in it["name"].lower() or q in it["description"].lower()
        ]

    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1

    return {"counts": counts, "items": items}


def inspect_weaver(settings: Settings, kind: str, name: str) -> dict[str, Any]:
    """读某产物完整内容. 内置 skill 不在 weaver 体系内, 不支持 inspect."""
    k = _check_kind(kind)
    if k == "skill":
        entry = index.find_entry(settings, "skill", name)
        if entry is None:
            raise index.WeaverError(
                f"skill {name!r} 不存在. 注: 内置 skill 不在 weaver 里, "
                "直接 Read agent/.claude/skills/<name>/SKILL.md"
            )
        meta = skill.read_skill_meta(settings, name)
        return {
            "name": name,
            "kind": "skill",
            "source": entry.source,
            "description": entry.description,
            "content": skill.read_skill_md(settings, name),
            "meta": meta.model_dump(mode="json") if meta else None,
        }
    if k == "app":
        entry = index.find_entry(settings, "app", name)
        if entry is None:
            raise index.WeaverError(f"app {name!r} 不存在")
        meta = app_biz.read_meta(settings, name)
        summary = app_biz.manifest_invocations_summary(settings, name)
        return {
            "name": name,
            "kind": "app",
            "source": entry.source,
            "description": entry.description,
            "summary": summary,
            "meta": meta.model_dump(mode="json") if meta else None,
        }
    # subagent / workflow — 在后续里程碑实装
    raise index.WeaverError(
        f"inspect_weaver(kind={kind}) 只支持 skill / app; "
        f"{kind} 在后续里程碑实装"
    )


def edit_weaver(
    settings: Settings, kind: str, name: str, new_content: str
) -> dict[str, Any]:
    """改某产物. skill 是改 SKILL.md 全文. 内置 skill 不在 weaver 内, 不会被点名."""
    k = _check_kind(kind)
    if k == "skill":
        meta = skill.edit_skill(settings, name, new_content)
        return {"name": name, "kind": "skill", "edited": True, "meta": meta.model_dump(mode="json")}
    raise index.WeaverError(
        f"M14 阶段 edit_weaver(kind={kind}) 只支持 skill; {kind} 在后续里程碑实装"
    )


def delete_weaver(settings: Settings, kind: str, name: str) -> dict[str, Any]:
    """软删. skill 是搬到 .trash/. 内置 skill 不在 weaver 内, 不会被点名.

    trash_path 为 None 时表示孤儿条目 — index 有但物理目录已被外部清掉, 这次只清了 index.
    """
    k = _check_kind(kind)
    if k == "skill":
        trash_path = skill.delete_skill_soft(settings, name)
        return {
            "name": name, "kind": "skill", "deleted": True,
            "trash_path": str(trash_path) if trash_path else None,
            "was_orphan": trash_path is None,
        }
    if k == "app":
        trash_path = app_biz.delete_app_soft(settings, name)
        return {
            "name": name, "kind": "app", "deleted": True,
            "trash_path": str(trash_path) if trash_path else None,
            "was_orphan": trash_path is None,
        }
    raise index.WeaverError(
        f"delete_weaver(kind={kind}) 只支持 skill / app; {kind} 在后续里程碑实装"
    )


def run_weaver(
    settings: Settings, kind: str, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """运行某产物. workflow 才有意义; skill / subagent 是被加载, 没 'run' 语义."""
    k = _check_kind(kind)
    if k == "skill":
        raise index.WeaverError(
            "skill 是被动加载, 没 'run' 语义. 用 inspect_weaver 看内容, "
            "或者发对话让 agent 隐式触发"
        )
    if k == "subagent":
        raise index.WeaverError(
            "subagent 通过 Task 工具派单, 不走 run_weaver. M17 才有 UI 配置"
        )
    raise NotImplementedError(
        f"run_weaver(kind={kind}) M16 才实装 (workflow); M14 这里先占位"
    )


def tail_weaver_logs(
    settings: Settings, kind: str, name: str, n: int = 20
) -> dict[str, Any]:
    """读某产物运行历史. app 走 app_runtime.tail_run_logs (logs/runs.jsonl).
    skill 没有运行语义, skill 调这条会抛.
    """
    k = _check_kind(kind)
    if k == "app":
        entry = index.find_entry(settings, "app", name)
        if entry is None:
            raise index.WeaverError(f"app {name!r} 不存在")
        runs = app_runtime.tail_run_logs(settings, name, limit=max(1, n))
        return {"name": name, "kind": "app", "runs": runs}
    if k == "skill":
        raise index.WeaverError(
            "skill 是被动加载, 没有运行历史. 用 inspect_weaver 读 SKILL.md"
        )
    raise NotImplementedError(
        f"tail_weaver_logs(kind={kind}) 在 workflow milestone 才实装"
    )

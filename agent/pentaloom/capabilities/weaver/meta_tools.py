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
        # D-3-min: 加 running services snapshot (lazy import 防循环)
        from pentaloom.capabilities.weaver.service_registry import service_registry
        running_services = service_registry().list_for_app(name)
        return {
            "name": name,
            "kind": "app",
            "source": entry.source,
            "description": entry.description,
            "summary": summary,
            "meta": meta.model_dump(mode="json") if meta else None,
            "running_services": running_services,  # [{name, status, port, pid, started_at, restart_count, log_path}]
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
    settings: Settings, kind: str, name: str, n: int = 20,
    mode: str = "runs",
) -> dict[str, Any]:
    """读某产物运行历史 / 服务日志.

    mode (kind=app 时):
      - "runs" (默认): 读 logs/runs.jsonl — invoke_app 历次 run 记录
      - "service:<svc_name>": 读 logs/service-<svc_name>.log — service stdout/stderr
        (含 [stdout]/[stderr] 行首前缀). agent 想看 uvicorn / fastapi 真实输出走这.

    skill 没有运行语义, skill 调这条会抛.
    """
    k = _check_kind(kind)
    if k == "app":
        entry = index.find_entry(settings, "app", name)
        if entry is None:
            raise index.WeaverError(f"app {name!r} 不存在")
        # service log mode
        if mode.startswith("service:"):
            svc_name = mode[len("service:"):].strip()
            if not svc_name:
                raise index.WeaverError(
                    "mode='service:<name>' 必须指定 service name (e.g., 'service:api')"
                )
            from pentaloom.capabilities.weaver import paths
            log_file = paths.app_logs_dir(settings, name) / f"service-{svc_name}.log"
            if not log_file.exists():
                return {
                    "name": name, "kind": "app", "mode": mode,
                    "log_file": str(log_file),
                    "lines": [],
                    "note": "log file 不存在 — service 没启动过",
                }
            # 读最后 n 行 (大文件高效写法应该 seek, 但 service log 一般小, 简单 splitlines OK)
            text = log_file.read_text(errors="replace")
            lines = text.splitlines()[-max(1, n):]
            return {
                "name": name, "kind": "app", "mode": mode,
                "log_file": str(log_file),
                "lines": lines,
            }
        # 默认 runs mode
        if mode != "runs":
            raise index.WeaverError(
                f"mode={mode!r} 不合法 (支持 'runs' | 'service:<svc_name>')"
            )
        runs = app_runtime.tail_run_logs(settings, name, limit=max(1, n))
        return {"name": name, "kind": "app", "mode": "runs", "runs": runs}
    if k == "skill":
        raise index.WeaverError(
            "skill 是被动加载, 没有运行历史. 用 inspect_weaver 读 SKILL.md"
        )
    raise NotImplementedError(
        f"tail_weaver_logs(kind={kind}) 在 workflow milestone 才实装"
    )

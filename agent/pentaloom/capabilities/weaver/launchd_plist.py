"""launchd_plist — service / schedule / watch 三类组件 OS 化的入口.

每个组件渲一份独立的 launchd UserAgent plist (`~/Library/LaunchAgents/`),
PentaLoom finalize_app 后调 launchctl load, OS 接管它的生命周期; PentaLoom
关掉 / 卸载也不影响, 只有用户主动 unload 才停 (实现"应用真正存在").

plist label 规约: `com.pentaloom.app.<app-name>.<kind>.<comp-name>`
  - kind ∈ {svc, sched, wch}
  - 卸载脚本一行 grep 能扫干净: `launchctl list | grep com.pentaloom.app.`

每个 plist 的 ProgramArguments 都调一个 wrapper:
  python -m pentaloom.weaver_runner --app=<n> --component=<n> --kind=<service|schedule|watch>
wrapper 自己读 app.json 找到 spec, 干对应的事 (起 service / 跑 schedule invoke / 跑 watch invoke).
这样 plist 模板简单, 业务逻辑都在 wrapper 里 Python 写.

cron 转 launchd 字段策略:
  - 标准 5-field (e.g. "0 9 * * *") → StartCalendarInterval 拆字段
  - 步长 (e.g. "*/5 * * * *") → fallback StartInterval 秒数
  - 复杂表达式 (列表 / 范围) → 抛错让用户简化 schedule 表达式 (launchd 天然限制)

watch 字段策略:
  - WatchPaths = [absolute(app_files_dir/spec.path)]
  - spec.events / debounce_ms 在 wrapper 端二次过滤 — launchd 自身不分事件类型
"""
from __future__ import annotations

import logging
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Literal

from pentaloom.capabilities.weaver.models import (
    AppScheduleSpec,
    AppServiceSpec,
    AppWatchSpec,
)
from pentaloom.capabilities.weaver.paths import app_dir, weaver_root
from pentaloom.config import Settings, get_settings

logger = logging.getLogger(__name__)


LABEL_PREFIX = "com.pentaloom.app"
KIND_SVC = "svc"
KIND_SCHED = "sched"
KIND_WCH = "wch"
ComponentKind = Literal["svc", "sched", "wch"]


# ────────────────────────────────────────────────────────────────────
# 路径 / label
# ────────────────────────────────────────────────────────────────────


def _label(app_name: str, kind: ComponentKind, comp_name: str) -> str:
    return f"{LABEL_PREFIX}.{app_name}.{kind}.{comp_name}"


def _plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _logs_dir() -> Path:
    return Path.home() / ".pentaloom" / "logs" / "apps"


def _wrapper_python() -> str:
    """PentaLoom venv 的 python 绝对路径. plist ProgramArguments 第一个 entry."""
    # PentaLoom 没真正打包前, 跑当前进程同一个 python (sys.executable). 装系统时
    # finalize 写的 plist 含的就是用户当前用的 venv python, 之后挪 venv 要重新
    # finalize 一次 — 跟现有 ServiceRegistry lazy spawn 一致行为.
    return sys.executable


# ────────────────────────────────────────────────────────────────────
# 三类 plist 模板渲染
# ────────────────────────────────────────────────────────────────────


def _common_dict(label: str, kind: ComponentKind) -> dict[str, Any]:
    """三类公共字段: Label / ProgramArguments (wrapper) / log redirect / env."""
    log_stem = label  # log 文件直接用 label, 易于 grep
    log_dir = _logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    # PYTHONPATH: dev 态 pentaloom 是源码 (agent/pentaloom/), 不是装的 wheel — launchd
    # 不继承 shell 环境, 不显式注 PYTHONPATH 的话 `python -m pentaloom.weaver_runner`
    # 直接 ModuleNotFoundError. AGENT_ROOT 是 pentaloom 包的父目录.
    from pentaloom.config.settings import AGENT_ROOT
    return {
        "Label": label,
        # ProgramArguments 占位 — render_* 各自填.
        "StandardOutPath": str(log_dir / f"{log_stem}.stdout.log"),
        "StandardErrorPath": str(log_dir / f"{log_stem}.stderr.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(AGENT_ROOT),
        },
    }


def _wrapper_args(app_name: str, comp_name: str, kind: str) -> list[str]:
    """生成 ProgramArguments: <python> -m pentaloom.weaver_runner --app=X --component=Y --kind=Z."""
    return [
        _wrapper_python(),
        "-m",
        "pentaloom.weaver_runner",
        f"--app={app_name}",
        f"--component={comp_name}",
        f"--kind={kind}",
    ]


def render_service_plist(app_name: str, spec: AppServiceSpec) -> tuple[str, dict[str, Any]]:
    """service: KeepAlive 长服务. 返 (label, plist_dict).

    KeepAlive 映射:
      - restart=always     → KeepAlive=true
      - restart=on_failure → KeepAlive={"SuccessfulExit": False}
      - restart=never      → 不写 KeepAlive (跑一次退出就退, 但仍在 launchd registry)
    """
    label = _label(app_name, KIND_SVC, spec.name)
    d = _common_dict(label, KIND_SVC)
    d["ProgramArguments"] = _wrapper_args(app_name, spec.name, "service")
    d["RunAtLoad"] = True

    if spec.restart == "always":
        d["KeepAlive"] = True
    elif spec.restart == "on_failure":
        d["KeepAlive"] = {"SuccessfulExit": False}
    # restart="never": 不写 KeepAlive

    return label, d


def render_schedule_plist(app_name: str, spec: AppScheduleSpec) -> tuple[str, dict[str, Any]]:
    """schedule: 时间触发. cron → StartCalendarInterval / StartInterval."""
    label = _label(app_name, KIND_SCHED, spec.name)
    d = _common_dict(label, KIND_SCHED)
    d["ProgramArguments"] = _wrapper_args(app_name, spec.name, "schedule")
    d["RunAtLoad"] = False  # schedule 不要启动时立刻跑一次

    cal, interval = _cron_to_launchd(spec.schedule)
    if cal is not None:
        d["StartCalendarInterval"] = cal
    elif interval is not None:
        d["StartInterval"] = interval
    else:
        raise ValueError(
            f"schedule {spec.schedule!r} launchd 不支持 — 仅支持每字段单值或纯 */N 步长. "
            f"复杂列表/范围请简化."
        )

    return label, d


def render_watch_plist(
    app_name: str,
    spec: AppWatchSpec,
    files_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """watch: 文件变化触发. WatchPaths = absolute(files_dir / spec.path).

    spec.invocation_id is None 不写 plist (仅 UI 浏览模式) — 调用方应跳过.
    """
    if spec.invocation_id is None:
        raise ValueError(f"watch {spec.name!r} 没 invocation_id, 不该 render plist")

    label = _label(app_name, KIND_WCH, spec.name)
    d = _common_dict(label, KIND_WCH)
    d["ProgramArguments"] = _wrapper_args(app_name, spec.name, "watch")
    d["RunAtLoad"] = False

    target = (files_dir / spec.path).resolve()
    d["WatchPaths"] = [str(target)]
    return label, d


# ────────────────────────────────────────────────────────────────────
# cron → launchd 字段
# ────────────────────────────────────────────────────────────────────

_CRON_FIELDS = ("Minute", "Hour", "Day", "Month", "Weekday")


def _cron_to_launchd(
    cron: str,
) -> tuple[dict[str, int] | None, int | None]:
    """5-field cron → (StartCalendarInterval dict, StartInterval seconds).

    返 (cal, None): launchd StartCalendarInterval 路径 (单值字段)
    返 (None, sec): StartInterval 路径 (纯 */N 步长)
    返 (None, None): launchd 不支持 (列表/范围)
    """
    parts = cron.split()
    if len(parts) != 5:
        return (None, None)

    # 全部 */N 步长 (e.g. "*/5 * * * *") → StartInterval. 优先识别这条,
    # 因为 launchd StartCalendarInterval 不支持 */N.
    if _all_step_cron(parts):
        # 找到唯一 */N 字段 (其他都是 *)
        for i, p in enumerate(parts):
            if p.startswith("*/"):
                step = int(p[2:])
                # 字段索引: 0=分, 1=时, 2=日, 3=月, 4=周
                multipliers = (60, 3600, 86400)  # 日/月/周不能简单 StartInterval, return None
                if i >= 3:
                    return (None, None)
                return (None, step * multipliers[i])
        return (None, None)

    # 单值字段 (e.g. "0 9 * * *") → StartCalendarInterval
    cal: dict[str, int] = {}
    for field, val in zip(_CRON_FIELDS, parts):
        if val == "*":
            continue
        if val.isdigit():
            cal[field] = int(val)
        else:
            # 含 , 或 - 或 /N 等, launchd 不支持
            return (None, None)
    if not cal:
        # 全是 * → 每分钟. launchd 表达不出"每分钟" 的 cron, 用 StartInterval 60.
        return (None, 60)
    return (cal, None)


def _all_step_cron(parts: list[str]) -> bool:
    """判定: 恰好一个字段是 */N, 其他都是 *. 用于 StartInterval 路径."""
    star_count = parts.count("*")
    step_count = sum(1 for p in parts if re.fullmatch(r"\*/\d+", p))
    return star_count + step_count == 5 and step_count == 1


# ────────────────────────────────────────────────────────────────────
# launchctl 包装
# ────────────────────────────────────────────────────────────────────


def _launchctl(*args: str) -> tuple[int, str, str]:
    """跑 launchctl, 返 (returncode, stdout, stderr). 不抛异常."""
    proc = subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _write_plist(label: str, body: dict[str, Any]) -> Path:
    path = _plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(body, f)
    return path


def _load_plist(label: str) -> None:
    """launchctl load. 已 load 的话先 unload (idempotent)."""
    path = _plist_path(label)
    # 先 unload 容错 (如果已 load), 失败吞.
    _launchctl("unload", str(path))
    rc, _out, err = _launchctl("load", str(path))
    if rc != 0:
        logger.warning("launchctl load %s failed rc=%d err=%s", label, rc, err.strip())


def _unload_and_remove(label: str) -> None:
    """unload + 删 plist 文件. 任一步失败都吞 (idempotent)."""
    path = _plist_path(label)
    _launchctl("unload", str(path))  # 失败吞 (可能没 load)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("rm plist %s failed: %s", path, e)


# ────────────────────────────────────────────────────────────────────
# 对外 API — 给 app.py / routers/weaver.py 用
# ────────────────────────────────────────────────────────────────────


def reload_for_app(app_name: str, settings: Settings | None = None) -> dict[str, Any]:
    """根据 app.json 重新生成 + load app 的所有 service/schedule/watch plist.

    finalize_app 调; 也给前端 'reload' 按钮用. 流程:
      1. 先 unload + 删 app 已有的所有 plist (前缀 grep)
      2. 读 app.json, 渲所有 service/schedule/watch plist
      3. 写 + load 每个 plist
    返 {"loaded": [label, ...], "skipped": [...]}, 失败 raise.
    """
    settings = settings or get_settings()
    # 先清旧 plist
    unload_for_app(app_name)

    # 延迟 import 防循环
    from pentaloom.capabilities.weaver.app import read_app_definition

    app_def = read_app_definition(settings, app_name)
    if app_def is None:
        raise FileNotFoundError(f"app.json missing for {app_name!r}")
    files_dir = app_dir(settings, app_name) / "files"
    components = app_def.components

    loaded: list[str] = []
    skipped: list[str] = []

    for svc in components.services:
        try:
            label, body = render_service_plist(app_name, svc)
            _write_plist(label, body)
            _load_plist(label)
            loaded.append(label)
        except Exception as e:
            logger.warning("render service %s.%s failed: %s", app_name, svc.name, e)
            skipped.append(f"svc:{svc.name}: {e}")

    for sch in components.schedules:
        try:
            label, body = render_schedule_plist(app_name, sch)
            _write_plist(label, body)
            _load_plist(label)
            loaded.append(label)
        except Exception as e:
            logger.warning("render schedule %s.%s failed: %s", app_name, sch.name, e)
            skipped.append(f"sched:{sch.name}: {e}")

    for wch in components.watches:
        if wch.invocation_id is None:
            # 仅 UI 浏览模式, 不进 launchd
            continue
        try:
            label, body = render_watch_plist(app_name, wch, files_dir)
            _write_plist(label, body)
            _load_plist(label)
            loaded.append(label)
        except Exception as e:
            logger.warning("render watch %s.%s failed: %s", app_name, wch.name, e)
            skipped.append(f"wch:{wch.name}: {e}")

    logger.info(
        "launchd_plist.reload_for_app %s: loaded=%d skipped=%d",
        app_name, len(loaded), len(skipped),
    )
    return {"loaded": loaded, "skipped": skipped}


def unload_for_app(app_name: str) -> int:
    """unload + 删 app 的所有 plist (扫前缀匹配). 返清理数量."""
    prefix = f"{LABEL_PREFIX}.{app_name}."
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if not la_dir.exists():
        return 0
    count = 0
    for plist in la_dir.glob(f"{prefix}*.plist"):
        label = plist.stem
        _unload_and_remove(label)
        count += 1
    return count


def list_for_app(app_name: str) -> list[dict[str, Any]]:
    """列 app 的所有 plist + 每个的 launchctl 状态.

    返 [{"label", "kind", "comp_name", "loaded": bool, "pid": int|None,
         "last_exit_status": int|None}, ...]. 给 routers/weaver.py /detail 用.
    """
    prefix = f"{LABEL_PREFIX}.{app_name}."
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if not la_dir.exists():
        return []

    # 一次 launchctl list 拉所有跑着的, 然后跟 plist 文件交叉.
    rc, out, _err = _launchctl("list")
    running: dict[str, dict[str, Any]] = {}
    if rc == 0:
        # launchctl list 输出格式: PID  Status  Label\n
        for line in out.strip().split("\n")[1:]:
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            pid_s, status_s, label = cols[0], cols[1], cols[2]
            if not label.startswith(prefix):
                continue
            running[label] = {
                "pid": None if pid_s == "-" else int(pid_s),
                "last_exit_status": None if status_s == "-" else int(status_s),
            }

    out_list: list[dict[str, Any]] = []
    log_dir = _logs_dir()
    for plist in sorted(la_dir.glob(f"{prefix}*.plist")):
        label = plist.stem
        # label = com.pentaloom.app.<app>.<kind>.<comp>
        rest = label[len(prefix):]  # <kind>.<comp>
        kind, _, comp = rest.partition(".")
        info = running.get(label, {"pid": None, "last_exit_status": None})
        out_list.append({
            "label": label,
            "kind": kind,
            "comp_name": comp,
            "loaded": label in running,
            "pid": info["pid"],
            "last_exit_status": info["last_exit_status"],
            "plist_path": str(plist),
            "stdout_path": str(log_dir / f"{label}.stdout.log"),
            "stderr_path": str(log_dir / f"{label}.stderr.log"),
        })
    return out_list


def restart_component(app_name: str, kind: ComponentKind, comp_name: str) -> bool:
    """unload + load 一个具体组件 plist (给 service stop/restart 按钮用).

    返 True 成功 / False 该 plist 不存在.
    """
    label = _label(app_name, kind, comp_name)
    path = _plist_path(label)
    if not path.exists():
        return False
    _launchctl("unload", str(path))
    _load_plist(label)
    return True


def stop_component(app_name: str, kind: ComponentKind, comp_name: str) -> bool:
    """unload (停掉) 一个具体 plist, **不删** plist 文件 — 让 reload 时再 load 回去.

    PR 1 需求: ServiceRow stop 按钮; 想"暂停" service 但保留配置.
    """
    label = _label(app_name, kind, comp_name)
    path = _plist_path(label)
    if not path.exists():
        return False
    rc, _out, _err = _launchctl("unload", str(path))
    return rc == 0

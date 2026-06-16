"""weaver_app_env — script / service / schedule / watch subprocess 的统一 env 注入.

为什么集中收口:
  - script (invoke_app spawn subprocess) / service (weaver_runner execvpe) / schedule
    + watch (weaver_runner async invoke_app) 三条路径都要给被起的进程一组 PENTALOOM_*
    env 让脚本能反向调 host (开窗 / 找 sibling service / 读自家 files).
  - 之前 service 那段在 weaver_runner 里手撸, script 路径完全没注入 — 这次全收回到
    一个 helper, 三处复用.

注入字段语义:
  PENTALOOM_LOOM         loom binary 绝对路径 (script subprocess 调 loom 开关窗)
  PENTALOOM_LOOMER       loomer binary 绝对路径 (一般不直调, loom open 内部 spawn)
  PENTALOOM_LOOMCTL      loomctl binary 绝对路径 (script 反向调能力的统一 CLI)
  PENTALOOM_APP_NAME     当前 app 名 (script 多 app 跑时区分自己)
  PENTALOOM_APP_DIR      ~/.pentaloom/sandboxes/<app>
  PENTALOOM_FILES_DIR    .../files (window entry 算绝对路径用)
  PENTALOOM_RUNTIME_DIR  .../.runtime (script 读 sibling service .port 文件用)
  PENTALOOM_LOGS_DIR     .../logs (script 写自定义日志用, runs.jsonl 也在这)
  PENTALOOM_RUNS_DIR     兼容老 service env (老字段名), 同 LOGS_DIR
  PENTALOOM_INVOCATION_ID  本次 invoke 的 invocation id (script 路径填, service 不填)
  PENTALOOM_TRIGGER        user / schedule / watch / workflow (script 路径填, service 不填)
  PENTALOOM_APP_PORT       service 路径填; script 不填 (script 自己不 listen port)
  PENTALOOM_SERVICE_NAME   service 路径填
"""
from __future__ import annotations

from typing import Optional

from pentaloom.capabilities.weaver import paths
from pentaloom.config import Settings


def weaver_app_env(
    settings: Settings,
    app_name: str,
    *,
    invocation_id: Optional[str] = None,
    trigger: str = "user",
    service_name: Optional[str] = None,
    service_port: Optional[int] = None,
) -> dict[str, str]:
    """组一份 weaver 上下文 env. 调用方 update 进 spawn env.

    invocation_id / trigger 给 script 路径用 (handler 想知道是谁触发的).
    service_name / service_port 给 service 路径用.
    """
    app_root = paths.app_dir(settings, app_name)
    files_root = paths.app_files_dir(settings, app_name)
    runs_root = paths.app_runs_dir(settings, app_name)
    runtime_dir = app_root / ".runtime"
    logs_dir = paths.app_logs_dir(settings, app_name)

    env: dict[str, str] = {
        "PENTALOOM_LOOM": str(settings.loom_bin),
        "PENTALOOM_LOOMER": str(settings.loomer_bin),
        "PENTALOOM_LOOMCTL": str(settings.loomctl_bin),
        "PENTALOOM_APP_NAME": app_name,
        "PENTALOOM_APP_DIR": str(app_root),
        "PENTALOOM_FILES_DIR": str(files_root),
        "PENTALOOM_RUNTIME_DIR": str(runtime_dir),
        "PENTALOOM_LOGS_DIR": str(logs_dir),
        "PENTALOOM_RUNS_DIR": str(runs_root),
    }
    if invocation_id:
        env["PENTALOOM_INVOCATION_ID"] = invocation_id
    if trigger:
        env["PENTALOOM_TRIGGER"] = trigger
    if service_name:
        env["PENTALOOM_SERVICE_NAME"] = service_name
    if service_port is not None:
        env["PENTALOOM_APP_PORT"] = str(service_port)
    return env

"""Workflow runtime.

invoke_workflow → 跑 step DAG (线性) → 每步 dispatch 到 invoke_app/call_llm/set_var
→ 独立 logs/runs.jsonl 落 → 返 {run_id, status, output, steps}.

设计要点:
  - mustache 渲染规则: invoke_app.args / set_var.value / output_template /
    call_llm.prompt 同一套 (递归 dict/list, string 单 {{path}} 返原值类型, 多 {{...}}
    嵌文本拼字符串)
  - call_llm 用独立 anthropic.AsyncAnthropic, base_url + api_key 跟主对话同
    (settings.anthropic_api_key / anthropic_base_url), 但 stateless (不接 LoomPool)
  - call_llm output_format=json runtime 强 json.loads, 失败 step failed
  - 中间步失败 → break, 后续不跑 (没 retry, MVP)
  - 独立 runs.jsonl 在 weaver/workflows/<name>/logs/, 不混 app log
  - step log 含 app_name/invocation_id/app_run_id (invoke_app 步) 给 modal 追踪
  - 双向 use_count: workflow.bump_use_count + app_runtime.invoke_app 自己也涨 app.use_count
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from pentaloom.capabilities.weaver import app_runtime, paths, workflow as workflow_biz
from pentaloom.capabilities.weaver.models import (
    CallLlmStep,
    InvokeAppStep,
    SetVarStep,
    WorkflowDefinition,
)
from pentaloom.config import Settings


class WorkflowError(Exception):
    """workflow runtime 错误. caller 包成 ToolResult 给 agent."""


# ─── Mustache 渲染 ─────────────────────────────────────────────────────────

_MUSTACHE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _lookup_path(ctx: dict, path: str) -> Any:
    """Path lookup: 'inputs.foo' / 'steps.s1.output.bar.baz' / 'steps.s1.value'."""
    parts = path.split(".")
    cur: Any = ctx
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                raise WorkflowError(
                    f"mustache 引用 {path!r} 失败 — list 索引 {p!r} 不合法"
                )
        else:
            raise WorkflowError(
                f"mustache 引用 {path!r} 失败 — 路径 {p!r} 在 {type(cur).__name__} 里找不到"
            )
    return cur


def _render_string(template: str, ctx: dict) -> Any:
    """单 {{path}} 整 string 替换 → 返原值类型 (int/dict/list 不强制 str 化).
    多个 {{...}} 嵌文本 → 字符串拼接 (强制 str)."""
    matches = list(_MUSTACHE_RE.finditer(template))
    if not matches:
        return template
    if len(matches) == 1 and matches[0].group(0) == template.strip():
        # 整 string 就一个 {{...}} → 返原值类型
        return _lookup_path(ctx, matches[0].group(1))
    # 多个或嵌文本 → 字符串拼接
    def replace(m: re.Match) -> str:
        v = _lookup_path(ctx, m.group(1))
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    return _MUSTACHE_RE.sub(replace, template)


def render_mustache(value: Any, ctx: dict) -> Any:
    """递归渲染. dict/list 遍历, str 走 _render_string, 其他原样."""
    if isinstance(value, str):
        return _render_string(value, ctx)
    if isinstance(value, dict):
        return {k: render_mustache(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_mustache(v, ctx) for v in value]
    return value


# ─── 主入口 ────────────────────────────────────────────────────────────────

async def invoke_workflow(
    settings: Settings,
    *,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑一个 workflow (status=ready). args = workflow inputs."""
    args = args or {}
    started_at_ms = int(time.time() * 1000)
    run_id = _new_run_id()

    meta = workflow_biz.read_meta(settings, name)
    if meta is None:
        raise WorkflowError(f"workflow {name!r} 不存在")
    if meta.status != "ready":
        hint = {
            "draft": "还在写, 用 weave_workflow_finalize 收口",
            "dirty": "改过没重新 finalize, 用 weave_workflow_finalize 重校",
            "failed": f"上次 finalize 失败 ({meta.last_finalize_error or 'unknown'}), 修完用 weave_workflow_finalize",
        }.get(meta.status, "status 不对劲")
        raise WorkflowError(
            f"workflow {name!r} status={meta.status!r}, 不能 invoke. {hint}"
        )

    definition = workflow_biz.read_workflow(settings, name)

    # inputs_schema 校验 (只用第三方 jsonschema 太重, 简单做 — type=object + required 校)
    _validate_inputs(args, definition.inputs_schema)

    ctx: dict[str, Any] = {"inputs": args, "steps": {}}
    step_results: list[dict] = []
    overall_status = "success"
    error_msg: str | None = None

    for step in definition.steps:
        s_started_ms = int(time.time() * 1000)
        try:
            if step.kind == "set_var":
                output = await _run_set_var_step(step, ctx)  # type: ignore[arg-type]
                step_log: dict[str, Any] = {"id": step.id, "kind": "set_var"}
                ctx["steps"][step.id] = {"output": output, "value": output}
            elif step.kind == "invoke_app":
                output, app_run_id = await _run_invoke_app_step(settings, step, ctx)  # type: ignore[arg-type]
                step_log = {
                    "id": step.id, "kind": "invoke_app",
                    "app_name": step.app_name,           # type: ignore[union-attr]
                    "invocation_id": step.invocation_id,  # type: ignore[union-attr]
                    "app_run_id": app_run_id,
                }
                ctx["steps"][step.id] = {"output": output}
            elif step.kind == "call_llm":
                output = await _run_call_llm_step(settings, step, ctx)  # type: ignore[arg-type]
                step_log = {"id": step.id, "kind": "call_llm"}
                ctx["steps"][step.id] = {"output": output}
            else:
                raise WorkflowError(f"未知 step.kind: {step.kind!r}")

            step_log.update({
                "status": "success",
                "duration_ms": int(time.time() * 1000) - s_started_ms,
                "output_summary": _truncate_for_log(output),
            })
            step_results.append(step_log)
        except Exception as e:
            err = str(e)[:500]
            step_results.append({
                "id": step.id, "kind": step.kind,
                "status": "failed",
                "duration_ms": int(time.time() * 1000) - s_started_ms,
                "error": err,
            })
            overall_status = "failed"
            error_msg = f"step {step.id!r} failed: {err}"
            logger.warning(
                f"invoke_workflow step failed: {name}/{step.id} ({step.kind}) — {err}"
            )
            break  # 不 retry, 后续 step 不跑

    duration_ms = int(time.time() * 1000) - started_at_ms

    # 最终 output: output_template 渲染 ctx, 没 template 返最后步 output
    final_output: Any
    if overall_status == "success":
        if definition.output_template is not None:
            try:
                final_output = render_mustache(definition.output_template, ctx)
            except WorkflowError as e:
                # output_template 渲染挂 → 整 workflow 失败 (不应该, 但兜底).
                # 加一条 pseudo step 让 modal Recent runs 能看到错在哪 (不然 step_results
                # 全 success, 但 run.status=failed 就成"幽灵失败"了).
                overall_status = "failed"
                error_msg = f"output_template 渲染失败: {e}"
                step_results.append({
                    "id": "__output_template__",
                    "kind": "output_template",
                    "status": "failed",
                    "duration_ms": 0,
                    "error": str(e)[:500],
                })
                final_output = {}
        elif step_results:
            last_id = step_results[-1]["id"]
            final_output = ctx["steps"][last_id].get("output")
        else:
            final_output = {}
    else:
        final_output = {}

    _append_workflow_run_log(
        settings, name, run_id, overall_status, duration_ms, error_msg, step_results,
    )

    if overall_status == "success":
        workflow_biz.bump_use_count(settings, name)
        logger.info(
            f"invoke_workflow success: {name} run={run_id} ({len(step_results)} steps, {duration_ms}ms)"
        )

    return {
        "run_id": run_id,
        "status": overall_status,
        "duration_ms": duration_ms,
        "output": final_output,
        "steps": step_results,
        "error": error_msg,
    }


# ─── Step dispatchers ──────────────────────────────────────────────────────

async def _run_set_var_step(step: SetVarStep, ctx: dict) -> Any:
    """set_var.value 递归渲染 mustache 后整体当 step.output."""
    return render_mustache(step.value, ctx)


async def _run_invoke_app_step(
    settings: Settings, step: InvokeAppStep, ctx: dict,
) -> tuple[Any, str]:
    """调 app_runtime.invoke_app(trigger='workflow').

    返 (output, app_run_id). app 自己的 use_count 由 invoke_app 内部递增 — workflow
    跟 app 双向涨, 用户在 app modal 看 use_count 是 user+workflow+schedule+watch 总和.
    """
    rendered_args = render_mustache(step.args, ctx)
    if not isinstance(rendered_args, dict):
        raise WorkflowError(
            f"invoke_app step.args 渲染后不是 dict (是 {type(rendered_args).__name__})"
        )
    try:
        result = await app_runtime.invoke_app(
            settings,
            app_name=step.app_name,
            invocation_id=step.invocation_id,
            args=rendered_args,
            trigger="workflow",
        )
    except app_runtime.InvokeError as e:
        raise WorkflowError(f"invoke_app failed: {e}") from e
    return result["output"], result["run_id"]


async def _run_call_llm_step(
    settings: Settings, step: CallLlmStep, ctx: dict,
) -> Any:
    """跑 anthropic.AsyncAnthropic 一次, 返 text 或 json (强校).

    用同 settings 的 api_key + base_url, 不接 LoomPool (stateless).
    output_format=json runtime 强 json.loads, 失败 → step failed.
    """
    rendered_prompt = render_mustache(step.prompt, ctx)
    if not isinstance(rendered_prompt, str):
        rendered_prompt = json.dumps(rendered_prompt, ensure_ascii=False)
    rendered_system = render_mustache(step.system, ctx) if step.system else ""
    if not isinstance(rendered_system, str):
        rendered_system = json.dumps(rendered_system, ensure_ascii=False)

    import anthropic  # noqa: PLC0415, 延迟 import 防启动慢
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
    )
    model = step.model or settings.model

    try:
        msg = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=step.max_tokens,
                system=rendered_system or "You are a helpful assistant.",
                messages=[{"role": "user", "content": rendered_prompt}],
            ),
            timeout=step.timeout_s,
        )
    except asyncio.TimeoutError as e:
        raise WorkflowError(
            f"call_llm 超时 ({step.timeout_s}s, model={model})"
        ) from e
    except Exception as e:
        raise WorkflowError(f"call_llm API 调用失败: {e}") from e

    text = "".join(b.text for b in msg.content if b.type == "text")

    if step.output_format == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise WorkflowError(
                f"call_llm output_format=json 但模型返非 JSON. "
                f"原始返回 (前 200 字): {text[:200]!r}. 解析错: {e}"
            ) from e
    return text


# ─── inputs 校验 (轻量, 不引 jsonschema) ───────────────────────────────────

def _validate_inputs(args: dict, schema: dict) -> None:
    """简单校 type=object + required keys. 不做 type 严格校 — runtime 错就报."""
    if not schema:
        return
    if schema.get("type") == "object":
        required = schema.get("required") or []
        for k in required:
            if k not in args:
                raise WorkflowError(
                    f"inputs 缺必填字段 {k!r} (inputs_schema.required)"
                )


# ─── 日志 ──────────────────────────────────────────────────────────────────

def _append_workflow_run_log(
    settings: Settings,
    name: str,
    run_id: str,
    status: str,
    duration_ms: int,
    error: str | None,
    steps: list[dict],
) -> None:
    """workflow 独立 logs/runs.jsonl. trigger 字段固定 'user' (workflow 当前只支持
    agent 触发; 远期 cron 触发 workflow 时改成 'schedule')."""
    log_file = paths.workflow_logs_dir(settings, name) / "runs.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "duration_ms": duration_ms,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "trigger": "user",
        "steps": steps,
    }
    if error:
        entry["error"] = error[:500]
    with log_file.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def tail_workflow_run_logs(
    settings: Settings, name: str, limit: int = 20,
) -> list[dict]:
    """读 runs.jsonl 倒序末尾 N 条. 跟 app_runtime.tail_run_logs 同款."""
    log_file = paths.workflow_logs_dir(settings, name) / "runs.jsonl"
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _truncate_for_log(value: Any, max_len: int = 200) -> Any:
    """step output_summary 截断 — 大对象 (image base64 / 大 dict) 不撑爆 jsonl."""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + f"...({len(value)} chars)"
    if isinstance(value, dict):
        s = json.dumps(value, ensure_ascii=False)
        if len(s) > max_len:
            return {"_truncated": True, "_size": len(s), "_preview": s[:max_len]}
    if isinstance(value, list) and len(value) > 20:
        return {"_truncated": True, "_count": len(value), "_preview": value[:5]}
    return value


def _new_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


# ─── 动态模式: 把 workflow 渲染成 plan markdown 注给主 agent ─────────────
# invoke_workflow_dynamic 工具调这个, 不跑任何 step. 主 agent 看到 ToolResult
# 后自己 TodoWrite 拆任务, 一步步调 invoke_app / 自己 reasoning, 跟普通对话一样.
#
# 跟静态 invoke_workflow 的差别:
#   - call_llm step → 翻译成"主 agent 自己处理这步" (主 agent 就是 LLM, 起子 LLM 冗余)
#   - set_var step  → 翻译成"记住这些常量" (agent 上下文里天然有, 不需要 runtime 状态)
#   - invoke_app    → 翻译成"建议你调 invoke_app(...)" (agent 仍可临时改 args)
#   - mustache 占位 (e.g. {{steps.s1.output.x}}) 原样保留, 让 agent 自己读 — 不渲染
#
# 不写 runs.jsonl: 动态版的"运行过程"在主对话流里, 没单独的 run 概念.

def render_plan_markdown(
    definition: WorkflowDefinition, inputs: dict[str, Any] | None,
) -> str:
    """把 workflow definition + inputs 渲染成给主 agent 的 plan markdown."""
    inputs = inputs or {}
    parts: list[str] = []
    parts.append(f"Workflow `{definition.name}` 已加载, 请按以下 plan 执行:")
    parts.append("")
    parts.append(f"**Description**: {definition.description}")
    parts.append("")

    # Inputs
    if inputs:
        parts.append("## Inputs")
        for k, v in inputs.items():
            parts.append(f"- **{k}**: {_fmt_value_for_plan(v)}")
        parts.append("")

    # Steps
    parts.append("## Steps")
    for i, step in enumerate(definition.steps, start=1):
        parts.append(f"### {i}. `{step.id}` ({step.kind})")
        if step.kind == "invoke_app":
            parts.append(
                f"调 `invoke_app(name='{step.app_name}', "  # type: ignore[union-attr]
                f"invocation_id='{step.invocation_id}', args=...)`"  # type: ignore[union-attr]
            )
            if step.args:  # type: ignore[union-attr]
                parts.append("建议 args:")
                parts.append("```json")
                parts.append(json.dumps(step.args, ensure_ascii=False, indent=2))  # type: ignore[union-attr]
                parts.append("```")
            parts.append(
                "*args 里的 `{{inputs.x}}` / `{{steps.x.output.y}}` 占位你自己读上下文填. "
                "拿到 output 后下一步用.*"
            )
        elif step.kind == "call_llm":
            parts.append(
                "**这步主 agent 自己处理** — 不需要起子 LLM, 你就是 LLM. 直接按下面的 "
                "prompt 思考 / 回答 / 写出来即可:"
            )
            if step.system:  # type: ignore[union-attr]
                parts.append("")
                parts.append("**System hint**:")
                parts.append("```")
                parts.append(str(step.system))  # type: ignore[union-attr]
                parts.append("```")
            parts.append("")
            parts.append("**Prompt** (含 `{{...}}` 占位):")
            parts.append("```")
            parts.append(str(step.prompt))  # type: ignore[union-attr]
            parts.append("```")
            if step.output_format == "json":  # type: ignore[union-attr]
                parts.append("*output 期望是合法 JSON*")
        else:  # set_var
            parts.append("记住以下常量, 后续步骤会引用:")
            parts.append("```json")
            parts.append(json.dumps(step.value, ensure_ascii=False, indent=2))  # type: ignore[union-attr]
            parts.append("```")
        parts.append("")

    # Output
    if definition.output_template is not None:
        parts.append("## Output (final return)")
        parts.append("跑完所有步骤后, 按以下结构整理结果返给用户:")
        parts.append("```json")
        parts.append(
            json.dumps(definition.output_template, ensure_ascii=False, indent=2)
        )
        parts.append("```")
        parts.append(
            "*占位 `{{...}}` 自己根据上下文填; 这是返给用户看的 final output.*"
        )
    else:
        parts.append("## Output")
        parts.append("没设 output_template, 把最后一步的产物当 final output 返给用户.")
    parts.append("")

    parts.append("---")
    parts.append(
        "**执行建议**: 用 TodoWrite 把每步建一个 todo 跟踪进度, 完成一步标 done 再做下一步. "
        "中间任何步骤想偏离 plan (e.g. 改个 arg / 跳过一步) 都可以, 你是 plan 的执行者不是奴仆."
    )

    return "\n".join(parts)


def _fmt_value_for_plan(v: Any, max_len: int = 200) -> str:
    """渲染输入值给 plan markdown — 短的原样, 长的 truncate."""
    if isinstance(v, str):
        if len(v) <= max_len:
            return f'"{v}"'
        return f'"{v[:max_len]}..." ({len(v)} chars)'
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
        if len(s) <= max_len:
            return f"`{s}`"
        return f"`{s[:max_len]}...` ({len(s)} chars)"
    return f"`{v}`"


async def render_dynamic_plan(
    settings: Settings, *, name: str, inputs: dict[str, Any] | None,
) -> str:
    """主入口: invoke_workflow_dynamic 工具调这个. 校 ready 状态 + 输入 + 渲染.

    跟 invoke_workflow 一样校 status=ready 跟 inputs_schema, 但不跑任何 step,
    只返 plan markdown.
    """
    meta = workflow_biz.read_meta(settings, name)
    if meta is None:
        raise WorkflowError(f"workflow {name!r} 不存在")
    if meta.status != "ready":
        hint = {
            "draft": "还在写, 用 weave_workflow_finalize 收口",
            "dirty": "改过没重新 finalize, 用 weave_workflow_finalize 重校",
            "failed": f"上次 finalize 失败 ({meta.last_finalize_error or 'unknown'}), 修完用 weave_workflow_finalize",
        }.get(meta.status, "status 不对劲")
        raise WorkflowError(
            f"workflow {name!r} status={meta.status!r}, 不能 invoke. {hint}"
        )

    inputs = inputs or {}
    definition = workflow_biz.read_workflow(settings, name)
    _validate_inputs(inputs, definition.inputs_schema)

    # 跟静态版共享 use_count — workflow 跑过就涨, 不分静态/动态
    workflow_biz.bump_use_count(settings, name)
    logger.info(f"render_dynamic_plan: {name} ({len(definition.steps)} steps)")

    return render_plan_markdown(definition, inputs)

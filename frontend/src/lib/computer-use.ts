import { truncate } from "./tool-meta";

export interface ComputerUseInfo {
  action: string;
  target: string;
  summary: string;
  detailLabel: string;
  detail: string;
}

export function parseComputerUseInput(input: Record<string, unknown>): ComputerUseInfo | null {
  const action = String(input.action ?? "").trim();
  if (!action) return null;

  const target = targetForAction(action, input);
  const summary = [displayActionFor(action), target].filter(Boolean).join(" ");
  return {
    action,
    target,
    summary: summary || action,
    detailLabel: action === "set_value" ? "Value" : "Details",
    detail: detailForAction(action, input),
  };
}

export function computerUseSummary(input: Record<string, unknown>, max = 56): string {
  const info = parseComputerUseInput(input);
  return info ? truncate(info.summary, max) : "";
}

function displayActionFor(action: string): string {
  switch (action) {
    case "permissions":
      return "check permissions";
    case "apps":
      return "list apps";
    case "snapshot":
      return "inspect app";
    case "set_value":
      return "set value";
    case "focus":
      return "focus";
    case "key":
      return "key";
    case "menu":
      return "menu";
    default:
      return action.replace(/_/g, " ");
  }
}

function targetForAction(action: string, input: Record<string, unknown>): string {
  switch (action) {
    case "snapshot":
    case "focus":
      return String(input.target ?? "").trim();
    case "menu": {
      const path = input.path;
      const menuPath = Array.isArray(path) ? path.map(String).join(" > ") : "";
      const target = String(input.target ?? "").trim();
      return [target, menuPath].filter(Boolean).join(" · ");
    }
    case "press":
      return input.index == null ? "" : `#${String(input.index)}`;
    case "set_value":
      return input.index == null ? "" : `#${String(input.index)}`;
    case "key":
      return String(input.combo ?? "").trim();
    case "permissions":
      return input.prompt ? "request" : "";
    default:
      return "";
  }
}

function detailForAction(action: string, input: Record<string, unknown>): string {
  if (action === "set_value") return String(input.value ?? "").trim();

  const details: string[] = [];
  for (const key of ["target", "snapshot_id", "index", "combo", "depth", "max_children", "prompt"]) {
    const value = input[key];
    if (value === undefined || value === null || value === "") continue;
    details.push(`${key}: ${String(value)}`);
  }
  const path = input.path;
  if (Array.isArray(path) && path.length > 0) details.push(`path: ${path.map(String).join(" > ")}`);
  return details.join("\n");
}

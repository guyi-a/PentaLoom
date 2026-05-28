import { truncate } from "./tool-meta";

export interface BrowserBridgeInfo {
  action: string;
  target: string;
  summary: string;
  detailLabel: string;
  detail: string;
}

export function parseBrowserBridgeInput(input: Record<string, unknown>): BrowserBridgeInfo | null {
  const action = String(input.action ?? "").trim();
  if (!action) return null;

  const target = targetForAction(action, input);
  const displayAction = displayActionFor(action);
  const summary = [displayAction, target].filter(Boolean).join(" ");
  const detail = detailForAction(action, input);
  return {
    action,
    target,
    summary: summary || action,
    detailLabel: action === "execute_script" ? "Page Script" : "Details",
    detail,
  };
}

export function browserBridgeSummary(input: Record<string, unknown>, max = 56): string {
  const info = parseBrowserBridgeInput(input);
  return info ? truncate(info.summary, max) : "";
}

function displayActionFor(action: string): string {
  switch (action) {
    case "extension_status":
      return "check extension";
    case "list_sessions":
      return "list browsers";
    case "list_pages":
      return "list pages";
    case "list_windows":
      return "list windows";
    case "open_tab":
      return "open";
    case "focus_page":
      return "focus page";
    case "close_tab":
      return "close tab";
    case "go_back":
      return "go back";
    case "read_state":
      return "inspect page";
    case "wait_for":
      return "wait";
    case "describe_element":
      return "describe element";
    case "dropdown_options":
      return "dropdown options";
    case "select_dropdown":
      return "select dropdown";
    case "execute_script":
      return "page automation";
    default:
      return action.replace(/_/g, " ");
  }
}

function targetForAction(action: string, input: Record<string, unknown>): string {
  const index = input.index == null ? "" : `#${String(input.index)}`;
  switch (action) {
    case "open_tab":
      return String(input.url ?? "").trim();
    case "click":
    case "hover":
    case "dblclick":
    case "rightclick":
    case "extract":
    case "describe_element":
    case "dropdown_options":
      return index;
    case "type":
      return [index, String(input.text ?? "").trim()].filter(Boolean).join(" ");
    case "select_dropdown":
      return [index, String(input.text ?? "").trim()].filter(Boolean).join(" ");
    case "press":
      return String(input.key ?? "").trim();
    case "scroll": {
      const x = input.x == null ? 0 : Number(input.x);
      const y = input.y == null ? 0 : Number(input.y);
      return index ? `${index} ${x},${y}` : `${x},${y}`;
    }
    case "wait_for":
      return input.timeout_ms == null ? "" : `${String(input.timeout_ms)}ms`;
    default:
      return "";
  }
}

function detailForAction(action: string, input: Record<string, unknown>): string {
  if (action === "execute_script") return String(input.script ?? "").trim();

  const details: string[] = [];
  for (const key of ["browser_id", "page_id", "url", "index", "text", "key", "timeout_ms", "x", "y"]) {
    const value = input[key];
    if (value === undefined || value === null || value === "") continue;
    details.push(`${key}: ${String(value)}`);
  }
  return details.join("\n");
}

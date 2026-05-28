// browser-use command parsing helpers for UI display.
// Mirrors the backend's lightweight CLI parsing closely enough for summaries.

const VALUED_GLOBAL_FLAGS = new Set([
  "--profile",
  "--session",
  "--cdp-url",
  "--browser",
  "-p",
  "-s",
  "-b",
]);

const NO_VALUE_GLOBAL_FLAGS = new Set(["--headed", "--connect"]);

export interface BrowserCommandInfo {
  raw: string;
  globalArgs: string[];
  action: string;
  args: string[];
  target: string;
  summary: string;
}

export function shellSplit(input: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: '"' | "'" | null = null;
  let escaping = false;

  for (const ch of input) {
    if (escaping) {
      current += ch;
      escaping = false;
      continue;
    }
    if (ch === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (ch === quote) quote = null;
      else current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }
    current += ch;
  }

  if (escaping) current += "\\";
  if (current) tokens.push(current);
  return tokens;
}

export function parseBrowserCommand(command: unknown): BrowserCommandInfo | null {
  const raw = String(command ?? "").trim();
  if (!raw) return null;

  const parts = shellSplit(raw);
  if (parts.length === 0) return null;

  const globalArgs: string[] = [];
  let actionIndex = -1;
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (!part.startsWith("-")) {
      actionIndex = i;
      break;
    }
    globalArgs.push(part);
    if (VALUED_GLOBAL_FLAGS.has(part) && i + 1 < parts.length) {
      i += 1;
      globalArgs.push(parts[i]);
    } else if (!VALUED_GLOBAL_FLAGS.has(part) && !NO_VALUE_GLOBAL_FLAGS.has(part)) {
      // Unknown global-looking flag: treat it as valueless, matching the backend's
      // "first non-flag token is action" heuristic.
    }
  }

  if (actionIndex === -1) {
    return {
      raw,
      globalArgs,
      action: "",
      args: [],
      target: "",
      summary: raw,
    };
  }

  const action = parts[actionIndex];
  const args = parts.slice(actionIndex + 1);
  const target = targetForAction(action, args);
  const summary = [action, target].filter(Boolean).join(" ") || raw;
  return { raw, globalArgs, action, args, target, summary };
}

export function browserCommandSummary(command: unknown, max = 100): string {
  const parsed = parseBrowserCommand(command);
  if (!parsed) return "";
  return truncate(compactSummary(parsed), max);
}

function compactSummary(parsed: BrowserCommandInfo): string {
  switch (parsed.action) {
    case "eval":
      return "page automation";
    case "state":
    case "close":
    case "back":
    case "close-tab":
      return parsed.action;
    case "screenshot":
      return parsed.target ? `screenshot ${parsed.target}` : "screenshot";
    default:
      return parsed.summary;
  }
}

function targetForAction(action: string, args: string[]): string {
  if (args.length === 0) return "";
  switch (action) {
    case "open":
    case "click":
    case "dblclick":
    case "rightclick":
    case "hover":
    case "switch":
    case "scroll":
    case "wait":
    case "select":
    case "upload":
    case "get":
    case "extract":
      return args.join(" ");
    case "type":
    case "input":
      return args.length >= 2 ? `${args[0]} ${args.slice(1).join(" ")}` : args.join(" ");
    case "keys":
    case "eval":
      return args.join(" ");
    case "screenshot":
      return screenshotTarget(args);
    case "cookies":
      return args.join(" ");
    default:
      return args.join(" ");
  }
}

function screenshotTarget(args: string[]): string {
  const withoutFull = args.filter((arg) => arg !== "--full");
  if (withoutFull.length === 0) return args.includes("--full") ? "--full" : "";
  return withoutFull.join(" ");
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max - 1) + "…";
}

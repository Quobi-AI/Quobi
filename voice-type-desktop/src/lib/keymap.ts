// Map a browser KeyboardEvent.code (physical, layout-independent) to the
// daemon's evdev key name (must match input.py's _EVDEV_KEY_ALIASES).
// Returns null for keys the daemon can't bind.

const CODE_TO_NAME: Record<string, string> = {
  Backquote: "grave",
  ControlRight: "ctrl_r",
  ControlLeft: "ctrl_l",
  AltRight: "alt_r",
  AltLeft: "alt_l",
  ShiftRight: "shift_r",
  ShiftLeft: "shift_l",
  MetaRight: "cmd_r",
  MetaLeft: "cmd_l",
  ContextMenu: "menu",
  Pause: "pause",
  ScrollLock: "scroll_lock",
  CapsLock: "caps_lock",
  Escape: "esc",
  Tab: "tab",
  Space: "space",
  Enter: "enter",
  Backspace: "backspace",
  Insert: "insert",
  Delete: "delete",
  Home: "home",
  End: "end",
  PageUp: "page_up",
  PageDown: "page_down",
  Minus: "minus",
  Equal: "equals",
  BracketLeft: "bracket_left",
  BracketRight: "bracket_right",
  Backslash: "backslash",
  Semicolon: "semicolon",
  Quote: "apostrophe",
  Comma: "comma",
  Period: "period",
  Slash: "slash",
};
// F1..F24
for (let i = 1; i <= 24; i++) CODE_TO_NAME[`F${i}`] = `f${i}`;

// Friendly labels for display, keyed by daemon name.
const NAME_TO_LABEL: Record<string, string> = {
  grave: "` Backtick",
  ctrl_r: "Right Ctrl",
  ctrl_l: "Left Ctrl",
  alt_r: "Right Alt",
  alt_l: "Left Alt",
  shift_r: "Right Shift",
  shift_l: "Left Shift",
  cmd_r: "Right Super",
  cmd_l: "Left Super",
  menu: "Menu",
  pause: "Pause",
  scroll_lock: "Scroll Lock",
  caps_lock: "Caps Lock",
  esc: "Esc",
  minus: "-",
  equals: "=",
  bracket_left: "[",
  bracket_right: "]",
  backslash: "\\",
  semicolon: ";",
  apostrophe: "'",
  comma: ",",
  period: ".",
  slash: "/",
};

export function codeToDaemonKey(code: string): string | null {
  return CODE_TO_NAME[code] ?? null;
}

export function keyLabel(name: string): string {
  if (NAME_TO_LABEL[name]) return NAME_TO_LABEL[name];
  if (/^f\d+$/.test(name)) return name.toUpperCase();
  return name;
}

// Keys that, used as a hotkey, would be disruptive (letters, digits, common
// modifiers that fire constantly). We allow them but warn.
export function isRiskyHotkey(name: string): boolean {
  return ["space", "enter", "tab", "backspace", "esc"].includes(name);
}

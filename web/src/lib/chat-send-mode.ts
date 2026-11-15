"use client";

import * as React from "react";

import { getLocalStorageValue, setLocalStorageValue } from "@/lib/local-storage.client";

// Composer send/newline shortcuts are client-only preferences in localStorage, not database/account synced,
// so a different browser needs its own setting. See issue #39: 0.3.2 changed Ctrl+Enter to Enter for sending;
// offer the old shortcut as an option.
export type ChatSendMode = "enter" | "ctrl-enter";

export const CHAT_SEND_MODE_KEY = "artex_chat_send_mode";
export const DEFAULT_CHAT_SEND_MODE: ChatSendMode = "enter";

export const CHAT_SEND_MODE_OPTIONS: { value: ChatSendMode; label: string }[] = [
  { value: "enter", label: "Enter sends, Shift+Enter adds a newline" },
  { value: "ctrl-enter", label: "Ctrl+Enter sends, Enter adds a newline" },
];

function parseMode(raw: string | null): ChatSendMode {
  return raw === "ctrl-enter" || raw === "enter" ? raw : DEFAULT_CHAT_SEND_MODE;
}

// Subscribers in this tab: storage events fire only in other tabs,
// so emit informs local composers of setting changes without requiring refresh.
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

// String snapshots compare by value with Object.is, avoiding useSyncExternalStore loops.
function getSnapshot(): ChatSendMode {
  return parseMode(getLocalStorageValue(CHAT_SEND_MODE_KEY));
}

// Render defaults server-side without localStorage; getSnapshot corrects after hydration.
function getServerSnapshot(): ChatSendMode {
  return DEFAULT_CHAT_SEND_MODE;
}

export function useChatSendMode(): ChatSendMode {
  return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function setChatSendMode(mode: ChatSendMode) {
  setLocalStorageValue(CHAT_SEND_MODE_KEY, mode);
  for (const listener of listeners) listener();
}

// Determine whether a keypress should submit.
// isComposing/keyCode 229 indicates IME composition; do not submit when Enter confirms a candidate.
// Enter mode excludes only Shift, matching 0.3.2 exactly for unchanged preferences.
// Ctrl-Enter mode accepts Ctrl and Cmd on macOS.
export function shouldSubmitOnKey(e: React.KeyboardEvent, mode: ChatSendMode): boolean {
  if (e.key !== "Enter") return false;
  if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return false;
  if (mode === "ctrl-enter") return e.ctrlKey || e.metaKey;
  return !e.shiftKey;
}

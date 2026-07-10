export interface ChatUser {
  id: number;
  platform: string;
  username: string;
  role: "director" | "admin" | "banned";
  added_at: string;
}

export interface SettingsDto {
  llm_provider: "gemini" | "openai";
  gemini_api_key: string;
  gemini_model: string;
  openai_base_url: string;
  openai_api_key: string;
  openai_model: string;
  twitch_channel: string;
  text_enabled: boolean;
  voice_enabled: boolean;
  chat_commands_enabled: boolean;
  commands_open_to_all: boolean;
  global_cooldown_s: number;
  debounce_s: number;
  debounce_max_s: number;
  user_cooldown_s: number;
  tts_max_age_s: number;
  default_voice: string;
  voice_by_priority: Record<string, string>;
  voice_overrides: Record<string, string>;
}

export interface StatusDto {
  wotstat?: {
    status: "connected" | "waiting";
    game_state?: string;
    events_found?: number;
  };
  lol?: {
    status: "connected" | "waiting";
    events_found?: number;
  };
  active_game?: string;
  chat?: string;
  tts?: boolean;
  tts_status?: string;
  llm_configured?: boolean;
  llm_last_error?: string | null;
  llm_provider?: string;
  director?: {
    queue_len: number;
    replicas_last_minute: number;
  };
  memory?: string[];
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  getSettings: () => req<SettingsDto>("/api/settings"),
  putSettings: (patch: Partial<SettingsDto>) =>
    req<SettingsDto>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  listUsers: () => req<ChatUser[]>("/api/users"),
  addUser: (username: string, role: string) =>
    req("/api/users", { method: "POST", body: JSON.stringify({ username, role }) }),
  deleteUser: (platform: string, username: string) =>
    req(`/api/users/${platform}/${encodeURIComponent(username)}`, { method: "DELETE" }),
  getStatus: () => req<StatusDto>("/api/status"),
  testLlm: () =>
    req<{ ok: boolean; reply: string | null; error: string | null }>("/api/llm/test", {
      method: "POST",
    }),
  getVoices: () => req<{ voices: string[] }>("/api/voices"),
  previewVoice: (voice: string) =>
    fetch("/api/tts/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.blob();
    }),
};

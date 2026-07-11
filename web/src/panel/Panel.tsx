import { useCallback, useEffect, useState } from "react";
import { api, ChatUser, SettingsDto, StatusDto } from "../shared/api";

function Badge({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return (
    <span className={`badge ${ok ? "badge-ok" : "badge-bad"}`} title={detail}>
      {label}
    </span>
  );
}

export function Panel() {
  const [settings, setSettings] = useState<SettingsDto | null>(null);
  const [users, setUsers] = useState<ChatUser[]>([]);
  const [status, setStatus] = useState<StatusDto>({});
  const [apiKey, setApiKey] = useState("");
  const [newUser, setNewUser] = useState("");
  const [newRole, setNewRole] = useState<"director" | "admin" | "banned">("director");
  const [message, setMessage] = useState("");
  const [voices, setVoices] = useState<string[]>([]);
  const [newOverrideType, setNewOverrideType] = useState("");

  const refreshUsers = useCallback(() => {
    api.listUsers().then(setUsers).catch(() => {});
  }, []);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setMessage("Сервер недоступен"));
    refreshUsers();
    api.getVoices().then((v) => setVoices(v.voices)).catch(() => {});
    const t = setInterval(() => api.getStatus().then(setStatus).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [refreshUsers]);

  if (!settings) return <div className="panel">Загрузка…</div>;

  const LLM_FIELDS = [
    "llm_provider", "gemini_api_key", "gemini_model",
    "openai_base_url", "openai_api_key", "openai_model",
  ];

  const patch = async (p: Partial<SettingsDto>) => {
    try {
      setSettings(await api.putSettings(p));
      // Смена модели/провайдера/ключа — сразу проверяем LLM живым запросом.
      if (Object.keys(p).some((k) => LLM_FIELDS.includes(k))) {
        setMessage("Проверяю LLM…");
        const t = await api.testLlm();
        setMessage(t.ok ? `LLM отвечает: «${t.reply}»` : `LLM не отвечает: ${t.error}`);
        api.getStatus().then(setStatus).catch(() => {});
        setTimeout(() => setMessage(""), 5000);
      } else {
        setMessage("Сохранено");
        setTimeout(() => setMessage(""), 1500);
      }
    } catch (e) {
      setMessage(String(e));
    }
  };

  const preview = async (voice: string) => {
    try {
      const blob = await api.previewVoice(voice);
      const audio = new Audio(URL.createObjectURL(blob));
      audio.play().catch(() => {});
    } catch (e) {
      setMessage(String(e));
    }
  };

  const addUser = async () => {
    if (!newUser.trim()) return;
    try {
      await api.addUser(newUser.trim(), newRole);
      setNewUser("");
      refreshUsers();
    } catch (e) {
      setMessage(String(e));
    }
  };

  return (
    <div className="panel">
      <h1>
        Stream Director
        <span className="badges">
          <Badge
            label={status.active_game === "wot" ? "WoT ●" : "WoT"}
            ok={status.wotstat?.status === "connected"}
            detail={status.wotstat ? `${status.wotstat.status} (${status.wotstat.game_state ?? "?"})` : undefined}
          />
          <Badge
            label={status.active_game === "lol" ? "LoL ●" : "LoL"}
            ok={status.lol?.status === "connected"}
            detail={status.lol?.status ?? "waiting"}
          />
          <Badge label="чат" ok={status.chat === "connected"} detail={status.chat} />
          <Badge
            label={status.llm_provider ? `LLM: ${status.llm_provider}` : "LLM"}
            ok={!!status.llm_configured && !status.llm_last_error}
            detail={status.llm_last_error ?? (status.llm_configured ? "ok" : "не настроен")}
          />
          <Badge label="голос" ok={!!status.tts} detail={status.tts_status} />
        </span>
      </h1>
      {message && <div className="message">{message}</div>}
      {status.update_available && (
        <div className="message">
          Доступна версия {status.update_available.version} —{" "}
          <a href={status.update_available.url} target="_blank" rel="noreferrer">
            скачать на GitHub
          </a>
        </div>
      )}

      <section>
        <h2>Подключения</h2>
        <label className="check">
          LLM-провайдер
          <select
            value={settings.llm_provider}
            onChange={(e) => patch({ llm_provider: e.target.value as SettingsDto["llm_provider"] })}
          >
            <option value="gemini">Gemini</option>
            <option value="openai">OpenAI-совместимый</option>
          </select>
        </label>
        {settings.llm_provider === "gemini" && (
          <>
            <label>
              API-ключ Gemini
              <div className="row">
                <input
                  type="password"
                  value={apiKey}
                  placeholder={settings.gemini_api_key ? "сохранён" : "AIza…"}
                  onChange={(e) => setApiKey(e.target.value)}
                />
                <button onClick={() => apiKey && patch({ gemini_api_key: apiKey })}>Сохранить</button>
              </div>
            </label>
            <label>
              Модель Gemini
              <input
                value={settings.gemini_model}
                onChange={(e) => setSettings({ ...settings, gemini_model: e.target.value })}
                onBlur={(e) => patch({ gemini_model: e.target.value.trim() })}
              />
            </label>
          </>
        )}
        {settings.llm_provider === "openai" && (
          <>
            <label>
              Base URL (Например: Ollama Cloud: https://ollama.com/v1)
              <input
                value={settings.openai_base_url}
                onChange={(e) => setSettings({ ...settings, openai_base_url: e.target.value })}
                onBlur={(e) => patch({ openai_base_url: e.target.value.trim() })}
              />
            </label>
            <label>
              API-ключ (Ollama Cloud — ключ с ollama.com; локальному Ollama не нужен)
              <div className="row">
                <input
                  type="password"
                  value={apiKey}
                  placeholder={settings.openai_api_key ? "сохранён" : "ключ провайдера"}
                  onChange={(e) => setApiKey(e.target.value)}
                />
                <button onClick={() => apiKey && patch({ openai_api_key: apiKey })}>Сохранить</button>
              </div>
            </label>
            <label>
              Модель (Ollama: gpt-oss:20b, qwen3:8b и т.п.)
              <input
                value={settings.openai_model}
                onChange={(e) => setSettings({ ...settings, openai_model: e.target.value })}
                onBlur={(e) => patch({ openai_model: e.target.value.trim() })}
              />
            </label>
          </>
        )}
        <label>
          Канал Twitch (применяется сразу после сохранения)
          <input
            value={settings.twitch_channel}
            onChange={(e) => setSettings({ ...settings, twitch_channel: e.target.value })}
            onBlur={(e) => patch({ twitch_channel: e.target.value.trim() })}
          />
        </label>
      </section>

      <section>
        <h2>Режиссёр</h2>
        <div className="row wrap">
          <label className="check">
            <input
              type="checkbox"
              checked={settings.text_enabled}
              onChange={(e) => patch({ text_enabled: e.target.checked })}
            />
            Текст
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.voice_enabled}
              onChange={(e) => patch({ voice_enabled: e.target.checked })}
            />
            Голос
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.chat_commands_enabled}
              onChange={(e) => patch({ chat_commands_enabled: e.target.checked })}
            />
            Чат-команды
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.commands_open_to_all}
              onChange={(e) => patch({ commands_open_to_all: e.target.checked })}
            />
            Команды всем (кроме забаненных)
          </label>
        </div>
        <label className="check">
          Шаблоны реплик (LoL)
          <select
            value={settings.template_mode}
            onChange={(e) =>
              patch({ template_mode: e.target.value as SettingsDto["template_mode"] })
            }
          >
            <option value="seed">затравка для LLM (уникальные реплики)</option>
            <option value="verbatim">сначала дословно, потом LLM</option>
            <option value="off">только при сбое LLM</option>
          </select>
        </label>
        <label>
          Не озвучивать реплики старше, секунд (текст всё равно покажем)
          <input
            type="number"
            min={0}
            step={1}
            value={settings.tts_max_age_s}
            onChange={(e) => setSettings({ ...settings, tts_max_age_s: Number(e.target.value) })}
            onBlur={(e) => patch({ tts_max_age_s: Number(e.target.value) })}
          />
        </label>
        {status.director && (
          <p className="hint">
            очередь: {status.director.queue_len}, реплик за минуту: {status.director.replicas_last_minute}
          </p>
        )}
      </section>

      <section>
        <h2>Голос</h2>
        <label>
          Голос по умолчанию
          <div className="row">
            <select
              value={settings.default_voice}
              onChange={(e) => patch({ default_voice: e.target.value })}
            >
              {voices.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
            <button onClick={() => preview(settings.default_voice)}>прослушать</button>
          </div>
        </label>

        <p className="hint">Голос по важности события (пусто — как по умолчанию):</p>
        {(["low", "normal", "high", "critical"] as const).map((p) => (
          <label key={p} className="check">
            {p}
            <div className="row">
              <select
                value={settings.voice_by_priority[p] ?? ""}
                onChange={(e) => {
                  const next = { ...settings.voice_by_priority };
                  if (e.target.value) next[p] = e.target.value;
                  else delete next[p];
                  patch({ voice_by_priority: next });
                }}
              >
                <option value="">— по умолчанию —</option>
                {voices.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
              {settings.voice_by_priority[p] && (
                <button onClick={() => preview(settings.voice_by_priority[p])}>прослушать</button>
              )}
            </div>
          </label>
        ))}

        <p className="hint">
          Точечно по типу события (напр. death, frag, multikill) — важнее правила по важности:
        </p>
        {Object.entries(settings.voice_overrides).map(([type, voice]) => (
          <div className="row" key={type}>
            <input value={type} readOnly />
            <select
              value={voice}
              onChange={(e) => {
                const next = { ...settings.voice_overrides, [type]: e.target.value };
                patch({ voice_overrides: next });
              }}
            >
              {voices.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
            <button onClick={() => preview(voice)}>прослушать</button>
            <button
              className="danger"
              onClick={() => {
                const next = { ...settings.voice_overrides };
                delete next[type];
                patch({ voice_overrides: next });
              }}
            >
              удалить
            </button>
          </div>
        ))}
        <div className="row">
          <input
            placeholder="тип события (напр. death)"
            value={newOverrideType}
            onChange={(e) => setNewOverrideType(e.target.value)}
          />
          <button
            onClick={() => {
              const t = newOverrideType.trim();
              if (!t || !voices.length) return;
              patch({ voice_overrides: { ...settings.voice_overrides, [t]: voices[0] } });
              setNewOverrideType("");
            }}
          >
            добавить оверрайд
          </button>
        </div>
      </section>

      <section>
        <h2>Белый список чата</h2>
        <div className="row">
          <input
            placeholder="ник"
            value={newUser}
            onChange={(e) => setNewUser(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addUser()}
          />
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value as "director" | "admin" | "banned")}
          >
            <option value="director">director</option>
            <option value="admin">admin</option>
            <option value="banned">banned</option>
          </select>
          <button onClick={addUser}>Добавить</button>
        </div>
        <table>
          <thead>
            <tr><th>Ник</th><th>Роль</th><th>Добавлен</th><th /></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.role}</td>
                <td>{u.added_at.slice(0, 10)}</td>
                <td>
                  <button
                    className="danger"
                    onClick={() => api.deleteUser(u.platform, u.username).then(refreshUsers)}
                  >
                    удалить
                  </button>
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={4} className="hint">
                  {settings.commands_open_to_all
                    ? "пусто — команды доступны всем зрителям"
                    : "пусто — команды чата никому не доступны"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <p className="hint">
          Единственная команда: !dir &lt;текст&gt; — заказ реплики режиссёру.
          {settings.commands_open_to_all
            ? " Открытый режим включён: доступна всем, кроме роли banned."
            : " Доступна только пользователям из белого списка (роль director/admin)."}
          {" "}Роль banned запрещает команды всегда.
        </p>
      </section>

      <section>
        <h2>Память сессии</h2>
        <ul className="memory">
          {(status.memory ?? []).map((line, i) => <li key={i}>{line}</li>)}
          {(status.memory ?? []).length === 0 && <li className="hint">пока пусто</li>}
        </ul>
      </section>
    </div>
  );
}

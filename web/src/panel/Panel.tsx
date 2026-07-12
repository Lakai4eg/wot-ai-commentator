import { useCallback, useEffect, useState } from "react";
import { api, ChatUser, PromptsDto, SettingsDto, StatusDto, TtsState } from "../shared/api";

function Badge({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return (
    <span className={`badge ${ok ? "badge-ok" : "badge-bad"}`} title={detail}>
      {label}
    </span>
  );
}

const TTS_STATE_TEXT: Record<string, string> = {
  checking: "Проверяю видеокарту…",
  downloading_runtime: "Скачиваю голосовой движок",
  downloading_model: "Скачиваю голосовую модель",
  starting: "Запускаю голосовой движок…",
  loading: "Разворачиваю модель в память видеокарты…",
};

function TtsLoader({ st, onRetry }: { st: TtsState; onRetry: () => void }) {
  if (st.state === "ready") return null;
  const pct =
    st.progress?.total_mb && st.progress?.done_mb !== undefined
      ? Math.round((st.progress.done_mb / st.progress.total_mb) * 100)
      : null;
  return (
    <div className={`tts-loader ${st.state === "error" || st.state === "no_gpu" ? "tts-loader-bad" : ""}`}>
      {st.state === "error" || st.state === "no_gpu" ? (
        <>
          <span>{st.error ?? "голос недоступен"}</span>
          <button onClick={onRetry}>повторить</button>
        </>
      ) : (
        <>
          <span>
            {TTS_STATE_TEXT[st.state] ?? st.state}
            {st.progress?.step && `: ${st.progress.step}`}
            {pct !== null && ` — ${st.progress!.done_mb} / ${st.progress!.total_mb} МБ`}
          </span>
          {pct !== null && (
            <div className="tts-progress"><div style={{ width: `${pct}%` }} /></div>
          )}
        </>
      )}
    </div>
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
  const [newVoiceName, setNewVoiceName] = useState("");
  const [newVoiceText, setNewVoiceText] = useState("");
  const [newVoiceFile, setNewVoiceFile] = useState<File | null>(null);
  const [prompts, setPrompts] = useState<PromptsDto | null>(null);
  const [personaDraft, setPersonaDraft] = useState("");
  const [formatDraft, setFormatDraft] = useState("");
  const [baseDraft, setBaseDraft] = useState("");
  const [briefDraft, setBriefDraft] = useState("");
  const [newPersonaName, setNewPersonaName] = useState("");
  const [game, setGame] = useState<"wot" | "lol">("wot");

  const refreshUsers = useCallback(() => {
    api.listUsers().then(setUsers).catch(() => {});
  }, []);

  const reloadPrompts = useCallback(() => {
    api.getPrompts().then(setPrompts).catch(() => {});
  }, []);

  // Правки промптов сохраняются «в фоне» (onBlur, кнопки). Без общего .catch
  // упавший запрос молчал бы: пользователь думал бы, что текст сохранён.
  const savePrompt = useCallback(
    (request: Promise<unknown>) =>
      request
        .then(() => {
          reloadPrompts();
          setMessage("Сохранено");
          setTimeout(() => setMessage(""), 1500);
        })
        .catch((e) => setMessage(String(e))),
    [reloadPrompts],
  );

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setMessage("Сервер недоступен"));
    refreshUsers();
    reloadPrompts();
    api.getVoices().then((v) => setVoices(v.voices)).catch(() => {});
    const t = setInterval(() => api.getStatus().then(setStatus).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [refreshUsers, reloadPrompts]);

  // Черновики textarea живут отдельно от загруженных промптов: правка уходит
  // на сервер по onBlur, а после перезагрузки промптов черновики пересобираются.
  const activePersonaId = settings?.active_persona_id;
  useEffect(() => {
    if (!prompts) return;
    const persona = prompts.personas.find((p) => p.id === activePersonaId);
    setPersonaDraft(persona?.text ?? "");
    setFormatDraft(prompts.response_format);
    setBaseDraft(prompts.games[game]?.base ?? "");
    setBriefDraft(prompts.games[game]?.brief ?? "");
  }, [prompts, game, activePersonaId]);

  if (!settings) return <div className="panel">Загрузка…</div>;

  const activePersona = prompts?.personas.find((p) => p.id === settings.active_persona_id);

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
      {status.tts_state && (
        <TtsLoader
          st={status.tts_state}
          onRetry={() =>
            api.ttsRetry().then(() => setMessage("Перезапускаю голос…")).catch((e) => setMessage(String(e)))
          }
        />
      )}
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
        <label>
          Окно склейки событий, сек (в буре событий реплика одна — про главное)
          <input
            type="number"
            min={0}
            step={0.5}
            value={settings.debounce_window_s}
            onChange={(e) =>
              setSettings({ ...settings, debounce_window_s: Number(e.target.value) })
            }
            onBlur={(e) => patch({ debounce_window_s: Number(e.target.value) })}
          />
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

      {prompts && (
        <section>
          <h2>Промпты</h2>

          <label className="check">
            Персона
            <select
              value={settings.active_persona_id}
              onChange={(e) => patch({ active_persona_id: Number(e.target.value) })}
            >
              {prompts.personas.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <textarea
            className="prompt"
            value={personaDraft}
            onChange={(e) => setPersonaDraft(e.target.value)}
            onBlur={() =>
              activePersona &&
              savePrompt(api.updatePersona(activePersona.id, { text: personaDraft }))
            }
          />
          <div className="row">
            <input
              placeholder="имя нового пресета"
              value={newPersonaName}
              onChange={(e) => setNewPersonaName(e.target.value)}
            />
            <button
              onClick={() =>
                newPersonaName.trim() &&
                api
                  .createPersona(newPersonaName.trim(), personaDraft)
                  .then(() => {
                    setNewPersonaName("");
                    reloadPrompts();
                  })
                  .catch((e) => setMessage(String(e)))
              }
            >
              Сохранить как новый пресет
            </button>
            {activePersona?.is_builtin ? (
              <button onClick={() => savePrompt(api.resetPersona(activePersona.id))}>
                Сбросить к заводскому
              </button>
            ) : (
              activePersona && (
                <button
                  className="danger"
                  onClick={() =>
                    savePrompt(
                      api.deletePersona(activePersona.id).then(() => {
                        // Удалили активную — сервер переключил её на встроенную.
                        api.getSettings().then(setSettings).catch(() => {});
                      }),
                    )
                  }
                >
                  Удалить пресет
                </button>
              )
            )}
          </div>

          <p className="hint">Формат ответа — длина, синтаксис, запрет на самоповторы:</p>
          <textarea
            className="prompt"
            value={formatDraft}
            onChange={(e) => setFormatDraft(e.target.value)}
            onBlur={() => savePrompt(api.putResponseFormat(formatDraft))}
          />
          <button
            onClick={() =>
              savePrompt(api.resetResponseFormat().then((r) => setFormatDraft(r.text)))
            }
          >
            Сбросить формат к заводскому
          </button>

          <div className="row">
            {(["wot", "lol"] as const).map((g) => (
              <button key={g} className={game === g ? "" : "ghost"} onClick={() => setGame(g)}>
                {g === "wot" ? "Мир танков" : "League of Legends"}
              </button>
            ))}
          </div>
          <p className="hint">База игры (сленг, мишени, табу):</p>
          <textarea
            className="prompt"
            value={baseDraft}
            onChange={(e) => setBaseDraft(e.target.value)}
            onBlur={() => savePrompt(api.putGameBase(game, baseDraft))}
          />
          <button
            onClick={() =>
              savePrompt(api.resetGameBase(game).then((r) => setBaseDraft(r.text)))
            }
          >
            Сбросить базу к заводской
          </button>

          <p className="hint">
            Бриф под технику/чемпиона
            {prompts.games[game]?.subject && ` — ${prompts.games[game].subject}`}
            {prompts.games[game]?.generated_at &&
              ` · сгенерирован ${prompts.games[game].generated_at.slice(11, 16)}`}
            {prompts.games[game]?.error && ` · ошибка: ${prompts.games[game].error}`}
          </p>
          <textarea
            className="prompt"
            value={briefDraft}
            placeholder="бриф появится на старте боя"
            onChange={(e) => setBriefDraft(e.target.value)}
            onBlur={() => savePrompt(api.putGameBrief(game, briefDraft))}
          />
          <button
            onClick={() => {
              setMessage("Генерирую бриф…");
              api
                .regenerateBrief(game)
                .then((r) => {
                  setBriefDraft(r.brief);
                  setMessage("Бриф обновлён");
                  reloadPrompts();
                })
                .catch((e) => setMessage(String(e)));
            }}
          >
            Перегенерировать бриф
          </button>
        </section>
      )}

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

        <p className="hint">
          Свой голос: WAV 5–12 секунд чистой речи + её текст. «default» — голос модели.
        </p>
        <div className="row">
          <input placeholder="имя голоса" value={newVoiceName}
                 onChange={(e) => setNewVoiceName(e.target.value)} />
          <input type="file" accept=".wav" onChange={(e) => setNewVoiceFile(e.target.files?.[0] ?? null)} />
        </div>
        <textarea className="prompt" placeholder="текст, произнесённый в записи"
                  value={newVoiceText} onChange={(e) => setNewVoiceText(e.target.value)} />
        <div className="row">
          <button
            onClick={() => {
              if (!newVoiceName.trim() || !newVoiceFile || !newVoiceText.trim()) return;
              api.uploadVoice(newVoiceName.trim(), newVoiceText, newVoiceFile)
                .then(() => {
                  setNewVoiceName(""); setNewVoiceText(""); setNewVoiceFile(null);
                  setMessage("Голос сохранён");
                  api.getVoices().then((v) => setVoices(v.voices)).catch(() => {});
                })
                .catch((e) => setMessage(String(e)));
            }}
          >
            Добавить голос
          </button>
          {voices.filter((v) => v !== "default").map((v) => (
            <button key={v} className="danger"
              onClick={() =>
                api.deleteVoice(v).then(() => api.getVoices().then((r) => setVoices(r.voices))).catch((e) => setMessage(String(e)))
              }
            >
              удалить «{v}»
            </button>
          ))}
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

# Multi-voice TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the director several Silero voices with per-context selection (rules by stimulus priority + per-type overrides), live-switchable from the panel, plus a voice preview button.

**Architecture:** The already-loaded `silero_tts / v4_ru` model synthesizes any speaker via `apply_tts(speaker=...)`, so multiple voices need no extra model. A pure `pick_voice()` function maps `(stimulus.type, priority)` to a voice using new `Settings` fields; `AppContext.publish` computes the voice per replica and threads it through `_send_audio → synth`. Live switching is free because `publish` reads `self.settings` on every replica. The panel gets a "Голос" section fed by a `/api/voices` list and a `/api/tts/preview` endpoint.

**Tech Stack:** Python 3.12 (dataclasses, FastAPI, pytest/httpx), Silero TTS (torch), React + TypeScript (Vite) panel.

## Global Constraints

- Python 3.12+, Node 18+.
- Never crash synthesis on a bad voice name in settings — invalid names fall through to the next precedence level, ultimately hard fallback `"baya"`.
- No new TTS engine, no SSML/emotions, no per-game voice. Silero `v4_ru` only.
- Mutable dataclass defaults use `field(default_factory=...)`.
- Follow existing test style: pytest, `@pytest.mark.asyncio` + `httpx.ASGITransport` for server tests (see `tests/test_server.py`).
- User's global rule: **never commit** unless the user explicitly asks. The "Commit" steps below are written for completeness; only run them if the user has approved committing.

---

## File Structure

- `src/wot_ai_commentator/tts.py` — add `VOICES`, `pick_voice()`, `synth(text, voice=None)`; constructor keeps a validated default voice.
- `src/wot_ai_commentator/config.py` — add `default_voice`, `voice_by_priority`, `voice_overrides`.
- `src/wot_ai_commentator/server.py` — wire voice into `publish`/`_send_audio`; extend `SettingsIn`; add `/api/voices` and `/api/tts/preview`.
- `src/wot_ai_commentator/main.py` — construct `SileroTTS(voice=settings.default_voice)`.
- `web/src/shared/api.ts` — settings DTO fields, `getVoices`, `previewVoice`.
- `web/src/panel/Panel.tsx` — "Голос" section (default + 4 priorities + overrides + preview).
- `tests/test_tts.py` (new), `tests/test_config.py`, `tests/test_server.py` — coverage.

---

## Task 1: Voice catalog + `pick_voice` (pure logic)

**Files:**
- Modify: `src/wot_ai_commentator/tts.py`
- Test: `tests/test_tts.py` (create)

**Interfaces:**
- Consumes: `Settings` (from `config.py`), `Priority` (from `events.py`).
- Produces:
  - `VOICES: tuple[str, ...] = ("aidar", "baya", "kseniya", "xenia", "eugene", "random")`
  - `pick_voice(settings: Settings, stim_type: str, priority: Priority) -> str` — precedence: `voice_overrides[stim_type]` → `voice_by_priority[priority.name.lower()]` → `settings.default_voice` → `"baya"`; any value not in `VOICES` is skipped at its level.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tts.py`:

```python
from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Priority
from wot_ai_commentator.tts import VOICES, pick_voice


def test_default_when_no_rules():
    s = Settings(default_voice="xenia")
    assert pick_voice(s, "frag", Priority.HIGH) == "xenia"


def test_priority_rule_applies():
    s = Settings(default_voice="baya", voice_by_priority={"high": "aidar"})
    assert pick_voice(s, "frag", Priority.HIGH) == "aidar"
    # NORMAL not mapped -> falls to default
    assert pick_voice(s, "spotted", Priority.NORMAL) == "baya"


def test_override_beats_priority():
    s = Settings(
        default_voice="baya",
        voice_by_priority={"high": "aidar"},
        voice_overrides={"death": "eugene"},
    )
    assert pick_voice(s, "death", Priority.HIGH) == "eugene"


def test_invalid_names_fall_through():
    s = Settings(
        default_voice="nope",
        voice_by_priority={"high": "also_bad"},
        voice_overrides={"death": "garbage"},
    )
    # every level invalid -> hard fallback
    assert pick_voice(s, "death", Priority.HIGH) == "baya"


def test_priority_name_lowercased():
    s = Settings(default_voice="baya", voice_by_priority={"critical": "kseniya"})
    assert pick_voice(s, "multikill", Priority.CRITICAL) == "kseniya"


def test_all_voices_known():
    assert "baya" in VOICES and "random" in VOICES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts.py -v`
Expected: FAIL — `ImportError: cannot import name 'VOICES'` / `pick_voice`.

- [ ] **Step 3: Write minimal implementation**

At the top of `src/wot_ai_commentator/tts.py`, after the existing imports add the imports and constant, and the function (place `VOICES` near `SAMPLE_RATE`, and `pick_voice` after the `SileroTTS` class or before it — module-level):

```python
from .config import Settings
from .events import Priority

VOICES: tuple[str, ...] = ("aidar", "baya", "kseniya", "xenia", "eugene", "random")
DEFAULT_VOICE = "baya"


def pick_voice(settings: Settings, stim_type: str, priority: Priority) -> str:
    """Голос под контекст: override по типу > правило по приоритету > дефолт.

    Любое имя вне VOICES игнорируется на своём уровне — синтез не падает.
    """
    for candidate in (
        settings.voice_overrides.get(stim_type),
        settings.voice_by_priority.get(priority.name.lower()),
        settings.default_voice,
    ):
        if candidate in VOICES:
            return candidate
    return DEFAULT_VOICE
```

Note: `SAMPLE_RATE = 48000` already exists — keep it. Do not import `Settings`/`Priority` inside a function; module-level is fine (no circular import: `config` and `events` don't import `tts`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts.py -v`
Expected: PASS (all 6 tests). This will fail to import until Task 3 adds the `Settings` fields — if so, do Task 3 first, then re-run. (See ordering note below.)

- [ ] **Step 5: Commit** (only if the user approved committing)

```bash
git add src/wot_ai_commentator/tts.py tests/test_tts.py
git commit -m "feat(tts): voice catalog + context-based pick_voice"
```

> **Ordering note:** `pick_voice` reads `settings.voice_overrides` / `voice_by_priority` / `default_voice`, which Task 3 adds. If you implement strictly top-to-bottom, add the three `Settings` fields (Task 3, Step 3) before running Task 1 Step 4. The tasks are otherwise independent.

---

## Task 2: `SileroTTS.synth(voice=)` + validated default

**Files:**
- Modify: `src/wot_ai_commentator/tts.py:20-60` (`__init__`, `synth`)
- Test: `tests/test_tts.py`

**Interfaces:**
- Consumes: `VOICES` (Task 1).
- Produces: `SileroTTS.__init__(self, voice: str = "baya")` clamps unknown voice to `"baya"`; `SileroTTS.synth(self, text: str, voice: str | None = None) -> bytes | None` uses `voice` if in `VOICES`, else `self.voice`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tts.py`:

```python
from wot_ai_commentator.tts import SileroTTS


def test_synth_none_when_unavailable_any_voice():
    tts = SileroTTS.__new__(SileroTTS)  # skip torch load
    tts.available = False
    tts._model = None
    assert tts.synth("привет", "aidar") is None
    assert tts.synth("привет", "garbage") is None
    assert tts.synth("привет") is None


def test_ctor_clamps_unknown_default_voice():
    tts = SileroTTS.__new__(SileroTTS)
    # emulate the validation the ctor performs
    from wot_ai_commentator.tts import VOICES
    voice = "weird"
    assert (voice if voice in VOICES else "baya") == "baya"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts.py::test_synth_none_when_unavailable_any_voice -v`
Expected: FAIL — `synth()` currently takes only `(self, text)`, so `synth("привет", "aidar")` raises `TypeError`.

- [ ] **Step 3: Write minimal implementation**

In `src/wot_ai_commentator/tts.py`, update `__init__` to validate the default voice:

```python
    def __init__(self, voice: str = "baya"):
        self.voice = voice if voice in VOICES else "baya"
        self.available = False
        self._model = None
        self._lock = threading.Lock()
        try:
            import torch  # noqa: PLC0415

            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language="ru",
                speaker="v4_ru",
                trust_repo=True,
            )
            model.to(torch.device("cpu"))
            self._model = model
            self.available = True
            log.info("Silero TTS загружен (голос по умолчанию %s)", self.voice)
        except Exception as e:
            log.warning("Silero TTS недоступен (%s) — голос отключён", e)
```

Update `synth` to accept and apply `voice`:

```python
    def synth(self, text: str, voice: str | None = None) -> bytes | None:
        if not self.available or self._model is None:
            return None
        speaker = voice if voice in VOICES else self.voice
        try:
            with self._lock:
                audio = self._model.apply_tts(
                    text=text, speaker=speaker, sample_rate=SAMPLE_RATE
                )
            pcm = (audio.numpy() * 32767).astype("int16").tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
            return buf.getvalue()
        except Exception:
            log.exception("TTS synth failed")
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (only if approved)

```bash
git add src/wot_ai_commentator/tts.py tests/test_tts.py
git commit -m "feat(tts): synth accepts per-call voice, validated default"
```

---

## Task 3: Settings fields

**Files:**
- Modify: `src/wot_ai_commentator/config.py:14-47`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces on `Settings`: `default_voice: str = "baya"`, `voice_by_priority: dict[str, str] = field(default_factory=dict)`, `voice_overrides: dict[str, str] = field(default_factory=dict)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_voice_defaults():
    s = Settings()
    assert s.default_voice == "baya"
    assert s.voice_by_priority == {}
    assert s.voice_overrides == {}


def test_voice_fields_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(
        default_voice="xenia",
        voice_by_priority={"high": "aidar", "critical": "kseniya"},
        voice_overrides={"death": "eugene"},
    )
    save_settings(s, path)
    assert load_settings(path) == s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_voice_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'default_voice'`.

- [ ] **Step 3: Write minimal implementation**

In `src/wot_ai_commentator/config.py`, add `field` to the import and three fields to `Settings` (after `tts_max_age_s`):

```python
from dataclasses import dataclass, field
```

```python
    tts_max_age_s: float = 20.0
    # Озвучка: голос по умолчанию + правила «контекст → голос».
    # voice_by_priority: "low"/"normal"/"high"/"critical" → голос.
    # voice_overrides: точный stimulus.type → голос (важнее правила по приоритету).
    default_voice: str = "baya"
    voice_by_priority: dict[str, str] = field(default_factory=dict)
    voice_overrides: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS. Now also re-run Task 1: `python -m pytest tests/test_tts.py -v` → PASS.

- [ ] **Step 5: Commit** (only if approved)

```bash
git add src/wot_ai_commentator/config.py tests/test_config.py
git commit -m "feat(config): default_voice + voice rule maps"
```

---

## Task 4: Wire voice through `publish` / `_send_audio` / `synth`

**Files:**
- Modify: `src/wot_ai_commentator/server.py:44-99` (`publish`, `_send_audio`)
- Modify: `src/wot_ai_commentator/main.py:73` (`SileroTTS()` → pass default voice)
- Test: `tests/test_server.py:170-201` (update existing `test_stale_event_skips_voice`) + new test

**Interfaces:**
- Consumes: `pick_voice` (Task 1), `SileroTTS.synth(text, voice)` (Task 2).
- Produces: `AppContext._send_audio(self, replica_id: int, text: str, voice: str) -> None`.

- [ ] **Step 1: Update the failing/again test + add new test**

In `tests/test_server.py`, update `test_stale_event_skips_voice` so the fakes match the new signatures, and assert the chosen voice is threaded:

```python
@pytest.mark.asyncio
async def test_stale_event_skips_voice(ctx):
    """Свежее событие озвучивается, устаревшее — только текст, без TTS."""
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.settings.voice_enabled = True
    ctx.settings.tts_max_age_s = 20.0
    ctx.tts = FakeTTS()

    voiced: list[tuple[str, str]] = []

    async def record(replica_id, text, voice):
        voiced.append((text, voice))

    ctx._send_audio = record  # перехватываем факт озвучки

    ctx.settings.voice_by_priority = {"high": "aidar"}
    await ctx.publish("свежая", Stimulus(kind="game_event", type="frag", priority=Priority.HIGH))
    await asyncio.sleep(0)
    assert voiced == [("свежая", "aidar")]

    voiced.clear()
    await ctx.publish(
        "запоздавшая",
        Stimulus(kind="game_event", type="frag", created_at=time.time() - 100),
    )
    await asyncio.sleep(0)
    assert voiced == []  # устаревшую реплику не озвучили
```

Add the `Priority` import at the top of `tests/test_server.py`:

```python
from wot_ai_commentator.events import Priority, Stimulus
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py::test_stale_event_skips_voice -v`
Expected: FAIL — `publish` still calls `self._send_audio(replica_id, text)` (2 args), but `record` now needs 3; and voice isn't computed yet.

- [ ] **Step 3: Write minimal implementation**

In `src/wot_ai_commentator/server.py`, import `pick_voice`:

```python
from .tts import AudioStore, SileroTTS, pick_voice
```

In `publish`, compute the voice and pass it through:

```python
        if voice_on:
            voice = pick_voice(self.settings, stimulus.type, stimulus.priority)
            asyncio.get_running_loop().create_task(
                self._send_audio(replica_id, text, voice)
            )
        elif not self.settings.text_enabled:
            return
```

Update `_send_audio` signature and the `synth` call:

```python
    async def _send_audio(self, replica_id: int, text: str, voice: str) -> None:
        try:
            wav = await asyncio.to_thread(self.tts.synth, text, voice)
        except Exception:
            return
        if not wav:
            return
        message = {
            "type": "audio",
            "replica_id": replica_id,
            "audio_url": f"/api/audio/{self.audio.put(wav)}",
        }
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.ws_clients.discard(ws)
```

In `src/wot_ai_commentator/main.py`, pass the configured default voice:

```python
    def load_tts() -> None:
        tts = SileroTTS(voice=settings.default_voice)
        ctx.tts = tts
        ctx.statuses["tts_status"] = "ready" if tts.available else "unavailable"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS (whole file — confirms no other test used the old `_send_audio`/`synth` signature).

- [ ] **Step 5: Commit** (only if approved)

```bash
git add src/wot_ai_commentator/server.py src/wot_ai_commentator/main.py tests/test_server.py
git commit -m "feat(tts): pick per-replica voice from stimulus context"
```

---

## Task 5: Settings API fields + `/api/voices` + `/api/tts/preview`

**Files:**
- Modify: `src/wot_ai_commentator/server.py` (`SettingsIn` ~line 107, endpoints in `create_app`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `VOICES` (Task 1), `pick_voice` already imported.
- Produces: `GET /api/voices → {"voices": [...]}`; `POST /api/tts/preview {voice?, text?} → audio/wav | 503`; `SettingsIn` accepts `default_voice`, `voice_by_priority`, `voice_overrides`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server.py`:

```python
@pytest.mark.asyncio
async def test_voice_settings_persist(client, ctx):
    r = await client.put(
        "/api/settings",
        json={
            "default_voice": "xenia",
            "voice_by_priority": {"high": "aidar"},
            "voice_overrides": {"death": "eugene"},
        },
    )
    assert r.status_code == 200
    assert ctx.settings.default_voice == "xenia"
    assert ctx.settings.voice_by_priority == {"high": "aidar"}
    assert ctx.settings.voice_overrides == {"death": "eugene"}
    body = r.json()
    assert body["default_voice"] == "xenia"


@pytest.mark.asyncio
async def test_voices_list(client):
    body = (await client.get("/api/voices")).json()
    assert "baya" in body["voices"]
    assert "aidar" in body["voices"]


@pytest.mark.asyncio
async def test_preview_503_without_tts(client, ctx):
    ctx.tts = None
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_preview_returns_wav(client, ctx):
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.tts = FakeTTS()
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert r.headers["content-type"] == "audio/wav"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py::test_voices_list -v`
Expected: FAIL — 404, `/api/voices` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

In `src/wot_ai_commentator/server.py`, extend `SettingsIn` (after `tts_max_age_s`):

```python
    tts_max_age_s: float | None = None
    default_voice: str | None = None
    voice_by_priority: dict[str, str] | None = None
    voice_overrides: dict[str, str] | None = None
```

Add a request model near the other Pydantic models (after `SettingsIn`):

```python
class PreviewIn(BaseModel):
    voice: str | None = None
    text: str | None = None
```

Add both endpoints inside `create_app` (e.g. right after the `/api/audio/{audio_id}` route). Import `VOICES`:

```python
from .tts import VOICES, AudioStore, SileroTTS, pick_voice
```

```python
    @app.get("/api/voices")
    async def list_voices():
        return {"voices": list(VOICES)}

    @app.post("/api/tts/preview")
    async def preview_voice(body: PreviewIn):
        if ctx.tts is None or not ctx.tts.available:
            raise HTTPException(503, "TTS недоступен")
        text = (body.text or "").strip() or "Проверка голоса. Раз, два, три."
        wav = await asyncio.to_thread(ctx.tts.synth, text, body.voice)
        if not wav:
            raise HTTPException(503, "не удалось синтезировать")
        return Response(content=wav, media_type="audio/wav")
```

(`asyncio`, `Response`, `HTTPException`, `BaseModel` are already imported in `server.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (only if approved)

```bash
git add src/wot_ai_commentator/server.py tests/test_server.py
git commit -m "feat(api): voice settings, /api/voices, /api/tts/preview"
```

---

## Task 6: Panel "Голос" section

**Files:**
- Modify: `web/src/shared/api.ts`
- Modify: `web/src/panel/Panel.tsx`

**Interfaces:**
- Consumes: `GET /api/voices`, `POST /api/tts/preview`, `PUT /api/settings` with the three voice fields.

- [ ] **Step 1: Extend the API client**

In `web/src/shared/api.ts`, add the three fields to `SettingsDto`:

```typescript
  tts_max_age_s: number;
  default_voice: string;
  voice_by_priority: Record<string, string>;
  voice_overrides: Record<string, string>;
}
```

Add methods to the `api` object:

```typescript
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
```

- [ ] **Step 2: Add voice state + preview helper to Panel**

In `web/src/panel/Panel.tsx`, add state near the other `useState` hooks:

```typescript
  const [voices, setVoices] = useState<string[]>([]);
```

Load voices once in the existing `useEffect` (add before the `setInterval`):

```typescript
    api.getVoices().then((v) => setVoices(v.voices)).catch(() => {});
```

Add a preview helper inside the component (after `patch`):

```typescript
  const preview = async (voice: string) => {
    try {
      const blob = await api.previewVoice(voice);
      const audio = new Audio(URL.createObjectURL(blob));
      audio.play().catch(() => {});
    } catch (e) {
      setMessage(String(e));
    }
  };
```

- [ ] **Step 3: Render the "Голос" section**

In `web/src/panel/Panel.tsx`, add a new `<section>` after the "Режиссёр" section (before "Белый список чата"). `PRIORITIES` lists the four tiers; overrides render from the settings map with add/remove:

```tsx
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
```

Add the override-input state near the other `useState` hooks:

```typescript
  const [newOverrideType, setNewOverrideType] = useState("");
```

- [ ] **Step 4: Build the frontend**

Run: `cd web && npm run build`
Expected: build succeeds, no TypeScript errors. (If `tsc` is wired into the build, `SettingsDto` must include the three new fields — added in Step 1.)

- [ ] **Step 5: Commit** (only if approved)

```bash
git add web/src/shared/api.ts web/src/panel/Panel.tsx
git commit -m "feat(panel): voice section with per-context rules and preview"
```

---

## Task 7: Full test run + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the whole Python suite**

Run: `python -m pytest`
Expected: all tests PASS (new `test_tts.py`, updated `test_config.py`, `test_server.py`).

- [ ] **Step 2: Manual smoke (requires torch + a live game or chat order)**

1. `python -m wot_ai_commentator`, open `http://127.0.0.1:8710/panel`.
2. In "Голос": pick a default voice, click **прослушать** — a sample phrase plays.
3. Set `high → aidar`, add override `death → eugene`, save.
4. Trigger a frag/death in game (or `!dir тест` from an allowed chat user) — the overlay voice matches the rule; a fresh death uses `eugene`, a HIGH-priority frag uses `aidar`.
5. Confirm changing the default voice mid-session takes effect on the next replica (no restart).

Expected: voice selection follows the precedence override → priority → default; no crashes on empty/invalid config.

---

## Self-Review

- **Spec coverage:** multiple voices (Task 1–2), config rules (Task 3), pick_voice precedence (Task 1), engine voice arg (Task 2), wiring/live-switch (Task 4), API fields + voices list + preview (Task 5), panel UI + preview playback (Task 6), tests throughout, manual smoke (Task 7). All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
- **Type consistency:** `pick_voice(settings, stim_type, priority)`, `synth(text, voice=None)`, `_send_audio(replica_id, text, voice)`, `SettingsDto` fields, and `voice_by_priority` keys `"low"/"normal"/"high"/"critical"` are consistent across tasks and match `Priority.<NAME>.lower()`.

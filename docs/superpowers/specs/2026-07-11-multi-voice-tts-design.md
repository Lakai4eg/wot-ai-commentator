# Разные виды озвучки (multi-voice TTS)

Дата: 2026-07-11

## Задача

Дать режиссёру несколько голосов Silero вместо одного фиксированного `baya`:
- выбор из всех спикеров `v4_ru`;
- переключение на лету из панели (без перезапуска);
- голос под контекст — правило «событие → голос» по приоритету стимула
  с точечными оверрайдами по типу события;
- превью голоса на тестовой фразе прямо в панели.

Вне объёма: другие TTS-движки (ElevenLabs/OpenAI/edge-tts), эмоции/SSML,
голос отдельно на игру. Остаёмся внутри текущего Silero.

## Ключевые факты (почему дизайн такой)

- Одна уже загруженная модель `silero_tts / v4_ru` синтезирует **всех**
  спикеров: `model.apply_tts(..., speaker=<voice>)` принимает голос на каждый
  вызов. Несколько голосов = ноль дополнительной памяти и загрузок.
- Переключение на лету бесплатно: `AppContext.publish` читает `self.settings`
  на каждую реплику. PATCH настроек применяется со следующей реплики без
  перезапуска — как текущее переключение LLM-провайдера.
- У каждого `Stimulus` уже есть `type` и `priority` (LOW/NORMAL/HIGH/CRITICAL).
  Приоритет по смыслу и есть ось «спокойно ↔ хайп», поэтому база правил —
  по приоритету, а не по ~25 типам вручную.

## Модель данных (`config.Settings`)

Три новых поля:

```python
default_voice: str = "baya"                 # фолбэк, если правило не сматчилось
voice_by_priority: dict[str, str] = {}      # "low"/"normal"/"high"/"critical" → voice
voice_overrides: dict[str, str] = {}        # точный stimulus.type → voice (напр. "death")
```

Мутабельные значения по умолчанию — через `field(default_factory=dict)`.
`load_settings` уже фильтрует по известным полям и ловит `TypeError` — dict-поля
переживают round-trip как есть; битые значения приводят к дефолтам.

## Выбор голоса — `pick_voice`

Чистая, независимо тестируемая функция в `tts.py`:

```python
def pick_voice(settings: Settings, stim_type: str, priority: Priority) -> str
```

Приоритет правил (первое валидное побеждает):
1. `voice_overrides[stim_type]` — если задан и голос валиден;
2. `voice_by_priority[priority.name.lower()]` — если задан и валиден;
3. `settings.default_voice` — если валиден;
4. жёсткий фолбэк `"baya"`.

«Валиден» = входит в `VOICES`. Любое неизвестное имя игнорируется на своём
уровне и правило проваливается ниже — синтез никогда не падает из-за настройки.

## Известные голоса

Константа в `tts.py` — единственный источник истины для валидации и панели:

```python
VOICES = ("aidar", "baya", "kseniya", "xenia", "eugene", "random")
```

## Движок (`SileroTTS`)

- `synth(text: str, voice: str | None = None) -> bytes | None`: новый
  необязательный `voice`. `None` или невалидное имя → `self.voice` (дефолт из
  `settings.default_voice`, заданный в конструкторе). Модель не меняется —
  меняется только аргумент `speaker=` в `apply_tts`.
- Поведение при отсутствии torch (`available=False`) не меняется: `None`
  при любом `voice`.

## Проводка (`server.py`)

- `AppContext.publish`: вычисляет голос через
  `pick_voice(self.settings, stimulus.type, stimulus.priority)` и передаёт в
  `_send_audio(replica_id, text, voice)`.
- `_send_audio(replica_id, text, voice)` → `self.tts.synth(text, voice)`.
- `_voice_fresh`, кулдауны, дебаунс, `voice_enabled` — без изменений.

## API

- `SettingsIn` + `_masked_settings`: добавить `default_voice`,
  `voice_by_priority`, `voice_overrides`.
- Список `VOICES` отдаётся панели (в текущем payload настроек/статуса), чтобы
  выпадающие списки заполнялись из бэкенда, а не хардкодились во фронте.
- Превью: `POST /api/tts/preview {voice, text?}` → синтез тестовой фразы
  выбранным голосом, ответ `audio/wav` (или `503`, если TTS недоступен).
  Дефолтная фраза задаётся на бэке, если `text` не передан.

## Панель (`web/src/panel/Panel.tsx`)

Секция «Голос»:
- выпадающий список **дефолтного голоса** (из `VOICES` бэкенда);
- четыре списка **по приоритету** (low/normal/high/critical) — пусто = наследует
  дефолт;
- компактный редактор **оверрайдов**: имя типа события + голос + добавить/удалить;
- кнопка **«прослушать»** рядом с каждым выбором голоса → `POST /api/tts/preview`,
  проигрывает WAV в панели.

Все изменения сохраняются как остальные настройки (save-on-change, живое
применение).

## Обработка ошибок

- Невалидное имя голоса в настройках → тихо игнорируется в `pick_voice`
  (падение до следующего уровня), синтез идёт дефолтным.
- Превью при `tts.available == False` → `503`, панель показывает, что голос
  недоступен.
- Битый `settings.json` с dict-полями → дефолты (существующий путь
  `load_settings`).

## Тесты

- `pick_voice`: приоритет override > priority > default > жёсткий фолбэк;
  игнор невалидных имён на каждом уровне; регистр приоритета
  (`Priority.HIGH` → `"high"`).
- `config`: round-trip save/load с непустыми `voice_by_priority` и
  `voice_overrides`; битые значения → дефолты.
- `synth`: при `available=False` возвращает `None` при любом `voice`;
  невалидный `voice` не роняет.
- `server`: PATCH настроек принимает три новых поля; payload настроек содержит
  список `VOICES`; `POST /api/tts/preview` отдаёт `503` без TTS.

## Файлы

- `src/wot_ai_commentator/tts.py` — `VOICES`, `pick_voice`, `synth(voice=)`.
- `src/wot_ai_commentator/config.py` — три поля.
- `src/wot_ai_commentator/server.py` — проводка, `SettingsIn`,
  `_masked_settings`, `/api/tts/preview`, отдача `VOICES`.
- `web/src/panel/Panel.tsx` (+ `web/src/shared/api.ts`) — секция «Голос».
- `tests/` — `test_tts.py` (новый), дополнения в `test_config.py`,
  `test_server.py`.

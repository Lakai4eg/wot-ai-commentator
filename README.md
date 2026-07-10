# Stream Director

AI-режиссёр стрима для «Мира танков» и League of Legends: получает события боя
из мода [wotstat-data-provider](https://github.com/wotstat/wotstat-data-provider)
(WoT) или из Riot Live Client Data API (LoL, работает из коробки — мод не нужен),
реагирует ехидными репликами (плашки в OBS + голос Silero) и исполняет
команды доверенных зрителей из чата Twitch.

## Первый запуск (с Gemini)

1. **Мод**: скачай `wotstat.data-provider_<версия>.mtmod` из
   [релизов](https://github.com/wotstat/wotstat-data-provider/releases) и положи в
   `<папка игры>/mods/<версия игры>/`. Перезапусти игру.
2. **Зависимости** (Python 3.12+, Node 18+):
   ```bash
   python -m pip install -e .[dev,ml]      # ml = голос (torch + Silero)
   cd web && npm install && npm run build && cd ..
   ```
3. **Ключ Gemini**: бесплатно в [Google AI Studio](https://aistudio.google.com/apikey)
   (из РФ нужен маршрут до `generativelanguage.googleapis.com` — VPN/pbr).
4. **Запуск**:
   ```bash
   python -m stream_director
   ```
5. **Панель** http://127.0.0.1:8710/panel — вставь API-ключ Gemini (провайдер
   «Gemini» выбран по умолчанию), укажи канал Twitch. После сохранения ключа
   панель сама проверит LLM пробным запросом.
6. **OBS**: добавь http://127.0.0.1:8710/overlay как Browser Source на весь холст.

Готово: все четыре бейджа в шапке панели зелёные (`wotstat`, `чат`, `LLM`, `голос`) —
иди в бой, реплики пойдут сами.

![Панель управления](docs/panel.png)

## LLM-провайдеры

Кроме Gemini поддерживается любой OpenAI-совместимый API (переключается в панели
на лету): Groq (`https://api.groq.com/openai/v1`), OpenRouter, Mistral,
Ollama Cloud (`https://ollama.com/v1`), локальный Ollama
(`http://localhost:11434/v1`, без ключа).

## League of Legends

Отдельная настройка не нужна: Riot Live Client Data API поднимается самой игрой
на `https://127.0.0.1:2999` во время матча. Оба источника слушаются одновременно —
какая игра запущена, ту режиссёр и комментирует (бейджи WoT/LoL в панели,
активная отмечена ●).

## Чат-команда (для ников из белого списка)

Единственная команда — `!dir <текст>`: заказ реплики режиссёру. Заказ
отрабатывает всегда: глобальный кулдаун и дебаунс игровых событий его
не задерживают (только пер-зрительский антиспам-кулдаун).

## Разработка

```bash
python -m pytest            # тесты ядра
cd web && npm run dev       # фронтенд с hot-reload (proxy на :8710)
```

Архитектура: `games/<игра>/client.py` (транспорт: WoT — WebSocket мода,
LoL — поллер Live Client API) → `games/<игра>/mapper.py` (события → стимулы)
→ `director.py` (очередь, кулдаун, LLM; игро-независим) → оверлей + TTS.
Игро-специфичное (память, промпт-колорит, фолбэки) — в `games/<игра>/`.
Спеки — в `docs/superpowers/specs/`.

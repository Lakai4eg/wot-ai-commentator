"""LolMapper: снапшоты Live Client Data API → Stimulus.

Два механизма (спека §3.2): журнал events (обрабатываем только EventID больше
последнего виденного; при коннекте посреди матча историю проматываем) и
дельты снапшота (низкий ХП, isDead-страховка). Стример определяется
сравнением riotId из activePlayer с allPlayers (фолбэк — summonerName).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Callable

from ...stimulus import Priority, Stimulus
from .event_log import NullEventLog

log = logging.getLogger(__name__)

# Доля ХП, ниже которой считаем «на грани».
_LOW_HP_FRACTION = 0.2
# Порог «свежего» матча: при первом снапшоте старше — историю не переигрываем.
_FRESH_GAME_S = 30.0
# Антидубль смерти: isDead-страховка молчит, если death уже был недавно.
_DEATH_DEDUP_S = 3.0

# Подколки союзников: пороги и глобальный интервал (анти-спам TTS).
_ALLY_FEED_START = 5     # первая подколка фидера
_ALLY_FEED_STEP = 3      # дальше на каждых +3 смертях (5, 8, 11…)
_ALLY_LEAD_MIN_KILLS = 8  # керри: минимум киллов союзника…
_ALLY_LEAD_GAP = 5        # …и отрыв от стримера
_ALLY_EVENT_INTERVAL_S = 60.0  # не чаще одного союзного события в минуту
_TEAM_GAP_TIME_S = 600.0       # «наблюдатель»: стример 0/0/0 к 10-й минуте
_TEAM_GAP_TEAM_KILLS = 5       # …при этом команда уже навела шороху
_TEAM_GAP_BEHIND_DIFF = 10     # команда отстаёт по киллам

_MULTIKILL_LABELS = {2: "дабл-килл", 3: "трипл-килл", 4: "квадра-килл", 5: "пентакилл"}
# вид объекта: (русская строка для LLM, машинный ключ для шаблонов)
_OBJECTIVE_KINDS = {"DragonKill": ("дракон", "dragon"),
                    "HeraldKill": ("герольд", "herald"),
                    "BaronKill": ("барон", "baron")}


class LolMapper:
    """Переводит снапшоты Live Client API в игровые стимулы."""

    def __init__(self, submit: Callable[[Stimulus], None], event_log: Any = None) -> None:
        self.submit = submit
        # Пофайловый журнал сырых событий (по файлу на матч); None — без журнала.
        self._event_log = event_log if event_log is not None else NullEventLog()
        self._events_found = 0
        self._last_events: deque[str] = deque(maxlen=12)
        self._game_time = 0.0
        self._reset_game_trackers()

    def _reset_game_trackers(self) -> None:
        self._last_event_id = -1
        self._started = False
        self._was_dead = False
        self._low_hp_flagged = False
        self._death_emitted_at = 0.0
        # Счётчик смертей стримера (scores.deaths) — авторитетный сигнал смерти.
        # None до первого снапшота: там синхронизируемся, не озвучивая историю.
        self._death_count: int | None = None
        # Страховка озвучила смерть, а журнал её ещё не подтвердил.
        self._snapshot_death_pending = False
        # Счёт союзников (kills, deaths) по ключу имени; None до первого
        # снапшота — синхронизируемся молча, историю не переигрываем.
        self._ally_scores: dict[str, tuple[int, int]] | None = None
        self._ally_feed_voiced: dict[str, int] = {}  # последний озвученный порог
        self._ally_lead_voiced: set[str] = set()
        self._team_gap_voiced: set[str] = set()
        self._last_ally_event_at = 0.0
        # Последний килл журнала (killer, victim) — FirstBlood несёт только
        # Recipient, жертву достаём из парного ChampionKill того же килла.
        self._last_kill: tuple[Any, Any] = (None, None)

    # --- диагностика ---------------------------------------------------

    @property
    def diag(self) -> dict:
        return {"events_found": self._events_found, "last_events": self._last_events}

    # --- вход ------------------------------------------------------------

    def handle_payload(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        raw_time = (data.get("gameData") or {}).get("gameTime")
        if raw_time is None:
            # Частичный payload без gameData/gameTime (экран загрузки) — молча
            # пропускаем (спека §4.5): иначе gameTime=0 читался бы как «время
            # пошло назад» и ложно сбрасывал матч посреди игры.
            return
        game_time = float(raw_time)
        if game_time < self._game_time:
            # Время пошло назад — начался новый матч.
            self._reset_game_trackers()
        self._game_time = game_time

        players = data.get("allPlayers") or []
        me = self._identify_me(data, players)
        self._process_events(data, me, players)
        self._process_snapshot(data, me)
        self._process_team(data, me, players)

    # --- вспомогательное --------------------------------------------------

    @staticmethod
    def _name_key(name: Any) -> str:
        """Ключ имени: без Riot-тэга «#EUW» и в нижнем регистре.

        События журнала (KillerName/VictimName) часто присылают имя без тэга
        или в другом регистре, чем allPlayers/activePlayer (riotId «Имя#ТЭГ»).
        Без нормализации киллы/объекты не атрибутируются, и объект ложно
        уходит «противнику» (см. _side_of).
        """
        if not name:
            return ""
        return str(name).split("#", 1)[0].strip().casefold()

    @staticmethod
    def _matches(name: Any, player: dict | None) -> bool:
        if not name or not isinstance(player, dict):
            return False
        key = LolMapper._name_key(name)
        if not key:
            return False
        return any(LolMapper._name_key(ident) == key
                   for ident in (player.get("riotId"), player.get("summonerName")))

    @staticmethod
    def _identify_me(data: dict, players: list) -> dict | None:
        active = data.get("activePlayer") or {}
        name = active.get("riotId") or active.get("summonerName")
        if not name:
            return None
        for p in players:
            if LolMapper._matches(name, p):
                return p
        return None

    @staticmethod
    def _is_me(name: Any, me: dict | None) -> bool:
        return LolMapper._matches(name, me)

    @staticmethod
    def _champion_of(name: Any, players: list) -> str:
        for p in players:
            if LolMapper._matches(name, p):
                return p.get("championName") or str(name)
        return str(name) if name else "неизвестный"

    def _side_of(self, killer_name: Any, me: dict | None, players: list) -> str:
        """ours — убийца в команде стримера, theirs — во вражеской.

        Если убийцу не удалось сопоставить ни с одним игроком, возвращаем
        unknown: врать «забрал противник» нельзя (это и была причина ложных
        комментариев про дракона).
        """
        my_team = (me or {}).get("team")
        for p in players:
            if self._matches(killer_name, p):
                return "ours" if my_team and p.get("team") == my_team else "theirs"
        return "unknown"

    @staticmethod
    def _turret_side(turret_name: Any, me: dict | None) -> str:
        """ours — пала вражеская башня, theirs — наша.

        Сторону определяем по ИМЕНИ башни (Turret_T1_… принадлежит ORDER,
        Turret_T2_… — CHAOS), а не по убийце: последний удар часто за
        миньоном, которого _side_of не сопоставит с игроками.
        """
        my_team = (me or {}).get("team")
        name = str(turret_name or "")
        owner = "ORDER" if "_T1_" in name else "CHAOS" if "_T2_" in name else None
        if not my_team or owner is None:
            return "unknown"
        return "theirs" if owner == my_team else "ours"

    def _emit(self, type_: str, payload: dict,
              priority: Priority = Priority.NORMAL, ttl_s: float = 20.0) -> None:
        stim = Stimulus(kind="game_event", type=type_, game="lol",
                        priority=priority, payload=payload, ttl_s=ttl_s)
        self._events_found += 1
        self._last_events.append(type_)
        try:
            self.submit(stim)
        except Exception:  # шоу продолжается даже если приёмник упал
            log.exception("LolMapper: submit упал на событии %s", type_)

    def _emit_battle_start(self, data: dict, me: dict | None, *, silent: bool = True) -> None:
        # silent=True — тихий старт (подключение посреди матча): только память.
        # silent=False — настоящий GameStart: звучит интро.
        if self._started:
            return
        self._started = True
        gd = data.get("gameData") or {}
        meta = {"map": gd.get("mapName"), "mode": gd.get("gameMode"),
                "champion": (me or {}).get("championName")}
        # Начало матча — единственная точка на игру: заводим новый файл журнала.
        self._event_log.start_game({**meta, "silent": silent})
        self._emit("battle_start", {**meta, "silent": silent})

    # --- журнал событий ----------------------------------------------------

    def _process_events(self, data: dict, me: dict | None, players: list) -> None:
        events = (data.get("events") or {}).get("Events") or []
        ids = [int(e.get("EventID", -1)) for e in events if isinstance(e, dict)]
        if ids and max(ids) < self._last_event_id:
            # Журнал стал короче прежнего курсора — это журнал НОВОГО матча
            # (он начинается заново с GameStart, EventID 0), даже если gameTime
            # не пошёл назад: сбрасываемся (спека §3.2).
            self._reset_game_trackers()
        fresh = [e for e in events
                 if isinstance(e, dict) and int(e.get("EventID", -1)) > self._last_event_id]
        if not fresh:
            return
        if self._last_event_id == -1 and self._game_time > _FRESH_GAME_S:
            # Подключились посреди матча: историю не переигрываем,
            # но карту/чемпиона в память отдаём. Текущее isDead — тоже история
            # (смерть случилась до нас): синхронизируемся, чтобы isDead-страховка
            # не озвучила её как свежую (спека §3.2/§4.5).
            self._last_event_id = max(int(e.get("EventID", -1)) for e in fresh)
            self._was_dead = bool((me or {}).get("isDead"))
            self._emit_battle_start(data, me)
            return
        for e in fresh:
            self._last_event_id = int(e.get("EventID", -1))
            try:
                self._dispatch_event(e, data, me, players)
            except Exception:
                log.exception("LolMapper: событие %r сломало обработку", e.get("EventName"))
            # После диспатча: GameStart уже открыл файл матча, событие туда пишем.
            # Логируем все события, включая необработанные (чтобы поймать личинки).
            self._event_log.log_event(e)

    def _dispatch_event(self, ev: dict, data: dict, me: dict | None, players: list) -> None:
        name = ev.get("EventName")
        if name == "GameStart":
            self._emit_battle_start(data, me, silent=False)
        elif name == "ChampionKill":
            killer, victim = ev.get("KillerName"), ev.get("VictimName")
            assisters = ev.get("Assisters") or []
            self._last_kill = (killer, victim)  # для парного FirstBlood
            if self._is_me(victim, me):
                # Журнал авторитетен: разные EventID — разные смерти, друг с
                # другом их не дедупим. Молчим только если isDead-страховка
                # уже озвучила ЭТУ смерть (журнал отставал) — и ровно один раз.
                if (self._snapshot_death_pending
                        and time.time() - self._death_emitted_at <= _DEATH_DEDUP_S):
                    self._snapshot_death_pending = False
                else:
                    self._snapshot_death_pending = False
                    self._death_emitted_at = time.time()
                    self._emit("death", {"killer": self._champion_of(killer, players)},
                               Priority.HIGH, ttl_s=30)
            elif self._is_me(killer, me):
                self._emit("frag", {"target": self._champion_of(victim, players)},
                           Priority.HIGH, ttl_s=20)
            elif any(self._is_me(a, me) for a in assisters):
                # killer — союзник, который добил: ЛЛМ должна знать, кто именно
                # убил, чтобы не приписать килл (или смерть) стримеру.
                self._emit("assist",
                           {"target": self._champion_of(victim, players),
                            "killer": self._champion_of(killer, players)},
                           Priority.LOW, ttl_s=10)
        elif name == "Multikill":
            killer = ev.get("KillerName")
            streak = int(ev.get("KillStreak") or 2)
            label = _MULTIKILL_LABELS.get(streak, "мультикилл")
            if self._is_me(killer, me):
                self._emit(
                    "multikill",
                    {"count": streak, "label": label},
                    Priority.CRITICAL if streak >= 5 else Priority.HIGH,
                    ttl_s=20,
                )
            elif (self._side_of(killer, me, players) == "ours"
                  and self._ally_event_ok()):
                # Мультикилл союзника: хвалим его, подкалываем стримера.
                self._emit_ally("ally_carrying",
                                {"champion": self._champion_of(killer, players),
                                 "label": label, "count": streak}, ttl_s=15)
        elif name == "FirstBlood":
            recipient = ev.get("Recipient")
            # Жертва: из парного ChampionKill этого же килла (журнал шлёт их
            # рядом). Не совпало — жертву не выдумываем (None).
            last_killer, last_victim = self._last_kill
            victim = (last_victim
                      if self._name_key(last_killer) == self._name_key(recipient)
                      else None)
            self._emit(
                "first_blood",
                {"by_me": self._is_me(recipient, me),
                 "actor": self._champion_of(recipient, players),
                 "side": self._side_of(recipient, me, players),
                 "victim": self._champion_of(victim, players) if victim else None,
                 "victim_me": self._is_me(victim, me)},
                Priority.HIGH, ttl_s=15,
            )
        elif name in _OBJECTIVE_KINDS:
            kind, kind_key = _OBJECTIVE_KINDS[name]
            if name == "DragonKill" and ev.get("DragonType"):
                kind = f"дракон ({ev['DragonType']})"
            stolen = str(ev.get("Stolen", "False")) == "True"
            self._emit(
                "objective",
                {"kind": kind, "kind_key": kind_key,
                 "side": self._side_of(ev.get("KillerName"), me, players),
                 "stolen": stolen},
                Priority.HIGH if stolen else Priority.NORMAL, ttl_s=15,
            )
        elif name == "TurretKilled":
            if self._is_me(ev.get("KillerName"), me):
                self._emit("turret", {}, Priority.NORMAL, ttl_s=15)
            else:
                side = self._turret_side(ev.get("TurretKilled"), me)
                if side in ("ours", "theirs"):
                    self._emit("turret", {"side": side}, Priority.NORMAL, ttl_s=15)
        elif name == "InhibKilled":
            if self._is_me(ev.get("KillerName"), me):
                self._emit("inhib", {}, Priority.NORMAL, ttl_s=15)
        elif name == "Ace":
            my_team = (me or {}).get("team")
            side = "ours" if my_team and ev.get("AcingTeam") == my_team else "theirs"
            self._emit("ace", {"side": side}, Priority.HIGH, ttl_s=15)
        elif name == "GameEnd":
            outcome = "win" if str(ev.get("Result", "")).lower().startswith("win") else "loss"
            self._emit("battle_result", {"outcome": outcome, "silent": True},
                       Priority.CRITICAL, ttl_s=20)
        # MinionsSpawning и прочее — сознательно игнорируем (спека §3.2).

    # --- дельты снапшота ----------------------------------------------------

    def _process_snapshot(self, data: dict, me: dict | None) -> None:
        if me is None:
            return
        # Смерть определяем по РОСТУ счётчика scores.deaths, а не по isDead.
        # isDead — «липкий» флаг (держится весь таймер респауна) и в practice
        # tool / при чужой смерти может стоять true без реальной смерти стримера
        # — раньше это давало ложное «ты умер». Счётчик растёт ровно на реальной
        # смерти (в т.ч. не от чемпиона, чего нет в журнале ChampionKill).
        deaths = int((me.get("scores") or {}).get("deaths") or 0)
        if self._death_count is None:
            self._death_count = deaths  # первый снапшот: синхронизируемся молча
        elif deaths > self._death_count:
            self._death_count = deaths
            # Дедуп со свежей журнальной смертью (тот же смертельный удар).
            if time.time() - self._death_emitted_at > _DEATH_DEDUP_S:
                self._death_emitted_at = time.time()
                self._snapshot_death_pending = True
                self._emit("death", {"killer": "неизвестный"}, Priority.HIGH, ttl_s=30)
        elif deaths < self._death_count:
            self._death_count = deaths  # новый матч/рассинхрон — просто пересинк

        dead = bool(me.get("isDead"))
        if not dead and self._was_dead:
            self._low_hp_flagged = False  # респаун — «на грани» снова возможно
        self._was_dead = dead

        stats = (data.get("activePlayer") or {}).get("championStats") or {}
        cur = float(stats.get("currentHealth") or 0.0)
        max_hp = float(stats.get("maxHealth") or 0.0)
        if (not dead and max_hp > 0 and 0 < cur < _LOW_HP_FRACTION * max_hp
                and not self._low_hp_flagged):
            self._low_hp_flagged = True
            self._emit("low_hp", {"silent": True}, Priority.NORMAL, ttl_s=8)

    # --- команда: счёт союзников и поводы для подколок -----------------------

    def _ally_event_ok(self) -> bool:
        """Глобальный интервал союзных событий — не спамим озвучкой."""
        return time.time() - self._last_ally_event_at >= _ALLY_EVENT_INTERVAL_S

    def _emit_ally(self, type_: str, payload: dict, ttl_s: float = 20.0) -> None:
        self._last_ally_event_at = time.time()
        self._emit(type_, payload, Priority.NORMAL, ttl_s=ttl_s)

    def _collect_team(self, me: dict, players: list) -> tuple[list[dict], int]:
        """Союзники стримера (кроме него) и суммарные киллы противника."""
        my_team = me.get("team")
        me_key = self._name_key(me.get("riotId") or me.get("summonerName"))
        allies: list[dict] = []
        enemy_kills = 0
        for p in players:
            if not isinstance(p, dict):
                continue
            scores = p.get("scores") or {}
            kills = int(scores.get("kills") or 0)
            deaths = int(scores.get("deaths") or 0)
            if p.get("team") != my_team:
                enemy_kills += kills
                continue
            key = self._name_key(p.get("riotId") or p.get("summonerName"))
            if not key or key == me_key:
                continue
            allies.append({"key": key, "champion": p.get("championName") or "союзник",
                           "kills": kills, "deaths": deaths})
        return allies, enemy_kills

    def _process_team(self, data: dict, me: dict | None, players: list) -> None:
        if me is None or not me.get("team"):
            return
        allies, enemy_kills = self._collect_team(me, players)
        if not allies:
            return

        fresh = {a["key"]: (a["kills"], a["deaths"]) for a in allies}
        if self._ally_scores is None:
            self._ally_scores = fresh  # первый снапшот: молча синхронизируемся
        elif fresh != self._ally_scores:
            self._ally_scores = fresh
            # Тихий стимул: память получает таблицу союзников, реплики нет.
            self._emit("team_state",
                       {"allies": [{k: a[k] for k in ("champion", "kills", "deaths")}
                                   for a in allies],
                        "silent": True},
                       Priority.LOW, ttl_s=30)

        self._check_ally_feeding(allies)

        my_scores = me.get("scores") or {}
        my_kills = int(my_scores.get("kills") or 0)
        my_deaths = int(my_scores.get("deaths") or 0)
        my_assists = int(my_scores.get("assists") or 0)
        self._check_ally_lead(allies, my_kills)

        team_kills = my_kills + sum(a["kills"] for a in allies)
        if ("spectator" not in self._team_gap_voiced and self._ally_event_ok()
                and self._game_time >= _TEAM_GAP_TIME_S
                and my_kills == my_deaths == my_assists == 0
                and team_kills >= _TEAM_GAP_TEAM_KILLS):
            self._team_gap_voiced.add("spectator")
            self._emit_ally("team_gap",
                            {"kind": "spectator", "team_kills": team_kills})

        if ("behind" not in self._team_gap_voiced and self._ally_event_ok()
                and enemy_kills - team_kills >= _TEAM_GAP_BEHIND_DIFF):
            self._team_gap_voiced.add("behind")
            self._emit_ally("team_gap",
                            {"kind": "behind", "diff": enemy_kills - team_kills})

    def _check_ally_feeding(self, allies: list[dict]) -> None:
        for a in allies:
            if not self._ally_event_ok():
                return  # интервал не вышел — пороги не сжигаем, дождёмся снапшота
            deaths = a["deaths"]
            if deaths < _ALLY_FEED_START:
                continue
            threshold = (_ALLY_FEED_START
                         + (deaths - _ALLY_FEED_START) // _ALLY_FEED_STEP * _ALLY_FEED_STEP)
            if threshold > self._ally_feed_voiced.get(a["key"], 0):
                self._ally_feed_voiced[a["key"]] = threshold
                self._emit_ally("ally_feeding",
                                {"champion": a["champion"], "deaths": deaths})

    def _check_ally_lead(self, allies: list[dict], my_kills: int) -> None:
        for a in allies:
            if not self._ally_event_ok():
                return
            if (a["kills"] >= _ALLY_LEAD_MIN_KILLS
                    and a["kills"] - my_kills >= _ALLY_LEAD_GAP
                    and a["key"] not in self._ally_lead_voiced):
                # Один раз за игру на союзника: мишень шутки — стример.
                self._ally_lead_voiced.add(a["key"])
                self._emit_ally("ally_carrying",
                                {"champion": a["champion"], "kills": a["kills"],
                                 "my_kills": my_kills}, ttl_s=15)

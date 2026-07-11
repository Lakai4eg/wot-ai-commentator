from stream_director.games.template_pool import TemplatePool
from stream_director.stimulus import Stimulus


def stim(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol", payload=payload)


def key_by_type(stimulus):
    return stimulus.type


def make_pool(tmp_path, files, variant_key=key_by_type):
    for name, content in files.items():
        (tmp_path / f"{name}.txt").write_text(content, encoding="utf-8")
    return TemplatePool(tmp_path, variant_key)


def test_loads_lines_skips_comments_and_blank(tmp_path):
    pool = make_pool(tmp_path, {"frag": "один\n\n# коммент\nдва\n"})
    assert pool.templates == {"frag": ["один", "два"]}


def test_survives_notepad_bom(tmp_path):
    # ﻿ — BOM, который Блокнот Windows дописывает при сохранении.
    (tmp_path / "frag.txt").write_bytes("﻿строка".encode("utf-8"))
    pool = TemplatePool(tmp_path, key_by_type)
    assert pool.templates == {"frag": ["строка"]}


def test_take_unique_until_exhausted(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\nb\nc\n"})
    got = {pool.take(stim("frag")) for _ in range(3)}
    assert got == {"a", "b", "c"}
    assert pool.take(stim("frag")) is None


def test_take_falls_back_to_base_type_key(tmp_path):
    # Файла objective_ours нет — берём общий objective (как старый фолбэк).
    def variant(s):
        return f"objective_{s.payload['side']}"
    pool = make_pool(tmp_path, {"objective": "общий\n"}, variant)
    assert pool.take(stim("objective", side="ours")) == "общий"


def test_variant_key_does_not_leak_to_base_when_file_exists(tmp_path):
    # Файл objective_ours есть, но исчерпан — на общий НЕ откатываемся:
    # исчерпание значит «дальше без шаблонов», а не «берём чужие».
    def variant(s):
        return f"objective_{s.payload['side']}"
    pool = make_pool(tmp_path, {"objective": "общий\n", "objective_ours": "наш\n"}, variant)
    assert pool.take(stim("objective", side="ours")) == "наш"
    assert pool.take(stim("objective", side="ours")) is None


def test_take_none_for_unknown_event(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\n"})
    assert pool.take(stim("death")) is None


def test_missing_dir_yields_empty_pool(tmp_path):
    pool = TemplatePool(tmp_path / "нет_такой_папки", key_by_type)
    assert pool.templates == {}
    assert pool.take(stim("frag")) is None
    assert pool.exhausted_pick(stim("frag")) is None


def test_exhausted_pick_avoids_recent_three(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\nb\nc\nd\ne\n"})
    picks = [pool.exhausted_pick(stim("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]


def test_exhausted_pick_single_option_still_returns(tmp_path):
    pool = make_pool(tmp_path, {"frag": "одна\n"})
    assert pool.exhausted_pick(stim("frag")) == "одна"
    assert pool.exhausted_pick(stim("frag")) == "одна"


def test_exhausted_pick_not_immediately_after_same_take(tmp_path):
    # take() тоже пишет в «недавние»: аварийный выбор не повторяет
    # только что прозвучавший шаблон.
    pool = make_pool(tmp_path, {"frag": "a\nb\n"})
    last = [pool.take(stim("frag")) for _ in range(2)][-1]
    assert pool.exhausted_pick(stim("frag")) != last

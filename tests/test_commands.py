from stream_director.chat.commands import parse_command


def test_dir_with_text():
    cmd = parse_command("!dir зароасть стримера")
    assert cmd is not None
    assert cmd.name == "dir"
    assert cmd.arg == "зароасть стримера"


def test_dir_without_text_is_none():
    assert parse_command("!dir") is None
    assert parse_command("!dir   ") is None


def test_non_command_is_none():
    assert parse_command("просто сообщение") is None
    assert parse_command("!unknown") is None
    assert parse_command("") is None


def test_case_insensitive():
    assert parse_command("!DIR привет").name == "dir"


def test_removed_commands_are_none():
    # Осталась единственная команда !dir — остальные больше не парсятся.
    for text in ("!roast", "!hype", "!stats", "!mute 5m", "!mood токсичный"):
        assert parse_command(text) is None

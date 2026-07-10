from wot_ai_commentator.chat.commands import (
    ADMIN_COMMANDS,
    parse_command,
    parse_mute_arg,
)


def test_dir_with_text():
    cmd = parse_command("!dir зароасть стримера")
    assert cmd is not None
    assert cmd.name == "dir"
    assert cmd.arg == "зароасть стримера"


def test_dir_without_text_is_none():
    assert parse_command("!dir") is None
    assert parse_command("!dir   ") is None


def test_simple_commands_no_arg():
    for name in ("roast", "hype", "stats"):
        cmd = parse_command(f"!{name}")
        assert cmd is not None and cmd.name == name and cmd.arg is None


def test_non_command_is_none():
    assert parse_command("просто сообщение") is None
    assert parse_command("!unknown") is None
    assert parse_command("") is None


def test_case_insensitive():
    assert parse_command("!DIR привет").name == "dir"
    assert parse_command("!Roast").name == "roast"


def test_admin_commands_set():
    assert ADMIN_COMMANDS == {"mute"}


def test_parse_mute_arg():
    assert parse_mute_arg("10m") == 600
    assert parse_mute_arg("30s") == 30
    assert parse_mute_arg("5") == 300  # голое число = минуты
    assert parse_mute_arg("мусор") is None
    assert parse_mute_arg(None) is None
    assert parse_mute_arg("0") is None


def test_mood_command_removed():
    assert parse_command("!mood токсичный") is None

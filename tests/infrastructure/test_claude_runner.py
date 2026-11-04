import json
import re
import stat
import sys
import threading
from pathlib import Path

import pytest

from mlbooster.application.dto import ResearchEvent, RunnerReadiness
from mlbooster.infrastructure import claude_runner as module
from mlbooster.infrastructure.claude_runner import (
    ALLOWED_TOOLS,
    ClaudeCodeRunner,
    _outcome,
    child_environment,
    command,
    prompt_for,
)

INIT = {"type": "system", "subtype": "init", "tools": list(ALLOWED_TOOLS), "apiKeySource": "none"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "Rapor"}
CALLS = [
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "WebSearch"}, {"type": "tool_use", "id": "2", "name": "WebFetch"}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1"}, {"type": "tool_result", "tool_use_id": "2"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "3", "name": ALLOWED_TOOLS[4]}, {"type": "tool_use", "id": "4", "name": ALLOWED_TOOLS[4]}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "3"}, {"type": "tool_result", "tool_use_id": "4", "is_error": True}]}},
]


def fake_claude(tmp_path: Path, events: list[dict], sleep: float = 0) -> str:
    """A stand-in `claude` program: records how it was started, then prints the events."""
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\n"
        f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}}, open({str(tmp_path / 'call.json')!r}, 'w'))\n"
        "print('Warning: not json')\n"
        f"for e in {events!r}:\n    print(json.dumps(e), flush=True)\ntime.sleep({sleep})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    def build(events, sleep=0, timeout=30):
        exe = fake_claude(tmp_path, events, sleep)
        made = ClaudeCodeRunner(tmp_path / "data", timeout=timeout)
        monkeypatch.setattr(made, "readiness", lambda: RunnerReadiness(True, executable=exe, plugin_dir="/plugin"))
        return made

    return build


def _run(made, brand="Migros", cancel=None):
    seen: list[str] = []

    def on_event(event: ResearchEvent):
        seen.append(event.kind)

    return made.run(brand, "perakende", on_event, cancel or threading.Event()), seen


def test_child_runs_on_the_login_with_only_the_agents_tools(runner, tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_OAUTH_TOKEN", "EVDS_API_KEY", "EPIAS_PASSWORD"):
        monkeypatch.setenv(name, "secret-value")
    outcome, seen = _run(runner([INIT, *CALLS, RESULT]))
    call = json.loads((tmp_path / "call.json").read_text(encoding="utf-8"))
    assert outcome.status == "finished" and outcome.report == "Rapor"
    assert seen == ["started", "search", "page", "record", "refused"]
    assert "secret-value" not in call["env"].values() and call["env"]["MLBOOSTER_DATA_DIR"] == str(tmp_path / "data")
    assert Path(call["cwd"]).resolve() == (tmp_path / "data" / "research").resolve()
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch" and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--agent") + 1] == "ml-booster:share-research" and "--max-turns" in argv and "--max-budget-usd" in argv
    assert "Bash" not in " ".join(argv) and "--dangerously-skip-permissions" not in argv


def test_files_the_child_kept_are_deleted_and_nothing_else(runner, tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    made = runner([INIT, RESULT])
    scratch = (tmp_path / "data" / "research")
    scratch.mkdir(parents=True)
    ours = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(scratch.resolve())) / "session" / "tool-results"
    other = config / "projects" / "-Users-someone-project"
    ours.mkdir(parents=True)
    other.mkdir(parents=True)
    (ours / "webfetch.pdf").write_bytes(b"%PDF-")
    _run(made)
    assert not ours.exists() and other.exists()


def test_a_hostile_name_stays_one_argument_and_a_json_value():
    name = 'x"; rm -rf ~ $(id) --tools Bash'
    args = command("claude", "/plugin", name, None)
    assert args[1:3] == ["-p", prompt_for(name, None)] and "--tools" == args[args.index("--tools")]
    assert json.dumps(name, ensure_ascii=False) in args[2] and args.count("Bash") == 0


def test_environment_keeps_what_the_login_needs():
    env = child_environment({"PATH": "/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": "/c", "ANTHROPIC_MODEL": "x", "GITHUB_TOKEN": "t", "MY_SECRET": "s"}, Path("/d"))
    assert env == {"PATH": "/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": "/c", "MLBOOSTER_DATA_DIR": "/d"}


@pytest.mark.parametrize(
    ("init", "needle"),
    [({**INIT, "tools": [*ALLOWED_TOOLS, "Bash"]}, "izin verilmeyen"), ({**INIT, "apiKeySource": "ANTHROPIC_API_KEY"}, "API key")],
)
def test_a_child_that_breaks_the_rules_is_stopped_at_its_first_event(runner, init, needle):
    outcome, seen = _run(runner([init, *CALLS], sleep=20))
    assert outcome.status == "failed" and needle in outcome.message and "started" not in seen


def test_cancel_stops_the_child(runner):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    outcome, _ = _run(runner([INIT], sleep=20), cancel=cancel)
    assert outcome.status == "cancelled"


def test_timeout_stops_the_child(runner):
    outcome, _ = _run(runner([INIT], sleep=20, timeout=0.3))
    assert outcome.status == "failed" and "dakikalık" in outcome.message


def test_one_search_per_computer(runner, tmp_path):
    first = runner([INIT, RESULT], sleep=2)
    cancel = threading.Event()
    thread = threading.Thread(target=_run, args=(first,), kwargs={"cancel": cancel})
    thread.start()
    while not (tmp_path / "call.json").exists():
        pass
    second = ClaudeCodeRunner(tmp_path / "data")
    second.readiness = first.readiness
    outcome, _ = _run(second)
    cancel.set()
    thread.join()
    assert outcome.status == "failed" and "başka bir web araması" in outcome.message


@pytest.mark.parametrize(
    ("result", "needle"),
    [
        ({"subtype": "error_max_turns", "is_error": True}, "tur sınırına"),
        ({"subtype": "error_max_budget_usd", "is_error": True}, "bütçe"),
        ({"subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}, "oturum"),
        ({"subtype": "error_during_execution", "is_error": True}, "tamamlayamadı"),
        (None, "sonuç vermeden"),
    ],
)
def test_failures_say_what_happened(result, needle):
    outcome = _outcome(result, 1, [], False, False)
    assert outcome.status == "failed" and needle in outcome.message


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A stand-in `claude` for the readiness commands."""

    def build(auth: dict, plugins: list[dict]):
        script = tmp_path / "claude"
        script.write_text(f"#!{sys.executable}\nimport sys\nprint({json.dumps(auth)!r} if sys.argv[1] == 'auth' else {json.dumps(plugins)!r})\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(module.shutil, "which", lambda _: str(script))
        return ClaudeCodeRunner(tmp_path / "data")

    return build


LOGIN = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
PLUGIN = [{"id": "ml-booster@ml-booster-tools", "enabled": True, "installPath": "/p"}]


def test_readiness_is_ready_with_a_login_and_the_plugin(cli):
    ready = cli(LOGIN, PLUGIN).readiness()
    assert ready.ready and ready.plugin_dir == "/p"


@pytest.mark.parametrize(
    ("auth", "plugins", "needle"),
    [
        ({"loggedIn": False}, PLUGIN, "/login"),
        ({**LOGIN, "authMethod": "api_key"}, PLUGIN, "API hesabına"),
        ({**LOGIN, "apiProvider": "bedrock"}, PLUGIN, "API hesabına"),
        (LOGIN, [], "plugin install"),
        (LOGIN, [{**PLUGIN[0], "enabled": False}], "plugin install"),
    ],
)
def test_readiness_names_the_next_step(cli, auth, plugins, needle):
    ready = cli(auth, plugins).readiness()
    assert not ready.ready and needle in f"{ready.reason} {ready.next_step}"


def test_missing_claude_says_how_to_install(tmp_path, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    ready = ClaudeCodeRunner(tmp_path / "data").readiness()
    assert not ready.ready and "kurun" in ready.next_step

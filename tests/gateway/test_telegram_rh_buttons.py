"""Regression tests for RH trade/NFT Telegram callbacks.

The alert formatter can paint ``rb:*`` buttons independently of the Telegram
adapter. These tests pin the missing bridge and the stdin-only API-key transport
so a pretty card cannot silently ship with dead spend controls again.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO = str(Path(__file__).resolve().parents[2])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test", extra={}))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _query(data="rb:nf:1:10:birbsrh", chat_id=1426127634, user_id=1426127634):
    message = MagicMock()
    message.chat_id = chat_id
    message.chat.type = "private"
    message.message_thread_id = None
    query = AsyncMock()
    query.data = data
    query.message = message
    query.from_user.id = user_id
    query.from_user.first_name = "DEMI"
    return query


@pytest.mark.asyncio
async def test_rb_callback_is_dispatched_with_chat_context():
    adapter = _adapter()
    adapter._handle_rh_trade_callback = AsyncMock()
    query = _query()

    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
        await adapter._handle_callback_query(
            SimpleNamespace(callback_query=query), SimpleNamespace()
        )

    adapter._handle_rh_trade_callback.assert_awaited_once()
    kwargs = adapter._handle_rh_trade_callback.await_args.kwargs
    assert kwargs["query_chat_id"] == 1426127634
    assert kwargs["query_chat_type"] == "private"


@pytest.mark.asyncio
async def test_rh_callback_runs_profile_dispatcher_and_answers_result(tmp_path):
    adapter = _adapter()
    query = _query()
    script = tmp_path / "rh_tg_callback.py"
    script.write_text("print('unused')")
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"ARMED floor birbsrh", b"")

    with (
        patch.object(adapter, "_rh_callback_script_path", return_value=script),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as spawn,
        patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False),
    ):
        await adapter._handle_rh_trade_callback(
            query,
            "rb:nf:1:10:birbsrh",
            query_chat_id=1426127634,
            query_chat_type="private",
            query_thread_id=None,
            query_user_name="DEMI",
        )

    argv = spawn.await_args.args
    assert argv[-2:] == ("rb:nf:1:10:birbsrh", "1426127634")
    query.answer.assert_awaited_once_with(text="ARMED floor birbsrh")


def test_dispatcher_uses_stdin_auth_in_both_headers():
    path = Path.home() / ".hermes/profiles/oracle/scripts/rh_tg_callback.py"
    spec = importlib.util.spec_from_file_location("rh_tg_callback_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    seen = {}

    def fake_run(argv, *, input, capture_output, text, timeout):
        seen.update(argv=argv, input=input)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    with patch.object(module.subprocess, "run", fake_run):
        assert module._ssh_curl("secret-not-printed", "GET", "/api/agent/kill")["ok"]

    remote = seen["argv"][-1]
    assert 'x-api-key: $AUTH' in remote
    assert 'Authorization: Bearer $AUTH' in remote
    assert "***" not in remote
    assert seen["input"] == "secret-not-printed\n"


def test_floor_prepare_with_insufficient_wrap_never_arms(tmp_path):
    path = Path.home() / ".hermes/profiles/oracle/scripts/rh_tg_callback.py"
    spec = importlib.util.spec_from_file_location("rh_tg_callback_insufficient", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setattr(module, "PENDING_DIR", tmp_path)
    env = {
        "RH_AGENT_API_KEY": "test",
        "RH_AGENT_EXECUTOR_ALLOW_NFT_BUY": "1",
        "TELEGRAM_ALLOWED_USERS": "1426127634",
    }
    response = {
        "error": "insufficient funds: fund or wrap first",
        "wrap": {"insufficient": True, "wouldWrapEth": "0.047"},
    }

    with (
        patch.object(module, "_debounce", return_value=False),
        patch.object(module, "_ssh_curl", return_value=response),
        patch.object(module, "_tg_send") as send,
    ):
        result = module.handle("rb:nf:1:50:birbsrh", env, "1426127634")

    assert "insufficient" in result.lower()
    assert "ARMED" not in result
    assert list(tmp_path.rglob("*.json")) == []
    send.assert_not_called()


def _load_dispatcher(name):
    path = Path.home() / ".hermes/profiles/oracle/scripts/rh_tg_callback.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_token_sell_is_prepare_then_exact_hash_confirm(tmp_path):
    module = _load_dispatcher("rh_tg_callback_sell")
    module.PENDING_DIR = tmp_path
    env = {"RH_AGENT_API_KEY": "test", "TELEGRAM_ALLOWED_USERS": "1426127634"}
    intent = {"id": "sell_abc", "intentHash": "a" * 64, "amountIn": "250", "expiresAt": "2099-01-01T00:00:00.000Z"}
    calls = []

    def api(_key, method, path, body=None, timeout=120):
        calls.append((method, path, body))
        if path == "/api/agent/prepare-executor-sell":
            return {"intent": intent, "trigger": {"type": "manual_percent", "positionPct": 25}, "prepared": {"kind": "swap"}}
        if path == "/api/agent/execute-sell":
            assert body == {"intent": intent, "intentHash": intent["intentHash"]}
            return {"executed": {"hash": "0x" + "ab" * 32}, "balanceDelta": {"sold": "250"}}
        raise AssertionError(path)

    with patch.object(module, "_debounce", return_value=False), patch.object(module, "_ssh_curl", side_effect=api), patch.object(module, "_tg_send"):
        first = module.handle("rb:s:25:1111111111111111111111111111111111111111", env, "1426127634")
        second = module.handle("rb:s:25:1111111111111111111111111111111111111111", env, "1426127634")

    assert "ARMED" in first
    assert "✓ sell" in second
    assert [path for _, path, _ in calls] == ["/api/agent/prepare-executor-sell", "/api/agent/execute-sell"]


def test_take_initials_uses_same_two_tap_exact_intent(tmp_path):
    module = _load_dispatcher("rh_tg_callback_initials")
    module.PENDING_DIR = tmp_path
    env = {"RH_AGENT_API_KEY": "test", "TELEGRAM_ALLOWED_USERS": "1426127634"}
    intent = {"id": "sell_initials", "intentHash": "b" * 64, "amountIn": "333", "expiresAt": "2099-01-01T00:00:00.000Z"}
    calls = []

    def api(_key, method, path, body=None, timeout=120):
        calls.append((path, body))
        if path == "/api/agent/prepare-executor-sell":
            assert body["action"] == "take_initials"
            return {"intent": intent, "trigger": {"type": "take_initials", "multiple": 3, "minimum": 3}, "prepared": {"kind": "approval"}}
        return {"executed": {"hash": "0x" + "cd" * 32}, "balanceDelta": {"sold": "333"}}

    with patch.object(module, "_debounce", return_value=False), patch.object(module, "_ssh_curl", side_effect=api), patch.object(module, "_tg_send"):
        first = module.handle("rb:i:1111111111111111111111111111111111111111", env, "1426127634")
        second = module.handle("rb:i:1111111111111111111111111111111111111111", env, "1426127634")

    assert "INITIALS ARMED" in first
    assert "✓ sell" in second
    assert [path for path, _ in calls] == ["/api/agent/prepare-executor-sell", "/api/agent/execute-sell"]

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated credentials/state before server.py is imported (it reads these paths at import time).
_TMP = Path(tempfile.mkdtemp(prefix="ipasen-tests-"))
os.environ["IPASEN_STATE_FILE"] = str(_TMP / "session.json")
os.environ["IPASEN_CREDENTIALS_FILE"] = str(_TMP / "credentials.env")
for key in ("IPASEN_USER", "IPASEN_PASSWORD", "IPASEN_CONFIRM_MODE", "IPASEN_TELEGRAM_BOT_TOKEN", "IPASEN_TELEGRAM_CHAT_ID"):
    os.environ.pop(key, None)
(_TMP / "credentials.env").write_text("IPASEN_USER=test_user\nIPASEN_PASSWORD=test_password\n")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import server  # noqa: E402
from fake_api import FakeApi  # noqa: E402


@pytest.fixture
def api(monkeypatch):
    """Fresh fake API and empty state for every test."""
    fake = FakeApi()
    monkeypatch.setattr(server, "_client", httpx.Client(base_url=server.BASE_URL, transport=fake.transport()))
    server.STATE_FILE.unlink(missing_ok=True)
    server._children_cache.clear()
    yield fake
    server._children_cache.clear()


@pytest.fixture
def telegram(monkeypatch):
    """Telegram confirmation mode with a captured outbox instead of real messages."""
    sent: list[str] = []
    monkeypatch.setenv("IPASEN_CONFIRM_MODE", "telegram")
    monkeypatch.setenv("IPASEN_TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("IPASEN_TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(server, "_telegram", sent.append)
    return sent

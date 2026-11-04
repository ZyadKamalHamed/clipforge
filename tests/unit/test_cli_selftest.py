"""``clipforge selftest``: the browser route's self-test from the Terminal, a line per selector. The self-test
itself is faked here, so no window opens; tests/ui runs the real one on the look-alike pages."""

import os

import pytest

from clipforge import cli
from clipforge.app import take_home
from clipforge.paths import Paths
from clipforge.post.browser import selftest
from clipforge.post.browser.selftest import SelfTestResult
from clipforge.post.browser.tiktok import CHANGED, LOGGED_OUT, READY
from tests.fakes import fake_engine
from tests.posting import add_account


@pytest.fixture
def accounts(monkeypatch):
    """Two accounts on the test home, and each run of the command on an engine of its own there."""
    engine = fake_engine()
    try:
        made = {
            "tiktok": add_account(engine, "tiktok", "mine"),
            "api": add_account(engine, "instagram", "gram"),
            "ig": add_account(engine, "instagram", "both", method="browser"),
            "tt": add_account(engine, "tiktok", "both"),
        }
    finally:
        engine.close()
    monkeypatch.setattr(cli, "open_engine", lambda verbose=False: fake_engine())
    return made


def faked(monkeypatch, result):
    tested = []
    monkeypatch.setattr(selftest, "self_test", lambda engine, account: tested.append(account.id) or result)
    return tested


def test_selftest_cli_reports_found_and_missing(accounts, monkeypatch, capsys):
    tested = faked(monkeypatch, SelfTestResult(found=["caption", "post"], missing=["file_input"]))
    assert cli.main(["selftest", "@Mine"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "found    caption",
        "found    post",
        "MISSING  file_input",
        CHANGED,
    ]
    assert tested == [accounts["tiktok"].id]

    faked(monkeypatch, SelfTestResult(found=["caption", "post", "file_input"]))
    assert cli.main(["selftest", accounts["tiktok"].id]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "found    caption",
        "found    post",
        "found    file_input",
        READY,
    ]


def test_selftest_cli_says_when_logged_out(accounts, monkeypatch, capsys):
    faked(monkeypatch, SelfTestResult([], ["file_input"], logged_in=False))
    assert cli.main(["selftest", "@mine"]) == 1
    assert capsys.readouterr().out.splitlines() == [LOGGED_OUT.format(handle="mine")]


def test_selftest_cli_needs_one_browser_account(accounts, monkeypatch, capsys):
    tested = faked(monkeypatch, SelfTestResult(found=["file_input"]))
    assert cli.main(["selftest", "@gram"]) == 1
    assert "@gram posts through Instagram's API" in capsys.readouterr().err
    assert cli.main(["selftest", "@nobody"]) == 1
    assert "No account is @nobody." in capsys.readouterr().err
    assert cli.main(["selftest", "@both"]) == 1
    err = capsys.readouterr().err
    assert accounts["ig"].id in err and accounts["tt"].id in err
    assert tested == []


def test_selftest_cli_while_clipforge_is_open(capsys, monkeypatch):
    paths = Paths.from_env()
    paths.ensure()
    fd = take_home(paths)  # the window's engine has the home
    tested = faked(monkeypatch, SelfTestResult(found=["file_input"]))
    try:
        assert cli.main(["selftest", "@mine"]) == 1
    finally:
        os.close(fd)
    assert capsys.readouterr().err.strip() == cli.SELFTEST_ELSEWHERE
    assert tested == []

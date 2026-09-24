"""RuntimeConfig.test / verbose 与 AppContext.is_*、--help --verbose 别名。"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import pytest

from wcore import AppContext, RuntimeConfig


@pytest.fixture
def restore_appcontext():
    prev_rc = AppContext.runtime_config
    prev_cfg = AppContext.config
    prev_log = AppContext.logger
    prev_wd = RuntimeConfig.workdir
    prev_cf = RuntimeConfig.config_file
    cwd = os.getcwd()
    yield
    os.chdir(cwd)
    AppContext.runtime_config = prev_rc
    AppContext.config = prev_cfg
    AppContext.logger = prev_log
    RuntimeConfig.workdir = prev_wd
    RuntimeConfig.config_file = prev_cf


def test_is_flags_false_when_uninitialized(restore_appcontext):
    AppContext.runtime_config = None
    assert AppContext.is_test() is False
    assert AppContext.is_verbose() is False


def test_is_flags_follow_runtime_config(restore_appcontext):
    AppContext.runtime_config = SimpleNamespace(test=True, verbose=False)
    assert AppContext.is_test() is True
    assert AppContext.is_verbose() is False
    AppContext.runtime_config = SimpleNamespace(test=False, verbose=True)
    assert AppContext.is_test() is False
    assert AppContext.is_verbose() is True


def test_cli_test_sets_debug_logger(restore_appcontext):
    AppContext(None, "wcore", extra_args=["--test"])
    assert AppContext.is_test() is True
    assert AppContext.is_verbose() is False
    assert logging.getLogger("wcore").level == logging.DEBUG


def test_cli_verbose_does_not_set_debug(restore_appcontext):
    AppContext(None, "wcore", extra_args=["--verbose"])
    assert AppContext.is_verbose() is True
    assert AppContext.is_test() is False
    assert logging.getLogger("wcore").level == logging.INFO


def test_yaml_verbose_and_no_verbose_override(restore_appcontext, tmp_path):
    (tmp_path / "wcore.yaml").write_text("verbose: true\ntest: false\n", encoding="utf-8")
    AppContext(None, "wcore", extra_args=["--workdir", str(tmp_path)])
    assert AppContext.is_verbose() is True
    AppContext(None, "wcore", extra_args=["--workdir", str(tmp_path), "--no-verbose"])
    assert AppContext.is_verbose() is False


def test_help_verbose_aliases_help_all(restore_appcontext, capsys):
    marker = "--digest-interval-sec"
    with pytest.raises(SystemExit) as ei:
        AppContext(None, "wcore", extra_args=["--help"])
    assert ei.value.code == 0
    basic = capsys.readouterr().out
    assert "digest_interval_sec" not in basic or "(使用 --help-all" in basic
    assert "--help-all" in basic

    with pytest.raises(SystemExit):
        AppContext(None, "wcore", extra_args=["--help-all"])
    all_help = capsys.readouterr().out
    assert marker in all_help

    with pytest.raises(SystemExit):
        AppContext(None, "wcore", extra_args=["--help", "--verbose"])
    verbose_help = capsys.readouterr().out
    assert marker in verbose_help

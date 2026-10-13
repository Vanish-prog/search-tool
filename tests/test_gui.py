import os
from pathlib import Path

from search_tool.cli import main


def test_gui_without_directory_dispatches_without_loading_model(monkeypatch):
    import search_tool.gui as gui
    calls = []
    monkeypatch.setattr(gui, 'launch', lambda args: calls.append(args) or 0)
    assert main(['--gui'], engine_factory=lambda: (_ for _ in ()).throw(AssertionError('model loaded'))) == 0
    assert len(calls) == 1
    assert calls[0].directory is None


def test_gui_preserves_options_and_multiple_references(tmp_path, monkeypatch):
    import search_tool.gui as gui
    calls = []
    monkeypatch.setattr(gui, 'launch', lambda args: calls.append(args) or 7)
    first, second = tmp_path / 'one image.png', tmp_path / 'two.txt'
    assert main(['--gui', str(tmp_path), 'query with spaces', '-r', str(first),
                 '-r', str(second), '--limit', '3', '--rebuild']) == 7
    args = calls[0]
    assert args.directory == tmp_path
    assert args.query == 'query with spaces'
    assert args.reference == [first, second]
    assert args.limit == 3 and args.rebuild


def test_gui_rejects_json_before_launch(monkeypatch):
    import pytest
    import search_tool.gui as gui
    monkeypatch.setattr(gui, 'launch', lambda _: (_ for _ in ()).throw(AssertionError('launched')))
    with pytest.raises(SystemExit) as exc:
        main(['--gui', '--json'])
    assert exc.value.code == 2


def test_help_explains_gui_without_loading_frontend(capsys):
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert '--gui' in output
    assert 'findmatch --gui' in output
    assert 'GPUI' in output


def test_launcher_uses_argument_array_and_current_python(tmp_path, monkeypatch):
    import sys
    import search_tool.gui as gui
    calls = []
    monkeypatch.setenv('FINDMATCH_GUI_BINARY', '/test/native gui')
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return type('Process', (), {'wait': lambda self: 0})()
    monkeypatch.setattr(gui.subprocess, 'Popen', run)
    assert main(['--gui', str(tmp_path), 'a; not a shell', '-r', 'one.png', '-r', 'two.png', '--rebuild']) == 0
    command, kwargs = calls[0]
    assert command[0] == '/test/native gui'
    assert command[command.index('--query') + 1] == 'a; not a shell'
    assert command.count('--reference') == 2
    assert '--gui' not in command
    assert '--rebuild' in command
    assert kwargs['env']['FINDMATCH_GUI_PYTHON'] == sys.executable
    assert Path(kwargs['env']['PYTHONPATH'].split(os.pathsep)[0]).joinpath('search_tool').is_dir()
    assert 'shell' not in kwargs
    assert kwargs['start_new_session'] is True


def test_keyboard_interrupt_terminates_and_reaps_gui(monkeypatch):
    import search_tool.gui as gui
    monkeypatch.setenv('FINDMATCH_GUI_BINARY', '/test/gui')
    calls = []
    class Interrupted:
        def wait(self, timeout=None):
            calls.append(('wait', timeout))
            if timeout is None:
                raise KeyboardInterrupt
            return -15
        def terminate(self):
            calls.append(('terminate',))
    monkeypatch.setattr(gui.subprocess, 'Popen', lambda *a, **kw: Interrupted())
    assert main(['--gui']) == 130
    assert calls == [('wait', None), ('terminate',), ('wait', 5)]


def test_unresponsive_gui_is_killed_after_interrupt(monkeypatch):
    import search_tool.gui as gui
    monkeypatch.setenv('FINDMATCH_GUI_BINARY', '/test/gui')
    calls = []
    class Stuck:
        def wait(self, timeout=None):
            calls.append(('wait', timeout))
            if len(calls) == 1:
                raise KeyboardInterrupt
            if timeout is not None:
                raise gui.subprocess.TimeoutExpired('/test/gui', timeout)
            return -9
        def terminate(self):
            calls.append(('terminate',))
        def kill(self):
            calls.append(('kill',))
    monkeypatch.setattr(gui.subprocess, 'Popen', lambda *a, **kw: Stuck())
    assert main(['--gui']) == 130
    assert calls == [('wait', None), ('terminate',), ('wait', 5), ('kill',), ('wait', None)]


def test_missing_frontend_reports_install_instructions(monkeypatch, capsys):
    import search_tool.gui as gui
    monkeypatch.delenv('FINDMATCH_GUI_BINARY', raising=False)
    monkeypatch.setattr(gui.shutil, 'which', lambda _: None)
    monkeypatch.setattr(gui.Path, 'is_file', lambda _: False)
    assert main(['--gui']) == 1
    assert 'Nix flake package' in capsys.readouterr().err

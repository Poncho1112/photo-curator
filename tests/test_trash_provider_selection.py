import functools

from engine.delete import trash_providers
from engine.delete.trash_providers.macos import trash_to_macos_trash
from engine.delete.trash_providers.windows import trash_to_windows_bin


def test_selects_macos_provider_on_darwin(monkeypatch):
    monkeypatch.setattr(trash_providers.platform, "system", lambda: "Darwin")

    fn = trash_providers.select_default_trash_fn()

    assert fn is trash_to_macos_trash


def test_selects_windows_provider_with_quarantine_dir_under_app_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(trash_providers.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        trash_providers.AppPaths,
        "default",
        classmethod(lambda cls: trash_providers.AppPaths.from_root(tmp_path)),
    )

    fn = trash_providers.select_default_trash_fn()

    assert isinstance(fn, functools.partial)
    assert fn.func is trash_to_windows_bin
    assert fn.keywords["quarantine_dir"] == tmp_path / "trash"


def test_linux_fallback_delegates_to_send2trash_and_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(trash_providers.platform, "system", lambda: "Linux")
    calls = []
    fake_module = type(
        "FakeSend2Trash", (), {"send2trash": staticmethod(lambda path: calls.append(path))}
    )
    monkeypatch.setitem(__import__("sys").modules, "send2trash", fake_module)

    fn = trash_providers.select_default_trash_fn()
    target = tmp_path / "target.jpg"
    result = fn(target)

    assert calls == [target]
    assert result is None

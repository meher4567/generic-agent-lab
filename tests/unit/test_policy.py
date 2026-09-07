import os

import pytest

from agentlab.models import LabError
from agentlab.policy import list_files, read_file, source_hash


@pytest.mark.parametrize("path", ["/etc/passwd", "../../etc/passwd", "x/../../x", "x\\y", "x\x00y"])
def test_path_escape_rejected(tmp_path, path):
    with pytest.raises(LabError, match="relative"):
        read_file(tmp_path, path)


def test_symlinks_and_symlink_parents_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("private")
    (workspace / "link").symlink_to(secret)
    (workspace / "parent").symlink_to(tmp_path, target_is_directory=True)
    for name in ("link", "parent/secret"):
        with pytest.raises(LabError):
            read_file(workspace, name)
    with pytest.raises(LabError):
        list_files(workspace)


def test_fifo_and_hardlink_do_not_block_or_leak(tmp_path):
    original = tmp_path / "regular"
    original.write_text("value")
    os.link(original, tmp_path / "hardlink")
    os.mkfifo(tmp_path / "pipe")
    for name in ("hardlink", "pipe"):
        with pytest.raises(LabError):
            read_file(tmp_path, name)


def test_source_hash_detects_untracked_and_content_changes(tmp_path):
    (tmp_path / "a.py").write_text("a")
    initial = source_hash(tmp_path)
    (tmp_path / "untracked.py").write_text("b")
    assert source_hash(tmp_path) != initial
    (tmp_path / "untracked.py").unlink()
    assert source_hash(tmp_path) == initial
    (tmp_path / "a.py").write_text("modified")
    assert source_hash(tmp_path) != initial


def test_file_size_limit(tmp_path):
    (tmp_path / "large").write_text("0123456789")
    with pytest.raises(LabError):
        read_file(tmp_path, "large", limit=3)

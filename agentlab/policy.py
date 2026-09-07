from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

from .models import LabError

MAX_FILE = 20 * 1024 * 1024


def relative_parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
            or "\x00" in value or any(ord(c) < 32 for c in value)):
        raise LabError("PATH_DENIED", "Only relative paths inside this job are allowed")
    return path.parts


def read_file(root: Path, relative: str, *, limit: int = MAX_FILE) -> bytes:
    """Walk descriptors, refusing symlinks at every component (including final file)."""
    parts = relative_parts(relative)
    if not parts:
        raise LabError("PATH_DENIED", "A regular file is required")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise LabError("PATH_DENIED", "Expected one bounded, regular, unlinked file")
            with os.fdopen(os.dup(file_fd), "rb") as handle:
                content = handle.read(limit + 1)
            if len(content) > limit:
                raise LabError("OUTPUT_LIMIT", "File exceeds the read limit")
            return content
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise LabError("PATH_DENIED", "File is missing, inaccessible, or a link") from exc
    finally:
        os.close(fd)


def list_files(root: Path, relative: str = ".") -> list[str]:
    parts = relative_parts(relative)
    # Descriptor traversal keeps a directory replacement from redirecting the walk.
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    names: list[str] = []

    visited = 0

    def walk(current_fd: int, prefix: str, depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if visited > 2000 or depth > 64:
            raise LabError("OUTPUT_LIMIT", "Workspace directory count/depth exceeds the limit")
        for name in sorted(os.listdir(current_fd)):
            if name == ".git":
                continue
            info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            entry = f"{prefix}/{name}".lstrip("/")
            if stat.S_ISDIR(info.st_mode):
                nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=current_fd)
                try:
                    walk(nested, entry, depth + 1)
                finally:
                    os.close(nested)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                names.append(entry)
                if len(names) > 2000:
                    raise LabError("OUTPUT_LIMIT", "Workspace exceeds 2000 files")
            else:
                raise LabError("PATH_DENIED", "Links and special files are not accepted")
    try:
        for part in parts:
            nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nested
        walk(fd, "/".join(parts))
    except OSError as exc:
        raise LabError("PATH_DENIED", "Directory is missing, inaccessible, or a link") from exc
    finally:
        os.close(fd)
    return names


def source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    for name in list_files(root):
        content = read_file(root, name)
        total += len(content)
        if total > 128 * 1024 * 1024:
            raise LabError("OUTPUT_LIMIT", "Source exceeds 128 MiB")
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

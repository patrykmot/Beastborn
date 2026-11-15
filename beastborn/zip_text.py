"""Text files kept compressed in a zip archive: the one way the big generated files are saved and read.

Used for the training files (``train_ai_bot.bgn.zip``, ``train_ai_bot.benchmark.bgn.zip``, ``train_ai_bot.log.zip``)
so they stay small in git: BGN games get about 6x smaller, logs about 15x.

Format: a normal zip archive. The text (UTF-8) is cut into members of at most CHUNK_BYTES, ``part00000.txt``,
``part00001.txt``..., cut at line breaks; the members read in order give the text back. Any zip tool that knows
COMPRESSION (bzip2: 7-Zip, WinRAR, ``unzip``; not the Windows Explorer) can open it.

- Saving (``write_text``, ``append_text``, ``ZipTextWriter``) always writes a zip, whatever the file is called;
  ``zip_path`` adds ``.zip`` to a file name.
- Reading (``read_text``, ``TextFollower``) takes a zip, or plain UTF-8 text (files written before this module).
  ``ZipTextWriter`` also turns an old plain file it appends to into a zip.
- Appending only compresses the last, still growing member again (and adds new ones), so a long training run stays
  cheap: the members before it are copied byte for byte.
- Every save goes to ``<file>.tmp`` first, which then replaces the file. A reader (``tools/train_monitor.py``) never
  sees a half-written archive, and Ctrl+C in the middle of a save leaves the old file as it was.

Standard library only, like ``constant.py``: every layer and the tools may import it.
"""
from __future__ import annotations

import logging
import os
import time
import zipfile
import zlib
from pathlib import Path

ZIP_SUFFIX: str = ".zip"
COMPRESSION: int = zipfile.ZIP_BZIP2  # smallest of the fast ones (ZIP_DEFLATED: ~1.6x bigger, but Explorer opens it)
CHUNK_BYTES: int = 1 << 20  # text per member: only the last member is compressed again when text is appended
FLUSH_SECONDS: float = 5.0  # ZipTextWriter: buffered text is saved at most this long after it was written
MEMBER: str = "part{:05d}.txt"
TMP_SUFFIX: str = ".tmp"
REPLACE_TRIES: int = 40  # Windows: the file cannot be replaced while a reader has it open, so try again a while
REPLACE_WAIT: float = 0.05  # seconds between two tries


def zip_path(path: Path | str) -> Path:
    """``path`` with ``.zip`` added at the end (unchanged when it already ends so): ``a.bgn`` -> ``a.bgn.zip``."""
    path = Path(path)
    return path if path.name.lower().endswith(ZIP_SUFFIX) else path.with_name(path.name + ZIP_SUFFIX)


def strip_zip(path: Path | str) -> Path:
    """``path`` without a final ``.zip``: ``a.bgn.zip`` -> ``a.bgn`` (the name of what is inside)."""
    path = Path(path)
    return path.with_name(path.name[:-len(ZIP_SUFFIX)]) if path.name.lower().endswith(ZIP_SUFFIX) else path


def is_zip(path: Path | str) -> bool:
    """The file exists and is a zip archive (by its content, not its name)."""
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def read_text(path: Path | str, errors: str = "strict") -> str:
    """The whole text of ``path``: a zip written by this module, or plain UTF-8 text. ``FileNotFoundError`` if it
    does not exist."""
    path = Path(path)
    if not is_zip(path):
        return path.read_text(encoding="utf-8", errors=errors)
    with zipfile.ZipFile(path) as archive:
        return b"".join(archive.read(info) for info in _members(archive)).decode("utf-8", errors=errors)


def write_text(path: Path | str, text: str) -> None:
    """A new zip with ``text`` (an existing file is replaced)."""
    with ZipTextWriter(path, new=True) as writer:
        writer.write(text)


def append_text(path: Path | str, text: str) -> None:
    """Add ``text`` at the end of ``path`` (created if needed). For many appends keep a ``ZipTextWriter`` open."""
    with ZipTextWriter(path) as writer:
        writer.write(text)


class ZipTextWriter:
    """Appends text to a zipped text file, kept open for a whole run (a training run, a benchmark, a log).

    ``write`` only buffers the text; it is saved when FLUSH_SECONDS have passed since the last save (checked at each
    ``write``), on ``flush`` and on ``close``. A save that fails (``OSError``, e.g. the disk is full) keeps the text
    in the buffer for the next one, so nothing is lost while the writer lives.
    ``new``: start an empty file (saved on the first ``flush``); otherwise the text is added to what is there
    (``OSError`` when it cannot be read).
    """

    def __init__(self, path: Path | str, new: bool = False, flush_seconds: float = FLUSH_SECONDS) -> None:
        self.path: Path = Path(path)
        self.flush_seconds: float = flush_seconds
        self._pending: list[str] = []  # written, not saved yet
        self._dirty: bool = new  # save even without pending text (a new, empty file)
        self._saved_at: float = time.monotonic()
        self._closed: bool = False
        # the archive on disk: members that are full (copied as they are) and the text of the growing last one
        self._full: list[zipfile.ZipInfo] = []
        self._full_end: int = 0  # bytes of the file taken by the full members (they come first)
        self._tail: bytes = b""
        if not new:
            try:
                self._load()
            except (zipfile.BadZipFile, EOFError) as error:  # one OSError for every file that cannot be read
                raise OSError(f"{self.path} is not a readable zip: {error}") from error

    # ---------------------------------------------------------- public
    def write(self, text: str) -> None:
        if self._closed:
            raise ValueError(f"{self.path} is closed")
        if text:
            self._pending.append(text)
        if time.monotonic() - self._saved_at >= self.flush_seconds:
            self.flush()

    def flush(self) -> None:
        """Save the buffered text now."""
        if not self._pending and not self._dirty:
            return
        data: bytes = self._tail + "".join(self._pending).encode("utf-8")
        chunks: list[bytes] = _split(data)
        tmp: Path = self.path.with_name(self.path.name + TMP_SUFFIX)
        with open(tmp, "wb") as out:
            if self._full_end:
                with open(self.path, "rb") as old:  # the full members, byte for byte
                    _copy(old, out, self._full_end)
            with zipfile.ZipFile(out, "w", compression=COMPRESSION) as archive:
                archive.filelist.extend(self._full)  # listed in the new central directory, at the same offsets
                archive.NameToInfo.update((info.filename, info) for info in self._full)
                written: list[zipfile.ZipInfo] = []
                for chunk in chunks:
                    archive.writestr(MEMBER.format(len(self._full) + len(written)), chunk)
                    written.append(archive.filelist[-1])
                    end: int = archive.start_dir  # where the next member starts
                    if len(written) == len(chunks) - 1:
                        full_end = end
        _replace(tmp, self.path)
        # the file is saved: the members before the last one are full now and never compressed again
        if len(chunks) > 1:
            self._full.extend(written[:-1])
            self._full_end = full_end
        self._tail = chunks[-1] if chunks else b""
        self._pending, self._dirty, self._saved_at = [], False, time.monotonic()

    def close(self) -> None:
        if not self._closed:
            self.flush()
            self._closed = True

    def __enter__(self) -> ZipTextWriter:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------------------------------------------------- the file as it is
    def _load(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._dirty = True  # create it on the first flush
            return
        if not is_zip(self.path):  # a plain text file from before: its text goes into the zip, line breaks as "\n"
            self._tail = self.path.read_text(encoding="utf-8").encode("utf-8")  # like read_text (\r\n -> \n)
            self._dirty = True
            return
        with zipfile.ZipFile(self.path) as archive:
            members: list[zipfile.ZipInfo] = _members(archive)
            if len(members) != len(archive.infolist()) or any(
                    info.filename != MEMBER.format(i) for i, info in enumerate(members)):
                # not written by this module: rewrite it in our layout
                self._tail = b"".join(archive.read(info) for info in members)
                self._dirty = True
                return
            if members and members[-1].file_size < CHUNK_BYTES:  # the growing last member
                self._tail = archive.read(members[-1])
                self._full, self._full_end = members[:-1], members[-1].header_offset
            else:
                self._full, self._full_end = members, archive.start_dir


class ZipLogHandler(logging.Handler):
    """Like ``logging.FileHandler(path, mode="a")``, but the log is zipped (``ZipTextWriter``: saved every
    FLUSH_SECONDS while records come, and on close). ``new``: start an empty log."""

    def __init__(self, path: Path | str, new: bool = False) -> None:
        super().__init__()
        self.writer: ZipTextWriter = ZipTextWriter(path, new=new)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.writer.write(self.format(record) + "\n")
        except OSError:
            pass  # the line stays in the buffer: saved with a later record or on close
        except Exception:  # noqa: BLE001  (logging must never stop the program)
            self.handleError(record)

    def flush(self) -> None:
        try:
            self.writer.flush()
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.writer.close()
        except OSError:
            pass
        finally:
            super().close()


class TextFollower:
    """Follows a text file while another program writes it (a zip of this module, or plain text).

    ``read()`` gives the bytes (UTF-8) added since the previous call. A file that was replaced by a new one (a new
    training run starts the file again) is read again from its start; ``read`` then says so.
    For a zip only the members that changed are decompressed: the growing last one and the new ones.
    """

    HEAD: int = 64  # plain files: first bytes, compared to notice that the file was replaced

    def __init__(self, path: Path | str) -> None:
        self.path: Path = Path(path)
        self._switched: bool = False
        self.restart()

    def restart(self) -> None:
        """Forget what was read: the next ``read`` starts at the beginning."""
        self._stamp: tuple[int, int, int] | None = None  # (mtime, size, inode) at the last read
        self._zipped: bool | None = None  # the file was a zip at the last read (None = not read yet)
        self._members: list[tuple[str, int, int]] = []  # zip: (name, size, CRC-32) of the members read
        self._offset: int = 0  # plain: bytes read
        self._head: bytes = b""

    def read(self) -> tuple[bool, bytes]:
        """(True when the file was started again and is read from its start, the new bytes). Unchanged or not
        readable right now (missing, being replaced): ``(False, b"")``, and the next call tries again."""
        try:
            stat = self.path.stat()
            stamp: tuple[int, int, int] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)  # a replaced file: new ino
            if stamp == self._stamp:
                return False, b""
            zipped: bool = is_zip(self.path)
            if self._zipped is not None and zipped != self._zipped:  # e.g. an old plain file was zipped
                self.restart()
                self._switched = True  # said by the next read that works
            restarted, data = self._read_zip() if zipped else self._read_plain()
        except (OSError, EOFError, zipfile.BadZipFile, ValueError):
            return False, b""
        restarted, self._switched = restarted or self._switched, False
        self._stamp, self._zipped = stamp, zipped
        return restarted, data

    def _read_zip(self) -> tuple[bool, bytes]:
        with zipfile.ZipFile(self.path) as archive:
            members: list[zipfile.ZipInfo] = _members(archive)
            known: list[tuple[str, int, int]] = self._members
            # the members read before must still be there, all but the last one unchanged; the last one may have grown
            same: bool = len(members) >= len(known) and all(
                _key(members[i]) == known[i] for i in range(len(known) - 1))
            start: int = 0
            parts: list[bytes] = []
            if same and known:
                last: zipfile.ZipInfo = members[len(known) - 1]
                name, size, crc = known[-1]
                if _key(last) == known[-1]:
                    start = len(known)
                elif last.filename == name and last.file_size >= size:
                    data: bytes = archive.read(last)
                    if zlib.crc32(data[:size]) == crc:
                        start = len(known)
                        parts.append(data[size:])
                    else:
                        same = False
                else:
                    same = False
            if not same:
                start, parts = 0, []
            parts.extend(archive.read(info) for info in members[start:])
        self._members = [_key(info) for info in members]
        return not same and bool(known), b"".join(parts)

    def _read_plain(self) -> tuple[bool, bytes]:
        with open(self.path, "rb") as file:
            head: bytes = file.read(self.HEAD)
            size: int = file.seek(0, 2)
            restarted: bool = size < self._offset or not head.startswith(self._head)
            offset: int = 0 if restarted else self._offset
            file.seek(offset)
            data: bytes = file.read(size - offset)
        self._offset, self._head = offset + len(data), head
        return restarted, data


# ---------------------------------------------------------------------- helpers
def _members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The text members, in archive order."""
    return [info for info in archive.infolist() if not info.is_dir()]


def _key(info: zipfile.ZipInfo) -> tuple[str, int, int]:
    return info.filename, info.file_size, info.CRC


def _split(data: bytes) -> list[bytes]:
    """``data`` in members of at most CHUNK_BYTES, cut after a line break (or, in a very long line, before a UTF-8
    character). The last one is the growing one; ``[]`` for no data."""
    chunks: list[bytes] = []
    start: int = 0
    while len(data) - start > CHUNK_BYTES:
        end: int = data.rfind(b"\n", start, start + CHUNK_BYTES) + 1
        if end <= start:
            end = start + CHUNK_BYTES
            while end > start + 1 and data[end] & 0xC0 == 0x80:  # a UTF-8 continuation byte: not here
                end -= 1
        chunks.append(data[start:end])
        start = end
    if start < len(data):
        chunks.append(data[start:])
    return chunks


def _copy(source, target, size: int) -> None:
    while size > 0:
        block: bytes = source.read(min(size, 1 << 20))
        if not block:
            raise OSError("the file got shorter while it was copied")
        target.write(block)
        size -= len(block)


def _replace(tmp: Path, path: Path) -> None:
    """``os.replace`` that waits a little while another program (a reader, on Windows) has ``path`` open."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT)


__all__ = ["ZIP_SUFFIX", "COMPRESSION", "CHUNK_BYTES", "FLUSH_SECONDS", "zip_path", "strip_zip", "is_zip",
           "read_text", "write_text", "append_text", "ZipTextWriter", "ZipLogHandler", "TextFollower"]

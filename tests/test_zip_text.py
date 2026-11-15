"""Zipped text files (beastborn/zip_text.py): the training games and logs are saved and read through it."""
import ast
import logging
import zipfile
from pathlib import Path

import pytest

from beastborn import zip_text
from beastborn.zip_text import TextFollower, ZipLogHandler, ZipTextWriter


@pytest.fixture
def small_chunks(monkeypatch):
    """Members of 100 bytes, so a few lines already need several of them."""
    monkeypatch.setattr(zip_text, "CHUNK_BYTES", 100)


def lines(first: int, last: int) -> str:
    return "".join(f"line {i:04d} zażółć\n" for i in range(first, last))  # not only ASCII


def test_names():
    assert zip_text.zip_path("a/train_ai_bot.bgn") == Path("a/train_ai_bot.bgn.zip")
    assert zip_text.zip_path("train_ai_bot.bgn.zip") == Path("train_ai_bot.bgn.zip")
    assert zip_text.strip_zip("a/train_ai_bot.log.zip") == Path("a/train_ai_bot.log")
    assert zip_text.strip_zip("train_ai_bot.csv") == Path("train_ai_bot.csv")


def test_write_append_read(tmp_path, small_chunks):
    path = tmp_path / "games.bgn.zip"
    zip_text.write_text(path, lines(0, 10))
    assert zip_text.is_zip(path) and zip_text.read_text(path) == lines(0, 10)
    zip_text.append_text(path, lines(10, 25))
    zip_text.append_text(path, "")
    assert zip_text.read_text(path) == lines(0, 25)
    with zipfile.ZipFile(path) as archive:  # a normal zip: any tool reads it
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == [f"part{i:05d}.txt" for i in range(len(names))] and len(names) > 3
        assert all(info.compress_type == zip_text.COMPRESSION for info in archive.infolist())
        assert all(info.file_size <= 100 for info in archive.infolist())
        assert all(archive.read(name).endswith(b"\n") for name in names)  # cut at line breaks
    zip_text.write_text(path, "new")  # replaced
    assert zip_text.read_text(path) == "new"
    assert not (tmp_path / "games.bgn.zip.tmp").exists()


def test_any_name_is_saved_as_zip(tmp_path):
    path = tmp_path / "train.bgn"
    zip_text.write_text(path, "abc\n")
    assert zip_text.is_zip(path) and zip_text.read_text(path) == "abc\n"


def test_long_line_is_cut_between_characters(tmp_path, small_chunks):
    path = tmp_path / "long.zip"
    text = "ż" * 250 + "\n"  # 2 bytes per character, no line break for 500 bytes
    zip_text.write_text(path, text)
    assert zip_text.read_text(path) == text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_reads_and_converts_an_old_plain_file(tmp_path, newline):
    path = tmp_path / "train_ai_bot.bgn"
    path.write_text(lines(0, 3), encoding="utf-8", newline=newline)  # "\r\n": a file written in text mode on Windows
    assert zip_text.read_text(path) == lines(0, 3)
    zip_text.append_text(path, lines(3, 5))  # the old text goes into the zip
    assert zip_text.is_zip(path) and zip_text.read_text(path) == lines(0, 5)


def test_writer_buffers_and_saves(tmp_path, small_chunks):
    path = tmp_path / "train.bgn.zip"
    zip_text.write_text(path, "old\n")
    writer = ZipTextWriter(path, new=True, flush_seconds=3600)
    writer.flush()  # a new run: the file is emptied now
    assert zip_text.read_text(path) == ""
    for i in range(30):
        writer.write(lines(i, i + 1))
    assert zip_text.read_text(path) == ""  # not saved yet
    writer.flush()
    assert zip_text.read_text(path) == lines(0, 30)
    writer.write(lines(30, 31))
    writer.close()
    assert zip_text.read_text(path) == lines(0, 31)
    with pytest.raises(ValueError):
        writer.write("x")
    with ZipTextWriter(path, flush_seconds=0) as again:  # appends to what is there, saved at every write
        again.write(lines(31, 40))
        assert zip_text.read_text(path) == lines(0, 40)


def test_writer_keeps_the_text_when_a_save_fails(tmp_path, monkeypatch):
    path = tmp_path / "train.bgn.zip"
    writer = ZipTextWriter(path, new=True, flush_seconds=3600)
    writer.write("one\n")

    def locked(*_args):
        raise PermissionError("locked")

    monkeypatch.setattr(zip_text.os, "replace", locked)
    monkeypatch.setattr(zip_text, "REPLACE_WAIT", 0)
    with pytest.raises(OSError):
        writer.flush()
    monkeypatch.undo()
    writer.write("two\n")
    writer.close()
    assert zip_text.read_text(path) == "one\ntwo\n"


def test_broken_zip_cannot_be_appended_to(tmp_path):
    path = tmp_path / "broken.zip"
    zip_text.write_text(path, "abc\n" * 1000)
    data = bytearray(path.read_bytes())
    data[60:70] = b"x" * 10  # inside the compressed data
    path.write_bytes(bytes(data))
    with pytest.raises(OSError):
        ZipTextWriter(path)


def test_log_handler(tmp_path):
    path = tmp_path / "train_ai_bot.log.zip"
    log = logging.getLogger("test_zip_text")
    log.setLevel(logging.INFO)
    for run in range(2):  # appended to, like logging.FileHandler
        handler = ZipLogHandler(path)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        try:
            log.info("run %d", run)
        finally:
            log.removeHandler(handler)
            handler.close()
    assert zip_text.read_text(path) == "run 0\nrun 1\n"


def test_follower_reads_only_what_is_new(tmp_path, small_chunks):
    path = tmp_path / "train.bgn.zip"
    follower = TextFollower(path)
    assert follower.read() == (False, b"")  # no file yet
    zip_text.write_text(path, lines(0, 2))
    assert follower.read() == (False, lines(0, 2).encode())
    assert follower.read() == (False, b"")
    with ZipTextWriter(path, flush_seconds=0) as writer:
        text = ""
        for i in range(2, 20):  # the last member grows, then new ones come
            writer.write(lines(i, i + 1))
            restarted, data = follower.read()
            assert not restarted
            text += data.decode()
        assert text == lines(2, 20)
    zip_text.write_text(path, lines(50, 60))  # a new run: started again, longer than what was read
    assert follower.read() == (True, lines(50, 60).encode())
    zip_text.write_text(path, "")
    assert follower.read() == (True, b"")


def test_follower_reads_plain_files(tmp_path):
    path = tmp_path / "train_ai_bot.csv"
    follower = TextFollower(path)
    path.write_bytes(b"a,b\n1,")  # bytes: the same on Windows (text mode would write \r\n)
    assert follower.read() == (False, b"a,b\n1,")
    with open(path, "ab") as file:
        file.write(b"2\n")
    assert follower.read() == (False, b"2\n")
    path.write_bytes(b"c\n")  # shorter: replaced
    assert follower.read() == (True, b"c\n")


def test_zip_text_is_a_leaf_module():
    """Every layer and the tools import it, so it must not import beastborn itself."""
    tree = ast.parse(Path(zip_text.__file__).read_text(encoding="utf-8"))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(m.split(".")[0] == "beastborn" for m in modules), modules

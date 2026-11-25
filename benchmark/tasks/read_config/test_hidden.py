import builtins

import pytest
from solution import read_config


def write(tmp_path, text):
    path = tmp_path / "app.cfg"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parses_pairs(tmp_path):
    path = write(tmp_path, "# comment\n\nhost = example.com\nport=8080\n")
    assert read_config(path) == {"host": "example.com", "port": "8080"}


def test_value_may_contain_equals(tmp_path):
    assert read_config(write(tmp_path, "url = a=b\n")) == {"url": "a=b"}


def test_malformed_line_reports_line_number(tmp_path):
    path = write(tmp_path, "a=1\nbroken\n")
    with pytest.raises(Exception, match="2"):
        read_config(path)


def test_file_closed_even_on_error(tmp_path, monkeypatch):
    path = write(tmp_path, "a=1\nbroken\n")
    opened = []
    real_open = builtins.open

    def tracking_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(builtins, "open", tracking_open)
    with pytest.raises(Exception):
        read_config(path)
    assert all(handle.closed for handle in opened)

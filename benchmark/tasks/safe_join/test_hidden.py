import os
import pytest
from solution import safe_join


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def test_simple_relative_path(base):
    assert os.path.realpath(safe_join(str(base), "a/b.txt")) == str((base / "a" / "b.txt").resolve())


def test_dotdot_inside_base_is_allowed(base):
    assert os.path.realpath(safe_join(str(base), "a/../b.txt")) == str((base / "b.txt").resolve())


def test_parent_traversal_rejected(base):
    with pytest.raises(Exception):
        safe_join(str(base), "../secret.txt")


def test_absolute_path_rejected(base):
    with pytest.raises(Exception):
        safe_join(str(base), os.path.abspath(os.sep + "etc"))


def test_sibling_with_same_prefix_rejected(base):
    # base is ".../data"; ".../data2/x" starts with the same characters but is outside it
    with pytest.raises(Exception):
        safe_join(str(base), "../data2/x.txt")

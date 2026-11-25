from solution import render_table


def test_basic_table():
    rows = [{"name": "Ann", "age": "30"}, {"name": "Bob", "age": "25"}]
    assert render_table(rows) == ("<table><tr><th>name</th><th>age</th></tr>"
                                  "<tr><td>Ann</td><td>30</td></tr><tr><td>Bob</td><td>25</td></tr></table>")


def test_empty():
    assert render_table([]) == "<table></table>"


def test_missing_key_is_empty_cell():
    rows = [{"a": "1", "b": "2"}, {"a": "3"}]
    assert "<tr><td>3</td><td></td></tr>" in render_table(rows)


def test_values_are_escaped():
    html = render_table([{"comment": "<script>alert(1)</script> & more"}])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp; more" in html


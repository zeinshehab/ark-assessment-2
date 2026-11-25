from html import escape


def render_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<table></table>"
    headers = list(rows[0])
    parts = ["<table><tr>", *(f"<th>{escape(h)}</th>" for h in headers), "</tr>"]
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{escape(str(row.get(h, '')))}</td>" for h in headers) + "</tr>")
    parts.append("</table>")
    return "".join(parts)

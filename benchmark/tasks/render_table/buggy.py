def render_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<table></table>"
    headers = list(rows[0])
    html = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    for row in rows:
        html += "<tr>" + "".join(f"<td>{row.get(h, '')}</td>" for h in headers) + "</tr>"
    return html + "</table>"

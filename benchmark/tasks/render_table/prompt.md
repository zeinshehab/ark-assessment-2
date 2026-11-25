For our admin dashboard, write a function called `render_table` that turns a list of rows into an HTML table. Each row
is a dictionary from column name to value, and the column names come from the first row. Use `<table>`, a header row
of `<th>` cells and then one `<tr>` of `<td>` cells per row, with no spaces or line breaks between the tags. The values
are comments typed in by our users. If a later row is missing a column, leave that cell empty. An empty list gives
`<table></table>`.

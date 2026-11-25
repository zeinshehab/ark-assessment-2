Our app keeps its users in a SQLite database, in a table called `users` with the columns `id`, `username` and `email`.
Write a function called `find_user` that takes an open database connection and the username someone typed into our
website's login form, and gives back that user's id, username and email as a dictionary with those three keys, or
`None` if there is no such user.

Our app reads its settings from a text file where each line looks like `name=value`. Write a function called
`read_config` that takes the path of the file and gives back the settings as a dictionary. Empty lines and lines
starting with `#` should be skipped, and spaces around names and values should be ignored. If a line doesn't follow
the `name=value` format, raise an error that says which line number is wrong.

Users of our file-sharing site download files by typing a path such as `reports/2024/q1.pdf`. Write a function called
`safe_join` that takes our storage folder and the path the user typed, and gives back the full path of that file
inside the storage folder. Users must never be able to reach anything outside the storage folder; if they try,
raise an error. The file doesn't have to exist yet.

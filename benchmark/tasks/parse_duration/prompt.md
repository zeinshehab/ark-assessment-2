People type durations into our timer app, like "1h30m", "45s" or "1h 30m 10s": hours, minutes and seconds, in upper
or lower case, with or without spaces. Write a function called `parse_duration` that takes what they typed and gives
back the total number of seconds. Each unit can be used at most once, in the order hours, minutes, seconds. Anything
else, such as empty input, an unknown unit or a number without a unit, should cause an error.

# Python Coding Standard (review reference)

The reviewer cites these sections by ID. Sections are retrieved on demand by keyword, never pasted in whole.

## CS-1: Validate inputs and handle edge cases
Every public function must define its behaviour for empty input, `None` where allowed, zero, negative numbers and boundary values. Invalid arguments raise `ValueError` (or `TypeError` for wrong types) with a clear message. Never return a silently wrong result for an input the specification calls invalid.
Keywords: empty, none, zero, negative, boundary, valueerror, typeerror, raise, invalid, validate, edge, len, if not

## CS-2: Ranges, indices and off-by-one errors
Check every `range()`, slice and index arithmetic against the smallest and largest valid input. State whether bounds are inclusive or exclusive. 1-indexed user inputs (pages, line numbers) must be converted explicitly. Prefer slicing over manual index loops.
Keywords: range, slice, index, page, offset, window, start, end, inclusive, exclusive, len, off-by-one, enumerate

## CS-3: Release resources deterministically
Files, sockets, database connections and locks must be acquired with a `with` statement (or closed in `finally`), so they are released even when an exception is raised. Do not rely on garbage collection to close resources.
Keywords: open, close, with, file, socket, connection, cursor, finally, resource, leak, lock, sqlite3, read, readlines

## CS-4: Never build queries by string formatting
SQL must use parameterised queries (`?` placeholders with a parameter tuple). f-strings, `%` formatting, `.format()` or concatenation of user data into SQL are forbidden. The same rule applies to any interpreted language: shell, HTML, regular expressions.
Keywords: sql, select, insert, execute, cursor, query, sqlite3, f-string, format, injection, s608, where, parameter

## CS-5: Untrusted paths, shells and markup
User-supplied paths must be resolved and checked to stay inside the intended base directory (reject `..` traversal and absolute paths). Never use `shell=True`, `os.system` or `eval`/`exec` on user data. Text inserted into HTML must be escaped with `html.escape`.
Keywords: path, os.path, pathlib, resolve, join, traversal, absolute, shell, subprocess, eval, exec, html, escape, markup, xss, s603, s602, s307

## CS-6: Exceptions
Catch specific exceptions only. No bare `except:` and no `except Exception: pass`. Do not swallow errors that the caller needs to see. Error messages must say what was wrong.
Keywords: except, exception, try, raise, error, bare, pass, e722, b001, s110

## CS-7: Numbers and money
Do not use binary floats for money or exact decimal arithmetic. Use `decimal.Decimal` (constructed from strings) or integer cents, and state the rounding mode. Compare floats with a tolerance.
Keywords: float, decimal, money, cents, round, rounding, currency, precision, sum, divide, quantize, total

## CS-8: No hidden shared state
No mutable default arguments. Functions must not mutate their input arguments unless the specification says so. Avoid module-level mutable state.
Keywords: default, mutable, list, dict, append, sort, mutate, global, b006, copy, in-place

## CS-9: Tests
Tests must cover the happy path, every documented edge case, and every documented error (`pytest.raises`). Each test checks one behaviour and has a descriptive name. Tests must not depend on network, wall-clock time or execution order.
Keywords: test, pytest, assert, raises, coverage, edge, fixture, parametrize, missing test

## CS-10: Simplicity over cleverness
Prefer the simplest correct implementation using the standard library. Do not add features, parameters, abstractions or dependencies the specification did not ask for. A change that makes the code longer must make it more correct, safer or clearer. Readability findings are advisory (minor/nit), never blocking.
Keywords: simple, complex, readability, refactor, abstraction, class, helper, style, naming, docstring, type hint, unnecessary

## CS-11: Text parsing
Parsers must accept the documented format exactly: handle surrounding whitespace, case rules, empty strings and malformed input explicitly, and raise `ValueError` for malformed input rather than returning partial results. Use anchored regular expressions (`fullmatch`).
Keywords: parse, string, strip, split, regex, re, match, fullmatch, format, unicode, whitespace, malformed, lower, upper

## CS-12: Dates and times
Use the `datetime` module; never do date arithmetic by hand. State whether date ranges are inclusive. Remember weekday numbering (`Monday == 0`).
Keywords: date, datetime, timedelta, weekday, day, month, year, iso, fromisoformat, calendar, business

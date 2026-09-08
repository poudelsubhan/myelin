"""Strict CSV intake for the existing serial batch scheduler."""

import csv
import io


def parse_csv(text, fields, max_rows=5):
    if len(text.encode()) > 100_000:
        raise ValueError("CSV is too large")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV needs unique column names")
    if set(reader.fieldnames) != set(fields):
        raise ValueError("CSV columns must exactly match the task input fields")
    rows = list(reader)
    if not 1 <= len(rows) <= max_rows or any(None in row or None in row.values() for row in rows):
        raise ValueError("CSV needs one to five complete rows")
    return rows

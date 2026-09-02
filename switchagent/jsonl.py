"""Bounded reads over big JSONL transcripts.

A transcript can be tens of megabytes. Resolving which session to hand over
only ever touches a slice of a file; the one full pass happens when the handoff
document is built.
"""

import json


def iter_records(path):
    """Every decodable record in a JSONL file, in order."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def head_records(path, limit_bytes=65536):
    """Records from the first `limit_bytes` of the file."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        chunk = handle.read(limit_bytes)
    return _decode_lines(chunk.split("\n")[:-1] if "\n" in chunk else [])


def tail_records(path, limit_bytes=131072):
    """Records from the last `limit_bytes` of the file.

    The first line of the slice is usually cut in half, so it is dropped.
    """
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - limit_bytes)
        handle.seek(start)
        chunk = handle.read().decode("utf-8", errors="replace")
    lines = chunk.split("\n")
    if start > 0 and lines:
        lines = lines[1:]
    return _decode_lines(lines)


def _decode_lines(lines):
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records

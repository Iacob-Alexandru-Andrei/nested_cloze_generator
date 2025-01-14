#!/usr/bin/env python3

import sys
import re
import difflib

# Regex to detect only the cloze markup tokens:
#   - opener: {{c\d+::
#   - closer: }}
TOKEN_REGEX = re.compile(r"(\{\{c\d+::|\}\})")

def normalize_clozes(original_text: str) -> str:
    """
    Removes only the cloze markup (e.g. '{{c10::' or '}}') from the text,
    leaving the inside content intact.

    Example:
      '{{c10::Hello}}' => 'Hello'
      'Nested {{c5::abc {{c2::def}} ghi}}' => 'Nested abc def ghi'
    """
    result = []
    stack = []
    last_pos = 0

    for match in TOKEN_REGEX.finditer(original_text):
        start, end = match.start(), match.end()
        token = match.group(0)

        # Add all literal text prior to this token
        result.append(original_text[last_pos:start])

        if token.startswith("{{c"):
            # opener
            stack.append(True)
        else:
            # closer
            if stack:
                stack.pop()
            else:
                # stray '}}' => treat it as text
                result.append(token)

        last_pos = end

    # Add any remaining text after the last token
    result.append(original_text[last_pos:])
    return "".join(result)

def compare_before_after(before: str, after: str):
    """
    Return (is_same, diff_text):
      - is_same = True if they differ only in cloze markup
      - diff_text = a unified diff if they differ in real text
    """
    nb = normalize_clozes(before)
    na = normalize_clozes(after)

    if nb == na:
        return True, ""

    diff = difflib.unified_diff(
        nb.splitlines(),
        na.splitlines(),
        fromfile="before (normalized)",
        tofile="after (normalized)",
        lineterm=""
    )
    diff_text = "\n".join(diff)
    return False, diff_text

def parse_note_blocks(lines):
    """
    Generator function that yields (note_id, before_text, after_text).
    We expect each block to look like:

    Note ID: <number>
      Field: <some field>

    Before:
      <some lines>

    After:
      <some lines>

    (blank lines in between)
    """
    note_id = None
    before_text = []
    after_text = []

    reading_before = False
    reading_after = False

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")

        # Detect "Note ID: <number>"
        if line.startswith("Note ID: "):
            # If we already had a block in progress, yield it
            if note_id is not None:
                yield (note_id, "\n".join(before_text), "\n".join(after_text))

            # Start a new block
            note_id = line.split("Note ID: ", 1)[1].strip()
            before_text = []
            after_text = []
            reading_before = False
            reading_after = False
            i += 1
            continue

        # Detect "Before:"
        if line.strip() == "Before:":
            reading_before = True
            reading_after = False
            i += 1
            continue

        # Detect "After:"
        if line.strip() == "After:":
            reading_before = False
            reading_after = True
            i += 1
            continue

        # If we're reading the 'Before' text
        if reading_before:
            before_text.append(line)

        # If we're reading the 'After' text
        elif reading_after:
            after_text.append(line)

        i += 1

    # End of file => yield the last block if any
    if note_id is not None:
        yield (note_id, "\n".join(before_text), "\n".join(after_text))

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_cloze_differences.py <modified_notes.txt>")
        sys.exit(1)

    filename = sys.argv[1]
    with open(filename, "r", encoding="utf-8") as f:
        lines = f.readlines()

    blocks = list(parse_note_blocks(lines))
    if not blocks:
        print("No 'Note ID:' blocks found.")
        sys.exit(0)

    block_count = 0
    for (nid, before, after) in blocks:
        block_count += 1
        before = before.strip("\n")
        after = after.strip("\n")

        # If both are empty, skip
        if not before and not after:
            continue

        is_same, diff_text = compare_before_after(before, after)

        print(f"=== Note ID: {nid} (Block {block_count}) ===")
        if is_same:
            print("  => No difference except for clozes.")
        else:
            print("  => Content differs beyond just cloze markup!")
            if diff_text.strip():
                print("----- Diff (Normalized) -----")
                print(diff_text)
                print("----------------------------")
        print()

if __name__ == "__main__":
    main()

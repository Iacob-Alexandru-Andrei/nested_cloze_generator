#!/usr/bin/env python3
import requests
import re
import os
from tqdm import tqdm

###############################################################################
# Configuration
###############################################################################
ANKI_CONNECT_URL = "http://localhost:8765"
CLOZE_RE = r"\{\{c\d+::[\s\S]*?\}\}"
MAX_BASE_INDEX = 5  # We allow c1..c5 as clozes; if base index is >5, remove markup.

def invoke(action, **params):
    """
    Helper function to send a JSON request to AnkiConnect.
    """
    payload = {"action": action, "version": 6, "params": params}
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    if response.get("error"):
        raise Exception(f"AnkiConnect error: {response['error']}")
    return response["result"]

def should_skip_field(field_name: str) -> bool:
    """
    Return True if we should not process this field (i.e. if its name is "Occlusion").
    """
    return field_name == "Occlusion"

def find_clozes_in_text(text: str):
    """
    Return a list of all top-level cloze matches in 'text' (which might contain nested clozes).
    """
    return re.findall(CLOZE_RE, text)

TOKEN_REGEX = re.compile(r'(\{\{c(\d+)::|\}\})')
MAX_BASE_INDEX = 5
TOKEN_PATTERN = re.compile(r"(\{\{c(\d+)::|\}\})", flags=re.DOTALL)

def remove_all_trailing_hints(s: str) -> str:
    """
    Remove *all* trailing '::someHint' segments from the end of s,
    as long as 'someHint' does not contain another colon.
    
    For example:
      "some text::h1::h2" -> "some text"
      "some text::h1:extra" -> (unchanged, because 'h1:extra' has a colon)
      "some text" -> unchanged
    """
    while True:
        new_s = re.sub(r'::[^:\}]+$', '', s)
        if new_s == s:
            break
        s = new_s
    return s

def strip_nested_baseindex_hint(field_text: str) -> str:
    """
    Stack-based approach that ensures:
      1) We unify *all* nested clozes into one 'base' index (the smallest encountered).
      2) If base index <= 9, we output 'base' markup exactly once;
         if base > 9, remove all markup.
      3) We preserve all literal text in between.
      4) Only the snippet that physically writes markup (the real base snippet)
         can keep any trailing hints (like '::hint1::hint2').
         If a snippet is NOT the base (not is_written), we remove *all* trailing hints.

    Examples:
      - {{c1::{{c5::{{c6::{{c7::finite}}}}}}}} => base=1 => {{c1::finite}}
      - {{c10::Hello::outer}} => base=10 => "Hello"
      - {{c3::{{c3::stuff::h1::h2}}}} => repeated c3 => => {{c3::stuff::h1::h2}}
      - {{c7::{{c2::nested::hint2::extra}}::outerHint}} => base=2 => => {{c2::nested::hint2::extra}}
        (outerHint is removed)
    """

    result = []
    last_pos = 0

    # Stack of (base_idx, is_written)
    #  - base_idx = the min index so far in this snippet chain
    #  - is_written = True => physically write this snippet's markup
    stack = []

    for match in TOKEN_PATTERN.finditer(field_text):
        start = match.start()
        end = match.end()
        token = match.group(0)       # e.g. "{{c7::" or "}}"
        idx_str = match.group(2)     # e.g. "7" if opener, else None

        # Copy literal text from [last_pos..start)
        result.append(field_text[last_pos:start])

        if token.startswith("{{c"):
            # It's an opener: {{cX::
            x = int(idx_str)
            if not stack:
                # No parent snippet => base = x
                base = x
            else:
                parent_base, _ = stack[-1]
                base = min(parent_base, x)

            # If base <= 9, we only physically write markup if x == base
            if base <= MAX_BASE_INDEX and x == base:
                stack.append((base, True))
                # Actually write the opener
                result.append(f"{{{{c{base}::")
            else:
                stack.append((base, False))

        else:
            # It's a closer "}}"
            if stack:
                base_idx, is_written = stack.pop()

                if is_written and base_idx <= MAX_BASE_INDEX:
                    # physically write the closer
                    result.append("}}")
                else:
                    # remove all trailing hints from the last piece of text
                    if result:
                        result[-1] = remove_all_trailing_hints(result[-1])
            else:
                # Stray closer => keep literal
                result.append(token)

        last_pos = end

    # leftover text
    result.append(field_text[last_pos:])

    return "".join(result)


def main():
    note_ids = invoke("findNotes", query="deck:0Top::Studying")
    print(f"Found {len(note_ids)} notes.\n")
    if not note_ids:
        return

    batch_size = 50
    preview_file = os.path.join(os.getcwd(), "stripped_preview.txt")
    with open(preview_file, "w", encoding="utf-8") as f:
        f.write("=== Stripped Clozes Preview (No Changes in Anki) ===\n\n")

        with tqdm(total=len(note_ids), desc="Processing", unit="note") as pbar:
            for start_index in range(0, len(note_ids), batch_size):
                chunk = note_ids[start_index : start_index + batch_size]
                notes_info = invoke("notesInfo", notes=chunk)

                for note_info in notes_info:
                    note_id = note_info["noteId"]
                    fields = note_info["fields"]

                    # We'll store all before/after changes for this note
                    changes_for_note = []

                    # Convert fields to a list of {name, value}, preserving order
                    field_entries = []
                    for field_name in fields.keys():
                        field_entries.append({
                            "name": field_name,
                            "value": fields[field_name]["value"]
                        })

                    for fd in field_entries:
                        name = fd["name"]
                        old_val = fd["value"]
                        # Skip "Occlusion"
                        if should_skip_field(name):
                            continue

                        new_val = strip_nested_baseindex_hint(old_val)
                        if new_val != old_val:
                            changes_for_note.append((name, old_val, new_val))

                    # If we have changes, log them
                    if changes_for_note:
                        f.write(f"Note ID: {note_id}\n")
                        for (fname, before_text, after_text) in changes_for_note:
                            f.write(f"  Field: {fname}\n")
                            f.write("    Before:\n")
                            f.write(f"      {before_text}\n\n")
                            f.write("    After:\n")
                            f.write(f"      {after_text}\n\n")
                        f.write("=" * 60 + "\n\n")

                    pbar.update(1)

    print(f"Preview complete. See '{preview_file}' for details.")

if __name__ == "__main__":
    main()

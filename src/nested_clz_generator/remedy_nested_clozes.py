#!/usr/bin/env python3
import re
import requests
from tqdm import tqdm
import os

###############################################################################
# Configuration
###############################################################################
ANKI_CONNECT_URL = "http://localhost:8765"  # Change if you run on a non-default port
TOKEN_PATTERN = re.compile(r"(\{\{c(\d+)::|\}\})")

###############################################################################
# 1) Remove repeated same-index clozes via a token-based approach
###############################################################################
def remove_same_index_duplicate_clozes(field_text: str) -> str:
    """
    Removes repeated openers for the same cloze index, e.g.
        {{c7::{{c7::some text}}}}
    becomes
        {{c7::some text}}
    
    It preserves:
      - All text (including text between tokens)
      - Clozes of *different* indices
      - The first valid {{cX:: ... }} for any index
    Only the second (or further) openers for the *same index* that have not been
    closed yet are removed, along with their matching '}}'.
    """

    result = []
    last_pos = 0
    stack = []  # will hold tuples of (index, is_repeated)

    for match in TOKEN_PATTERN.finditer(field_text):
        start = match.start()
        end = match.end()
        token = match.group(0)  # e.g. "{{c7::" or "}}"
        idx = match.group(2)    # the cloze index if opener

        # Include literal text before this token
        result.append(field_text[last_pos:start])

        if token.startswith("{{c"):
            # It's an opener: {{cX::
            if stack and stack[-1][0] == idx and stack[-1][1] is False:
                # The same index is already "open" => repeated opener
                # Mark this as repeated so we skip it and its matching closer
                stack.append((idx, True))
                # Do NOT add the token to the result
            else:
                # Normal opener
                stack.append((idx, False))
                result.append(token)
        else:
            # It's a closer: "}}"
            if stack:
                top_idx, is_repeated = stack.pop()
                if not is_repeated:
                    # If opener wasn't repeated, output the closer
                    result.append(token)
            else:
                # No matching opener -> keep it as is (uncommon, but safe)
                result.append(token)

        last_pos = end

    # Remainder of text after last token
    result.append(field_text[last_pos:])

    return "".join(result)

###############################################################################
# 2) AnkiConnect helper
###############################################################################
def invoke(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    if response.get("error"):
        raise Exception(f"AnkiConnect error: {response['error']}")
    return response["result"]

###############################################################################
# 3) Main script
###############################################################################
def main():
    print("Finding all notes in your collection...")
    note_ids = invoke("findNotes", query="deck:0Top::Studying")  # or "deck:MyDeck" etc.
    print(f"Found {len(note_ids)} notes.\n")

    updated_count = 0
    batch_size = 50

    # We'll write all modifications to this file in the current directory
    log_file_path = os.path.join(os.getcwd(), "modified_notes.txt")

    with open(log_file_path, "w", encoding="utf-8") as log_file:
        log_file.write("=== Modified Notes Log ===\n\n")
        
        # Process notes in batches
        with tqdm(total=len(note_ids), desc="Processing", unit="note") as pbar:
            for start_index in range(0, len(note_ids), batch_size):
                batch_ids = note_ids[start_index : start_index + batch_size]
                notes_info = invoke("notesInfo", notes=batch_ids)

                for note_info in notes_info:
                    note_id = note_info["noteId"]
                    fields = note_info["fields"]
                    updated_fields = {}
                    changed_fields = {}
                    changed = False

                    # Check each field for repeated clozes
                    for field_name, field_data in fields.items():
                        old_value = field_data["value"]
                        new_value = remove_same_index_duplicate_clozes(old_value)

                        if new_value != old_value:
                            updated_fields[field_name] = new_value
                            changed_fields[field_name] = (old_value, new_value)
                            changed = True

                    # If changed, update the note in Anki and record to log
                    if changed:
                        # Update in Anki
                        invoke("updateNoteFields", note={
                            "id": note_id,
                            "fields": updated_fields
                        })
                        updated_count += 1

                        # Log changes
                        log_file.write(f"Note ID: {note_id}\n")
                        for fname, (old_val, new_val) in changed_fields.items():
                            log_file.write(f"  Field: {fname}\n")
                            log_file.write("    Before:\n")
                            log_file.write(f"      {old_val}\n\n")
                            log_file.write("    After:\n")
                            log_file.write(f"      {new_val}\n\n")
                        
                        log_file.write("=" * 60 + "\n\n")

                    pbar.update(1)

    print(f"\nDone! Updated {updated_count} notes. See 'modified_notes.txt' for details.")

if __name__ == "__main__":
    main()

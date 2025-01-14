#!/usr/bin/env python3
import re
import requests
from tqdm import tqdm
import os

###############################################################################
# 1) Token-based parser configuration
###############################################################################
# We look for:
#   1) An opener of the form {{c(\d+)::  (capturing the index)
#   2) A closer of the form }}
# Anything else is literal text that we preserve as-is.
TOKEN_PATTERN = re.compile(r"(\{\{c(\d+)::|\}\})", flags=re.DOTALL)

def remove_clozes_index_gt_9_keep_content(field_text: str) -> str:
    """
    Stack-based approach that removes the markup ({{cX::...}}) ONLY if X > 9,
    but retains the text inside those clozes.
    
    For example:
      - {{c10::Some Text}}  => Some Text
      - {{c5::Keep Me}}     => stays {{c5::Keep Me}}
      - Any literal text outside clozes is always preserved.
    """

    result = []
    last_pos = 0

    # Stack of (cloze_index, keep_markup_boolean)
    # - keep_markup_boolean = True if the opener+closer should appear in final text
    # - if False, we skip the opener and closer, but keep the text in between
    stack = []

    for match in TOKEN_PATTERN.finditer(field_text):
        start = match.start()
        end = match.end()
        token = match.group(0)      # e.g. "{{c10::" or "}}"
        idx_str = match.group(2)    # e.g. "10" if opener, None if closer

        # Always copy the literal text from last_pos to start
        # (unless we have some future reason to skip it, but we do NOT skip text).
        result.append(field_text[last_pos:start])

        if token.startswith("{{c"):
            # It's an opener: {{cX::
            idx = int(idx_str)
            if idx > 9:
                # We do NOT keep the markup (opener), but keep the text inside
                # So push (idx, False) => skip the opener & closer tokens
                stack.append((idx, False))
            else:
                # Keep it as-is
                stack.append((idx, True))
                result.append(token)
        else:
            # It's a closer: "}}"
            if stack:
                top_idx, keep_markup = stack.pop()
                if keep_markup:
                    # If we decided to keep it, append the closer
                    result.append(token)
            else:
                # A stray closer (no opener on the stack)
                # We'll just keep it for safety (uncommon scenario)
                result.append(token)

        last_pos = end

    # Append any trailing text after the last token
    result.append(field_text[last_pos:])

    return "".join(result)

###############################################################################
# 2) AnkiConnect helper
###############################################################################
def invoke(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    response = requests.post("http://localhost:8765", json=payload).json()
    if response.get("error"):
        raise Exception(f"AnkiConnect error: {response['error']}")
    return response["result"]

###############################################################################
# 3) Main script that processes all notes
###############################################################################
def main():
    print("Finding all notes in your collection...")
    note_ids = invoke("findNotes", query="deck:*")  # or "deck:MyDeck" etc.
    print(f"Found {len(note_ids)} notes.\n")

    updated_count = 0
    batch_size = 50

    log_file_path = os.path.join(os.getcwd(), "removed_clozes_gt_9.txt")
    with open(log_file_path, "w", encoding="utf-8") as log_file:
        log_file.write("=== Removed Clozes with Index > 9 (Markup Only) ===\n\n")

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

                    # Process each field, except if field_name == "Occlusion"
                    for field_name, field_data in fields.items():
                        if field_name == "Occlusion":
                            # Skip changes in this field
                            continue

                        old_value = field_data["value"]
                        new_value = remove_clozes_index_gt_9_keep_content(old_value)

                        if new_value != old_value:
                            updated_fields[field_name] = new_value
                            changed_fields[field_name] = (old_value, new_value)
                            changed = True

                    # If changed, update the note in Anki + log the difference
                    if changed:
                        invoke("updateNoteFields", note={
                            "id": note_id,
                            "fields": updated_fields
                        })
                        updated_count += 1

                        log_file.write(f"Note ID: {note_id}\n")
                        for fname, (old_val, new_val) in changed_fields.items():
                            log_file.write(f"  Field: {fname}\n")
                            log_file.write("    Before:\n")
                            log_file.write(f"      {old_val}\n\n")
                            log_file.write("    After (clozes>9 markup removed):\n")
                            log_file.write(f"      {new_val}\n\n")
                        log_file.write("=" * 60 + "\n\n")

                    pbar.update(1)

    print(f"\nDone! Updated {updated_count} notes. See 'removed_clozes_gt_9.txt' for details.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import re
import requests
import os
from tqdm import tqdm

###############################################################################
# AnkiConnect configuration and deck selection
###############################################################################
ANKI_CONNECT_URL = "http://localhost:8765"
DECK_QUERY = "deck:0Top::Studying"  # Adjust as needed

###############################################################################
# AnkiConnect helper function
###############################################################################
def invoke(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    resp = requests.post(ANKI_CONNECT_URL, json=payload).json()
    if resp.get("error"):
        raise Exception("AnkiConnect error: " + str(resp["error"]))
    return resp["result"]

###############################################################################
# Field skipping logic
###############################################################################
def should_skip_field(field_name: str) -> bool:
    """Skip fields whose names contain 'Occlusion'."""
    return "Occlusion" in field_name

###############################################################################
# Cloze extraction (existing helper)
###############################################################################
def extract_innermost_cloze_questions(text: str) -> set:
    """
    Extract innermost cloze question texts from the given text.
    This regex matches cloze markers whose inner content does not contain
    any additional curly braces (i.e. the innermost cloze).
    
    It matches both:
      {{c<number>::question}}
      {{c<number>::question::hint}}
    
    and returns the question (the text immediately following the first "::").
    
    A sanity check is added to ensure that the extracted question does not
    contain any nested cloze markers.
    """
    import re
    pattern = re.compile(r"\{\{c\d+::([^{}:]+)(?:::[^{}]+)?\}\}")
    questions = set()
    for match in pattern.finditer(text):
        question = match.group(1).strip()
        # Sanity check: ensure the question does not itself contain a nested cloze marker.
        if "{{c" in question:
            raise ValueError(f"Nested cloze marker found in innermost cloze question: '{question}'")
        questions.add(question)
    return questions


###############################################################################
# Cloze marker processing and selective hint removal
###############################################################################
def process_text(text: str, i: int = 0) -> str:
    """
    Processes the full text, scanning for cloze markers that start with exactly '{{c'
    (with two opening braces). When found, delegates to process_cloze.
    Other text is passed through unchanged.
    """
    result = []
    while i < len(text):
        # Look for a cloze marker that starts with exactly "{{" followed by 'c'
        if text.startswith("{{", i) and text.startswith("{{c", i):
            processed, i = process_cloze(text, i)
            result.append(processed)
        else:
            result.append(text[i])
            i += 1
    return "".join(result)

def process_cloze(text: str, i: int) -> (str, int):
    """
    Processes a cloze marker that starts at index i.
    
    A cloze marker has the form:
        {{c<number>::<question>[::<hint>]}}
    
    This function returns a tuple: the processed cloze marker (with its hint removed
    if the hint exactly matches one of the innermost cloze questions from the question part)
    and the index position after the cloze marker.
    """
    start = i
    i += 3  # Skip over "{{c"
    
    # Read cloze number (digits)
    num_start = i
    while i < len(text) and text[i].isdigit():
        i += 1
    cloze_num = text[num_start:i]
    
    # Expect the literal "::"
    if not text.startswith("::", i):
        # If not well-formed, return the remainder unmodified.
        return text[start:], len(text)
    i += 2  # Skip "::"
    
    # Collect the question part (which may include nested cloze markers)
    question_parts = []
    while i < len(text):
        if text.startswith("}}", i):
            # End of marker without a hint; return as is.
            i += 2
            return "{{c" + cloze_num + "::" + "".join(question_parts) + "}}", i
        if text.startswith("::", i):
            # Found a hint separator. Process the hint.
            i += 2  # Skip the "::" starting the hint
            hint_parts = []
            while i < len(text):
                if text.startswith("}}", i):
                    break
                if text.startswith("{{c", i):
                    nested, i = process_cloze(text, i)
                    hint_parts.append(nested)
                else:
                    hint_parts.append(text[i])
                    i += 1
            hint_text = "".join(hint_parts).strip()
            
            # Determine the innermost cloze questions from the question part.
            inner_questions = extract_innermost_cloze_questions("".join(question_parts))
            # If no nested cloze marker was found, treat the entire question as the innermost text.
            if not inner_questions:
                inner_questions = { "".join(question_parts).strip() }
            
            # Remove the hint (i.e. set final_hint to empty) only if it exactly matches one
            # of the innermost cloze questions.
            if hint_text in inner_questions:
                final_hint = ""
            else:
                final_hint = "::" + "".join(hint_parts)
            
            # Skip the closing "}}"
            if text.startswith("}}", i):
                i += 2
            return "{{c" + cloze_num + "::" + "".join(question_parts) + final_hint + "}}", i
        if text.startswith("{{c", i):
            # Process a nested cloze marker recursively.
            nested, i = process_cloze(text, i)
            question_parts.append(nested)
        else:
            question_parts.append(text[i])
            i += 1
    # If we exit the loop without encountering a proper closing, return the text unmodified.
    return text[start:], i

def remove_hint_occurrences(text: str) -> str:
    """
    Removes cloze hints only when the hint text exactly matches the innermost cloze question
    from the question part. Other parts of the text are left unchanged.
    
    This function processes the text and returns it with modified cloze markers.
    """
    return process_text(text)

###############################################################################
# Note processing and update logic
###############################################################################
def process_note(note: dict) -> (dict, bool):
    """
    Process all non-skipped fields in a note:
      1. Gather innermost cloze questions from all fields.
      2. Remove any cloze hint occurrence that matches the innermost cloze question from each field.
    
    Returns the (possibly modified) note and a flag indicating if any field changed.
    """
    modified = False
    # Process each non-skipped field
    for field_name, field_data in note["fields"].items():
        if should_skip_field(field_name):
            continue
        old_value = field_data["value"]
        new_value = remove_hint_occurrences(old_value)
        if new_value != old_value:
            note["fields"][field_name]["value"] = new_value
            modified = True
    return note, modified

###############################################################################
# MAIN script: Process notes, update Anki, and log modifications.
###############################################################################
def main():
    note_ids = invoke("findNotes", query=DECK_QUERY)
    print(f"Found {len(note_ids)} notes for deck query '{DECK_QUERY}'.")
    if not note_ids:
        return

    updated_count = 0
    BATCH_SIZE = 50
    log_path = os.path.join(os.getcwd(), "modified_notes.txt")
    
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("=== Modified Notes Log ===\n\n")
        
        for i in tqdm(range(0, len(note_ids), BATCH_SIZE), desc="Processing notes"):
            batch_ids = note_ids[i:i+BATCH_SIZE]
            notes_info = invoke("notesInfo", notes=batch_ids)
            
            for note in notes_info:
                note_id = note["noteId"]
                # Save original field values for logging
                original_fields = {fname: data["value"] for fname, data in note["fields"].items()}
                
                note, modified = process_note(note)
                if modified:
                    # Prepare update payload for non-skipped fields that changed
                    update_fields = {
                        fname: note["fields"][fname]["value"]
                        for fname in note["fields"]
                        if not should_skip_field(fname) and note["fields"][fname]["value"] != original_fields.get(fname, "")
                    }
                    if update_fields:
                        invoke("updateNoteFields", note={"id": note_id, "fields": update_fields})
                        updated_count += 1
                        # Log the before/after changes
                        log_file.write(f"Note ID: {note_id}\n")
                        for fname, new_val in update_fields.items():
                            log_file.write(f"Field: {fname}\n")
                            log_file.write("Before:\n" + original_fields[fname] + "\n")
                            log_file.write("After:\n" + new_val + "\n")
                            log_file.write("-" * 40 + "\n")
                        log_file.write("=" * 60 + "\n\n")
                        
    print(f"Done! Updated {updated_count} notes. See '{log_path}' for details.")

if __name__ == "__main__":
    main()

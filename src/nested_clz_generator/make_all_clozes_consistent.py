#!/usr/bin/env python3
from typing import cast
import requests
import re
import statistics
import itertools
import os
from tqdm import tqdm

###############################################################################
# AnkiConnect config
###############################################################################
ANKI_CONNECT_URL = "http://localhost:8765"

###############################################################################
# Regex to match any single cloze (which may internally have nested clozes).
###############################################################################
CLOZE_RE = r"\{\{c\d+::[\s\S]*?\}\}"

# We define a maximum base index for which we KEEP cloze markup (<= 5 is OK).
MAX_BASE_INDEX = 5
# We'll limit final cloze combos to 8, per your original code.
COMBO_LIMIT = 8

###############################################################################
# (A) For each field, skip if field name == "Occlusion"
###############################################################################
def should_skip_field(field_name: str) -> bool:
    """
    Return True if we should NOT modify this field at all,
    i.e. if its name is exactly "Occlusion".
    """
    return  "Occlusion" in field_name

###############################################################################
# 1) Helper to find all clozes in a text
###############################################################################
def find_clozes_in_text(text: str):
    """
    Return a list of full cloze matches (strings) in `text`.
    e.g. ["{{c7::some}}", "{{c3::{{c2::nested}}}}", etc.]
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

def strip_nested_to_base_cloze(field_text: str) -> str:
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

failures = []
###############################################################################
# 3) find_clozes() as in your snippet (after we've stripped nested)
###############################################################################
def find_clozes(note):
    """
    Finds all clozes in the note and returns them as a list.
    """
    clozes = {}
    original_cloze_hints = {}
    for fld_index, field_dict in enumerate(note["fields"]):
        fld = field_dict["value"]
        for cloze_match in re.findall(CLOZE_RE, fld):
            res = cast(str, cloze_match).split("::")
            cloze_hint = ""
            try:
                if len(res) == 2:
                    cloze_num, cloze_text = cast(str, cloze_match).split("::")
                    cloze_text = f"{cloze_num}::"+cloze_text
                else:
                    cloze_num, cloze_text, cloze_hint = cast(str, cloze_match).split("::")
                    cloze_hint = cloze_hint.replace("}", "")
                    cloze_text = f"{cloze_num}::"+cloze_text+f"::{cloze_hint}"+"}}"
            except:
                print(note)
                failures.append(note)
                raise WronglyFormatted

            if cloze_num not in clozes:
                clozes[cloze_num] = []

            clozes[cloze_num].append((fld_index, cloze_match, cloze_text, cloze_hint))
            original_cloze_hints[cloze_text] = cloze_hint
    return clozes, original_cloze_hints

###############################################################################
# 4) generate_combinations: same logic as your original
###############################################################################
def generate_combinations(clozes, limit):
    """
    Generates all unique combinations of the clozes and returns them as a list.
    """
    combinations = []    
    # Generate combinations in ascending order
    for i in reversed(range(1, len(clozes)-1)):
        if len(combinations) + len(clozes) >= limit:
            break
        combination = tuple(clozes[i:])
        if combination not in combinations:
            combinations.append(combination)
            
    if len(combinations) + len(clozes) < limit:
        for i in range(2, len(clozes)):
            choose_i = itertools.combinations(clozes, i)
            choose_i = sorted(
                choose_i,
                key=lambda c: statistics.mean([clozes.index(cloze) for cloze in c]),
            )
            choose_i = sorted(
                choose_i,
                key=lambda c: statistics.variance([clozes.index(cloze) for cloze in c]),
            )
            for combination in choose_i:
                if len(combinations) + len(clozes) >= limit:
                    break
                if combination not in combinations:
                    combinations.append(combination)
    
    # Add the final combination
    final_comb = tuple(clozes)
    if final_comb not in combinations and len(clozes) > 1:
        combinations.append(final_comb)
                    
    return combinations

class WronglyFormatted(Exception):
    pass
###############################################################################
# 5) The "modify_clozes" logic with skipping "Occlusion" fields
###############################################################################
def modify_clozes(note):
    """
    1) For each field (except "Occlusion"), remove overlapping clozes
       so only the smallest base index remains. 
       If base index > 5 => remove markup entirely.
    2) Re-scan for single clozes => generate combos
    3) Wrap them in nested combos (like your original code).
    """
    # Step A: strip in each non-"Occlusion" field
    for fd in note["fields"]:
        if should_skip_field(fd["name"]):
            # Do NOT modify
            return

        old_val = fd["value"]
        new_val = strip_nested_to_base_cloze(old_val)
        fd["value"] = new_val

    # Step B: find clozes => combos => re-wrap
    clozes, original_hints = find_clozes(note)
    cloze_keys = list(clozes.keys())
    combos = generate_combinations(cloze_keys, COMBO_LIMIT)

    for comb_index, combo in enumerate(combos):
        new_cloze = f"{{{{c{comb_index+len(clozes)+1}::"
        for cloze_num in combo:
            to_iterate_clozes = {(cloze_text, field_index): (field_index,cloze,cloze_hint) for field_index, cloze, cloze_text, cloze_hint in clozes[cloze_num]}
            
            
            for (cloze_text,field_index),(field_index, cloze, cloze_hint) in to_iterate_clozes.items():
                
                
                if original_hints[cloze_text] != "":
                    replaced_str = f"{new_cloze}{cloze_text}::{original_hints[cloze_text]}" + "}}"
                else:
                    replaced_str = f"{new_cloze}{cloze_text}" + "}}"

                # Update the field, if we're not skipping it
                # Because maybe the snippet belongs to an "Occlusion" field => skip
                if not should_skip_field(note["fields"][field_index]["name"]):
                    old_field_val = note["fields"][field_index]["value"]
                    new_field_val = old_field_val.replace(cloze_text, replaced_str, 1)
                    note["fields"][field_index]["value"] = new_field_val

###############################################################################
# 6) AnkiConnect helper
###############################################################################
def invoke(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    resp = requests.post(ANKI_CONNECT_URL, json=payload).json()
    if resp.get("error"):
        raise Exception(f"AnkiConnect error: {resp['error']}")
    return resp["result"]

###############################################################################
# 7) MAIN script to do everything
###############################################################################
def main():
    note_ids = invoke("findNotes", query="deck:0Top::Studying")
    print(f"Found {len(note_ids)} notes in total.")
    if not note_ids:
        return

    BATCH_SIZE = 50
    updated_count = 0
    
    with tqdm(total=len(note_ids), desc="Processing", unit="note") as pbar:
        for start_index in range(0, len(note_ids), BATCH_SIZE):
            chunk = note_ids[start_index : start_index + BATCH_SIZE]
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

                    new_val = strip_nested_to_base_cloze(old_val)
                    fd["value"] = new_val
                pbar.update(1)

    log_path = os.path.join(os.getcwd(), "modified_notes.txt")
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("=== Modified Notes Log ===\n\n")

        with tqdm(total=len(note_ids), desc="Processing", unit="note") as pbar:
            for start_i in range(0, len(note_ids), BATCH_SIZE):
                chunk = note_ids[start_i : start_i + BATCH_SIZE]
                notes_info = invoke("notesInfo", notes=chunk)

                for note_info in notes_info:
                    note_id = note_info["noteId"]
                    fields = note_info["fields"]

                    # Convert to a structure we can mutate
                    # note["fields"] = [ {name: X, value: Y}, ...]
                    # We'll keep track of the old vals for logging
                    note_data = {
                        "noteId": note_id,
                        "fields": []
                    }

                    to_cont = False
                    field_names = list(fields.keys())  # preserve the order
                    for fn in field_names:
                        note_data["fields"].append({
                            "name": fn,
                            "value": fields[fn]["value"]
                        })
                        if should_skip_field(fn):
                            to_cont = True
                            
                    if to_cont:
                        continue
                        

                    old_vals = [fd["value"] for fd in note_data["fields"]]

                    # Modify
                    try:
                        modify_clozes(note_data)

                        new_vals = [fd["value"] for fd in note_data["fields"]]

                        # Check if changed
                        changed = any(ov != nv for ov,nv in zip(old_vals,new_vals))
                        if changed:
                            # Prepare update
                            fields_to_update = {}
                            for i, fdict in enumerate(note_data["fields"]):
                                fields_to_update[fdict["name"]] = fdict["value"]

                            # Update in Anki
                            invoke("updateNoteFields", note={
                                "id": note_id,
                                "fields": fields_to_update
                            })
                            updated_count += 1

                            # Log before/after
                            log_file.write(f"Note ID: {note_id}\n")
                            for i, fdict in enumerate(note_data["fields"]):
                                if old_vals[i] != new_vals[i]:
                                    log_file.write(f"  Field: {fdict['name']}\n")
                                    log_file.write("    Before:\n")
                                    log_file.write(f"      {old_vals[i]}\n\n")
                                    log_file.write("    After:\n")
                                    log_file.write(f"      {new_vals[i]}\n\n")
                            log_file.write("="*60 + "\n\n")
                    except WronglyFormatted:
                        pass

                    pbar.update(1)

        log_file.write(f"\n Failures {len(failures)}\n")
        for failure in failures:
            log_file.write(failure)
            log.write("\n")

    print(f"Done! Updated {updated_count} notes. See '{log_path}' for details.")

if __name__ == "__main__":
    main()

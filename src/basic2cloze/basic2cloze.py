import re

from anki.hooks import wrap
from anki.notes import Note
from aqt import Qt, gui_hooks, mw
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import tooltip

from .consts import ANKI_VERSION_TUPLE
from .modelFinder import get_basic_note_type_ids, get_cloze_note_type_ids
from .modelSelector import target_model

try:
    from anki.notes import NoteFieldsCheckResult
except:
    pass

try:
    from aqt.editor import MODEL_CLOZE
except:
    pass

CLOZE_RE = r"\{\{c\d+::[\s\S]*?\}\}"


def contains_cloze(note: Note):
    for fld in note.fields:
        m = re.search(CLOZE_RE, fld)
        if m:
            return True
    return False


def main():
    def convert_basic_to_cloze(problem, note: Note):
        if not (
            note.note_type()['id'] in get_basic_note_type_ids() and
            contains_cloze(note)
        ):
            return problem

        if not target_model(note):
            tooltip("[Automatic Basic to Cloze] Cannot find target 'Cloze' model")
            return problem

        old_model = mw.col.models.get(note.mid)
        new_model = target_model(note)

        field_values = [
            note[old_model['flds'][i]['name']]
            for i in range(min(len(old_model['flds']), len(new_model['flds'])))
        ]

        note.__init__(mw.col, new_model)
        for i, value in enumerate(field_values):
            note[new_model['flds'][i]['name']] = value

        return None
    gui_hooks.add_cards_will_add_note.append(convert_basic_to_cloze)

    def change_notetype_from_cloze_to_basic_in_addcards_dialog(addcards: AddCards):
        try:
            if addcards.notetype_chooser.selected_notetype_id in get_cloze_note_type_ids():
                addcards.notetype_chooser.selected_notetype_id = get_basic_note_type_ids()[
                    0]
                addcards.notetype_chooser.show()
        except Exception as e:
            print(e)
            pass  # don't cause an error when note types are missing or this code becomes outdated
    gui_hooks.add_cards_did_init.append(
        change_notetype_from_cloze_to_basic_in_addcards_dialog)

    def add_cloze_shortcut_that_works_on_basic_notes(shortcuts, editor):

        original_onCloze = Editor.onCloze

        # code adapted from original onCloze and _onCloze
        def myOnCloze(self):
            if(
                self.note.note_type()['id'] in get_basic_note_type_ids() or
                self.note.note_type()["type"] == MODEL_CLOZE
            ):
                self.call_after_note_saved(
                    lambda: _myOnCloze(editor), keepFocus=True)
            else:
                original_onCloze(self)

        def _myOnCloze(self):
            # find the highest existing cloze
            highest = 0
            for name, val in list(self.note.items()):
                m = re.findall(r"\{\{c(\d+)::", val)
                if m:
                    highest = max(highest, sorted([int(x) for x in m])[-1])
            # reuse last?
            if not self.mw.app.keyboardModifiers() & Qt.AltModifier:
                highest += 1
            # must start at 1
            highest = max(1, highest)
            self.web.eval("wrap('{{c%d::', '}}');" % highest)

        if ANKI_VERSION_TUPLE >= (2, 1, 45):
            shortcuts.append(("Ctrl+Shift+C", lambda: myOnCloze(editor)))
            shortcuts.append(("Ctrl+Shift+Alt+C", lambda: myOnCloze(editor)))
    gui_hooks.editor_did_init_shortcuts.append(
        add_cloze_shortcut_that_works_on_basic_notes)

    def show_cloze_button(editor):
        if editor.note.note_type()['id'] in get_basic_note_type_ids():
            editor.web.eval(
                '$editorToolbar.then(({ templateButtons }) => templateButtons.showButton("cloze")); '
            )
    gui_hooks.editor_did_load_note.append(show_cloze_button)

    # hide cloze warnings
    if ANKI_VERSION_TUPLE >= (2, 1, 45):
        original_update_duplicate_display = Editor._update_duplicate_display

        def _update_duplicate_display_ignore_cloze_problems_for_basic_notes(self, result) -> None:
            if self.note.note_type()['id'] in get_basic_note_type_ids():
                if result == NoteFieldsCheckResult.NOTETYPE_NOT_CLOZE:
                    result = NoteFieldsCheckResult.NORMAL
                if result == NoteFieldsCheckResult.FIELD_NOT_CLOZE:
                    result = NoteFieldsCheckResult.NORMAL
            original_update_duplicate_display(self, result)
        Editor._update_duplicate_display = _update_duplicate_display_ignore_cloze_problems_for_basic_notes
    elif ANKI_VERSION_TUPLE >= (2, 1, 40):
        def _onClozeNew(self, *, _old):
            basicNoteTypes = get_basic_note_type_ids()
            model_id = self.note.model()["id"]
            if model_id in basicNoteTypes and self.addMode:
                model_type_backup = self.note.model()['type']
                self.note.model()['type'] = MODEL_CLOZE

            result = _old(self)

            if model_id in basicNoteTypes and self.addMode:
                self.note.model()['type'] = model_type_backup

            return result

        Editor._onCloze = wrap(Editor._onCloze, _onClozeNew, "around")
    else:
        def _onClozeNew(self, *, _old):
            model_id = self.note.model()["id"]
            if model_id in get_basic_note_type_ids() and self.addMode:
                hook_re_search()
                result = _old(self)
                unhook_re_search()
            return result

        _oldReSearch = None
        _clozeCheckerRegex = "{{(.*:)*cloze:"

        def hook_re_search():
            global _oldReSearch

            # Hook this template
            # if not re.search("{{(.*:)*cloze:", self.note.model()["tmpls"][0]["qfmt"]):
            def newSearch(pattern, string, flags=0, *, _old):
                if pattern == _clozeCheckerRegex:
                    return True
                return _old(pattern, string, flags)

            _oldReSearch = re.search
            re.search = wrap(re.search, newSearch, "around")

        def unhook_re_search():
            global _oldReSearch
            if _oldReSearch:
                re.search = _oldReSearch
                _oldReSearch = None

        Editor._onCloze = wrap(Editor._onCloze, _onClozeNew, "around")

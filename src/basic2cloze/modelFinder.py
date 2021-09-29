from anki.hooks import addHook
from aqt import mw
from aqt.utils import tooltip, tr

from .consts import ANKI_VERSION_TUPLE

_basic_note_type_ids = []
_cloze_note_type_ids = []


def model_ids_for_names(names):
    return [id for id in [mw.col.models.id_for_name(name) for name in names if name] if id]


def get_models():
    """Prepare note type"""
    global _basic_note_type_ids
    global _cloze_note_type_ids

    if ANKI_VERSION_TUPLE >= (2, 1, 45):
        _basic_note_type_ids = model_ids_for_names(
            ["Basic", tr.notetypes_basic_name()])
        _cloze_note_type_ids = model_ids_for_names(
            ["Cloze", tr.notetypes_cloze_name()])
    else:
        from anki.lang import _
        _basic_note_type_ids = model_ids_for_names(["Basic", _("Basic")])
        _cloze_note_type_ids = model_ids_for_names(["Cloze", _("Cloze")])

    if not _basic_note_type_ids:
        tooltip("[Automatic Basic to Cloze] Cannot find source 'Basic' model")

    if not _cloze_note_type_ids:
        tooltip("[Automatic Basic to Cloze] Cannot find target 'Cloze' model")


addHook("profileLoaded", get_models)


def get_basic_note_type_ids():
    return _basic_note_type_ids


def get_cloze_note_type_ids():
    return _cloze_note_type_ids

from anki import version as anki_version  # type: ignore

ANKI_VERSION_TUPLE = tuple(int(i) for i in anki_version.split("."))

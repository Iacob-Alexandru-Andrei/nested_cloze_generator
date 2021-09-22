from .consts import ANKI_VERSION_TUPLE

if ANKI_VERSION_TUPLE >= (2, 1, 40):
    from aqt import gui_hooks

    from .basic2cloze import main
    from .compat import add_compat_aliases

    gui_hooks.profile_did_open.append(add_compat_aliases)
    main()
else:
    from .basic2cloze_old.basic2cloze import main
    main()

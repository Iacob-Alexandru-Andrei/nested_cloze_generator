from aqt import gui_hooks

from .basic2cloze import main
from .compat import add_compat_aliases

gui_hooks.profile_did_open.append(add_compat_aliases)
main()

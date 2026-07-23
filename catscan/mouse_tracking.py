# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import Any

import urwid

XTERM_ENABLE_ALL_MOTION = "\033[?1003h"
XTERM_DISABLE_ALL_MOTION = "\033[?1003l"


def enable_mouse_hover_tracking(screen: urwid.BaseScreen) -> bool:
    screen.set_mouse_tracking(True)

    if not hasattr(screen, "write"):
        return False

    screen.write(XTERM_ENABLE_ALL_MOTION)

    if getattr(screen, "_catscan_hover_tracking_wrapped", False):
        return True

    original_start = screen.start
    original_stop = screen.stop

    def start_with_hover_tracking(*args: Any, **kwargs: Any) -> Any:
        context = original_start(*args, **kwargs)
        screen.write(XTERM_ENABLE_ALL_MOTION)
        return context

    def stop_with_hover_restore(*args: Any, **kwargs: Any) -> None:
        screen.write(XTERM_DISABLE_ALL_MOTION)
        return original_stop(*args, **kwargs)

    screen.start = start_with_hover_tracking
    screen.stop = stop_with_hover_restore
    screen._catscan_hover_tracking_wrapped = True
    return True

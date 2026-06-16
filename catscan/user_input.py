# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from enum import StrEnum


class ACTIONS(StrEnum):
    # Event Row
    EVENT_ROW_SELECT_FIRST_EVENT = "Select the first visible event in the focused row"
    EVENT_ROW_SELECT_EVENT = "Select the event(s) under the cursor"
    EVENT_ROW_EXTEND_SELECTION = "Extend the current selection to the current cursor position"
    EVENT_ROW_NEXT_EVENT = "Select the next event in the focused row"
    EVENT_ROW_PREV_EVENT = "Select the previous event in the focused row"
    EVENT_ROW_NEAREST_NEXT_EVENT = "Select the nearest (to cursor) next event in the row"
    EVENT_ROW_NEAREST_PREV_EVENT = "Select the nearest (to cursor) previous event in the row"

    # Event Detail Box
    EVENT_DETAIL_CLOSE = "Close The event-detail box"

    # Resource View
    RESOURCE_VIEW_TOGGLE_EXPANDED = "Toggle expanded/condensed event view in Resource View"

    # Status Bar
    STATUS_BAR_COMPLETION = "Complete status bar action (command/search)"
    STATUS_BAR_CANCEL = "Cancel in-progress status bar action (command/search)"
    STATUS_BAR_HISTORY_BACK = "Back in status-bar history action (command/search)"
    STATUS_BAR_HISTORY_FORWARD = "Forward in status-bar history action (command/search)"
    STATUS_BAR_SUGGESTION_BACK = "Back in status-bar suggestion action (command/search)"
    STATUS_BAR_SUGGESTION_FORWARD = "Forward in status-bar suggestion action (command/search)"
    STATUS_BAR_GOTO_START = "Go to start of text in status-bar"
    STATUS_BAR_GOTO_END = "Go to end of text in status-bar"

    # Pop-Up
    POPUP_CLOSE = "Close a popup (messages/help window)"

    # Global Actions (useful in multiple contexts)
    SCROLL_TOP = "Scroll to the top"
    SCROLL_BOTTOM = "Scroll to the bottom"
    SCROLL_DOWN = "Scroll down"
    SCROLL_UP = "Scroll up"
    SCROLL_PAGE_DOWN = "Scroll page down"
    SCROLL_PAGE_UP = "Scroll page up"
    SCROLL_HALF_PAGE_DOWN = "Scroll half page down"
    SCROLL_HALF_PAGE_UP = "Scroll half page up"
    TOP_FOCUS = "Move focused row to top"
    CENTER_FOCUS = "Move focused row to center"
    BOTTOM_FOCUS = "Move focused row to bottom"
    TRANSLATE_RIGHT = "Translate right"
    TRANSLATE_LEFT = "Translate left"
    ZOOM_IN = "Zoom in (fewer cycles per character) "
    ZOOM_OUT = "Zoom out (more cycles per character) "
    ZOOM_FIT_FOCUSED = "Zoom focused row so all events fit on the screen"
    QUIT = "Quit catscan"
    PAN = "Drag movement"
    SEARCH = "Begin searching"
    COMMAND = "Begin entering a command"
    HELP = "Display this help output"
    TOGGLE_TXN_HIGHLIGHT = 'Toggle highlighting ("Tag") of the transaction of the currently-selected event'
    TOGGLE_TXN_HIGHLIGHT_ALL = 'Toggle highlighting ("Tag") the transactions of the currently-selected event and all its ancestors and descendants'
    MARK_EVENT = "Mark an event (must be followed by the character with which to mark it)"
    GO_TO_MARKED_EVENT = "Zoom/pan to a marked event (must be followed by the character associated with the event)"
    GO_TO_FOCUSED_FIRST_EVENT = "Go to first event in row"
    GO_TO_FOCUSED_LAST_EVENT = "Go to last event in row"

    # Search
    SEARCH_NEXT = "Select the next search result"
    SEARCH_PREV = "Select the previous search result"


# It is possible for keypress actions (defined below) to match a combination of
# keypresses. Multiple keypresses will be combined by dashes to remove
# ambiguity (i.e. 'g-g' represents two 'g' keypresses, separated by
# KEYPRESS_COMBINATION_TIMEOUT or fewer seconds).
KEYPRESS_COMBINATION_TIMEOUT = 0.4  # seconds

# Timeout between when a press is considered a double-click
# NOTE: double-clicks do not suppress the first click, so this should only be used
# where it makes sense (e.g. the first click would not actually do anything)
DOUBLE_CLICK_TIMEOUT = 0.5  # seconds

# Maps all available keypress ACTIONS to all keypress events which trigger them
action_keypresses = {
    ACTIONS.EVENT_ROW_SELECT_FIRST_EVENT: ("enter",),
    ACTIONS.TOGGLE_TXN_HIGHLIGHT: ("t",),
    ACTIONS.TOGGLE_TXN_HIGHLIGHT_ALL: ("T",),
    ACTIONS.MARK_EVENT: ("m",),
    ACTIONS.GO_TO_MARKED_EVENT: ("'",),
    ACTIONS.GO_TO_FOCUSED_FIRST_EVENT: ("0",),
    ACTIONS.GO_TO_FOCUSED_LAST_EVENT: ("$",),
    ACTIONS.EVENT_ROW_NEXT_EVENT: ("right", "l"),
    ACTIONS.EVENT_ROW_PREV_EVENT: ("left", "h"),
    ACTIONS.EVENT_DETAIL_CLOSE: ("q", "Q", "esc"),
    ACTIONS.RESOURCE_VIEW_TOGGLE_EXPANDED: ("e", "E"),
    ACTIONS.STATUS_BAR_COMPLETION: ("enter",),
    ACTIONS.STATUS_BAR_CANCEL: ("esc",),
    ACTIONS.STATUS_BAR_HISTORY_BACK: ("up",),
    ACTIONS.STATUS_BAR_HISTORY_FORWARD: ("down",),
    ACTIONS.STATUS_BAR_SUGGESTION_BACK: ("tab",),
    ACTIONS.STATUS_BAR_SUGGESTION_FORWARD: ("shift tab",),
    ACTIONS.STATUS_BAR_GOTO_START: ("ctrl a",),
    ACTIONS.STATUS_BAR_GOTO_END: ("ctrl e",),
    ACTIONS.POPUP_CLOSE: ("esc", "q", "Q", "enter"),
    ACTIONS.SCROLL_TOP: ("g-g",),
    ACTIONS.SCROLL_BOTTOM: ("G",),
    ACTIONS.SCROLL_DOWN: ("down", "j"),
    ACTIONS.SCROLL_UP: ("up", "k"),
    ACTIONS.SCROLL_PAGE_DOWN: ("ctrl f",),
    ACTIONS.SCROLL_PAGE_UP: ("ctrl b",),
    ACTIONS.SCROLL_HALF_PAGE_DOWN: ("ctrl d",),
    ACTIONS.SCROLL_HALF_PAGE_UP: ("ctrl u",),
    ACTIONS.TOP_FOCUS: ("z-t",),
    ACTIONS.CENTER_FOCUS: ("z-z",),
    ACTIONS.BOTTOM_FOCUS: ("z-b",),
    ACTIONS.TRANSLATE_RIGHT: ("right", "l"),
    ACTIONS.TRANSLATE_LEFT: ("left", "h"),
    ACTIONS.ZOOM_IN: ("+",),
    ACTIONS.ZOOM_OUT: ("-",),
    ACTIONS.ZOOM_FIT_FOCUSED: ("=-=",),
    ACTIONS.QUIT: ("Z-Z",),
    ACTIONS.SEARCH: ("/",),
    ACTIONS.COMMAND: (":",),
    ACTIONS.SEARCH_NEXT: ("n",),
    ACTIONS.SEARCH_PREV: ("N",),
    ACTIONS.HELP: ("?",),
}


# Maps all available mouse ACTIONS to the user-friendly ("pre-translated")
# mouse events which trigger them
#
# Allowed mouse actions include:
# * Left-click/doubleclick/drag   : "(shift|ctrl|meta)? left_(click|doubleclick|drag)"
# * Middle-click/doubleclick/drag : "(shift|ctrl|meta)? middle_(click|doubleclick|drag)"
# * Right-click/doubleclick/drag  : "(shift|ctrl|meta)? right_(click|doubleclick|drag)"
# * Scroll-up                     : "(shift|ctrl|meta)? scroll_wheel_up"
# * Scroll-down                   : "(shift|ctrl|meta)? scroll_wheel_down"
action_mouseevents_pretranslated = {
    ACTIONS.EVENT_ROW_SELECT_EVENT: ("left_click",),
    ACTIONS.EVENT_ROW_EXTEND_SELECTION: ("shift left_click", "meta left_click"),
    ACTIONS.EVENT_ROW_NEAREST_NEXT_EVENT: ("left_doubleclick",),
    ACTIONS.EVENT_ROW_NEAREST_PREV_EVENT: ("meta left_doubleclick",),
    ACTIONS.TRANSLATE_RIGHT: ("shift scroll_wheel_up", "meta scroll_wheel_up"),
    ACTIONS.TRANSLATE_LEFT: ("shift scroll_wheel_down", "meta scroll_wheel_down"),
    ACTIONS.PAN: ("left_drag",),
    ACTIONS.ZOOM_IN: ("ctrl scroll_wheel_up",),
    ACTIONS.ZOOM_OUT: ("ctrl scroll_wheel_down",),
}


# Map human-readable mouse events to urwid versions, which expects:
# tuple("<optional keypress> mouse <action>", <mouse button number>)
def translate_mouseevent(readable_name: str) -> tuple[str, int]:
    try:
        keypress, mouse_button = readable_name.split(" ")
    except ValueError:
        keypress = ""
        mouse_button = readable_name

    if "drag" in mouse_button:
        action = "drag"
    elif "doubleclick" in mouse_button:
        action = "double_release"
    elif "click" in mouse_button:
        action = "release"
    else:
        action = "press"

    # Convert readable mouse-button into number for urwid
    mouse_buttons = {
        "left_click": 1,
        "middle_click": 2,
        "right_click": 3,
        "scroll_wheel_up": 4,
        "scroll_wheel_down": 5,
    }

    return (
        f"{keypress} mouse {action}".lstrip(),
        mouse_buttons[mouse_button.replace("drag", "click").replace("double", "")],
    )


# Translate the user-friendly ("pre-translated") versions of the mouse events
# to those that urwid understands. action_mouseevents is the dictionary that
# will be used directly by the Widgets themselves to detect whether a
# mouse/keypress event should trigger an action.
action_mouseevents = {
    action: list(map(translate_mouseevent, events)) for action, events in action_mouseevents_pretranslated.items()
}

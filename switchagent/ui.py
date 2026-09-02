"""The popup: choose a target agent, then watch the switch happen.

Two screens in one pane. The chooser is a plain list because there is only one
decision to make; the log that follows it matters more, because starting an
agent takes seconds and a silent popup that closes on failure tells you
nothing.
"""

import curses

HELP = "↑↓ move · enter switch · q cancel"


def choose_kind(source_line, choices):
    """Return the chosen kind, or None if the user cancelled."""
    return curses.wrapper(_choose, source_line, choices)


def _choose(screen, source_line, choices):
    curses.curs_set(0)
    _colours()
    index = _first_available(choices)

    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        _line(screen, 0, 0, "Switch agent", width, curses.A_BOLD)
        _line(screen, 1, 0, source_line, width, _pair(4))
        _line(screen, 3, 0, "Hand this session to:", width, curses.A_BOLD)

        top = 4
        room = max(1, height - top - 2)
        start = max(0, min(index - room // 2, len(choices) - room))
        for row, (kind, installed) in enumerate(choices[start : start + room]):
            position = start + row
            label = "  %s%s" % (kind, "" if installed else "   (not installed)")
            attr = curses.A_NORMAL if installed else _pair(3)
            if position == index:
                label = ">" + label[1:]
                attr = curses.A_REVERSE
            _line(screen, top + row, 0, label, width, attr)

        _line(screen, height - 1, 0, HELP, width, _pair(4))
        screen.refresh()

        key = screen.getch()
        if key in (ord("q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            index = _step(choices, index, -1)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = _step(choices, index, 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            kind, installed = choices[index]
            if installed:
                return kind


def _first_available(choices):
    for position, (_, installed) in enumerate(choices):
        if installed:
            return position
    return 0


def _step(choices, index, delta):
    return max(0, min(len(choices) - 1, index + delta))


class Log(object):
    """A running report of the switch, drawn in the same pane."""

    def __init__(self, screen, title):
        self.screen = screen
        self.title = title
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)
        self.draw()

    def draw(self, footer=""):
        screen = self.screen
        screen.erase()
        height, width = screen.getmaxyx()
        _line(screen, 0, 0, self.title, width, curses.A_BOLD)
        room = max(1, height - 3)
        for row, message in enumerate(self.lines[-room:]):
            _line(screen, 2 + row, 0, "  " + message, width, curses.A_NORMAL)
        if footer:
            _line(screen, height - 1, 0, footer, width, _pair(4))
        screen.refresh()

    def finish(self, message, ok=True):
        self.lines.append("")
        self.lines.append(message)
        self.draw("press any key to close")
        self.screen.nodelay(False)
        self.screen.getch()


def run_with_log(title, work):
    """Run `work(report)` inside a curses pane that shows its progress."""
    return curses.wrapper(_run_with_log, title, work)


def _run_with_log(screen, title, work):
    curses.curs_set(0)
    _colours()
    log = Log(screen, title)
    log("starting")
    try:
        result = work(log)
    except Exception as error:
        log.finish("failed: %s" % error, ok=False)
        raise
    log.finish(result or "done")
    return result


def _colours():
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(3, curses.COLOR_BLACK, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
    except curses.error:
        pass


def _pair(number):
    try:
        return curses.color_pair(number)
    except curses.error:
        return curses.A_NORMAL


def _line(screen, row, column, text, width, attr):
    """Write one clipped line, ignoring the bottom-right cell error."""
    try:
        screen.addnstr(row, column, text, max(0, width - column - 1), attr)
    except curses.error:
        pass

"""
Human-readable download filenames (CF-100).

Pure string work, no I/O — its own module because CF-101's bulk zip needs the
same names for its archive entries, and a scheme that lives in one router is a
scheme the next caller reimplements slightly differently.

**Deliberately not shared with `games._sanitize_filename`.** That one guards a
*storage key*: it neutralises `/`, `\\` and `..` so a crafted upload name cannot
escape the key prefix. This one guards a *header value* and the file a browser
then writes to disk, which is a wider problem — it must also survive
`Content-Disposition: attachment; filename="..."` unescaped, and land on Windows
and macOS filesystems. Merging them would mean one set of rules serving two
threat models, and the looser of the two would win.
"""
from __future__ import annotations

# Windows rejects all of these in a filename; `:` is the one that actually bites
# here, because a mm:ss timestamp is the obvious way to write the position in
# the game and it is illegal on Windows and awkward on macOS. `"`, `;` and `\`
# additionally have to go so the value cannot break out of the quoted string in
# Content-Disposition — storage.presign_url strips those again as a second line
# of defence, but a value that only becomes safe downstream is one nobody can
# reason about here.
_FORBIDDEN = set('";\\/:*?<>|')

# Game titles and player names are both String(255). Concatenated with the
# separators they would comfortably exceed 500 bytes of header, so each part is
# capped rather than the whole — truncating the joined string would silently
# drop the timestamp, which is the component that makes two clips from one game
# distinguishable.
MAX_COMPONENT_CHARS = 80

# Most filesystems cap a single name at 255 bytes. Four capped components plus
# their separators reach 258, so the joined stem is capped too — otherwise the
# one input that overflows is the one nobody tests.
MAX_FILENAME_CHARS = 255


def sanitize_component(value: str | None, max_chars: int = MAX_COMPONENT_CHARS) -> str:
    """One filename component: printable ASCII, no separators, length-capped.

    `max_chars` defaults to the per-component budget of the multi-part clip
    scheme. A single-component name has the whole filename to itself and should
    say so rather than inherit a cap sized for four.

    Non-ASCII is dropped rather than transliterated. That loses information for
    a title written in a non-Latin script, which is why the caller must cope
    with an empty result instead of assuming every component survives.
    """
    if not value:
        return ""
    # Whitespace becomes a space *before* the printable filter, not after. A tab
    # is not printable, so filtering first deletes it and welds the words either
    # side together — "Semi\tFinal" would come out "SemiFinal".
    spaced = "".join(" " if c.isspace() else c for c in value)
    kept = [
        c for c in spaced
        if c.isascii() and c.isprintable() and c not in _FORBIDDEN
    ]
    # Collapse runs of dots. Stripping `/` out of `../../etc/passwd` leaves
    # `....etcpasswd`, which cannot traverse anything once the separators are
    # gone but is an ugly thing to hand someone as a filename — and a leading
    # dot hides the file on Unix.
    text = "".join(kept)
    while ".." in text:
        text = text.replace("..", ".")
    # A leading dash makes the name look like a flag to any CLI it is later
    # passed to; a leading dot hides the file on Unix — a downloaded clip the
    # user cannot see in Finder is worse than a slightly wrong name. This does
    # mangle a title that legitimately starts with a dot (".38 Special" becomes
    # "38 Special"), which is the accepted cost rather than an oversight.
    collapsed = " ".join(text.split()).lstrip(". -")
    return collapsed[:max_chars].strip()


def format_timestamp(seconds: float) -> str:
    """`mm-ss`, not `mm:ss` — a colon is illegal in a Windows filename.

    NaN and the infinities are treated as 0 rather than allowed to raise:
    `int(float("nan"))` is a ValueError, and a corrupt start_time should cost a
    reader the position in the filename, not turn a download into a 500.
    """
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        seconds = 0.0
    total = max(0, int(seconds))
    return f"{total // 60:02d}-{total % 60:02d}"


def clip_download_filename(
    game_title: str | None,
    action: str | None,
    player_name: str | None,
    start_seconds: float,
) -> str:
    """`{game} - {action} - {player} - {mm-ss}.mp4`, minus whatever is missing.

    Components that sanitise away entirely are dropped rather than left as empty
    separators, so an untagged clip is `Match - spike - 04-12.mp4` and not
    `Match - spike -  - 04-12.mp4`. The timestamp always survives, which is what
    keeps two clips from one game apart when everything else is stripped.
    """
    parts = [
        sanitize_component(game_title),
        sanitize_component(action),
        sanitize_component(player_name),
        format_timestamp(start_seconds),
    ]
    name = " - ".join(p for p in parts if p)
    # Trim from the front so the timestamp survives: it is what keeps two clips
    # from one game apart once a long title has eaten the budget.
    stem = name[-(MAX_FILENAME_CHARS - len(".mp4")):].lstrip(". -")
    # Only reachable if the timestamp were somehow empty, which it cannot be —
    # belt and braces, because the alternative is a bare ".mp4".
    return f"{stem}.mp4" if stem else "clip.mp4"


def condensed_download_filename(game_title: str | None) -> str:
    """`{game} (condensed).mp4` — the shape already shipped for game downloads.

    Routed through the same sanitiser rather than interpolated raw, which is the
    whole reason this module exists: `presign_url`'s own strip drops only `";\\`,
    so a title carrying `:` or a path separator reached the header and then the
    disk. Kept as its own function rather than folded into
    clip_download_filename because the two schemes are genuinely different — a
    parenthetical qualifier, not a field list — and collapsing them would change
    a filename users already receive.
    """
    # The whole filename budget, not the four-component one: this name has a
    # single variable part, and capping it at 80 would silently shorten a
    # filename users already receive for long-titled games.
    suffix = " (condensed).mp4"
    title = sanitize_component(game_title, max_chars=MAX_FILENAME_CHARS - len(suffix))
    return f"{title}{suffix}" if title else "condensed.mp4"

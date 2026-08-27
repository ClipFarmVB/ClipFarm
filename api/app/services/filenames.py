"""
Human-readable download filenames (CF-100).

Pure string work, no I/O — its own module because CF-101's bulk zip needs the
same names for its archive entries, and a scheme that lives in one router is a
scheme the next caller reimplements slightly differently.

**Deliberately not shared with `games._sanitize_filename`.** That one guards a
*storage key*: it neutralises `/`, `\\` and `..` so a crafted upload name cannot
escape the key prefix. This one guards the file a browser then writes to disk,
which is a wider problem — it must land on Windows and macOS filesystems.
Merging them would mean one set of rules serving two threat models, and the
looser of the two would win.

**What this module does not do is encode a header.** It returns a filename, in
whatever script the game and the players are named in; turning that into a
`Content-Disposition` value — the ASCII fallback, the RFC 5987 `filename*`, the
quoted-string escaping — lives with the code that builds the header, in
`storage.presign_url`. Splitting it the other way would mean this module knowing
about HTTP so that CF-101, which writes these names into a zip's central
directory and sends no header at all, could ignore what it knew.
"""
from __future__ import annotations

# Windows rejects all of these in a filename; `:` is the one that actually bites
# here, because a mm:ss timestamp is the obvious way to write the position in
# the game and it is illegal on Windows and awkward on macOS. `"`, `;` and `\`
# are in the set for the header's sake even though the header is not built here
# — a name that only becomes safe downstream is one nobody can reason about at
# the point it is written, and storage.presign_url strips them again anyway.
_FORBIDDEN = set('";\\/:*?<>|')

# Game titles and player names are both String(255). Concatenated with the
# separators they would comfortably exceed 500 bytes of header, so each part is
# capped rather than the whole — truncating the joined string would silently
# drop the timestamp, which is the component that makes two clips from one game
# distinguishable.
#
# Bytes rather than characters, because a component is not ASCII-only: 80
# characters of Cyrillic is 160 bytes and of CJK is 240, and bytes are what both
# the filesystem limit below and the header budget are measured in.
MAX_COMPONENT_BYTES = 80

# Most filesystems cap a single name at 255 bytes, and four 80-byte components
# with their separators would reach 257 — so the joined stem is capped too.
#
# For *this* scheme that cap is unreachable, and the arithmetic above is why it
# looks like it isn't: `action` comes from ActionType, whose longest value is 7
# bytes, so the real worst case is 80 + 3 + 7 + 3 + 80 + 3 + 8 = 184 against a
# stem budget of 255 - len(".mp4") = 251. (Written as the subtraction because
# two readers in a row have read the result as 250.)
#
# The cap is not dead code — it is what holds for a caller passing a longer
# field set, which is what CF-101 does when it names zip entries, and
# TestFilenameLengthBound reaches it by passing an unbounded action. But no
# *route* can reach it, so do not read a green test there as evidence that
# production exercises this path.
MAX_FILENAME_BYTES = 255


def _truncate_bytes(text: str, max_bytes: int, *, from_end: bool = False) -> str:
    """Cut `text` to at most `max_bytes` of UTF-8, never mid-character.

    `errors="ignore"` is what makes that true: slicing the encoded form can land
    inside a multi-byte sequence, and the partial sequence is then dropped
    rather than decoded into a replacement character that would itself be
    written to disk.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[-max_bytes:] if from_end else encoded[:max_bytes]
    return cut.decode("utf-8", errors="ignore")


def sanitize_component(value: str | None, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """One filename component: printable, no separators, length-capped.

    `max_bytes` defaults to the per-component budget of the multi-part clip
    scheme. A single-component name has the whole filename to itself and should
    say so rather than inherit a cap sized for four.

    **Non-ASCII survives.** Dropping it meant a Cyrillic-titled game with a
    non-Latin player name sanitising away to nothing but a timestamp — every
    clip in that game downloading as `00-04-12.mp4`, which is the case the
    scheme exists to prevent. The caller must still cope with an empty result: a
    component that is nothing but forbidden characters still yields one.
    """
    if not value:
        return ""
    # Whitespace becomes a space *before* the printable filter, not after. A tab
    # is not printable, so filtering first deletes it and welds the words either
    # side together — "Semi\tFinal" would come out "SemiFinal".
    spaced = "".join(" " if c.isspace() else c for c in value)
    # `isprintable()` is False for control characters and for the non-ASCII
    # separators and format characters (U+00A0, U+200B, the bidi overrides), so
    # this keeps letters in any script while still closing CR/LF and the
    # right-to-left override trick that makes "…fdp.exe" read as "…exe.pdf".
    kept = [c for c in spaced if c.isprintable() and c not in _FORBIDDEN]
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
    # Strip *after* the cap, and at the other end too: the cap can land in the
    # middle of a component and leave a trailing dot or space, which Windows
    # then silently drops from the name it writes. `"x" * 79 + ".abc"` came out
    # `…x. - spike - …`.
    return _truncate_bytes(collapsed, max_bytes).rstrip(". -")


def format_timestamp(seconds: float) -> str:
    """`hh-mm-ss`, always, with dashes rather than colons.

    A colon is illegal in a Windows filename, which is why this does not read
    `hh:mm:ss` the way the UI does.

    **The hour is always present and always padded**, and that is the whole
    point rather than verbosity. These names land together in one downloads
    folder, so they are sorted as strings — and mixing `59-59` with `1-00-00`
    puts the 59-minute clip *after* the three-hour one, because "5" sorts after
    "1". Padding is the same argument one digit further out: unpadded, `9-00-00`
    sorts after `10-00-00`. One digit happens to be enough while uploads are
    capped at four hours, but that cap is `max_upload_duration_seconds`, a
    settings field somebody may raise — and the sort invariant should not be a
    downstream consequence of a configuration value.

    It carries hours at all for the same reason the UI does: without the
    rollover a late clip reads `239-59` where ClipCard and ClipModal both show
    `3:59:59`, and a reader has to divide to find the moment in the game.

    NaN and the infinities are treated as 0 rather than allowed to raise:
    `int(float("nan"))` is a ValueError, and a corrupt start_time should cost a
    reader the position in the filename, not turn a download into a 500.
    """
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        seconds = 0.0
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def clip_download_filename(
    game_title: str | None,
    action: str | None,
    player_name: str | None,
    start_seconds: float,
) -> str:
    """`{game} - {action} - {player} - {hh-mm-ss}.mp4`, minus what is missing.

    Components that sanitise away entirely are dropped rather than left as empty
    separators, so an untagged clip is `Match - spike - 00-04-12.mp4` and not
    `Match - spike -  - 00-04-12.mp4`. The timestamp always survives, which is
    what keeps two clips from one game apart when everything else is stripped.
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
    stem = _truncate_bytes(
        name, MAX_FILENAME_BYTES - len(".mp4"), from_end=True
    ).lstrip(". -")
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
    title = sanitize_component(game_title, max_bytes=MAX_FILENAME_BYTES - len(suffix))
    return f"{title}{suffix}" if title else "condensed.mp4"

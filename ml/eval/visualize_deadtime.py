"""
Generate a standalone HTML timeline comparing candidate condense variants
against the human-labeled ground truth, across the dead-time fixtures.

Reads the ball-cache from ml/eval/ball_caches/{md5}.json — no video download,
no app deps. Both ends of the ladder ship: v0 is condense_mode="rules", v5 is
condense_mode="guarded" (the default). v1-v4 are the intermediate prototypes in
deadtime_variants.py.

The variants were designed against test2 / test4 (DeadtimeLabel2.mp4 /
DeadtimeLabel4.mp4); every other fixture was never looked at while tuning, so
read those columns as the held-out result.

test5 is the cleanest of them: it was labeled *after* the variants existed, so
it could not have influenced them even indirectly. test1 and test3 were at least
present in the repo while they were being written.

Usage:
  python -m ml.eval.visualize_deadtime
  # -> ml/eval/results/deadtime_visualization.html
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

from ml.eval.deadtime_variants import VARIANTS, Game, load_game
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime, subtract, union
from ml.eval.tune_contacts import COND
from ml.pipeline.dead_time import active_windows_from_contacts

EVAL_DIR = Path(__file__).resolve().parent
OUT_PATH = EVAL_DIR / "results" / "deadtime_visualization.html"
TEST_IDS = ("test1", "test2", "test3", "test4", "test5")
TUNED_ON = ("test2", "test4")   # the rest are held out — see module docstring

# 1s of wrongly cut play costs this many seconds of kept dead time (CF-187's
# exchange rate).
LIVE_CUT_COST = 4.0

Interval = tuple[float, float]


def complement(spans: list[Interval], duration: float) -> list[Interval]:
    """Complement of `spans` inside [0, duration] — the dead time."""
    out: list[Interval] = []
    cursor = 0.0
    for s, e in sorted(spans):
        s, e = max(0.0, s), min(duration, e)
        if s > cursor:
            out.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        out.append((cursor, duration))
    return out


def net_seconds(s: DeadTimeSignals) -> float:
    dead_removed = (s.dead_removed_pct or 0.0) * s.human_dead_sec
    return dead_removed - LIVE_CUT_COST * s.live_removed_sec


def fmt_ts(seconds: float) -> str:
    total = int(round(seconds))
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds:.1f}s ({int(seconds) // 60}m{int(seconds) % 60:02d}s)"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def padding_ceiling(game: Game) -> DeadTimeSignals:
    """
    Most dead time the *rules* pads (5/4, merge 5) can remove **without cutting
    any live play**: pretend detection is perfect — a contact every second of every
    labeled rally — and run the same windowing every variant uses.

    Worth showing beside each game because it separates the two ways a variant
    falls short. Below this line the gap is detection; *at* it the detector is
    already doing everything the padding permits, and only pad_before /
    pad_after / merge_gap can buy more. pad 5 + pad 4 widens each rally by 9s
    and merge_gap then joins anything still within 5s, so no rally gap under 14s
    survives regardless of how good the ball model gets.

    Two things it does NOT bound, both of which show up in the table:
      - variants that cut real play — v4 removes 90.6% of test3's dead time
        against a 23.2% ceiling by cutting 162s of rally, so read this against
        the live-cut column, never alone;
      - variants that change the pads. v4/v5 — the latter being the shipping
        default — shrink them to 3/2 with merge 3 (an 8s budget against the
        rules path's 14s), which raises their own ceiling.
    It bounds only the variants that keep the rules pads: v0 through v3.

    Contacts are placed a second apart rather than at the rally boundaries only:
    a rally longer than gap_seconds would otherwise split into two groups and
    the oracle would cut its own middle out, charging the ceiling with live loss
    that the padding is not responsible for.
    """
    contacts = [
        {"time": t}
        for start, end in game.human_keep
        for t in [start + i for i in range(int(end - start) + 1)]
        if t <= end
    ]
    windows = active_windows_from_contacts(
        [{"time": c["time"]} for c in contacts], game.duration, **COND)
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def _join(items: list[str]) -> str:
    """Oxford-comma list — 'a and b' reads fine, 'a and b and c' does not."""
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# ── SVG rendering ─────────────────────────────────────────────────────────

SVG_W = 1400
ROW_H = 26
ROW_GAP = 6
PAD_L = 250   # must clear the longest row label ("vN agree  dead 20.8% · live -42s")
PAD_R = 20
PAD_TOP = 20
PAD_BOT = 40
GROUP_GAP = 14
TICK_EVERY = 60

C_PLAY = "#4a7fbf"
C_DEAD = "#c94b4b"
C_BOTH_PLAY = "#c8ced4"
C_BOTH_DEAD = "#2a9d8f"
C_OVERCUT = "#e63946"
C_MISSED = "#f4a261"


def tip_attrs(label: str, s: float, e: float) -> str:
    """Hover-tooltip payload. A native <title> works but waits ~1s to appear,
    which makes scanning a dense row impractical — the JS tooltip is instant."""
    return (
        f'data-label="{html.escape(label, quote=True)}" '
        f'data-range="{fmt_ts(s)}–{fmt_ts(e)}" '
        f'data-dur="{fmt_dur(e - s)}"'
    )


def _row_frame(y: int, label: str, sub: str = "") -> list[str]:
    text = (
        f'<text x="{PAD_L - 10}" y="{y + ROW_H / 2 + 4}" text-anchor="end" '
        f'font-size="12" fill="#333">{html.escape(label)}'
    )
    if sub:
        text += f'<tspan fill="#8a9199"> {html.escape(sub)}</tspan>'
    text += "</text>"
    return [
        f'<rect x="{PAD_L}" y="{y}" width="{SVG_W - PAD_L - PAD_R}" height="{ROW_H}" '
        f'fill="#eef1f4" stroke="#c7ccd1"/>',
        text,
    ]


def render_row(
    spans: list[Interval], y: int, duration: float, color: str, label: str, sub: str = "",
) -> str:
    scale = (SVG_W - PAD_L - PAD_R) / duration
    parts = _row_frame(y, label, sub)
    for s, e in spans:
        x = PAD_L + s * scale
        w = max(1.0, (e - s) * scale)
        parts.append(
            f'<rect class="span" x="{x:.2f}" y="{y}" width="{w:.2f}" height="{ROW_H}" '
            f'fill="{color}" fill-opacity="0.85" {tip_attrs(label, s, e)}/>'
        )
    return "\n".join(parts)


def render_agreement(
    human_dead: list[Interval], model_dead: list[Interval],
    y: int, duration: float, label: str, sub: str = "",
) -> str:
    """
    Colors every second by whether the variant agrees with the labels.
      grey  = both call it play        (kept live — good)
      teal  = both call it dead        (true dead removed — good)
      red   = only the variant is dead (OVER-CUT LIVE — real play removed)
      amber = only the human is dead   (MISSED DEAD — dead time kept)
    """
    h, m = union(human_dead), union(model_dead)
    full = [(0.0, duration)]
    # both dead = full − (¬h ∪ ¬m)
    both_dead = subtract(full, subtract(full, h) + subtract(full, m))
    parts = _row_frame(y, label, sub)
    scale = (SVG_W - PAD_L - PAD_R) / duration
    for spans, color, tag in (
        (subtract(subtract(full, h), m), C_BOTH_PLAY, "both play"),
        (both_dead, C_BOTH_DEAD, "both dead"),
        (subtract(m, h), C_OVERCUT, "OVER-CUT LIVE"),
        (subtract(h, m), C_MISSED, "MISSED DEAD"),
    ):
        for s, e in spans:
            x = PAD_L + s * scale
            w = max(1.0, (e - s) * scale)
            parts.append(
                f'<rect class="span" x="{x:.2f}" y="{y}" width="{w:.2f}" height="{ROW_H}" '
                f'fill="{color}" {tip_attrs(tag, s, e)}/>'
            )
    return "\n".join(parts)


def render_axis(y: int, duration: float) -> str:
    scale = (SVG_W - PAD_L - PAD_R) / duration
    parts = [
        f'<line x1="{PAD_L}" y1="{y}" x2="{SVG_W - PAD_R}" y2="{y}" stroke="#444"/>'
    ]
    t = 0
    while t <= duration + 0.5:
        x = PAD_L + t * scale
        parts.append(f'<line x1="{x:.2f}" y1="{y}" x2="{x:.2f}" y2="{y + 5}" stroke="#444"/>')
        parts.append(
            f'<text x="{x:.2f}" y="{y + 18}" text-anchor="middle" font-size="10" '
            f'fill="#444">{fmt_ts(t)}</text>'
        )
        t += TICK_EVERY
    return "\n".join(parts)


def render_game(game: Game, results: dict[str, tuple[list[Interval], DeadTimeSignals]]) -> str:
    duration = game.duration
    human_dead = complement(game.human_keep, duration)
    ceiling = padding_ceiling(game)

    n_rows = 2 + 2 * len(VARIANTS)
    svg_h = (
        PAD_TOP + (ROW_H + ROW_GAP) * n_rows
        + GROUP_GAP * (len(VARIANTS) + 1) + PAD_BOT
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {svg_h}" '
        f'width="100%" style="max-width:{SVG_W}px">'
    ]
    y = PAD_TOP
    lines.append(render_row(game.human_keep, y, duration, C_PLAY, "human — in play"))
    y += ROW_H + ROW_GAP
    lines.append(render_row(human_dead, y, duration, C_DEAD, "human — DEAD"))
    y += ROW_H + ROW_GAP

    for key, (_label, _) in VARIANTS.items():
        keep, s = results[key]
        y += GROUP_GAP
        lines.append(render_row(
            keep, y, duration, C_PLAY, f"{key} kept", f"({len(keep)} windows)",
        ))
        y += ROW_H + ROW_GAP
        lines.append(render_agreement(
            human_dead, complement(keep, duration), y, duration,
            f"{key} agree",
            f"dead {_pct(s.dead_removed_pct)} · live -{s.live_removed_sec:.0f}s",
        ))
        y += ROW_H + ROW_GAP

    y += GROUP_GAP
    lines.append(render_axis(y, duration))
    lines.append("</svg>")

    rows = "".join(
        f"<tr{' class=\"hl\"' if key == 'v0' else ''}>"
        f"<td class='k'>{key}</td><td>{html.escape(label)}</td>"
        f"<td class='n'>{_pct(results[key][1].dead_removed_pct)}</td>"
        f"<td class='n'>{results[key][1].live_removed_sec:.0f}s</td>"
        f"<td class='n'>{_pct(results[key][1].kept_play_pct)}</td>"
        f"<td class='n'>{net_seconds(results[key][1]):+.0f}s</td></tr>"
        for key, (label, _) in VARIANTS.items()
    )

    return f"""
    <section>
      <h2>{html.escape(game.test_id)} — {html.escape(game.video_file)}</h2>
      <p class="meta">{fmt_ts(duration)} ({duration:.0f}s) ·
        {len(game.human_keep)} labeled rallies ·
        {len(game.contacts)} ball contacts ·
        {len(game.positions)} track samples ·
        <b>padding ceiling {_pct(ceiling.dead_removed_pct)} dead removed</b>
        (perfect detection at the <code>rules</code> pads, zero live cut — bounds
        <code>v0</code>–<code>v3</code>; <code>v4</code>/<code>v5</code> shrink
        the pads and so raise their own ceiling)</p>
      <table class="stats">
        <tr><th></th><th>variant</th><th class="n">dead removed</th>
            <th class="n">live cut</th><th class="n">kept play</th><th class="n">net</th></tr>
        {rows}
      </table>
      {''.join(lines)}
    </section>
    """


def render_summary(all_results: dict[str, dict[str, tuple[list[Interval], DeadTimeSignals]]]) -> str:
    held_out = [t for t in TEST_IDS if t not in TUNED_ON]
    rows = []
    for key, (label, _) in VARIANTS.items():
        cells = []
        total_net = 0.0
        total_live = 0.0
        held_net = 0.0
        for tid in TEST_IDS:
            s = all_results[tid][key][1]
            net = net_seconds(s)
            total_net += net
            total_live += s.live_removed_sec
            if tid in held_out:
                held_net += net
            cls = " ho" if tid in held_out else ""
            cells.append(
                f"<td class='n{cls}'>{_pct(s.dead_removed_pct)}</td>"
                f"<td class='n{cls}'>{s.live_removed_sec:.0f}s</td>"
            )
        rows.append(
            f"<tr{' class=\"hl\"' if key == 'v0' else ''}>"
            f"<td class='k'>{key}</td><td>{html.escape(label)}</td>"
            + "".join(cells)
            + f"<td class='n'>{total_live:.0f}s</td>"
            f"<td class='n'>{held_net:+.0f}s</td>"
            f"<td class='n b'>{total_net:+.0f}s</td></tr>"
        )
    heads = "".join(
        f"<th class='n{" ho" if tid not in TUNED_ON else ""}' colspan='2'>{tid}"
        f"{'' if tid in TUNED_ON else ' *'}</th>"
        for tid in TEST_IDS
    )
    return f"""
    <section>
      <h2>Summary</h2>
      <table class="stats wide">
        <tr><th></th><th>variant</th>{heads}
            <th class="n">live cut</th><th class="n">held-out</th><th class="n">net</th></tr>
        <tr class="sub"><th></th><th></th>
            {'<th class="n">dead</th><th class="n">live</th>' * len(TEST_IDS)}
            <th class="n">total</th><th class="n">net</th><th class="n">score</th></tr>
        {''.join(rows)}
      </table>
      <p class="note"><b>net</b> = dead seconds removed − {LIVE_CUT_COST:.0f} × live seconds cut.
      Higher is better; <code>v0</code> is what ships today.</p>
      <p class="note"><b>*</b> {_join(held_out)} were never inspected while tuning —
      those columns and the <b>held-out net</b> are the only numbers here not fitted to the data.
      <b>test5</b> is the strongest of them: it was labeled after the variants were written,
      so it could not have shaped them even indirectly.</p>
    </section>
    """


LEGEND = """
<div class="legend">
  <span><i style="background:#4a7fbf"></i>in play / kept</span>
  <span><i style="background:#c94b4b"></i>dead</span>
  <span class="sep">agreement rows:</span>
  <span><i style="background:#c8ced4"></i>both call it play</span>
  <span><i style="background:#2a9d8f"></i>both call it dead (true removal)</span>
  <span><i style="background:#e63946"></i>OVER-CUT LIVE (cut real play)</span>
  <span><i style="background:#f4a261"></i>MISSED DEAD (kept dead time)</span>
</div>
"""

CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 24px; color: #222; }
h1 { margin: 0 0 4px 0; }
h2 { margin: 0 0 6px 0; font-size: 18px; }
p.sub { color: #555; margin: 0 0 20px 0; max-width: 1100px; }
p.meta { color: #666; font-size: 12px; margin: 0 0 12px 0; }
p.note { color: #666; font-size: 12px; margin: 10px 0 0 0; }
section { border: 1px solid #ddd; border-radius: 8px; padding: 16px 20px;
          margin-bottom: 28px; background: #fff; }
table.stats { border-collapse: collapse; margin: 4px 0 14px 0; font-size: 13px; }
table.stats th { text-align: left; font-weight: 600; color: #555;
                 padding: 3px 14px 3px 0; border-bottom: 1px solid #ddd; }
table.stats td { padding: 3px 14px 3px 0; }
table.stats td.n, table.stats th.n { text-align: right; }
table.stats td.k { font-family: ui-monospace, Menlo, monospace; color: #666; }
table.stats td.b { font-weight: 700; }
table.stats tr.hl td { background: #f4f6f8; }
table.stats tr.sub th { font-weight: 500; color: #888; border-bottom: 1px solid #ddd; }
table.stats .ho { background: #fbf7ec; }
.legend { display: flex; flex-wrap: wrap; gap: 12px 20px;
          font-size: 12px; color: #333; margin: 12px 0 20px 0; }
.legend i { display: inline-block; width: 14px; height: 12px;
            vertical-align: middle; margin-right: 6px;
            border: 1px solid rgba(0,0,0,0.15); }
.legend .sep { color: #888; margin-left: 10px; }
rect.span { cursor: crosshair; }
rect.span:hover { stroke: #111; stroke-width: 1.5; }
#tip { position: fixed; pointer-events: none; z-index: 10; display: none;
       background: rgba(20,22,25,0.94); color: #fff; border-radius: 5px;
       padding: 6px 9px; font-size: 12px; line-height: 1.45;
       box-shadow: 0 2px 8px rgba(0,0,0,0.25); white-space: nowrap; }
#tip .dur { font-weight: 700; font-size: 14px; }
#tip .lab { color: #b9c1c9; }
"""

TOOLTIP_JS = """
<div id="tip"></div>
<script>
(function () {
  var tip = document.getElementById('tip');
  function move(e) {
    var x = e.clientX + 14, y = e.clientY + 16;
    var r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  document.addEventListener('mouseover', function (e) {
    var t = e.target;
    if (!t.classList || !t.classList.contains('span')) return;
    tip.innerHTML = '<div class="dur">' + t.dataset.dur + '</div>' +
      '<div>' + t.dataset.range + '</div>' +
      '<div class="lab">' + t.dataset.label + '</div>';
    tip.style.display = 'block';
    move(e);
  });
  document.addEventListener('mouseout', function (e) {
    if (e.target.classList && e.target.classList.contains('span')) tip.style.display = 'none';
  });
  document.addEventListener('mousemove', function (e) {
    if (tip.style.display === 'block') move(e);
  });
})();
</script>
"""


def build_html(games: list[Game], all_results: dict) -> str:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Dead-time: rule-based variants vs human</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>Condense variants vs human labels</h1>",
        "<p class='sub'>Ground truth is the in-play spans; dead time is the complement. "
        "Both ends of the ladder ship from <code>ml/pipeline/dead_time.py</code>: "
        "<code>v0</code> is <code>condense_mode=\"rules\"</code> and "
        f"<code>{list(VARIANTS)[-1]}</code> is <code>condense_mode=\"guarded\"</code>, "
        "the default. The rungs between them are prototypes in "
        "<code>ml/eval/deadtime_variants.py</code>. Hover any block for its duration "
        "and timestamps.</p>",
        LEGEND,
        render_summary(all_results),
    ]
    parts.extend(render_game(g, all_results[g.test_id]) for g in games)
    parts.append(TOOLTIP_JS)
    parts.append("</body></html>")
    return "\n".join(parts)


def main() -> None:
    logging.disable(logging.INFO)
    games = [load_game(tid) for tid in TEST_IDS]
    all_results: dict[str, dict[str, tuple[list[Interval], DeadTimeSignals]]] = {}
    for g in games:
        all_results[g.test_id] = {
            key: (windows, evaluate_deadtime(g.human_keep, windows, g.duration))
            for key, (_, fn) in VARIANTS.items()
            for windows in [fn(g)]
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build_html(games, all_results), encoding="utf-8")
    print(f"Wrote {OUT_PATH}\n")

    header = f"{'variant':<46}{'net':>8}   " + "".join(f"{t:>28}" for t in TEST_IDS)
    print(header)
    for key, (label, _) in VARIANTS.items():
        total = sum(net_seconds(all_results[t][key][1]) for t in TEST_IDS)
        cells = ""
        for t in TEST_IDS:
            s = all_results[t][key][1]
            cells += f"{'dead ' + _pct(s.dead_removed_pct) + '  live -' + f'{s.live_removed_sec:.0f}s':>28}"
        print(f"{key + ' ' + label:<46}{total:>+7.0f}s   {cells}")


if __name__ == "__main__":
    main()

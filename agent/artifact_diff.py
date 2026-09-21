"""Build a git-style unified diff between two versions of a text artifact.

The Slack Code Code tab (`setView type:"diff"`) takes a complete unified-diff
string; Slack's client renders the filename banner, the +N/-N counts, and the
hunks from that string (and folds long diffs with "Show N more lines" on its
own). We generate the diff here with the stdlib (no dependency):

  - First turn (prev == ""): the whole file as additions — the
    ``@@ -0,0 +1,N @@`` all-green diff the screenshots show (`snake-game.html
    +310 -0`).
  - Follow-up turns: an incremental diff of the new file against the previously
    stored version — so the Code tab shows exactly what that request changed
    (the "living/breathing" behavior).

We always send the COMPLETE diff (Slack `setView` is a replace/upsert, not an
append). Full-file restatement by the agent is what lets us diff reliably; the
diff itself is incremental after turn 1.
"""

from __future__ import annotations

import difflib


def unified(prev: str, new: str, filename: str) -> str:
    """Return a git-style unified diff turning ``prev`` into ``new``.

    ``filename`` is the artifact's name (e.g. ``snake-game.html``); it appears in
    the ``diff --git a/<f> b/<f>`` header and the client's file banner. On the
    first turn pass ``prev=""`` for an all-additions diff.

    Returns "" when there is no change (identical content) — the caller skips
    re-publishing the Code tab in that case.
    """
    if prev == new:
        return ""

    # Split WITHOUT line endings and pass lineterm="" so difflib emits every line
    # (headers ---/+++/@@ AND content) with no trailing newline; we then join with
    # "\n". Using keepends=True with lineterm="" is the classic footgun — the
    # header lines get no newline and run into the content (verified). This way
    # every emitted line is clean and we control the separators.
    a = prev.splitlines()
    b = new.splitlines()
    lines = list(
        difflib.unified_diff(
            a, b,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )
    header = f"diff --git a/{filename} b/{filename}"
    return "\n".join([header, *lines]) + "\n"


def added_count(new: str) -> int:
    """Line count for a first-turn full-add diff (cosmetic; the client computes
    its own +N, but handy for tests/summaries)."""
    return len(new.splitlines())

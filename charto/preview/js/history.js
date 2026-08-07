/* Charto preview — undo / redo.
 *
 * TradingView's pair, on TradingView's terms: Ctrl+Z reverses the last thing
 * you did TO the chart, and Ctrl+Shift+Z puts it back. What that covers is
 * deliberately narrow and stated in one place — the WORKSPACE, meaning the
 * three sets of objects a user builds up over a session:
 *
 *   · drawings      what you drew, where you dragged it, what you deleted
 *   · scene         what the chat drew — levels, zones, patterns, profiles
 *   · indicators    which studies are on the chart
 *
 * and NOT the view. Panning, zooming, changing the interval or the instrument
 * are not edits, and a chart that scrolled back under Ctrl+Z would be a
 * different feature wearing this one's name. TradingView draws the same line.
 *
 * ── how it works ────────────────────────────────────────────────────────
 * Snapshots, not commands. Every mutating path in the app already ends in a
 * save or an event, so instead of teaching each one how to invert itself,
 * they all call `touch()` and this module reads the whole workspace and
 * compares it against the top of the stack. An action nobody remembered to
 * wire is therefore *still* undoable the moment it persists — the failure
 * mode is a missing entry, never a corrupt one.
 *
 * Two properties the rest of the app depends on:
 *
 *   · touches COALESCE. One user action fans out into several saves and
 *     events (an indicator add fires a change event, a re-render and a
 *     save); a frame's worth of them is one history entry, or Ctrl+Z would
 *     take three presses to undo one click.
 *   · a WRITE is not a touch. Restoring a snapshot fires exactly the same
 *     events as the edit that created it, so the module goes deaf for the
 *     duration — including across the await an indicator restore needs.
 *     Without that, undo would push its own result onto the stack and the
 *     history would never move.
 *
 * Session-scoped on purpose: the stack is not persisted. A reload is a new
 * session, and offering to undo an edit made before a page you no longer
 * have on screen is a promise this cannot keep.
 */
"use strict";

const Undo = (() => {
  // Deep enough to cover a working session, shallow enough that the snapshots
  // (a few hundred drawing anchors at worst) stay small in memory.
  const LIMIT = 60;

  let read = null, write = null;
  let past = [], future = [], present = null;
  let restoring = false, queued = 0;
  // Writes are SERIALISED, not refused. Undo is a key you hold down: a
  // restore takes a frame or two (an indicator has to be recomputed), and
  // rejecting presses for that long silently swallowed every second Ctrl+Z.
  // The stack pointer moves synchronously, so hammering it walks back N
  // steps and the writes replay in order behind it.
  let chain = Promise.resolve(), inflight = 0;
  const subs = [];

  const clone = (x) => JSON.parse(JSON.stringify(x));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  const state = () => ({ canUndo: past.length > 0, canRedo: future.length > 0 });
  const emit = () => { const s = state(); for (const f of subs) f(s); };

  /** Put a snapshot back, deaf to everything the restore itself sets off. */
  function apply(snap) {
    inflight++;
    restoring = true;
    if (queued) { cancelAnimationFrame(queued); queued = 0; }
    chain = chain
      .then(() => write(clone(snap)))
      .catch((e) => console.warn("[charto] undo write failed", e))
      .then(() => new Promise((r) => requestAnimationFrame(() => {
        // One frame past the last event this write fired — a touch queued
        // inside it would otherwise land after the flag cleared. Only the
        // LAST write in a burst re-opens the ear; an earlier one finishing
        // while a later is still queued must not.
        if (--inflight === 0) restoring = false;
        r();
      })));
    return chain;
  }

  return {
    /** Wire the one workspace this page has. Called ONCE, at the end of boot:
     *  everything restored from storage is the starting position, not a move,
     *  so binding earlier would leave a fresh tab able to "undo" its own
     *  session back to an empty chart. */
    bind(opts) {
      read = opts.read;
      write = opts.write;
      present = clone(read());
      past = []; future = [];
      emit();
    },

    /** Subscribe to the enabled/disabled state. Fires immediately, so a
     *  button wired before bind() starts out correctly greyed. */
    onChange(fn) { subs.push(fn); fn(state()); },

    /** Something changed. Cheap to call from anywhere, and safe to call
     *  before bind() or during a restore — both are no-ops. */
    touch() {
      if (!read || restoring || queued) return;
      queued = requestAnimationFrame(() => {
        queued = 0;
        const next = clone(read());
        if (same(next, present)) return;   // a save that changed nothing
        past.push(present);
        if (past.length > LIMIT) past.shift();
        present = next;
        // A new edit forks the timeline: what was undone is no longer
        // reachable, which is the one rule every undo stack shares.
        future.length = 0;
        emit();
      });
    },

    /** @returns true when there was something to undo. */
    undo() {
      if (!read || !past.length) return false;
      future.push(present);
      present = past.pop();
      apply(present);
      emit();
      return true;
    },

    redo() {
      if (!read || !future.length) return false;
      past.push(present);
      present = future.pop();
      apply(present);
      emit();
      return true;
    },

    get canUndo() { return past.length > 0; },
    get canRedo() { return future.length > 0; },
  };
})();

/* Charto preview — the conversation's hands on the workspace.
 *
 * The chat's `workspace` tool (data/dataserver.py) verifies a list of ops
 * against the catalog this file sends, runs anything that is business data
 * on the server (a screen through the screen engine, code through the
 * validator), and streams the ops here as `view_op` events while the model
 * is still answering. This file applies them through Dock.agent — the desk
 * assembles as the reply is written — and remembers how each one went, so
 * the next turn's envelope can say "op 3 failed: …" instead of the model
 * assuming it worked.
 *
 * What the model sees of the workspace is only what envelope() sends: each
 * widget's id, kind and one line of what it shows (its own "Ask in chat"
 * summary), the fuller content for read(), and the catalog of kinds. No DOM,
 * no raw config.
 *
 * Every turn that changes the desk is one Undo: the dock's state is
 * snapshotted before the turn's first op and restored on request.
 */
"use strict";

const AgentWS = (() => {
  const ok = () => typeof Dock !== "undefined" && Dock.agent;
  const READ_MAX = 3000;
  let ack = [];                 // outcomes of the last turn's ops, for the next envelope
  const undo = new Map();       // turn key → dock snapshot taken before its first op
  const applied = new Set();    // op seq numbers already applied from the stream

  /** What rides on every chat request. Small: the catalog is request bytes,
   *  never prompt tokens — the server serves it only through `describe`. */
  function envelope() {
    if (!ok()) return null;
    let widgets = [];
    try {
      widgets = Dock.agent.manifest().map((w) => {
        const got = Dock.agent.read(w.id);
        return { ...w, content: got && got.context ? String(got.context).slice(0, READ_MAX) : "" };
      });
    } catch (e) { console.warn("[agentws] manifest", e); }
    const out = { widgets, ack };
    ack = [];
    let catalog = [];
    try { catalog = Dock.agent.catalog(); } catch (e) { console.warn("[agentws] catalog", e); }
    return { workspace: out, widget_catalog: catalog };
  }

  /** Apply one streamed `workspace` op group: { seq, ops: [...] }. */
  function apply(group, turnKey) {
    if (!ok() || !group || applied.has(group.seq)) return [];
    applied.add(group.seq);
    if (turnKey && !undo.has(turnKey)) {
      try { undo.set(turnKey, Dock.agent.snapshot()); } catch { /* nothing to undo to */ }
    }
    const results = [];
    for (const [i, op] of (group.ops || []).entries()) {
      const r = Dock.agent.apply(op);
      results.push({ seq: group.seq, i, op: op.op, id: r.id || op.id || null, ok: r.ok !== false,
                     ...(r.error ? { error: String(r.error).slice(0, 240) } : {}) });
      if (r.ok === false) console.warn("[agentws] op failed", op, r.error);
    }
    ack.push(...results.filter((r) => !r.ok));
    ack = ack.slice(-12);
    return results;
  }

  const canUndo = (turnKey) => undo.has(turnKey);
  function revert(turnKey) {
    const snap = undo.get(turnKey);
    if (!snap || !ok()) return false;
    Dock.agent.restore(snap);
    undo.delete(turnKey);
    return true;
  }

  return { envelope, apply, canUndo, revert };
})();

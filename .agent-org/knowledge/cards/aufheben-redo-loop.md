---
name: aufheben-redo-loop
type: decision
source: 20260611-093609-8071de1
status: active
---

Aufheben emits one of three decisions: proceed, redo, or escalate.
Proceed keeps the existing implementation contract path unchanged.
Redo and escalate use the small aufheben verdict schema.
The controller owns the loop and re-invokes only named designers.
Each redo carries a specific redo_brief as appended input.
MAX 2 redo rounds before treating the result as escalate.
Record redo rounds and verdicts in run notes.
redo_max=2 is provisional, set to gather data; revise when thrash or premature-escalation patterns emerge.

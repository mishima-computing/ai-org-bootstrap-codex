# RFC phase current flow

The RFC phase has two isolated responsibilities:

- `receive.py` turns a raw common-8 request into a grounded RFC branch, or sends it back with clarification questions.
- `review.py` debates the direction of an already-formed `rfc.json` and commits `direction-ok` or `nak`.

```mermaid
flowchart TD
  REQ["REQUEST (common-8)<br/>title, problem, proposal, alternatives,<br/>intended_users, affected_area, impact, context"]
  REQ -->|"intake(): receive() validates title + problem"| V{valid?}
  V -->|no| REJ["status: rejected<br/>error"]
  V -->|yes| GROUND["_ground_request in receive.py<br/>read-only codex + web_search<br/>research subject, genre, prior art, and repo"]
  GROUND --> S{sufficient?}
  S -->|no| CLARIFY["status: needs_clarification<br/>questions[] + grounding_notes<br/>no RFC branch"]
  S -->|yes| PROD["produce_rfc(): write grounded rfc.json<br/>on ai-org/rfc/&lt;id&gt;"]

  PROD --> RUN["run_rfc_review(repo, id)"]
  RUN --> READ["git read: rfc.json @ ai-org/rfc/&lt;id&gt;"]
  READ -->|unreadable| NAK0["commit: rfc: nak (0 rounds)"]
  READ -->|ok| LOOP{{"round = 1 .. CAP (5)"}}
  LOOP --> REV["5 reviewers, read-only codex:<br/>NEED, APPROACH, COMPAT, SCOPE, MAINTENANCE"]
  REV --> Q{any unresolved objection?}
  Q -->|no| DOK["commit: rfc: direction-ok (N rounds)"]
  Q -->|yes| AUF["_aufheben_consolidate<br/>synthesize objections into revised_rfc"]
  AUF -->|escalate| NAK1["commit: rfc: nak"]
  AUF -->|proceed| REVISE["revised_rfc becomes current_view"]
  REVISE -->|"rounds < CAP"| LOOP
  REVISE -->|"rounds == CAP"| NAK2["commit: rfc: nak"]

  DOK --> NEXT["patch phase pulls the direction-ok RFC"]
  REJ --> BACK["back to requester"]
  CLARIFY --> BACK
  NAK0 --> BACK
  NAK1 --> BACK
  NAK2 --> BACK

  classDef ok fill:#d6f5d6,stroke:#2a7;
  classDef bad fill:#f8d6d6,stroke:#a33;
  classDef codex fill:#e6e6fa,stroke:#66c;
  class DOK,NEXT ok;
  class REJ,CLARIFY,NAK0,NAK1,NAK2,BACK bad;
  class GROUND,REV,AUF codex;
```

## Notes

- `intake(request, repo)` is the public entrance for raw requests. It returns `status: promoted`, `status: needs_clarification`, or `status: rejected`.
- Grounding belongs to intake because it forms the RFC. It may correct a wrong request, such as a mistaken genre reference, before a branch exists.
- If grounding cannot confidently identify the intended subject or scope, intake returns specific requester questions and does not create `ai-org/rfc/<id>`.
- `review.py` assumes `rfc.json` is already grounded. It only runs the five-reviewer and Aufheben direction debate.
- Codex output schemas remain codex-valid: no `allOf`, `anyOf`, or `oneOf`; `additionalProperties` is false; `required` lists every property.

# RFC phase current flow

The RFC phase has three isolated responsibilities:

- `submit.py` is the requester-facing entrance. It writes raw requests to the off-git inbox at `<repo>/.ai-org/inbox` or `AI_ORG_INBOX` and prints a receipt.
- `receive.py` accepts a raw request with only `raw_request` required, grounds it into the research-derived RFC field registry, or sends it back with a proposed interpretation and assumptions to confirm or correct.
- `review.py` debates the direction of an already-formed `rfc.json` and commits `direction-ok` or `nak`.

```mermaid
flowchart TD
  REQ["REQUEST<br/>raw_request required"]
  REQ --> SUBMIT["python -m ai_org.rfc.submit<br/>write off-git inbox record<br/>print id + path"]
  SUBMIT --> PULL["python -m ai_org.rfc / pull(repo)<br/>oldest unprocessed inbox item"]
  PULL -->|"intake(): receive() validates raw_request"| V{valid?}
  V -->|no| REJ["status: rejected<br/>error"]
  V -->|yes| GROUND["_ground_request in receive.py<br/>read-only codex + web_search<br/>research subject, genre, prior art, and repo"]
  GROUND --> S{confident?}
  S -->|no| CONFIRM["status: needs_confirmation / needs_work<br/>processed inbox result<br/>no RFC branch"]
  S -->|yes| PROD["produce_rfc(): write grounded registry rfc.json<br/>and technical-approach.json<br/>on ai-org/rfc/&lt;id&gt;"]

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
  REJ --> INBOXRESULT["processed/&lt;id&gt;.result.json<br/>requester status receipt"]
  CONFIRM --> INBOXRESULT
  NAK0 --> BACK
  NAK1 --> BACK
  NAK2 --> BACK
  INBOXRESULT --> BACK["back to requester"]

  classDef ok fill:#d6f5d6,stroke:#2a7;
  classDef bad fill:#f8d6d6,stroke:#a33;
  classDef codex fill:#e6e6fa,stroke:#66c;
  class DOK,NEXT ok;
  class REJ,CONFIRM,NAK0,NAK1,NAK2,BACK,INBOXRESULT bad;
  class GROUND,REV,AUF codex;
```

## Notes

- `python -m ai_org.rfc.submit <repo> <request>` is the public requester entrance. `<request>` may be a JSON file path, a JSON object string, or plain text. Plain text becomes `{"raw_request": <text>}`.
- `submit.py` creates `<repo>/.ai-org/inbox/` by default and appends `.ai-org/` to the target repo's `.gitignore` when the entry is absent. It does not commit or stage the `.gitignore` change. If `AI_ORG_INBOX` is set, that external inbox path is used instead.
- `pull(repo)` processes one unprocessed inbox file before it scans `ai-org/rfc/*` branches for review. If the inbox is empty, pull behaves as before.
- `intake(request, repo)` remains the receive gate for raw requests. It returns `status: promoted`, `status: needs_work`, `status: needs_confirmation`, or `status: rejected`.
- Grounding belongs to intake because it forms the RFC. It may correct a wrong request, such as a mistaken genre reference, before a branch exists.
- A promoted request is the first git artifact: `ai-org/rfc/<id>` with `rfc.json` and `technical-approach.json`. Needs-work and rejected requests do not create git branches; their status is written to `.ai-org/inbox/processed/<id>.result.json`.
- The RFC handoff shape is the in-code field registry: `raw_request`, `working_title`, `request_type`, `problem_or_motivation`, `intended_users_or_jobs`, `desired_outcomes_success`, `affected_area_platform`, `tech_stack`, `background_facts`, `constraints_assumptions`, `references`, `grounding_provenance`, `open_questions`, `non_goals_out_of_scope`, `proposal_hint`, and `alternatives_considered`.
- Each registry field carries `role`, `belongs`, `must_not`, `owner`, and `required_at`. The `must_not` text is the anti-dumping gate: research audit trail belongs in `grounding_provenance`, bounded domain facts belong in `background_facts`, external pointers belong in `references`, and `context` is no longer an RFC intake field.
- If grounding cannot confidently identify the intended subject or scope, intake still returns its best grounded guess as `proposed_rfc`, lists the `assumptions` behind that guess, and asks the requester to confirm or correct it. `questions` are only for gaps that research genuinely could not infer.
- `review.py` assumes `rfc.json` is already grounded. It only runs the five-reviewer and Aufheben direction debate.
- Codex output schemas remain codex-valid: no `allOf`, `anyOf`, or `oneOf`; `additionalProperties` is false; `required` lists every property.

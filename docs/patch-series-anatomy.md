# patch series の解剖図 — promoted DQ patch series（run14 実物）の JSON 構造

「ドラクエをつくって」一行から org が生成した実物の構造。数字は全て run14 の実測。

## 全体像 — patch series branch に載る一式

```mermaid
flowchart LR
    REQ["📨 依頼<br/>『ドラクエをつくって』<br/>(off-git inbox)"] --> BRANCH

    subgraph BRANCH["patch series branch (git が正本)"]
        patch series["patch-series-cover-letter.json<br/>17 fields · 22KB<br/><i>何を作るかの契約</i>"]
        TA["technical-approach-plan.json<br/>288KB<br/><i>どう作るかの導出木</i>"]
        DS["domain-spec/*.json<br/><i>ドメイン数値の器</i>"]
        SP["spine/ (新設)<br/>art-bible.json<br/>asset-manifest.schema.json<br/><i>子が相続する凍結契約</i>"]
        LN["patch-series-manifest.json<br/><i>節の権威 manifest</i>"]
    end

    patch series -.->|"手引き"| TA
    TA -.->|"数値の置き場"| DS
    style patch series fill:#1a4b6b,color:#fff
    style TA fill:#4b1a6b,color:#fff
    style SP fill:#6b4b1a,color:#fff
```

## patch-series-cover-letter.json — 17 フィールド（field registry 統治）

```mermaid
mindmap
  root((patch-series-cover-letter.json))
    入口
      raw_request 「ドラクエをつくって」verbatim
      request_type
    正体
      working_title ドラゴンクエスト本編再現ゲーム
      problem_or_motivation
      intended_users_or_jobs
      desired_outcomes_success 完全な冒険=タイトル→エンディング→クリア後
    技術
      tech_stack 構造タグ: React/TS/browser + provenance
      affected_area_platform
    UX 11節
      applicability user_facing
      experience_identity / presentation_model
      action_feedback_matrix 13行
      acceptance_tests screenshot+interaction+playtest
    接地
      background_facts
      references dragonquest.jp 等
      grounding_provenance
    境界
      constraints_assumptions
      non_goals_out_of_scope クローン禁止=自力導出
      open_questions 非ブロッキング記録(復活済)
```

## technical-approach-plan.json — 導出木（IBIS/Toulmin 接地）

```mermaid
flowchart TD
    P["problem<br/>(normalize 済み問題)"] --> G["goals ×10<br/>actor+capability+検証法<br/>(manual 5 / automated 5)"]
    P --> C["constraints<br/>hard ×29 / soft ×6<br/>(ORG_BUILDER_PROFILE 前置注入込み)"]
    P --> PA["prior_art ×6<br/>← Reference facet 接地<br/>(battle FSM / 進行グラフ / save…)"]
    P --> Q["question:approach<br/>『どの実装方針か』"]
    Q --> CAND["candidates ×3<br/>(各: 構造化要件で評価<br/>authoring/verification model)"]
    CAND --> DEC["decision<br/>selected: react_dom_svg<br/>rejected ×2 (理由つき棄却)"]
    DEC --> IMP["implementation"]
    IMP --> SYS["systems ×14<br/>App Shell→戦闘FSM→<br/>進行グラフ→Final Arc"]
    IMP --> DSP["domain_specification<br/>aspects ×1 ← 一便目の弱点<br/>(#18 の旅が増やす)"]
    IMP --> PP["patch_plan<br/>first_playable+follow_ups<br/>+deferred+risks"]
    IMP --> RK["risks ×7<br/>実在ノードid に anchor<br/>(enum 強制)"]
    P -.->|"cross_links ×112<br/>(全ノード相互参照)"| Q

    style DSP fill:#8b6b1a,color:#fff
```

## 読み方の要点

- **patch-series-cover-letter.json は requester との契約**、technical-approach は **org 内の導出記録**——別ファイルなのは
  「契約の安定」と「改版の分離」のため（親の外形 request と子の内側、と同じ分離原理）。
- **cross_links ×112** が導出の監査可能性の実体——どの決定がどの goal/constraint から
  導かれたかを機械が辿れる。
- 黄色いノード（aspects ×1）が一便目の弱点だった場所——#18 の旅が combat formula や
  content budget をここに増築する設計（門番研修中）。

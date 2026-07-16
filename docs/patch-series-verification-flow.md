# patch series Verification Flow — 「ドラクエをつくって」正式便の検証計画

patch series は promote で「できた」ことにならない。**書くことで埋まる穴が尽き（buildable-as-is）、
審査を通り、採番されて**初めて完成であり、実装解禁はその後（requester 規律 2026-07-03）。

```mermaid
flowchart TD
    subgraph 形成["① 形成（実証済み）"]
        A["入口: ai_org.launch<br/>『ドラクエをつくって』"] --> B["grounding<br/>(意味verifier・fail-closed)"]
        B --> C["② Reference build<br/>(正準井戸 650+ terms)"]
        C --> D["③ 11-step approach<br/>+ open_questions(復活済)"]
        D --> E["spine 導出<br/>art-bible.json / manifest.schema"]
    end

    subgraph 門番["② 完全性の門番 (#18) — 初live で4欠陥検出→修正中"]
        E --> F["行分解<br/>受入行チェックリスト→個別行<br/>(D1: メガ行事件)"]
        F --> G["検問 (決定論・ms)<br/>pointer解決+形状検査"]
        G -->|不合格行| H["旅 round 1<br/>store lookup (語句単位)<br/>(D2: 照準事件)"]
        H -->|dry| I["旅 round 2<br/>reference.expand (外部)"]
        H -->|hit| J["fill 実着地<br/>domain-spec aspects<br/>(D3: 未着地事件)"]
        I --> J
        I -->|なお dry| K["declared-unresolved<br/>{row, なぜ書けない, 何が埋める}<br/>(D4: 付箋事件)"]
        J --> L{"promote 検問"}
        K --> L
        L -->|filled or declared| M["✅ promote<br/>patch series branch: patch-series-cover-letter.json+approach<br/>+domain-spec+spine/"]
        L -->|undeclared+unfilled| N["⛔ needs_work<br/>(一便目はここで停止)"]
    end

    subgraph 検証["③ 検証（promote 後・これから）"]
        M --> O["第3次探索隊<br/>『この package だけで、質問ゼロで<br/>作り始められるか』"]
        O -->|buildable-with-fills| P["生成器修正→再走<br/>(いつものループ)"]
        O -->|buildable-as-is| Q["review 器官<br/>5軸+Aufheben — serial 0002 から<br/>org 自身の審査を通す(規律)"]
        Q -->|objections| R["v2 形成<br/>(著者側が改版)"]
        R --> Q
        Q -->|direction-ok| S["採番 ai-org/serial/0002<br/>(annotated tag)"]
        S --> T["宣言済み残gapの処置審査<br/>『build でしか埋まらない』に<br/>探索隊が同意するか"]
        T --> U["🏁 patch series 完成<br/>= 実装解禁"]
    end

    P -.-> A

    style N fill:#8b1a1a,color:#fff
    style U fill:#1a6b2a,color:#fff
    style M fill:#1a4b6b,color:#fff
```

## 検証の合格基準（批准済み）

| 段 | 判定者 | 基準 |
|---|---|---|
| 検問 | 決定論 (harness) | 全行 filled or declared |
| 探索隊 | 敵対的 codex (unled) | 「書くことで答えられた質問」がゼロ |
| review | org の審査器官 | direction-ok（serial 0002 から必須経路） |
| 残gap処置 | 探索隊+review | 宣言の正当性（build でしか埋まらぬこと）に同意 |

一便目の実績: 形成◎ / 門番=正しく拒否したが4欠陥（修正wave 走行中）/ 検証=未到達。

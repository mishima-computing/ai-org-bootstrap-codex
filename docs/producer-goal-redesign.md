# Producer-Goal Redesign (Success Criteria 見直し) — 設計材料集

Ratified 2026-07-12: **CUE化（Phase-2）完了直後の最優先実装項目**。
JSON スキーマへの後付けはしない — CUE の body 型設計として入れる（体用批准に従う）。

## 1. The defect (evidence-established, not speculation)

- 現行 goal 型 `success_criteria` = `{actor, capability{action,preconditions}, verifiable_outcome{expected_state,evidence}, verification{method∈automated_test|manual_check|metric, check}, ux_trace}` — **referee-only**。
- 実害: pixel-artisan run で入力 goal は「描け・出力しろ」（生産動詞のみ）だったのに、正規化後の 7 goals の actor が全員 inspector/reviewer/validator。「作る」仕事は `preconditions`（"Asset generation has completed successfully"）に沈み、全 candidate が生成手段を注入ポートに抽象化し、`deferred[3]` で正式に後回し → craft は誰にも task されなかった。
- 経緯（一次記録で確定）: 2026-07-01 16:29 `ea0487a` が「success_criteria: checkable statements for **what solved looks like**」として誕生させ（referee 定義が命名時に確定）、同日 20:03 `a4441d9` が測定可能性強制のため nested 化。requester 指示は「構造で保証」（正当）で、それを実装者が「測定可能な**受け入れテスト**」genre に翻訳した瞬間にバイアス混入。出典 methodology 引用なし（人間開発の acceptance-criteria 慣習の無意識輸入 — 人間チームでは producer が暗黙にフルタイムで存在するため成立していた前提が、AI Org には無い）。
- 下流の kind enum（2026-07-03 追加→07-04 廃止, `47958c2`）は別件: routing typology として正しく殺された。producer/commission 機構は content 検出（`patch_series_gate.py` `_is_commission_request`）へ退避し生存しているが、**上流 goal-model が "commission" 語を吐かないため発火せず飢える**。

## 2. Prior-art convergence (6 research strands, all pointing at the same shape)

| 系統 | 貢献 |
|---|---|
| **GORE/KAOS** | 完了規則: 「goal 精緻化は各 leaf goal が単一 agent に realizable に割当できた時点で終わる」（realizability = agent が帰結を**制御**できる。検査者は観測のみ→全 actor が unrealizable assignment）。requirement（割当済）vs expectation（誰も所有しない仮定）の区別 — 現行 preconditions は後者。means-end/operationalization リンク（手段 slot）。KAOS の死因は人間経済（モデル維持費・agile 代替・ツール商業死）で**構文の欠陥ではない**（RE'17 Mavin et al. 他）。LLM 時代に蘇生中（QUARE, 4D-ARE）。4D-ARE の警告: realizability は決定論 agent 前提 → 確率的 carrier では**割当は検証と対**（既存 acceptance gate がその相方）。 |
| **形式手法** | 理論的根拠: referee-only = witness なき古典的存在主張。直観主義の existence property（∃の証明は witness 構築を含む）を欠くため、検査をいくら足しても産出義務が生じない。実装形は **Make/Nix 型**（宣言 output + 生産レシピ + 鮮度検査を1構文 — Impl2 の `real_state_outputs_fresh` と同語彙）。精緻化計算の specification statement `w:[pre,post]` = 「埋めるべき穴」を第一級項に。DbC は postcondition を**supplier の義務**として当事者に紐づける。 |
| **MAS/Contract Net (Smith 1980)** | task announcement {task abstraction, eligibility spec（**自己判定**述語・worker 型ラベルでない）, bid spec, expiration} + mutual selection = 「routing is contention」と同型のまま producer を発注する既製構文。**zero-bid 診断（§IV-B）**: 全員に BUSY/INELIGIBLE/LOW-RANKING の理由表明を強制→理由別処方（待つ/eligibility 緩和/魅力増/manager 自走）。FIPA 版の閉ループ performative（refuse/failure/**inform-done**）。**見送り**: decommitment 違約金・限界費用入札・eager-bidder 統計（戦略的利己 agent の薬、carrier に病気なし）。 |
| **発話行為論 (Winograd&Flores CfA)** | 5状態 (1)request→(2)promise→(3)perform→(4)assert-done→(5)declare-accepted。**現行 schema は (4)(5) のみを形式化し (1)(2)(3) を削除した形**。 |
| **RACI/組織論** | 「A（承認者）のみで R（実行者）不在の matrix は、完了しない仕事に承認が待ち続けて停止」— 現行 goal リスト（7 referee, 0 producer）の failure mode が名前付きで文書化済み。 |
| **Linux kernel** | producer-voice core（changelog =「何を作ったか・なぜか」命令法 + Signed-off-by）が**先に完成**し、referee trailer（Reviewed-by/Acked-by）は**後から別人が積む**。referee-only goal = 「Reviewed-by 基準から producer の changelog を削除したもの」。 |
| **AI駆動開発** | referee-only は spec 系の支配形（Spec Kit/Kiro/SWE-bench 全部同病）。producer を第一級で座らせたのは role-org（MetaGPT の Requirement Pool + Engineer 割当）のみ。「producer が goal にならない」という名指し診断は先行例に見当たらない。SpecBench: test-pass 最適化は仕様充足の指標性を失う。 |

## 3. Convergent producer-goal shape (design input, not yet ratified as CUE types)

deliverable を要求する goal には、referee criterion と**対で**:

1. **deliverable 記述** — 作られるべき物（CNP task abstraction / CfA conditions of satisfaction）。worker 型・kind typology は書かない。
2. **eligibility 述語** — 取り手が自己判定する能力条件（CNP。routing ラベルではない）。
3. **commitment slot** — 空=announced、充填=commissive（contention 両立: mutual selection、ロックなし）。
4. **producer 完了主張** — 成果 evidence つき assertive（CNP result description / FIPA inform-done）。means（どの手段で作ったか）を含む。
5. **既存 referee 受理** — (4) の report を消費する形に接続（別立てで残す。kernel の分離規律）。
6. **expiration/期限** ＋ zero-bid 時の理由表明強制（BUSY/INELIGIBLE/LOW-RANKING）→ 理由別エスカレーション。

決定論強制: **KAOS 完了規則を cue vet に** — deliverable を要求する goal に (1)–(4) が立つまで分解を閉じない（unassigned deliverable を gate が機械的に拒否）。

## 4. Guards（再発防止の設計制約）

- **typology 再輸入禁止**: kind enum は condemned pattern（vibes classifier choosing a schema）。producer は「型を当てる」でなく「作る役が存在する」ことだけを保証する。
- **referee を弱めない**: 検査 goal は正しい。欠けているのは生産 actor であって、検査の過剰ではない。確率的 carrier では割当は検証と対（4D-ARE）。
- **人間経済の補償機構を輸入しない**: Linux Community と機械世界（CNP）で実際に生き残った機構のみ輸入（substrate-check）。

## 5. Provenance

- salvage 検死: pixel run `/tmp/pixel_artisan` (`run-20260712T005055Z-2d84905d`)、判定 = 落下は上流 goal 具体化不足でなく (ii) plan構築の silent drop + (iii) 手段未検討。
- 策定一次会話: 2026-07-01、`~/.gemini/antigravity-cli/brain/10f40da3-*/claude_conversation_backup.jsonl`（494MB、06-11〜07-07 連続）から復元。抜粋: scratchpad `formulation_0701.txt`。同日に requester が「作る気ないだろ！ｗ」と producer 不在を既に指摘していた記録あり。
- 研究レポート原文: 本 session の task outputs（揮発）→ 本ファイルが永続要約。

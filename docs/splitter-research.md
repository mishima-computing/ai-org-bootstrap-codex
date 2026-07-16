# Splitter リサーチ統合 — 2026-07-13（bias なし3体、全一次ソース接地）

status: 研究材料（goal 起草は producer-goal 改修 hand-off 後）。
**起草順序（requester 2026-07-15）: まず下記「重心」の要件を満たすのが goal 本体。4面文献は
その要件を満たすのに要る範囲で参照するだけ（先に文献を並べて背骨にしない）。**

## 重心（requester 2026-07-15・最重要・下の文献より優先して読む）
**改修の本丸 = CUE化の恩恵を splitter に活かすこと。** CUE 体は flat-diff（1行1事実・安定ID・
forward-only）で union 自動 merge できるよう設計済み。**codec を gitattributes で CUE body の
merge driver に登録**すれば「同じ body を触る2葉でも違う事実（違う安定ID行）なら conflict しない」＝
**merge-conflict の粒度がファイル単位→事実（行）単位に下がる**。ところが現行 splitter は
write_scope=`sub/<leaf>/`（サブツリー丸ごと＝ファイル粒度）で切り、この細粒度並列を捨てている。
裏取り: leaf7×8 の evidence 衝突（implementation-cue/・implementation-result.json ~50 file）を
「kernel 型 merge 除外」で逃がしたのは、まさにファイル粒度 conflict の band-aid。codec が merge
driver なら item_id union で衝突せず両葉が同 body 共有のまま並列できた。
**requester 判定(07-15): 下の4面統合は無意味でないが意義が薄い——「研究が済んだ」からではなく、
CUE mergeability という観点が欠けた調査だから。∴ 文献下調べは未完（積み増し不要ではない）。**

**観点駆動の下調べ = merge driver 側の prior art【2026-07-15 実施済み・結論 GO(条件付き)】**
（下記「merge-driver 下調べ結果」節に詳細。Q2(split) の実装可否は requester 判断待ち＝研究は GO を示すが着手は保留）
- (a) git 独自 merge driver / gitattributes（union・semantic driver、exit code が衝突定義）
- (b) CRDT / Operational Transform（事実粒度の無衝突並行編集の理論）
- (c) 安定ID keyed union merge の設計原則（どう body を設計すれば行単位 merge が clean か）
- (d) merge 前提で設計された書式の前例（towncrier news fragment・Cargo.lock 等 lockfile union・git-annex）
- (e) 構造化データ（CUE/JSON/YAML）の semantic merge ツールと false-negative リスク

## merge-driver 下調べ結果（2026-07-15・結論 = GO, gated）
**判定: fact-granular CUE merge driver（codec を安定ID-keyed な git merge driver として gitattributes 登録）は proven-shaped。採用推奨、ただし2条件で gate。**
- **前例が用途一致**: git `merge=union`（最も素朴）→ mergiraf（tree-sitter, JSON/YAML/code, drop-in driver）→ **weave**（entity-level driver、"independent agents editing the same file"＝**うちの用途そのもの**、identity=name+type+scope で照合、false conflict ~95%減／31シナリオで weave 100% vs mergiraf 83% vs git 48%）。既存/公式を先に＝weave/mergiraf の key-matching を借用、bespoke を発明しない。
- **driver 契約 = 提案通り**: `%O`(ancestor)/`%A`(ours)/`%B`(theirs)、exit 0=clean/非0=conflict。CRDT/OT 理論（WOOT/Logoot, INRIA）が保証する収束は「**安定・大域一意・全順序の ID ＋非破壊(idempotent) op**」で成立。state-based 3-way-by-key はその射影＝CRDT ランタイム不要。
- **body が守るべき3不変条件**: (i) 各 fact が安定・大域一意 ID を**行内に**持つ（identity は位置でなく行）。(ii) idempotent・append/forward-only・ID で正準ソート（reorder 由来の false-negative を殺す）。(iii) 同一 key の modify/delete 分岐は**依然 conflict**（disjoint-key のみ auto-merge）。
- **最大リスク = false-negative**: 構造化 merge は「論理的に衝突する fact を黙って auto-merge」する（Cavalcanti OOPSLA 2017、3万+ merge/50 project、semistructured は conflict を半減するが false-negative を約2倍に）。**guardrail = merge 後に必ず `cue vet` + acceptance vet**。driver は「テキスト調停」だけ担い、「妥当性」は決定論 gate が裁く（体用・log=view/git=decision と整合）。**driver の exit-0 単独を正しさにしない。**
- **推奨**: GO。非交渉の2条件 = (a) codec-write 時に上記3不変条件を強制（違反 body は reject）、(b) merge 後は常に cue vet + acceptance vet。(b) 無しは caution（masking 危険）、(b) 込みで clean go。
- 一次ソース: git gitattributes docs / Oster,Weiss WOOT&Logoot(INRIA) / Cargo PR#7070 / git-annex automatic_conflict_resolution / towncrier / ataraxy-labs/weave / mergiraf.org / Cavalcanti,Borba,Accioly OOPSLA2017 (10.1145/3133883)。

改修の追加背骨（この重心から）:
- write_scope を「どの body のどの安定ID行を書くか」の事実粒度で表現（サブツリー丸ごとをやめる）。
- conflict 審判 = 実際の CUE merge driver（codec）を回す（linux-next 型試し merge を codec driver 上で）。

## 主命題（requester 2026-07-13、逐語）
「はっきりいって分割の粒度は　GitでMergeしたときにConflictしない　以上でも以下でもありません」

## 相対化（3系譜）
1. 古典（Parnas 1972 / Stevens-Myers-Constantine 1974 / DDD / SRP / Conway）:
   全部「人間のコスト」（理解・波及・調整）の最小化。並行性は常に動機であって基準でない
   —「並行実装可能性を第一基準にした古典は見つからず」（3体一致）。
   → 古典の基準は write-set 非交差の代理変数。merge 審判が実在する org では原物を使える。
2. kernel/ビルド実務: 命題を既に生きている。
   - 所有権分割＋linux-next 日次全結合（衝突は予防でなく観測、失敗木は機械 drop）
   - 非対称原理: 過少宣言=ハードエラー即死（strict deps/layering_check/Nix sandbox）、
     過剰宣言=観測された実使用との差分で事後検出（unused_deps/.jdeps/DWYU）
   - 粒度下限: 「1 レビュー判断・1 bisect で白黒つく最大の塊」（500 patch 事例は明示批判）
3. プランニング理論: 命題の形式的な家。
   - 病名 1975 年既出: premature commitment（Sacerdoti NOAH、IJCAI-75）
   - causal-link 規律: 順序辺の出自は producer→consumer リンク or threat 解消のみ（Weld 1994）
   - Bäckström 1998: producer/consumer/threat 語彙なら最適 deordering は多項式
     （最初から最小順序は NP-hard）→ 書き手の正直さ不要、決定論後処理で幅回収
   - RAW vs WAR/WAW: 値が流れない順序は name dependence、renaming で消える
     （leaf7×8 evidence 衝突 = name dependence の実例、kernel 型除外はその解法の一種）
   - work-span: 並列度 = T₁/T∞。producer series 実測 13/10 = 1.3。CUE化も同型（2連続=機構の癖）。
     Span law: 不要順序の T∞ 増分は資源追加で回収不能
   - Erol 1994: 幅（順序未指定）は検証コストを買う（決定不能性）— ただし org の検証は
     plan-space 探索でなく leaf 契約＋merge 審判なので同じ形では効かない

## splitter 改修 goal の背骨（起草待ち）
1. 分割 = write_scope の非交差（審判 = git merge）。**訂正(07-15)**: write_scope field は
   producer-goal 改修の全 leaf が `allowed_subtree=sub/<leaf>/` で**活用中**（「未活用」は STALE）。
   ただし**ファイル/サブツリー粒度＝粗い**。CUE 細粒度 merge（→重心）は未活用。
2. depends edge = RAW のみ。edge に出自必須（前任が書く何の artifact を読むか）— cue vet 強制
3. 検針二段: ①決定論 deorder（出自語彙上の多項式後処理）②judge で宣言 vs 実読み照合
   （rollout の file read 記録 = org の .jdeps）
4. 試し merge = linux-next 型（並走 contrib の随時全結合、衝突は該当 leaf へ突き返し）
5. 質の計測: T₁/T∞ を series ledger に記録
6. 粒度下限 = 1 レビュー判断で白黒つく最大の塊（org の固定費 = receive/announce/accept per leaf）

## 一次ソース（主要）
Parnas: wstomv.win.tue.nl/edu/2ip30/references/criteria_for_modularization.pdf
Sacerdoti NOAH: ijcai.org/Proceedings/75/Papers/028.pdf
Weld 1994: cs.uky.edu/~sgware/reading/papers/weld1994introduction.pdf
Bäckström 1998: jair.org/index.php/jair/article/view/10210
Minton et al. 1994: cs.cmu.edu/afs/cs/project/jair/pub/volume2/minton94a.pdf
Erol/Hendler/Nau 1994: cs.umd.edu/~nau/papers/erol1994htn.pdf
Kelley-Walker 1959 / Amdahl 1967 / Brent 1974 / Blumofe-Leiserson 1999 / Cilk PLDI98
kernel: submitting-patches / 5.Posting / 2.Process / LWN linux-next 268881
Bazel SJD blog / unused_deps / layering_check / Buck2 dep files / Nix (Dolstra 2006)
Herbsleb & Grinter 1999: herbsleb.org/web-pubs/pdfs/herbsleb-splitting-1999.pdf

## 審判の物理（git merge conflict 調査、2026-07-13 追補）
- 衝突条件 = 編集域の重なり or 隣接0行（xmerge.c の `<` 厳密比較、git 2.54.0 実験で二重確認:
  gap0=CONFLICT / gap1=CLEAN）。**ファイルが違えば衝突は不可能**（ファイル単位独立判定）
  → write_scope のファイル粒度非交差 = 無衝突の十分条件
- 実証: 衝突の84.6% = 同一・連続行編集（Accioly 2018）。支配的予測因子は同時変更ファイル数のみ
  （Owhadi-Kareshk 2019）。spurious 28.97%（空白48%）
- **textually clean の33%が build/test 破壊**（Brun FSE 2011）→ A/B・test 層は必須（審判は必要条件）
- 衝突定義は差し替え可能: merge driver の exit code が定義（gitattributes 公式）。union は順序不定の代償
- **git-annex 前例**: 1行1事実・行内 key/timestamp・append-only → union で自動 merge 安全
  = 体用 flat diff 設計（1 fact/line・安定ID）と同型。将来: codec を CUE body の merge driver に
- AST merge: false conflict 24–79%減 / **false negative 約2倍**（20.6% vs 9.6%、Cavalcanti 2017）
  → 書式設計 > 構造 merge
- rerere/imerge = 解決コスト削減装置（衝突は消さない）。diff algorithm（ort 既定 histogram）で
  hunk 位置が変わり衝突有無も変わりうる
- 背骨最終形: ①ファイル粒度 write_scope で切る ②共有ファイルは書式武装（append-only/1行1事実/
  安定ID/union driver）③edge=RAW のみ・出自必須・judge+deorder 検針 ④意味は test が裁く
  ⑤T₁/T∞ を ledger 記録

## live 実例・JSON→CUE 因果・順序（requester 2026-07-15）
- **live 実例（producer-goal series 稼働中に観測）**: 全 leaf が同一 plan_id
  `canonical_root_producer_cohort`（root cohort）＋同一 item skeleton（first_proof +
  follow-up-01..12）を共有。leaf3/leaf4 とも同名 item_id（`...cohort#follow-up-01`）を
  **別 contrib branch に別内容で** commit（leaf3="Prepare Final Producer-Aware Root" /
  leaf4="Project Exact Series Scope"）。Splitter は deliverable（network manifest・内容別）は
  分けたが、**作業（item plan）は分けず root cohort を一律スタンプ**。item 数が各 leaf の実
  スコープに sized されず（全 leaf ~13）＝ item 数が template 由来＝**Potemkin リスク**。
  item_id 衝突が leaf3 で **resume-by-position hack を強いた根本**。
- **live 実例2: 98% 重複 precedent 検索（分割起因、requester 2026-07-15 観測→定量確認）**: producer-goal
  全 leaf run 横断で precedent 検索 **3407 回 / distinct term 60 だけ ＝ 98% 重複**（`Acceptance
  Authority Notes Ref` ×110、`ImplementationResult` ×108、`Item Commit Trace` ×100、同 (repo,path)
  も反復 fetch）。root = uniform-cohort split で leaf の scope が重なり各 leaf が同じ term を延々検索。
  副次: item 毎に走る precedent 検索が SQLite キャッシュありでも search intent の反復を止めていない
  （cache は band-aid、根は split）。実害 = 時間/token ＋ gh code search 3407 回で rate-limit 圧迫
  （`AI_ORG_GH_SEARCH_PER_MIN=20` 設定済でもこの量）。**fact 粒度分割は並列 merge を綺麗にする
  だけでなく検索重複を根絶する**（各 leaf が distinct scope → distinct 検索）。
- **JSON→CUE 因果（緩和材料）**: この粗い分割は Splitter の無能でなく **JSON 時代の substrate
  限界**。JSON body は whole-document、git は行 textual merge → 同 body の近接編集は必ず
  conflict、安定ID fact merge が無い → conflict 回避の唯一安全な分割 = leaf ごと whole subtree
  丸取り → cohort 一律スタンプ。CUE化がこれを持ち上げる（codec を merge-driver 登録 →
  安定ID行 union → 2 leaf が同 body の別 fact 行を書いても綺麗に merge → **fact 粒度の作業分割が
  可能**）。**ただし substrate 除去は必要条件、Splitter LOGIC を fact/安定ID write_scope 化する
  改修は別途必要**（今の cohort スタンプ = JSON 時代ロジックが CUE engine に未移行残存）。
- **コスト見積り（requester）**: 時間がかかる。接地面が多い（write_scope 表現・codec
  merge-driver・splitter logic・gate is-ancestor・resume・provenance）→ **テストもバグも多くなる**。
- **順序（requester 裁定 2026-07-15）**: Splitter改修は **Gate 除去（= ML充実、post-handoff）の次**。
  Gate 除去が先、Splitter改修が後。

## 順序・実行形態の上書き（requester 裁定 2026-07-16・上の 07-15 注記を反転）
逐語: 「Splitter を優先　執刀は外科だが Codex で、AI Org 単走時に並行で行う　CUE をもとにした分割は
JSON 時代より良い粒度を期待」。含意:
- **優先度反転**: 07-15 の「Gate 除去が先・Splitter 後」を撤回。**Splitter改修を先行・最優先**に格上げ。
- **実行形態 = 外科（Codex 直接執刀）**。org goal として receive→split→leaf にディスパッチするのではなく、
  requester が Codex に engine を直接執刀させる surgery（実装 carrier=Codex、私は orchestrate+verify）。
  ∴ 上部 status 行の「goal 起草は hand-off 後」は Splitter には非適用（起草ではなく執刀）。
- **実行スロット = AI Org 単走時の遊休レーンで並行**（隔離 worktree、producer-goal は既に split 済みで
  destabilize しない＝単走時並走）。現在は複走（2 leaf 走行中）ゆえ、単走落ち後に執刀開始。
- **期待成果**: CUE flat-diff（安定ID行 union）を merge 単位にした fact 粒度分割で、JSON 時代の
  サブツリー/ファイル粒度より細かい非衝突分割。ただし本節冒頭「非交渉の2ゲート」は保持
  （codec-write の3不変条件強制 ＋ merge 後 cue vet + acceptance vet）。

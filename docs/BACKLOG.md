# BACKLOG（正本）

requester ↔ Claude の作業台帳。ここが唯一のバックログ正本 — コンテキスト・memory 上の一覧は全てこのファイルの揮発コピーとみなす。更新はこのファイルへ直接（追記・消し込み）、完了項目は Done へ移動。文書網の入口は [../README.md](../README.md)（全 md は相互参照必須・孤児禁止）。

Last update: 2026-07-13（**CUE化 Phase-2 完了: 11/11 leaf 受理、統合 HEAD 959 passed / 0 failed / 1 skipped**。requester 裁定: この repo（ai-org-engine、CUE化済み engine）が本体に昇格、旧 JSON engine `ai-org-bootstrap-codex` はレガシー凍結・アーカイブ（pre-Phase-2 の全歴史と旧 branch 群の正本はレガシー側に残存）。次の最優先 = #4.5 Producer-Goal 改修）

## 裁定待ち（requester の回答でブロック中）

1. **codec 完成の認定と Phase-2 前倒し** — 無印樹（/tmp/cue_codec_restore/plain）が leaf-1 契約 6/6 green・functional_check×2 green。これを「完成した CUE Codec」としてPhase-2（engine への組み込み配線）に進むか。残 13 leaf は並行か後回しか。
2. **Phase-2 goal 文面の検分** — 起草済み（会話で提示済みの5行、組み込み工事として記述）。検分 OK → usage limit 解禁後 dispatch。
3. **完成樹の landing 先** — 私案: announced contrib branch `ai-org/contrib/0001-first_kind_admission` へ commit。対案: mishima-computing の standalone repo を先に立てる。樹は現在 git の外。
4. **15000 cap の除染** — series branch（/tmp/cue_codec_emergence, go-cue-canonical-codec）の technical-approach-plan.json / series-coverage-ledger.json に VOID 済み cap が実行可能仕様のまま残存。残 13 leaf を回す前にスクラブ必須（再摂取防止）。付: PORT 裁定後の「voided token grep 機械検査」私案の採否。

## 実行待ち（裁定済み、解禁/順番待ち）

4.5. **Producer-Goal 改修（Success Criteria 見直し）— CUE化完了直後の最優先**（requester 裁定 2026-07-12「実装計画に入れましょう　CUE化のあとにまっさきにやる」）。現行 `success_criteria` は referee-only（actor 全員が検査者、生産が preconditions に沈む — pixel-artisan 実害で確定、経緯は 07-01 策定会話復元で確定）。deliverable を要求する goal に producer 側 4 欄（deliverable 記述/eligibility 自己判定述語/commitment slot/完了主張）を referee 受理と対で立て、KAOS 完了規則（unassigned deliverable の分解を閉じない）を cue vet で決定論強制。typology(kind) 再輸入禁止・referee 弱体化禁止。設計材料集: `docs/producer-goal-redesign.md`（6 研究系統の収束・輸入/見送りリスト・provenance）。

5. **subsystem.py receive 再入稿** — 文面確定済み: 「Codex CLIをラップするsubsystem.pyを作れ　これはLinux Communityのメンテナと同じ働きをし、後続のLinusに渡すかどうかの判定を行う」。fresh vessel で。usage limit 21:41 解禁後いつでも。maintainer 空洞（acceptance: reachable 未接続）を埋める部品。

6. **God module 監査 → Linux に無い中央機構の解体（ML 充実と表裏一体）**（requester が対話で誘導・2026-07-14 確定）。

   **発端**: gate（`patchwork_queue/patch_series_gate.py`, 5501行）が God module と判明 — 受理判定＋依存順序＋lifecycle を1モジュールに独占、葉網全体が結合。Linux に「gate」は存在しない（受理=git is-ancestor / 順序=base-on-pending＋ML 調整）。「中央オーケストレーター禁止」（ML 設計裁定）の構造的違反。加えて gate の `depends` は「受理済み」を要求し、git の「base に在れば良い（pending 可）」より厳格 → 並行可能な作業を全直列に固めていた。

   **命名 tell（requester 指摘・恒久 heuristic）**: Claude が付ける抽象名詞の機能ラベル（gate / queue / store / stream / registry / worker …）＝ 実物に接地せず抽象を発明した高確率の兆候。確定条件 = 実物（git / ML / patch / lore）を包むか、宙に浮いた中央ハブか。`git_wrapper` は例外（名前は雑だが git を包む＝正当）。cf. concrete-over-vague-labels。

   **監査結果（中央性 fan-in × Linux 非対応）**:
   - **receive.py（10,587行）＝ 最大の God module**。goal→依存DAG網の自動分解機。Linux に無い（人間が patch に割る）。**gate の親玉**（gate は receive が吐いた DAG を強制）。receive＋gate ＝ invented pair。
   - patch_series_gate（5,501 / fan-in 10）＝ 確定。
   - patchwork_queue pull（fan-in 17）＝ 中央 dispatcher。Linux に無い（ML＋maintainer 木が媒体）。名指しした dispatcher トラップ。
   - review（fan-in 17）＝ Linux の review は on-list 分散、中央 adjudication engine は過剰中央化の疑い。
   - engineering_precedent_store（4,615）＝ **検分クローズ（2026-07-15）: God module ではない。** Web 検索（GitHub コード検索）の代替＋SQLite キャッシュ＝Reference の窓（state=git・body=codec と並ぶ第3の窓）。「store」命名 tell の false positive（git_wrapper と同型: 実物＝Web 検索を包む）。ML には載らない（検索結果は correspondence でない）。**付随して store 外科（旧確定 #4: hit 計装/FTS5/正規化キー）も不要と判明**: DB 棚卸し（term 1439 / term_key 1437 / source_url 集合が一致する語は 89 のみ＝94% は結果が別物）で、まとめる対象がほぼ存在しない＝#1 計測・#3 正規化は空振り。理由 = AI Org は毎回かなりの無理難題を解いており precedent が構造的に繰り返さない。store は気休め・再走の保険（低 stakes）。FTS5 は recall を多少助けるのみで低優先。**将来 SQLite が密になり pattern が繰り返し始めたら再検討**（requester 但し書き）。
   - 正当（残す）: git_wrapper（git 窓）/ body_codec（body 窓）/ mailing_list（媒体）/ subsystem（maintainer 席）。

   **解体方針 ＝ ML 充実と表裏一体**: 中央を取り除く（破壊側＝gate/receive 解体）と、分散調整を建て直す（建設側＝ML 充実: PATCH post が seam、subscriber が反応、take-count 受理、node-scoped、中央 orchestrator なし）は同一変換の両面。**ML が調整を担えて初めて中央機構が溶ける** → 順序は「先に壊す」でなく「ML 充実 → gate/receive の責務に行き場ができて溶ける」。∴ 別プロジェクト「gate 解体」は不要、**ML 充実 series に gate/receive 解体を含意として読み替える**。engine 凍結中の衝動的一括破壊は禁（5501行×fan-in を carrier に一括投げ＝時間/token 浪費、2026-07-14 に worktree 切って着手しかけ requester が制止）。

7. **Splitter 改修（分割粒度 = git merge 非衝突）** — 研究正本: [splitter-research.md](splitter-research.md)。**重心（2026-07-15）= CUE化の恩恵を splitter に活かす**: codec を gitattributes で CUE body の git merge driver に登録し、write_scope を「どの body のどの安定ID行を書くか」の**事実（安定ID行）粒度**で表現 → 同 body・別事実の2葉が非衝突で並列。現行は write_scope=`sub/<leaf>/` の**サブツリー粒度**で切り、CUE 細粒度 merge を捨てている（leaf7×8 の evidence 衝突を kernel 型 merge 除外で逃がしたのがその band-aid）。**起草順序: requester 要件（CUE merge-driver）が本体、文献は支援参照のみ。** 文献4面統合は「観点（CUE mergeability）が欠けた調査」ゆえ意義薄く、**merge-driver 観点の追加下調べは未完**（git 独自 merge driver/gitattributes・CRDT/OT・安定ID union merge・towncrier/lockfile/git-annex の merge 設計前例、doc の「観点駆動の下調べ」節）。起草は hand-off 後、ML 充実の下流。

## 作業残（非ブロッキング）

5.5. **leaf10/11 への requester 注記（cover letter 生成後すぐ、announce 前に port で append）** — requester 裁定 2026-07-12: 並列 leaf の evidence 衝突（実害: leaf7×8 が ai-org/implementation-cue/ と root implementation-result.json で ~50 file conflict、キー=item_id のみで leaf 成分ゼロ、mainline 上の evidence 読者は皆無）の恒久修理は**名前空間化でなく kernel 型 merge 時除外**: evidence/implementation-result は contrib branch 留め置き・subsystem merge で除外・mainline は commit message の Link で指す。leaf11 の CI は evidence を mainline でなく各 leaf の contrib ref 上で検証。研究根拠: kernel lore/Bazel/Nix/Gerrit 収束＋機構調査（code_worker.py:1684-1687、_merge_contribution 一点変更で両衝突同時解消）。暫定処置済み: leaf8 統合は leaf8 優先上書きで通過（leaf7 evidence は contrib ref に到達可能）。

6. **engine worktree 保存 fix の commit** — code_worker.py＋tests 2本が working tree に未 commit（606 tests pass 済み）。詳細メッセージ付きで commit する。
7. **Impl2 樹の処遇** — /tmp/cue_codec_restore/impl2 に復元のみで保存中。無印が契約 green になった今、競争の決着（勝者判定・敗者の扱い）が未実施。
8. **recap Step 3 裁定表の消化** — 保留中。主な未反映: 復元完了・両樹 green の LIVE UPDATE、runbook への worktree fix 注記。

## 凍結中・将来（動かさない）

9. **法廷機構（判事）** — body 確定まで凍結。それまで review 官僚化の知見蓄積（memory: court-adjudication-backlog.md）。
10. **goal-level acceptance profile** — 別穴として保留（memory: goal-level-acceptance-is-the-hole.md）。
11. **Codec standalone 公開 repo** — mishima-computing 配下、Apache-2.0（CUE 準拠）。landing 裁定（#3）の下流。
12. **残 13 leaf の pipeline 完走** — #1 と #4 の裁定次第。leaf network は af68889（14 leaf 直列、leaf-1 のみ ready だった）。

## Done

- **受理サイクルの手順確定（base 前進の欠落を発見・修復）**: requester-fiat 受理の正手順 = ①contrib に `acceptance: reachable` marker ②subsystem→mainline へ merge ③**default branch を mainline に ff 前進（欠けていた段 — 無いと次 leaf が前葉の成果を持たない base から fork する。leaf 2 が wrong base で40分走った実害で発見、停止→base 前進→正 base で再発射済み）** ④gate elaborate が次葉を自動開放。merge 自体は秒単位で並列化の利得なし — 律速は implement。2026-07-12

- 旧AI Orgコンテキストの除去（復元可能化の上で）: tracked の思考ログ backup 35MB を git rm（7adf013、履歴には残存）、untracked の archive/ 残骸 6.3MB を削除。復元点 = <local-backup-redacted>（223 entries）＋ git 履歴。残る旧 lore 経路 = 履歴541commits と旧 branch 群 → Phase-2 self-clone は `--single-branch --depth 1` で切る（dispatch 時に適用）。2026-07-10

- leaf-1 `sub/first_kind_admission` 実装: Sol 無印/Impl2 が usage limit で全損 → codex session rollout から両樹復元（build+test green）→ 無印を Claude worker が契約完成（6/6 criteria PASS、voided byte cap 除去が唯一の外科）。2026-07-10
- 15000 cap 混入経路の特定: v4 reform が plan 本体から cap を未スクラブ → refine が leaf へ継承 → 実行可能仕様(51回) が prose 裁定(3回) に勝った。2026-07-10
- engine cue-path sanitize fix commit f67c4d6。2026-07-10

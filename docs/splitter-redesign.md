# Splitter 再設計 — CUE-native な plan 分割（時短のためだけに、CUE が既に与える構造を使う）

status: 設計（requester 確定 2026-07-16）。実装は carrier（Codex）二刀流・単走時。
旧「B＝葉が所有 sub-item を実装」は却下（依存を無視した分割）。CUE merge-driver(A) の別立ては不要
——本設計は「CUE を作り直す」のでなく「CUE を使う」。

---

## 1. 唯一の基準 — 時短
分割の存在理由は wall-clock を縮めることだけ（requester 2026-07-16）。「conflict しない」は必要だが
不十分な proxy。真の軸は「実際に速くなるか」＝**独立な作業を並列にできた時だけ縮む**。

## 2. モデル — まず plan、その plan を割る
receive が **plan を1つ** 作る。splitter は**その plan を依存構造に沿って割る**（再 author ではなく分割）。
割ってよいのは**依存の無い所だけ**。依存の鎖はまとめる or 直列。1本の鎖なら **1葉**。

## 3. 核 — CUE-native（JSON 時代の機構を CUE の上に再実装しない）
現状の病根は「CUE の器に JSON 時代の plan（**位置依存の follow_ups リスト**＋**string の依存
`scope_item_ids`**）を入れ、独立性/partition/conflict-free を **Python で作り直している**」こと。
CUE が既に与えるものを使えば、その再実装は全部消える:

| いま Python で再実装しているもの | CUE-native では |
|---|---|
| 依存を `patch_plan:follow_up:N` の **string** で配り、exact-once を `validate_ledger_contract` で検証 | plan item を **stable-ID struct**、依存を **item 間の native reference**。依存グラフ＝CUE 構造そのもの |
| linux-next 型 **trial merge** で conflict-free を確認 | **CUE unification** が mergeable を構成上保証（textual trial-merge 不要） |
| 分割案を裁く**決定論 gate** | **cue vet**（別モジュールを置かない） |
| per-child に skeleton を**再 author** | plan の部分グラフの **CUE slice（projection）** |

**∴ CUE-native な分割**:
1. plan ＝ CUE body。各 work item は **stable-ID の struct**、item 間の依存は **native CUE reference**
   （「この item は あの item の出力の上に建つ」を string でなく参照で書く）。
2. 分割 ＝ その **reference グラフを弱連結の独立成分に partition**（CUE 構造から決定論的に計算）。
   独立成分＝並列葉、鎖状の成分＝1葉にまとめる or RAW reference で直列。
3. 各葉 ＝ 自成分の **CUE slice**（valid であることは cue vet が保証）。
4. 再結合 ＝ **CUE unification**（mergeable by construction）。
5. **gate は置かない**。正しさは「plan の native reference に従って割る＝構成上正しい」＋ cue vet で担保。
   「分割案を裁く別機構」を作らない（＝縮めた ML の穴埋めを再発しない）。

**時短はグラフ partition から自然に出る**: 1本の依存鎖は成分1つ＝自動的に1葉（割らない）。
独立成分が複数ある時だけ複数葉＝その時だけ並列で縮む。「縮まないなら割るな」を別ゲートで判定する必要が無い。

## 4. 現設計の病根（grounded, file:line）
- 分割軸 = **contention**（「routing is contention, not classification」`patch_series_gate.py:1598`、
  「Do not schedule execution」:1610）。ファイル非衝突は**振る舞い依存を見落とす**（follow_up は
  first_proof に `adds`＝別ファイルでも RAW 依存）→ 1つの依存 plan を N 並列葉に過分割。
- plan = goal 全体で1つ、receive が一度 author（`receive.py:6674`、`_right_size_patch_plan` :5316）、
  **位置依存の follow_ups リスト**（native reference なし）。
- split は sub-item を **string scope_item_ids** で配るだけ（`_scope_items` :2363、exact-once :1606、
  `validate_ledger_contract` :372）。`_child_approach`(:2342) は plan を持たず、全葉が root plan を
  丸読み（`code_worker.py:129`）→ N×redo・98%重複検索・時短ゼロ。
- 時短の実測なし（`critical_path` :2898 は unit 重みのトポロジ射影）。

## 5. 実装対象（engine、file:line。carrier 二刀流・単走時）
- **receive** `_right_size_patch_plan`(:5316) / `build_right_size_patch_plan_schema`(:933): plan を
  「位置依存 follow_ups リスト」から「**stable-ID item ＋ item 間 native 依存 reference**」の CUE 構造へ。
- **splitter** `patch_series_gate.py`: contention 分割（`_split_prompt` :1587）を捨て、**plan の reference
  グラフを独立成分に partition する決定論関数**に。string `scope_item_ids` 配分・`validate_ledger_contract`
  ・trial-merge・split を裁く gate は**削除**（CUE 構造＋cue vet が代替）。各葉には自成分の CUE slice を置く。
- **code_worker** `load_brief`(:129): root 丸読みを廃し、各葉の自成分 slice を node-scoped で読む。
  `plan_items` は自成分の item を列挙。

## 6. A（旧「CUE merge-driver」別立て）について
別立て不要。本設計が CUE の unification をそのまま再結合に使うので、「fact 粒度 merge-driver を別に建てる」
必要が無い。独立成分は write も自然に disjoint＝file 粒度で既に clean。

## 7. 未決・検証
- plan の依存 reference をどう表現するか（CUE の具体形）は実装時に最小で決める（over-engineering しない）。
- 検証 = 新規 goal を dogfood: (a) 1本鎖 plan が 1葉に畳まれる、(b) 独立 2 成分が 2 並列葉、
  (c) 依存成分が直列、(d) wall-clock が単一/過分割より縮む（実走で示す、宣言でなく）。

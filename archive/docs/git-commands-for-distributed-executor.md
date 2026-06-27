# Git commands for the distributed-branch executor

分散ブランチ統合 executor で使う/使える Git コマンドを、ライフサイクル順に列挙。
⭐＝あまり知られていないが高価値、(使用中)＝現コード(6890b07)が既に使用。

## 1. base 解決（immutable SHA = git truth）
- `git rev-parse --verify <rev>^{commit}` — rev を不変 SHA へ。base 検証。
- `git merge-base --is-ancestor <a> <b>` (使用中) — 祖先判定（stale-base 検出）。
- `git merge-base <a> <b>` — 共通祖先。

## 2. タスクごとの隔離作業場（worktree）
- `git worktree add --detach <path> <sha>` (使用中) — commit で detached worktree。
- `git worktree add -b <branch> <path> <sha>` — 新 branch 付き。
- `git worktree list --porcelain` — 一覧（machine 可読）。
- `git worktree remove [--force] <path>` (使用中) — 撤去。
- ⭐`git worktree lock/unlock <path>` — prune されないよう lock（mis-timed prune を構造で防止）。
- `git worktree prune` — 死んだ worktree 管理情報の掃除（※global、並列中は危険）。

## 3. 作業木なしで commit を組む（plumbing = 隔離に最適）
- ⭐`GIT_INDEX_FILE=/tmp/idx git ...` — 一時 index。worktree の index を汚さず組む。
- `git read-tree <tree>` / `git update-index --add` — index を組む。
- `git write-tree` — index → tree オブジェクト。
- `git commit-tree <tree> -p <parent> -m msg` (使用中) — checkout 無しで commit を直接生成。
- ⭐`git hash-object -w` / `git mktree` — blob/tree を直に書く。
- `git apply --3way <patch>` / `git apply --cached` — patch 適用。

## 4. タスク出力を ref に記録（atomic・CAS）
- `git update-ref <ref> <new> <old>` (使用中) — CAS 付き atomic 更新（old 不一致で fail）。
- `git update-ref -d <ref> <old>` — CAS 付き削除。
- ⭐`git update-ref --stdin`（start/update/commit）— 複数 ref を1トランザクションで atomic 更新。
- ⭐`git for-each-ref --format='%(objectname) %(refname)' refs/heads/ai-org/tasks/` — ref 列挙＝side-ledger の代わりの git-truth 台帳。
- `git show-ref` / `git rev-parse --verify --quiet <ref>` — ref 存在/SHA。
- `git symbolic-ref HEAD` — HEAD 操作。

## 5. 統合（controller 所有の merge/cherry-pick）
- `git cherry-pick [--allow-empty] [--keep-redundant-commits] <c>` (使用中) — child commit を適用。
- `git cherry-pick --abort` (使用中) — conflict で中断。
- ⭐`git merge-tree --write-tree <b1> <b2>` — worktree/index を触らず merge を計算し conflict 検出＋merged tree 出力。controller が checkout せず統合可否を判定（git-truth 統合の本命）。
- `git rebase --onto <newbase> <upstream> <branch>` — 新 base へ載せ替え（人間流「現 base に rebase」＝stale-base の正攻法／直列チェーンに）。
- ⭐`git rerere` — conflict 解決の再利用（同型 conflict を自動再適用）。
- `git read-tree -m -i <base> <ours> <theirs>` — index レベル 3-way。

## 6. 変更の有無検出（no-op child = git truth）
- `git diff --quiet <a> <b>` — 差分有無を exit code で（no-op 検出）。
- `git rev-parse <commit>^{tree}` (使用中) — tree OID 比較。
- ⭐`git cherry <upstream> <head>` — まだ upstream に無い commit を patch-id で判定（既適用/冗長 child 検出）。
- ⭐`git patch-id` — 安定した patch 同一性（「その変更はもう入っている」）。
- `git diff-tree -r <a> <b>` — tree 差分（plumbing）。

## 7. 「統合/完了したか」（git truth で完了判定）
- `git branch --merged <commit>` — その commit に merge 済の branch。
- `git merge-base --is-ancestor <task-tip> <integration>` — task が統合に含まれるか。
- ⭐`git for-each-ref --merged <commit> refs/heads/ai-org/tasks/` — 統合済 task ref を一括。
- ⭐`git notes add/show` — commit に metadata（task id・検証状態）を git 内で付与。
- `git tag` — 統合点の印。

## 8. 掃除（git truth ベース）
- `git for-each-ref refs/heads/ai-org/tasks/` → 条件付き `git update-ref -d` — 実 ref 状態で削除判断（in-memory 台帳を使わない）。
- `git worktree remove` + `git worktree prune`。
- `git gc` / `git prune`（※並列作業中は実行しない）。
- ⭐`git reflog expire --expire=now` — reflog 掃除。

## 9. 検査（read-only）
- `git cat-file -t/-p <obj>` — オブジェクト中身。
- `git ls-tree -r <tree>` / `git ls-files` — tree/index 一覧。
- `git status --porcelain` / `git log --format=...`。

---
**特に高価値（知られにくい）**: ⭐`merge-tree --write-tree`（checkout 無し統合判定）・⭐`update-ref --stdin`（atomic 多 ref）・⭐`for-each-ref`（git-truth 台帳）・⭐`cherry`/`patch-id`（冗長 child 検出）・⭐`worktree lock`（prune 事故防止）・⭐`GIT_INDEX_FILE`（隔離 index）・⭐`rerere`・⭐`notes`。

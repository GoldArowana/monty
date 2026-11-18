#!/usr/bin/env bash
set -euo pipefail

readonly source_commit=057da5c2ee1e1bf25b18fa07aff9c8afb8a388c4
readonly source_tree=985ad16b80e5a1a00c4f1566d7f0c0571f7cfaa1
readonly source_dir="$GITHUB_WORKSPACE/source"
readonly evidence="$GITHUB_WORKSPACE/evidence-lint"
mkdir -p "$evidence"

check_source() {
  local phase="$1"
  local status=0 head tree
  head=$(git -C "$source_dir" rev-parse HEAD)
  tree=$(git -C "$source_dir" rev-parse 'HEAD^{tree}')
  git -C "$source_dir" diff HEAD --exit-code > "$evidence/$phase.tracked.diff" || status=$?
  git -C "$source_dir" status --porcelain=v1 --untracked-files=all > "$evidence/$phase.status"
  if [[ "$head" != "$source_commit" || "$tree" != "$source_tree" || -s "$evidence/$phase.status" ]]; then
    status=1
  fi
  printf 'event_commit=%s\nsource_commit=%s\nsource_tree=%s\nexit_code=%s\n' \
    "$GITHUB_SHA" "$head" "$tree" "$status" | tee "$evidence/$phase.identity"
  return "$status"
}

if [[ "${1:-}" == --final-only ]]; then
  check_source always-final
  exit
fi

check_source initial
export CARGO_TARGET_DIR="$RUNNER_TEMP/monty1077-lint-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
test ! -e "$CARGO_TARGET_DIR"
mkdir -p "$CARGO_TARGET_DIR"
export PYO3_PYTHON
PYO3_PYTHON=$(command -v python3)
printf 'rust=%s\npython=%s\nuv=%s\ntarget=%s\n' \
  "$(rustc --version)" "$(python3 --version)" "$(uv --version)" "$CARGO_TARGET_DIR" \
  | tee "$evidence/runtime.txt"
test "$(python3 --version)" = 'Python 3.14.10'
cd "$source_dir"

last_check_exit=0
run_check() {
  local name="$1" time_limit="$2"
  shift 2
  last_check_exit=0
  printf 'started_at=%s\ncommand=' "$(date -u -Iseconds)" > "$evidence/$name.receipt"
  printf '%q ' "$@" >> "$evidence/$name.receipt"
  printf '\n' >> "$evidence/$name.receipt"
  timeout --kill-after=15s "$time_limit" "$@" \
    > "$evidence/$name.stdout" 2> "$evidence/$name.stderr" || last_check_exit=$?
  printf 'exit_code=%s\nfinished_at=%s\n' "$last_check_exit" "$(date -u -Iseconds)" >> "$evidence/$name.receipt"
  sha256sum "$evidence/$name.stdout" "$evidence/$name.stderr" >> "$evidence/$name.receipt"
  cat "$evidence/$name.receipt"
}

run_check install-py 120s make install-py
test "$last_check_exit" -eq 0
check_source after-bootstrap
run_check lint-py 900s make lint-py
lint_py=$last_check_exit
check_source after-lint-py
run_check lint-md 420s make lint-md
lint_md=$last_check_exit
check_source driver-final
printf 'lint_py=%s\nlint_md=%s\n' "$lint_py" "$lint_md" | tee "$evidence/lint.summary"
test "$lint_py" -eq 0 && test "$lint_md" -eq 0

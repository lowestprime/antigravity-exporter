# Source this file explicitly to load helpers; no profile or PATH is edited.
# An existing Python 3.10+ is used. No interpreter or package is downloaded.
_AGY_EXPORTER_SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/agy_export.py"

agy-export() {
    local agy_python=${AGY_EXPORT_PYTHON:-} agy_script=${AGY_EXPORT_SCRIPT:-$_AGY_EXPORTER_SCRIPT}
    if [[ -z $agy_python ]]; then
        agy_python=$(command -v python3 || command -v python) || { printf '%s\n' 'No existing Python found; set AGY_EXPORT_PYTHON.' >&2; return 2; }
    fi
    "$agy_python" "$agy_script" "$@"
}
agy-exporter() { agy-export "$@"; }
antigravity-exporter() { agy-export "$@"; }
agy-help() { agy-export --help "$@"; }
agy-sessions() { agy-export sessions "$@"; }
agy-projects() { agy-export projects "$@"; }
agy-config() { agy-export config "$@"; }
agy-archive-config() { agy-export config "$@"; }
agy-doctor() { agy-export doctor "$@"; }
agy-tokenizer() { agy-export tokenizer "$@"; }
agy-browse() { agy-export browse "$@"; }
agy-review() { agy-export review-from "$@"; }
agy-verify() { agy-export verify "$@"; }
agy-verify-set() { agy-export verify-set "$@"; }
agy-access() { agy-export diagnose-access "$@"; }
agy-repair-access() { agy-export repair-access "$@"; }
agy-audit() { if (($#)) && [[ $1 != -* ]]; then agy-export audit --id "$@"; else agy-export audit --all "$@"; fi; }
agy-turns() { (($#)) || { printf '%s\n' 'Usage: agy-turns UUID [options]' >&2; return 2; }; agy-export turns --id "$@"; }
agy-list() { (($#)) || return 2; agy-export records --id "$@"; }
agy-responses() { (($#)) || return 2; agy-export responses --id "$@"; }
agy-thread() { (($#)) || return 2; agy-export export --mode thread --id "$@"; }
agy-chat() { (($#)) || return 2; agy-export export --mode chat --id "$@"; }
agy-actions() { (($#)) || return 2; agy-export export --mode actions --id "$@"; }
agy-fast() { (($#)) || return 2; agy-export export --mode thread --fast --id "$@"; }
agy-final() { (($#)) || return 2; agy-export export --mode last-substantial --id "$@"; }
agy-live() { (($#)) || return 2; agy-export export --mode checkpoint-context --id "$@"; }
agy-thread-clip() { (($#)) || return 2; agy-export export --mode thread --clipboard --no-file --id "$@"; }
agy-open() { (($#)) || return 2; agy-export export --mode thread --open-after --id "$@"; }
agy-project() { (($#)) && [[ $1 != -* ]] || { printf '%s\n' 'Usage: agy-project PROJECT [options]' >&2; return 2; }; agy-export export --project "$@"; }
agy-latest() {
    local agy_mode=thread
    if (($#)) && [[ $1 != -* ]]; then agy_mode=$1; shift; fi
    agy-export export --latest-session --mode "$agy_mode" "$@"
}
agy-batch() {
    local -a agy_ids=()
    while (($#)) && [[ $1 != -* ]]; do agy_ids+=(--id "$1"); shift; done
    ((${#agy_ids[@]})) || { printf '%s\n' 'Usage: agy-batch UUID1 UUID2 [options]' >&2; return 2; }
    agy-export export --mode thread "${agy_ids[@]}" "$@"
}
agy-turn() { (($#>=2)) || return 2; local agy_id=$1 agy_n=$2; shift 2; agy-export export --mode turn --id "$agy_id" --turn "$agy_n" "$@"; }
agy-response() { (($#>=2)) || return 2; local agy_id=$1 agy_n=$2; shift 2; agy-export export --mode response --id "$agy_id" --turn "$agy_n" "$@"; }
agy-response-block() { (($#>=2)) || return 2; local agy_id=$1 agy_n=$2; shift 2; agy-export export --mode response-block --id "$agy_id" --turn "$agy_n" "$@"; }
agy-msg() { (($#>=2)) || return 2; local agy_id=$1 agy_n=$2; shift 2; agy-export export --mode message --id "$agy_id" --message "$agy_n" "$@"; }
agy-range() { (($#>=3)) || return 2; local agy_id=$1 agy_first=$2 agy_last=$3; shift 3; agy-export export --mode range --id "$agy_id" --from-step "$agy_first" --to-step "$agy_last" "$@"; }
agy-last() {
    (($#)) || return 2
    local agy_id=$1 agy_n=1; shift
    if (($#)) && [[ $1 =~ ^[0-9]+$ ]]; then agy_n=$1; shift; fi
    agy-export export --mode thread --id "$agy_id" --last-n-turns "$agy_n" "$@"
}
agy-since() { (($#>=2)) || return 2; local agy_id=$1 agy_anchor=$2; shift 2; agy-export export --mode thread --id "$agy_id" "--since=$agy_anchor" "$@"; }
agy-since-text() { (($#>=2)) || return 2; local agy_id=$1 agy_anchor=$2; shift 2; agy-export export --mode thread --id "$agy_id" "--since-text=$agy_anchor" "$@"; }
agy-since-turn() { (($#>=2)) || return 2; local agy_id=$1 agy_anchor=$2; shift 2; agy-export export --mode thread --id "$agy_id" "--since-turn=$agy_anchor" "$@"; }
agy-since-id() { (($#>=2)) || return 2; local agy_id=$1 agy_anchor=$2; shift 2; agy-export export --mode thread --id "$agy_id" "--since-id=$agy_anchor" "$@"; }
agy-since-time() { (($#>=2)) || return 2; local agy_id=$1 agy_anchor=$2; shift 2; agy-export export --mode thread --id "$agy_id" "--since-time=$agy_anchor" "$@"; }
agy-set-dir() { if (($#==1)); then agy-export config --set "out_dir=$1"; elif (($#==0)); then agy-export config --get out_dir; else return 2; fi; }
agy-set-template() { if (($#==1)); then agy-export config --set "template=$1"; elif (($#==0)); then agy-export config --get template; else return 2; fi; }

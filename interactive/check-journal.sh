#!/usr/bin/env bash
# End-to-end check THROUGH Journal: one chat and one voice note, no ingest. Needs the pair up.
# LLM: runs Journal's own shared.local_llm.chat inside its telegram container (the only
#   Journal LLM consumer; no HTTP endpoint exists). ASR: POSTs a synthetic clip (multipart field `audio`) to
#   transcribe_audio /transcribe, polls GET /transcribe/{job_id}, requires result.backend==local. Neither touches /ingest; the ingest log is checked before/after.
# Required: JOURNAL_COMPOSE_FILE, JOURNAL_ASR_AUDIO (synthetic known-answer wav, e.g. JFK sample).
# Optional: DOCKER (default docker), JOURNAL_TRANSCRIBE_URL, EXPECTED_TRANSCRIPT, JOURNAL_LLM_SERVICE.
set -u
cf="${JOURNAL_COMPOSE_FILE:?set JOURNAL_COMPOSE_FILE}"
audio="${JOURNAL_ASR_AUDIO:?set JOURNAL_ASR_AUDIO}"
dk="${DOCKER:-docker}"
url="${JOURNAL_TRANSCRIBE_URL:-http://localhost:8002}"
want="${EXPECTED_TRANSCRIPT:-ask not what your country can do for you}"
svc="${JOURNAL_LLM_SERVICE:-telegram}"
[ -f "$audio" ] || { echo "FAIL: audio file missing"; exit 1; }
# Fail closed: unreadable or empty ingest logs are an error, never zero hits.
ingest_hits() {
  local out
  out=$("$dk" compose -f "$cf" logs --no-log-prefix ingest 2>/dev/null) || return 1
  [ -n "$(printf '%s' "$out" | tr -d '[:space:]')" ] || return 1
  printf '%s\n' "$out" | grep -c 'POST /ingest'
}
before=$(ingest_hits)
# grep -c exits 1 on zero matches but still prints 0; only a missing number is a failure
case "$before" in ''|*[!0-9]*) echo "FAIL: ingest logs unreadable or empty (before)"; exit 1;; esac

echo "== LLM via Journal (shared.local_llm.chat in $svc)"
llm=$("$dk" compose -f "$cf" exec -T "$svc" python - <<'PY'
import asyncio
from shared import local_llm
print(asyncio.run(local_llm.chat([{"role": "user", "content": "Reply with the single word: pong"}], max_tokens=64)))
PY
) || { echo "FAIL: LLM request failed"; exit 1; }
[ -n "$(printf '%s' "$llm" | tr -d '[:space:]')" ] || { echo "FAIL: empty LLM content"; exit 1; }
echo "llm content: $llm"

echo "== ASR via Journal ($url/transcribe, job-based)"
sub=$(curl -sf -m 60 -F "audio=@$audio" "$url/transcribe") || { echo "FAIL: ASR submit failed"; exit 1; }
job=$(printf '%s' "$sub" | python3 -c 'import sys,json; print(json.load(sys.stdin)["job_id"])' 2>/dev/null)
[ -n "$job" ] || { echo "FAIL: no job_id in: $sub"; exit 1; }
asr=""; st=""
for _ in $(seq 1 "${JOURNAL_POLL_TRIES:-120}"); do
  asr=$(curl -sf -m 30 "$url/transcribe/$job") || { echo "FAIL: ASR poll failed"; exit 1; }
  st=$(printf '%s' "$asr" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)
  case "$st" in complete|failed) break;; esac
  sleep "${JOURNAL_POLL_SLEEP:-5}"
done
[ "$st" = complete ] || { echo "FAIL: ASR job not complete (status=$st): $asr"; exit 1; }
be=$(printf '%s' "$asr" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"].get("backend",""))' 2>/dev/null)
[ "$be" = local ] || { echo "FAIL: result.backend is '$be', not local (hosted fallback is not proof): $asr"; exit 1; }
printf '%s' "$asr" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"].get("text",""))' | tr '[:upper:]' '[:lower:]' | grep -qF "$(printf '%s' "$want" | tr '[:upper:]' '[:lower:]')" \
  || { echo "FAIL: wrong transcript: $asr"; exit 1; }
echo "asr response: $asr"

after=$(ingest_hits)
case "$after" in ''|*[!0-9]*) echo "FAIL: ingest logs unreadable or empty (after)"; exit 1;; esac
[ "$after" = "$before" ] || { echo "FAIL: ingest hit (POST /ingest $before -> $after)"; exit 1; }
echo "check complete"

#!/usr/bin/env bash
# Download GGUF weights into the weights drive.
#
# Plain curl against the HF resolve endpoint — no huggingface_hub dependency, and
# -C - makes a 17GB pull resumable, which matters more than any CLI nicety.
#
#   ./scripts/fetch-model.sh                      # default: the 4B smoke-test model
#   ./scripts/fetch-model.sh --27b                # the real one (Qwen3.8)
#   ./scripts/fetch-model.sh --27b-3.6            # the previous generation
#   ./scripts/fetch-model.sh --27b-3.5            # the one before that
#   ./scripts/fetch-model.sh --asr                # Qwen3-ASR 1.7B + projector
#   ./scripts/fetch-model.sh --asr-small          # Qwen3-ASR 0.6B + projector
#   ./scripts/fetch-model.sh <repo> <file> [...]  # anything else
set -euo pipefail

MODELS_DIR="${MODELS_HOST_PATH:-./models}/gguf"

# A multimodal model is TWO files: the text model and its `mmproj` projector,
# which holds the audio tower. llama-server refuses audio input without the
# projector, so fetching one without the other yields a server that starts
# cleanly and then rejects every transcription — fetch them together.
case "${1:-}" in
    --4b|"")   REPO="unsloth/Qwen3.5-4B-GGUF";  FILES=("Qwen3.5-4B-Q4_K_M.gguf") ;;
    # bartowski, not unsloth, for 3.8 only: this is the build actually in
    # service, and it is byte-identical to what is on the weights drive
    # (17,772,537,440). unsloth publishes a Qwen3.8-27B-Q4_K_M too, but at
    # 17,106,775,008 it is a different quant recipe — fetching it would give a
    # file that does not match the one every measurement in
    # docs/architecture/packets/llm.md was taken against.
    --27b)     REPO="bartowski/Qwen3.8-27B-GGUF"; FILES=("Qwen3.8-27B-Q4_K_M.gguf") ;;
    --27b-3.6) REPO="unsloth/Qwen3.6-27B-GGUF"; FILES=("Qwen3.6-27B-Q4_K_M.gguf") ;;
    --27b-3.5) REPO="unsloth/Qwen3.5-27B-GGUF"; FILES=("Qwen3.5-27B-Q4_K_M.gguf") ;;
    # Q8_0, not Q4_K_M: at 1.7B the 4-bit quant measurably degrades WER (3.28% vs
    # 2.84% on LibriSpeech test-clean) and the whole file is 2.2GB, so there is
    # nothing to save. ggml-org publishes no Q4_K_M for these anyway.
    --asr)     REPO="ggml-org/Qwen3-ASR-1.7B-GGUF"
               FILES=("Qwen3-ASR-1.7B-Q8_0.gguf" "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf") ;;
    --asr-small)
               REPO="ggml-org/Qwen3-ASR-0.6B-GGUF"
               FILES=("Qwen3-ASR-0.6B-Q8_0.gguf" "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf") ;;
    -h|--help)
        sed -n '2,13p' "$0" | sed 's/^# \?//'
        exit 0 ;;
    *)
        REPO="$1"; shift
        [[ $# -gt 0 ]] || { echo "usage: fetch-model.sh <repo> <filename.gguf> [...]" >&2; exit 1; }
        FILES=("$@") ;;
esac

if [[ ! -d "$MODELS_DIR" ]]; then
    echo "error: $MODELS_DIR does not exist — run 'sudo ./scripts/setup-models-drive.sh' first" >&2
    exit 1
fi

# Resolve every size before downloading anything: a two-file pull that dies
# halfway on a full disk leaves a model with no projector, which fails at
# transcription time rather than here.
declare -a SIZES URLS DESTS
TOTAL=0
for FILE in "${FILES[@]}"; do
    URL="https://huggingface.co/${REPO}/resolve/main/${FILE}?download=true"
    SIZE=$(curl -sIL "$URL" | grep -i '^content-length:' | tail -1 | tr -dc '0-9')
    if [[ -z "$SIZE" ]]; then
        echo "error: $REPO/$FILE not reachable on HuggingFace" >&2
        exit 1
    fi
    URLS+=("$URL"); SIZES+=("$SIZE"); DESTS+=("${MODELS_DIR}/${FILE}")
    TOTAL=$((TOTAL + SIZE))
done

echo "fetching ${#FILES[@]} file(s) from ${REPO}  ($(numfmt --to=iec "$TOTAL") total)"

AVAIL=$(df -B1 --output=avail "$MODELS_DIR" | tail -1)
if (( AVAIL < TOTAL )); then
    echo "error: need $(numfmt --to=iec "$TOTAL"), only $(numfmt --to=iec "$AVAIL") free" >&2
    exit 1
fi

for i in "${!FILES[@]}"; do
    echo
    echo "  ${FILES[$i]}  ($(numfmt --to=iec "${SIZES[$i]}"))"
    echo "  -> ${DESTS[$i]}"
    curl -L -C - --fail --progress-bar -o "${DESTS[$i]}" "${URLS[$i]}"

    ACTUAL=$(stat -c%s "${DESTS[$i]}")
    if (( ACTUAL != SIZES[i] )); then
        echo "error: size mismatch on ${FILES[$i]} — got $ACTUAL, expected ${SIZES[$i]} (rerun to resume)" >&2
        exit 1
    fi
done

echo
echo "done: ${#FILES[@]} file(s) in $MODELS_DIR"
echo
case "${1:-}" in
    --asr|--asr-small)
        echo "point the ASR service at it in .env:"
        echo "  ASR_MODEL_FILE=${FILES[0]}"
        echo "  ASR_MMPROJ_FILE=${FILES[1]}"
        echo "  COMPOSE_PROFILES=llm,asr" ;;
    *)
        echo "point the service at it with LLM_MODEL_FILE=${FILES[0]} in .env" ;;
esac

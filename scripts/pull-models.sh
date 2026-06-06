#!/bin/sh
set -e

MODEL_LIST="${EMBED_MODEL:-} ${CHAT_MODELS:-} ${REASONING_MODELS:-} ${AUTOCOMPLETE_MODELS:-}"
FAILED=0

for model in $MODEL_LIST; do
  echo "Pulling $model"
  if ollama pull "$model"; then
    echo "OK: $model"
  else
    echo "WARN: failed to pull $model (continuing)"
    FAILED=$((FAILED + 1))
  fi
done

if [ "$FAILED" -gt 0 ]; then
  echo "Model bootstrap finished with $FAILED failure(s). Core stack can still start."
  echo "Pull missing models later: docker exec -it ollama ollama pull <model>"
else
  echo "Model bootstrap complete"
fi

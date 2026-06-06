#!/bin/sh
set -e

MODEL_LIST="${EMBED_MODEL:-} ${CHAT_MODELS:-} ${REASONING_MODELS:-}"

for model in $MODEL_LIST; do
  echo "Pulling $model"
  ollama pull "$model"
done

echo "Model bootstrap complete"

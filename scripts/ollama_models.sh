#!/bin/sh
# Create 16k-context aliases of the stock Ollama models (the OpenAI endpoint cannot set num_ctx).
set -e
for m in "qwen2.5:7b" "llama3.1:8b" "ministral-3:8b" "qwen2.5:14b"; do
  if ollama show "$m" >/dev/null 2>&1; then
    printf 'FROM %s\nPARAMETER num_ctx 16384\n' "$m" > /tmp/sg.Modelfile
    ollama create "sg-$m" -f /tmp/sg.Modelfile >/dev/null && echo "created sg-$m"
  else
    echo "skip $m (not pulled; run: ollama pull $m)"
  fi
done

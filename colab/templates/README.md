# Chat templates served with vLLM

`tool_chat_template_llama3.1_json.jinja` is copied unmodified from vLLM v0.33.0
(`examples/tool_chat_template_llama3.1_json.jinja`, https://github.com/vllm-project/vllm, Apache-2.0).
SHA-256: `8aedd05f7ab549b897b929c89da9b62e537f4f4197f33736e9d2f10fea53b1bf` (checked by `sg_colab.fetch_template`).

It is kept here so every Llama run uses exactly the template of the pinned vLLM version without downloading it.
The template accepts one tool call per assistant message; the harness re-frames multi-call replies accordingly
(DECISIONS.md, C15). Do not add comments to the `.jinja` file: any text outside a tag becomes part of the prompt.

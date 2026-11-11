---
title: Marketing Script Generator
emoji: "✍️"
sdk: gradio
sdk_version: 6.15.1
python_version: 3.12.12
app_file: app.py
suggested_hardware: zero-a10g
license: apache-2.0
tags:
  - text-generation
  - qwen3
  - zerogpu
---

Private Gradio Space for generating research notes and short Spanish video
scripts. By default it runs `Qwen/Qwen3-1.7B` on CPU, capped at two threads,
so it does not consume ZeroGPU quota. The first request loads the model and may
take several minutes; later requests reuse it while the Space remains active.

The Space can remain private on its current ZeroGPU host; its default code path
does not request GPU. Set `USE_ZEROGPU=1` only to opt back into
`Qwen/Qwen3-4B` on the shared GPU quota. CPU Basic is a separate hardware tier
and may require an eligible paid Hub subscription for Gradio Spaces.

Call the `/generate` Gradio endpoint with `gradio_client` and a read-only
Hugging Face token that can access this private Space. Never commit that token.

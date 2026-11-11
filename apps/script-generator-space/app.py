import os
import threading

import spaces
import gradio as gr
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

USE_ZEROGPU = os.getenv("USE_ZEROGPU", "0").strip().lower() in {"1", "true", "yes"}
DEVICE = "cuda" if USE_ZEROGPU else "cpu"
MODEL_ID = "Qwen/Qwen3-4B" if USE_ZEROGPU else "Qwen/Qwen3-1.7B"

tokenizer = None
model = None
MODEL_LOCK = threading.Lock()

if not USE_ZEROGPU:
    # Keep CPU inference within the two-core shared Space allocation.
    torch.set_num_threads(max(1, min(2, os.cpu_count() or 1)))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


@spaces.GPU(duration=1)
def _gpu_runtime_marker() -> None:
    """Register ZeroGPU support; the default CPU path never invokes this."""
    return None


def maybe_request_gpu(function):
    """Keep CPU as the free default; opt into quota-backed ZeroGPU explicitly."""
    if USE_ZEROGPU:
        return spaces.GPU(duration=120)(function)
    return function


@maybe_request_gpu
def generate(prompt: str) -> str:
    global tokenizer, model
    if not prompt.strip():
        raise gr.Error("Escribe una instrucción para generar contenido.")
    if model is None:
        with MODEL_LOCK:
            if model is None:
                tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
                model = AutoModelForCausalLM.from_pretrained(
                    MODEL_ID,
                    torch_dtype=torch.bfloat16 if USE_ZEROGPU else torch.float32,
                    device_map=DEVICE,
                    low_cpu_mem_usage=True,
                )
                model.eval()

    messages = [
        {
            "role": "system",
            "content": (
                "Eres un asistente editorial en español. Basa las respuestas "
                "solo en la fuente proporcionada, distingue hechos de opiniones "
                "y no inventes datos ni citas."
            ),
        },
        {"role": "user", "content": prompt.strip()},
    ]
    inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    ).to(model.device)

    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=450,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.eos_token_id,
        )

    answer = generated[0, inputs["input_ids"].shape[1] :]
    result = tokenizer.decode(answer, skip_special_tokens=True).strip()
    if not result:
        raise gr.Error("El modelo no produjo una respuesta.")
    return result


demo = gr.Interface(
    fn=generate,
    inputs=gr.Textbox(label="Instrucción", lines=12),
    outputs=gr.Textbox(label="Resultado", lines=16),
    title="Marketing Content OS — generador editorial",
    api_name="generate",
)

if __name__ == "__main__":
    demo.launch()

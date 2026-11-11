import gradio as gr


def generate_visual_asset_agent(prompt: str, asset_type: str) -> str:
    if not prompt.strip() or not asset_type.strip():
        raise gr.Error("El prompt y el tipo de recurso son obligatorios.")
    raise gr.Error(
        "Backend de generacion visual no configurado. Integra el modelo antes de publicar este Space."
    )


demo = gr.Interface(
    fn=generate_visual_asset_agent,
    inputs=[gr.Textbox(label="Prompt"), gr.Textbox(label="Tipo de recurso")],
    outputs=gr.Textbox(label="URL del recurso"),
    title="Hyperframes API",
)

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)

import gradio as gr


def generate_script(topic: str, style: str, duration_seconds: int) -> str:
    if not topic.strip() or not style.strip() or duration_seconds < 1:
        raise gr.Error("Tema, estilo y duracion valida son obligatorios.")
    raise gr.Error(
        "Backend de generacion de texto no configurado. Integra un modelo antes de publicar este Space."
    )


demo = gr.Interface(
    fn=generate_script,
    inputs=[
        gr.Textbox(label="Tema"),
        gr.Textbox(label="Estilo", value="financial-news"),
        gr.Number(label="Duracion (segundos)", value=35, precision=0),
    ],
    outputs=gr.Textbox(label="Guion"),
    title="Open Gen AI API",
)

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)

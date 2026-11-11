---
title: VoiceStudio ZeroGPU Agent
emoji: "🎙️"
sdk: gradio
sdk_version: 6.15.1
python_version: 3.12.12
app_file: app.py
suggested_hardware: zero-a10g
license: agpl-3.0
tags:
  - audio
  - text-to-speech
  - zerogpu
---

Adaptador Gradio privado para el motor OmniVoice incluido en VoiceStudio.
Por defecto la inferencia usa CPU (dos hilos) y no solicita ZeroGPU, para no
consumir la cuota diaria. La primera llamada carga el modelo de forma diferida;
las siguientes reutilizan la instancia mientras el Space siga activo. CPU es
mucho mas lenta, en especial con clonacion, y el Space puede dormir tras horas
sin actividad.

Para optar por GPU compartida y consumir cuota, define `USE_ZEROGPU=1` en el
entorno del Space. No se necesita esa variable para el modo gratuito CPU.

La primera carga descarga los pesos publicos `k2-fsa/OmniVoice`. El endpoint
Gradio `/predict` recibe el texto, genero, edad y tono, y opcionalmente una
muestra de voz WAV mas su transcripcion exacta. OmniVoice usa la muestra para
clonacion zero-shot. Utiliza solo voces propias o autorizadas.

Manten el Space **Private**: Colab debe llamarlo con un token HF de solo lectura
guardado como secreto `HF_TOKEN`. No publiques ese token ni lo anadas al repo.
El codigo del motor VoiceStudio/OmniVoice se distribuye bajo AGPL-3.0; consulta
tambien la licencia del modelo antes de uso comercial.

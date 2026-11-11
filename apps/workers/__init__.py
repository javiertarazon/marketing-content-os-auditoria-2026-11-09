"""Workers ejecutables en local (Windows) para el Marketing Content OS.

Este paquete sustituye a los notebooks de Colab (``notebooks/01_research_scrapegraph.ipynb``
y ``notebooks/02_render_remotion.ipynb``) por dos procesos Python que se ejecutan en la
maquina local y se comunican con la API por HTTP plano (``http://127.0.0.1:8000``).

Modulos:

- ``worker_common``       Configuracion compartida, cliente de la API y bucle de trabajo.
- ``viral_research``      Busqueda de videos virales en YouTube (con o sin API key).
- ``remotion_project``    Generador del proyecto Remotion usado por el render.
- ``local_research_worker``  Etapa de investigacion + guion + voz.
- ``local_render_worker``    Etapa de render de video.
"""

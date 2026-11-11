"""Generador del proyecto Remotion que usa el worker de render.

Porta tal cual la celda 4 del notebook ``notebooks/02_render_remotion.ipynb``:
escribe ``package.json``, ``src/index.ts`` y ``src/Root.tsx`` con la composicion
``MarketingShort`` (1080x1920 a 30 fps, captions por bloques de 7 palabras, zoom,
overlay y barra de progreso) y devuelve los datos de render (duracion en frames y props).

La unica diferencia respecto al notebook es que el destino es un directorio escribible
cualquiera (en Windows no existe ``/content``): el worker de render lo crea dentro de
un directorio temporal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REMOTION_VERSION = "4.0.448"
REACT_VERSION = "19.0.0"
TYPESCRIPT_VERSION = "5.7.3"
COMPOSITION_ID = "MarketingShort"
FPS = 30
WIDTH = 1080
HEIGHT = 1920
DEFAULT_DURATION_FRAMES = 1050

PACKAGE_JSON: dict[str, Any] = {
    "private": True,
    "scripts": {"render": f"remotion render src/index.ts {COMPOSITION_ID}"},
    "dependencies": {
        "@remotion/cli": REMOTION_VERSION,
        "react": REACT_VERSION,
        "react-dom": REACT_VERSION,
        "remotion": REMOTION_VERSION,
    },
    "devDependencies": {"@types/react": REACT_VERSION, "typescript": TYPESCRIPT_VERSION},
}

INDEX_TS = (
    'import { registerRoot } from "remotion"; '
    'import { Root } from "./Root"; registerRoot(Root);'
)

TSCONFIG_JSON: dict[str, Any] = {
    "compilerOptions": {
        "target": "ES2020",
        "lib": ["ES2020", "DOM"],
        "module": "ESNext",
        "moduleResolution": "node",
        "jsx": "react-jsx",
        "esModuleInterop": True,
        "skipLibCheck": True,
        "forceConsistentCasingInFileNames": True,
        "noEmit": True,
    },
    "include": ["src"],
}

ROOT_TSX = r'''import React from "react";
import { AbsoluteFill, Audio, Composition, Img, OffthreadVideo, staticFile, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
type Props = { topic: string; script: string; duration_seconds: number; audio_duration_seconds: number; visual_asset?: { asset_type: "image" | "video"; filename: string } | null };
const captions = (text: string) => {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const chunks: string[] = [];
  for (let index = 0; index < words.length; index += 7) chunks.push(words.slice(index, index + 7).join(" "));
  return chunks.length ? chunks : [""];
};
const Video: React.FC<Props> = ({ topic, script, audio_duration_seconds, visual_asset }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const lines = captions(script);
  const segmentFrames = Math.max(1, audio_duration_seconds * fps / lines.length);
  const current = Math.min(lines.length - 1, Math.floor(frame / segmentFrames));
  const localFrame = frame - current * segmentFrames;
  const opacity = Math.min(1, Math.max(0.2, localFrame / 8));
  const offsetY = Math.max(0, (10 - localFrame) * 1.8);
  const zoom = interpolate(frame, [0, Math.max(1, audio_duration_seconds * fps)], [1, 1.045], { extrapolateRight: "clamp" });
  const progress = Math.min(1, frame / Math.max(1, audio_duration_seconds * fps));
  const mediaStyle: React.CSSProperties = { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", transform: "scale(" + zoom + ")" };
  return <AbsoluteFill style={{ background: "linear-gradient(145deg,#08121f,#16415d)", color: "white", fontFamily: "Arial, sans-serif" }}>
    {visual_asset?.asset_type === "image" && <Img src={staticFile(visual_asset.filename)} style={mediaStyle} />}
    {visual_asset?.asset_type === "video" && <OffthreadVideo src={staticFile(visual_asset.filename)} muted style={mediaStyle} />}
    <AbsoluteFill style={{ background: "linear-gradient(180deg,rgba(4,10,18,.52),rgba(4,10,18,.15) 42%,rgba(4,10,18,.68))" }} />
    <AbsoluteFill style={{ padding: 76, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
      <div>
        <div style={{ display: "inline-block", padding: "12px 20px", borderRadius: 999, background: "rgba(255,255,255,.16)", border: "1px solid rgba(255,255,255,.32)", fontSize: 24, letterSpacing: 3, fontWeight: 700 }}>IDEA CLAVE</div>
        <div style={{ marginTop: 28, fontSize: 54, lineHeight: 1.12, fontWeight: 800, textShadow: "0 3px 18px rgba(0,0,0,.55)" }}>{topic}</div>
      </div>
      <div style={{ opacity, transform: "translateY(" + offsetY + "px)", alignSelf: "center", maxWidth: "100%", padding: "26px 30px", borderRadius: 26, background: "rgba(3,9,16,.72)", border: "1px solid rgba(255,255,255,.24)", boxShadow: "0 16px 48px rgba(0,0,0,.28)", backdropFilter: "blur(8px)", fontSize: 48, lineHeight: 1.25, fontWeight: 700, textAlign: "center", textShadow: "0 2px 8px #000" }}>{lines[current]}</div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 24, letterSpacing: 2, fontWeight: 700 }}>
        <span>MARKETING CONTENT OS</span><span>{String(current + 1).padStart(2, "0")} / {String(lines.length).padStart(2, "0")}</span>
      </div>
      <div style={{ position: "absolute", left: 76, right: 76, bottom: 22, height: 6, background: "rgba(255,255,255,.28)", borderRadius: 99 }}>
        <div style={{ width: "100%", height: "100%", transform: "scaleX(" + progress + ")", transformOrigin: "left", background: "#58dcff", borderRadius: 99 }} />
      </div>
    </AbsoluteFill>
    <Audio src={staticFile("voice.wav")} />
  </AbsoluteFill>;
};
export const Root: React.FC = () => <Composition id="MarketingShort" component={Video} width={1080} height={1920} fps={30} durationInFrames={1050} defaultProps={{topic:"Tema",script:"",duration_seconds:35,audio_duration_seconds:35}} calculateMetadata={({props}) => ({durationInFrames:Math.max(30,Math.ceil(props.audio_duration_seconds*30))})} />;
'''


@dataclass(frozen=True)
class RenderPlan:
    """Datos que necesita el worker de render para lanzar el render."""

    project_dir: Path
    public_dir: Path
    src_dir: Path
    props_path: Path
    props: dict[str, Any]
    fps: int = FPS
    width: int = WIDTH
    height: int = HEIGHT
    composition_id: str = COMPOSITION_ID
    entry_point: str = "src/index.ts"
    audio_filename: str = "voice.wav"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def audio_path(self) -> Path:
        """Ruta del WAV dentro de ``public/`` (lo resuelve ``staticFile``)."""
        return self.public_dir / self.audio_filename

    def render_command(self, output_path: Path, npx: str = "npx") -> list[str]:
        """Comando exacto de render que ejecutara el worker."""
        return [
            npx,
            "remotion",
            "render",
            self.entry_point,
            self.composition_id,
            str(output_path),
            f"--props={self.props_path}",
            "--codec=h264",
            "--audio-codec=aac",
            "--crf=18",
            "--x264-preset=veryfast",
            "--pixel-format=yuv420p",
            "--concurrency=2",
        ]


def write_remotion_project(project_dir: Path, props: dict[str, Any]) -> RenderPlan:
    """Escribe el proyecto Remotion en ``project_dir`` y devuelve el plan de render.

    ``props`` debe traer ``topic``, ``script``, ``duration_seconds`` y
    ``audio_duration_seconds`` (opcionalmente ``visual_asset``).
    """
    project = Path(project_dir)
    public = project / "public"
    src = project / "src"
    public.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)

    (project / "package.json").write_text(
        json.dumps(PACKAGE_JSON, indent=2), encoding="utf-8"
    )
    (project / "tsconfig.json").write_text(
        json.dumps(TSCONFIG_JSON, indent=2), encoding="utf-8"
    )
    (src / "index.ts").write_text(INDEX_TS, encoding="utf-8")
    (src / "Root.tsx").write_text(ROOT_TSX, encoding="utf-8")

    props_path = project / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    return RenderPlan(
        project_dir=project,
        public_dir=public,
        src_dir=src,
        props_path=props_path,
        props=props,
    )

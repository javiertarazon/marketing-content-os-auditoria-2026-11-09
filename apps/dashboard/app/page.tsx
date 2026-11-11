"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

type Job = {
  job_id: string;
  parent_job_id?: string | null;
  worker: "research" | "render";
  status: string;
  request: { topic: string };
  progress: { stage: string; message: string; percent: number };
  output: {
    research?: {
      source?: string;
      notes?: string;
      sources?: Array<{
        platform: string;
        source_type?: string;
        title: string;
        url: string;
        channel?: string;
        view_count?: number;
        views_per_hour?: number;
        published_at?: string;
      }>;
      platform_status?: Array<{
        platform: string;
        status: string;
        result_count: number;
        message?: string;
      }>;
    };
    script?: string;
    audio_object_key?: string;
    audio_url?: string;
    video_object_key?: string;
    video_url?: string;
    approval?: {
      approved_at?: string;
      publish_requested?: boolean;
      platforms?: string[];
      note?: string | null;
    };
    publication?: {
      published?: boolean;
      error?: string;
      response?: { unit_id?: string; enqueued?: Array<{ id: string; target: string }> };
    };
    rejection?: { rejected_at?: string; reason?: string | null };
  } | null;
  error: string | null;
};

type Asset = {
  asset_id: string;
  asset_type: "voice" | "video" | "image";
  original_name: string;
  content_type: string;
  size_bytes: number;
};

type WorkerSession = {
  worker_id: string;
  worker: "research" | "render";
  state: "idle" | "working" | "stopped";
  current_job_id: string | null;
  stage: string;
  last_seen: string;
  online: boolean;
};

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const mixpostUrl = process.env.NEXT_PUBLIC_MIXPOST_URL || "http://localhost:3002";
const openDispatchUrl =
  process.env.NEXT_PUBLIC_OPEN_DISPATCH_URL || "http://localhost:8010";
const RESEARCH_PLATFORMS = [
  { value: "youtube", label: "YouTube" },
  { value: "reddit", label: "Reddit" },
  { value: "x", label: "X" },
  { value: "instagram", label: "Instagram" },
  { value: "tiktok", label: "TikTok" },
  { value: "facebook", label: "Facebook" },
];

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [workerSessions, setWorkerSessions] = useState<WorkerSession[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [topic, setTopic] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [platforms, setPlatforms] = useState(RESEARCH_PLATFORMS.map(({ value }) => value));
  const [voiceAssetId, setVoiceAssetId] = useState("");
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceCloneAuthorized, setVoiceCloneAuthorized] = useState(false);
  const [voiceGender, setVoiceGender] = useState("female");
  const [voiceAge, setVoiceAge] = useState("young adult");
  const [voicePitch, setVoicePitch] = useState("moderate pitch");
  const [visualAssetId, setVisualAssetId] = useState("");
  const [message, setMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);

  async function refreshJobs() {
    const response = await fetch(`${apiBase}/v1/jobs`, { cache: "no-store" });
    if (!response.ok) throw new Error(`API respondio HTTP ${response.status}`);
    setJobs((await response.json()) as Job[]);
  }

  async function refreshAssets() {
    const response = await fetch(`${apiBase}/v1/assets`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Biblioteca de medios: HTTP ${response.status}`);
    setAssets((await response.json()) as Asset[]);
  }

  async function refreshWorkers() {
    const response = await fetch(`${apiBase}/v1/workers`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Estado de workers: HTTP ${response.status}`);
    setWorkerSessions((await response.json()) as WorkerSession[]);
  }

  async function decideJob(jobId: string, action: "approve" | "reject") {
    setMessage(action === "approve" ? "Aprobando y publicando..." : "Rechazando el video...");
    try {
      const response = await fetch(`${apiBase}/v1/jobs/${jobId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          action === "approve" ? { publish: true } : { reason: "Rechazado desde el dashboard" },
        ),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`HTTP ${response.status}: ${detail}`);
      }
      const job = (await response.json()) as Job;
      const publication = job.output?.publication;
      if (action === "approve") {
        setMessage(
          publication?.published
            ? `Video aprobado y publicado en ${(publication.response?.enqueued ?? [])
                .map((item) => item.target)
                .join(", ")}`
            : `Video aprobado, pero la publicacion fallo: ${publication?.error ?? "sin detalle"}`,
        );
      } else {
        setMessage("Video rechazado: no se publico.");
      }
      await refreshJobs();
    } catch (error) {
      setMessage(`No se pudo ${action === "approve" ? "aprobar" : "rechazar"}: ${(error as Error).message}`);
    }
  }

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
    folderInput.current?.setAttribute("directory", "");
    const refresh = () => {
      Promise.all([refreshJobs(), refreshAssets(), refreshWorkers()]).catch((error: Error) => setMessage(error.message));
    };
    refresh();
    const interval = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(interval);
  }, []);

  async function uploadFolder(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;

    setIsUploading(true);
    setMessage(`Importando ${files.length} archivo(s) a R2 privado...`);
    let uploaded = 0;
    try {
      for (const file of files) {
        const extension = file.name.split(".").pop()?.toLowerCase();
        const assetType =
          extension === "wav" ? "voice" :
          extension === "mp4" ? "video" :
          ["jpg", "jpeg", "png", "webp"].includes(extension ?? "") ? "image" : null;
        if (!assetType) {
          throw new Error(`Formato no admitido: ${file.name}. Se omiten formatos distintos de WAV, MP4, JPG, PNG y WEBP.`);
        }
        const formData = new FormData();
        formData.set("asset_type", assetType);
        formData.set("file", file);
        const response = await fetch(`${apiBase}/v1/assets`, { method: "POST", body: formData });
        if (!response.ok) {
          const detail = (await response.json()) as { detail?: string };
          throw new Error(`${file.name}: ${detail.detail ?? `HTTP ${response.status}`}`);
        }
        uploaded += 1;
        setMessage(`Importados ${uploaded} de ${files.length} archivo(s)...`);
      }
      await refreshAssets();
      setMessage(`${uploaded} medio(s) importados en R2 privado.`);
    } catch (error) {
      setMessage(
        `${uploaded} archivo(s) importados antes del error: ${
          error instanceof Error ? error.message : "No se pudo importar la carpeta."
        }`,
      );
      try {
        await refreshAssets();
      } catch (refreshError) {
        setMessage(
          `${uploaded} archivo(s) importados antes del error. No se pudo actualizar la biblioteca: ${
            refreshError instanceof Error ? refreshError.message : "error desconocido"
          }`,
        );
      }
    } finally {
      setIsUploading(false);
    }
  }

  async function submitJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    if (voiceAssetId && (!voiceTranscript.trim() || !voiceCloneAuthorized)) {
      setMessage("Para clonar la voz, indica la transcripción exacta y confirma que tienes autorización.");
      return;
    }
    try {
      const response = await fetch(`${apiBase}/v1/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          worker: "research",
          request: {
            topic,
            source_url: sourceUrl.trim() || null,
            style: "financial-news",
            duration_seconds: 35,
            voice_gender: voiceGender,
            voice_age: voiceAge,
            voice_pitch: voicePitch,
            platforms,
            voice_reference_asset_id: voiceAssetId || null,
            voice_reference_transcript: voiceAssetId ? voiceTranscript : null,
            voice_clone_authorized: voiceAssetId ? voiceCloneAuthorized : false,
            visual_asset_id: visualAssetId || null,
          },
        }),
      });
      if (!response.ok) {
        const error = (await response.json()) as { detail?: string };
        throw new Error(error.detail ?? `API respondio HTTP ${response.status}`);
      }
      setTopic("");
      setSourceUrl("");
      setVoiceTranscript("");
      setVoiceCloneAuthorized(false);
      setMessage("Trabajo creado; esperando que los workers tomen cada módulo.");
      await refreshJobs();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "No se pudo crear el trabajo.");
    }
  }

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 32 }}>
      <h1>Marketing Content OS</h1>
      <p>Control local del pipeline. Los workers de Colab toman trabajos de la cola mientras sus sesiones estén activas.</p>
      <p>
        <a href={openDispatchUrl} target="_blank" rel="noreferrer">
          Abrir Open-Dispatch para programar contenido
        </a>
        {" · "}
        <a href={mixpostUrl} target="_blank" rel="noreferrer">
          Mixpost Lite (scheduler alternativo)
        </a>
      </p>
      <section aria-labelledby="workers-heading" style={{ border: "1px solid #ccc", padding: 16, margin: "24px 0" }}>
        <h2 id="workers-heading">Workers de Colab</h2>
        <p>Deja ejecutándose la celda worker de cada notebook. Si Colab se desconecta, vuelve a iniciarla; los trabajos pendientes permanecen en la cola local.</p>
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
          {(["research", "render"] as const).map((worker) => {
            const sessions = workerSessions.filter((session) => session.worker === worker);
            const online = sessions.filter((session) => session.online);
            const latest = sessions.sort((a, b) => Date.parse(b.last_seen) - Date.parse(a.last_seen))[0];
            const title = worker === "research" ? "Investigación y voz" : "Render de video";
            return (
              <article key={worker} style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12 }}>
                <strong>{title}</strong>
                <p role="status" style={{ margin: "8px 0" }}>
                  {online.length > 0 ? `Conectado · ${online.length} sesión(es)` : "Desconectado"}
                </p>
                {online.length > 0 ? (
                  online.map((session) => (
                    <small key={session.worker_id} style={{ display: "block" }}>
                      {session.state === "working" ? `Procesando: ${session.stage}` : "Esperando trabajos"}
                      {session.current_job_id ? ` · ${session.current_job_id.slice(0, 8)}` : ""}
                    </small>
                  ))
                ) : (
                  <small>
                    {latest?.state === "stopped" ? "La última sesión se detuvo." : "Abre el notebook correspondiente en Colab y ejecuta su celda worker."}
                    {latest ? ` Última señal: ${new Date(latest.last_seen).toLocaleTimeString()}.` : " No se ha conectado ninguna sesión."}
                  </small>
                )}
              </article>
            );
          })}
        </div>
      </section>
      <section style={{ border: "1px solid #ccc", padding: 16, margin: "24px 0" }}>
        <h2>Biblioteca local de medios</h2>
        <p>
          Selecciona una carpeta para importar WAV, MP4, JPG, PNG o WEBP. Los archivos se guardan en R2
          privado; la muestra de voz requiere su transcripción exacta.
        </p>
        <input
          ref={folderInput}
          type="file"
          multiple
          accept=".wav,.mp4,.jpg,.jpeg,.png,.webp"
          aria-label="Importar carpeta local de medios"
          onChange={uploadFolder}
          disabled={isUploading}
        />
        <ul>
          {assets.map((asset) => (
            <li key={asset.asset_id}>
              {asset.asset_type}: {asset.original_name} ({(asset.size_bytes / (1024 * 1024)).toFixed(1)} MB)
            </li>
          ))}
        </ul>
      </section>
      <form onSubmit={submitJob} style={{ display: "grid", gap: 8, margin: "24px 0" }}>
        <input aria-label="Tema del contenido" required value={topic}
          onChange={(event) => setTopic(event.target.value)} placeholder="Tema del contenido"
          style={{ padding: 10 }} />
        <label htmlFor="source-url">URL de referencia opcional</label>
        <input id="source-url" aria-label="URL de referencia opcional" type="url" value={sourceUrl}
          onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://sitio.com/articulo"
          style={{ padding: 10 }} />
        <fieldset style={{ display: "grid", gap: 8 }}>
          <legend>Buscar contenido público sin claves de redes sociales</legend>
          <small>
            ScrapeGraphAI consulta DuckDuckGo y analiza páginas accesibles públicamente.
            Los resultados dependen de la indexación y del acceso anónimo de cada red.
          </small>
          {RESEARCH_PLATFORMS.map((platform) => (
            <label key={platform.value}>
              <input
                type="checkbox"
                checked={platforms.includes(platform.value)}
                onChange={() => setPlatforms((current) =>
                  current.includes(platform.value)
                    ? current.filter((value) => value !== platform.value)
                    : [...current, platform.value]
                )}
              />
              {" "}{platform.label}
            </label>
          ))}
        </fieldset>
        <fieldset style={{ display: "grid", gap: 8 }}>
          <legend>Diseño de voz para OmniVoice</legend>
          <label>Voz
            <select value={voiceGender} onChange={(event) => setVoiceGender(event.target.value)}>
              <option value="female">Femenina</option><option value="male">Masculina</option>
            </select>
          </label>
          <label>Edad percibida
            <select value={voiceAge} onChange={(event) => setVoiceAge(event.target.value)}>
              {[["child", "Infantil"], ["teenager", "Adolescente"], ["young adult", "Adulta joven"], ["middle-aged", "Mediana edad"], ["elderly", "Mayor"]].map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>Tono
            <select value={voicePitch} onChange={(event) => setVoicePitch(event.target.value)}>
              {[["very low pitch", "Muy grave"], ["low pitch", "Grave"], ["moderate pitch", "Medio"], ["high pitch", "Agudo"], ["very high pitch", "Muy agudo"]].map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
        </fieldset>
        <label>
          Voz clonada (muestra WAV)
          <select value={voiceAssetId} onChange={(event) => setVoiceAssetId(event.target.value)}>
            <option value="">Voz del modelo</option>
            {assets.filter((asset) => asset.asset_type === "voice").map((asset) => (
              <option key={asset.asset_id} value={asset.asset_id}>{asset.original_name}</option>
            ))}
          </select>
        </label>
        {voiceAssetId && (
          <>
            <label htmlFor="voice-transcript">Transcripción exacta de la muestra de voz</label>
            <textarea
              id="voice-transcript"
              required
              value={voiceTranscript}
              onChange={(event) => setVoiceTranscript(event.target.value)}
              maxLength={5000}
              rows={3}
            />
            <label>
              <input
                type="checkbox"
                checked={voiceCloneAuthorized}
                onChange={(event) => setVoiceCloneAuthorized(event.target.checked)}
              />
              Confirmo que soy titular de esta voz o tengo autorización para clonarla.
            </label>
          </>
        )}
        <label>
          Fondo propio para el video
          <select value={visualAssetId} onChange={(event) => setVisualAssetId(event.target.value)}>
            <option value="">Usar fondo generado</option>
            {assets.filter((asset) => asset.asset_type !== "voice").map((asset) => (
              <option key={asset.asset_id} value={asset.asset_id}>
                {asset.asset_type}: {asset.original_name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" style={{ padding: "10px 16px" }}>Crear trabajo</button>
      </form>
      {message && <p role="status">{message}</p>}
      <h2>Progreso por módulo</h2>
      <button type="button" onClick={() => Promise.all([refreshJobs(), refreshWorkers()]).catch((error: Error) => setMessage(error.message))}>
        Actualizar
      </button>
      <ul>
        {jobs.map((job) => (
          <li key={job.job_id} style={{ margin: "16px 0" }}>
            <strong>{job.request.topic}</strong> — pipeline {job.parent_job_id ?? job.job_id} — módulo {job.worker} — {job.status}
            <p>{job.progress.message}</p>
            <progress
              aria-label={`Progreso de ${job.worker}`}
              max={100}
              value={job.progress.percent}
              style={{ width: "100%", height: 16 }}
            />
            <small>{job.progress.stage} · {job.progress.percent}%</small>
            {job.error && <p role="alert">{job.error}</p>}
            {job.worker === "research" && job.status === "succeeded" && job.output && (
              <details style={{ marginTop: 8 }}>
                <summary>Ver investigación y guion</summary>
                {job.output.research?.source && <p>Fuente: {job.output.research.source}</p>}
                {job.output.research?.notes && <p>{job.output.research.notes}</p>}
                {job.output.research?.sources && (
                  <section>
                    <strong>Fuentes extraídas</strong>
                    <ul>
                      {job.output.research.sources.map((source) => (
                        <li key={`${source.platform}-${source.url}`}>
                          {source.platform}: <a href={source.url} target="_blank" rel="noreferrer">
                            {source.title || source.url}
                          </a>
                          {source.source_type === "search_snippet" ? " (fragmento indexado, no el post completo)" : ""}
                          {source.view_count != null && (
                            <small> · {source.view_count.toLocaleString()} vistas · {source.views_per_hour?.toLocaleString()} vistas/h · {source.channel}</small>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {job.output.research?.platform_status && (
                  <section>
                    <strong>Resultado por plataforma</strong>
                    <ul>
                      {job.output.research.platform_status.map((status) => (
                        <li key={status.platform}>
                          {status.platform}: {status.status} ({status.result_count} fuentes)
                          {status.message ? ` — ${status.message}` : ""}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {job.output.script && <p><strong>Guion:</strong> {job.output.script}</p>}
              </details>
            )}
            {job.worker === "research" && job.status === "succeeded" &&
              (job.output?.audio_object_key || job.output?.audio_url) && (
              <div style={{ marginTop: 8 }}>
                <p><strong>Narración de voz generada:</strong></p>
                <audio
                  controls
                  src={job.output.audio_url ?? `${apiBase}/v1/jobs/${job.job_id}/media/audio`}
                  style={{ width: "100%", marginTop: 4 }}
                />
              </div>
            )}
            {job.worker === "render" &&
              ["awaiting_approval", "approved", "rejected"].includes(job.status) &&
              (job.output?.video_object_key || job.output?.video_url) && (
              <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
                <p>
                  <strong>
                    {job.status === "awaiting_approval"
                      ? "Video final listo: pendiente de tu aprobacion"
                      : job.status === "approved"
                        ? "Video aprobado"
                        : "Video rechazado"}
                  </strong>
                </p>
                <video
                  controls
                  src={job.output.video_url ?? `${apiBase}/v1/jobs/${job.job_id}/media/video`}
                  style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }}
                />
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <a
                    href={job.output.video_url ?? `${apiBase}/v1/jobs/${job.job_id}/media/video`}
                    download
                    style={{ padding: "8px 14px", background: "#0070f3", color: "#fff", borderRadius: 4, textDecoration: "none" }}
                  >
                    Descargar MP4
                  </a>
                  {job.status === "awaiting_approval" && (
                    <>
                      <button
                        type="button"
                        onClick={() => decideJob(job.job_id, "approve")}
                        style={{ padding: "8px 14px", background: "#10b981", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
                      >
                        Aprobar y publicar
                      </button>
                      <button
                        type="button"
                        onClick={() => decideJob(job.job_id, "reject")}
                        style={{ padding: "8px 14px", background: "#ef4444", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
                      >
                        Rechazar
                      </button>
                    </>
                  )}
                  <a
                    href={openDispatchUrl}
                    target="_blank"
                    rel="noreferrer"
                    style={{ padding: "8px 14px", background: "#6b7280", color: "#fff", borderRadius: 4, textDecoration: "none" }}
                  >
                    Cola del scheduler (Open-Dispatch)
                  </a>
                </div>
                {job.output?.publication && (
                  <p style={{ fontSize: 14 }}>
                    {job.output.publication.published ? (
                      <>
                        <strong>Publicado:</strong>{" "}
                        {(job.output.publication.response?.enqueued ?? [])
                          .map((item) => item.target)
                          .join(", ")}
                        {job.output.publication.response?.unit_id
                          ? ` · unidad ${job.output.publication.response.unit_id}`
                          : ""}
                      </>
                    ) : (
                      <>
                        <strong>La publicacion fallo:</strong>{" "}
                        {job.output.publication.error}
                      </>
                    )}
                  </p>
                )}
                {job.output?.rejection?.reason && (
                  <p style={{ fontSize: 14 }}>
                    <strong>Motivo del rechazo:</strong> {job.output.rejection.reason}
                  </p>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}

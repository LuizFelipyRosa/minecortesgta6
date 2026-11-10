#!/usr/bin/env python3
"""
MineCortes - motor (cortes, legendas, zoom, fila de postagem e agendamento).

A interface gráfica está em minecortes_gui.py. Este arquivo também funciona na linha de comando:

  python minecortes.py clips   live.mp4 --n 10 --len 40
  python minecortes.py compile live.mp4 --minutes 10
  python minecortes.py watch   C:\\Lives
  python minecortes.py fila            (só processa a fila de postagens, sem janela)

Requisitos: ffmpeg + ffprobe no PATH e `pip install -r requirements.txt`.
"""
import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

APP_NAME = "MineCortes"
BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "minecortes_config.json"
QUEUE_FILE = BASE / "minecortes_fila.json"
YT_TOKEN_FILE = BASE / "youtube_token.json"

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm"}

# Palavras (PT-BR) que costumam marcar reação / clímax.
KEYWORDS = {
    "caramba", "nossa", "socorro", "absurdo", "insano", "louco", "gg", "clutch",
    "ace", "pqp", "porra", "uau", "wow", "incrível", "impossível", "brabo",
    "mito", "inacreditável", "jesus", "misericórdia", "bora", "vamo", "morreu",
    "kkk", "kkkk", "haha", "hahaha",
}

# --------------------------------------------------------------------------- estilo de legenda
DEFAULT_STYLE = {
    "enabled": True,
    "font": "Arial", "size": 88, "bold": True, "uppercase": True,
    "mode": "destaque",           # destaque | palavra | linha
    "words": 3,                   # palavras por bloco
    "color": "#FFFFFF", "highlight": "#FFE600",
    "outline_color": "#000000", "outline": 7, "shadow": 2,
    "box": False, "box_color": "#000000", "box_opacity": 65, "box_pad": 14,
    "pop": True,                  # animação de "pulinho" quando a legenda entra
    "pos_y": 68,                  # altura da legenda, % a partir do topo
}

PRESETS = {
    "Clássico amarelo": {},
    "Karaokê neon": {"font": "Impact", "size": 96, "highlight": "#00F0FF",
                     "outline_color": "#7A00FF", "outline": 9, "shadow": 0, "pos_y": 66},
    "Caixa preta": {"size": 72, "box": True, "box_color": "#000000", "box_opacity": 70,
                    "outline": 0, "words": 4, "pos_y": 72},
    "Marker roxo": {"size": 80, "box": True, "box_color": "#7C3AED", "box_opacity": 100,
                    "highlight": "#FDE047", "outline": 0, "pos_y": 66},
    "Uma palavra (impacto)": {"font": "Impact", "size": 140, "mode": "palavra", "words": 1,
                              "highlight": "#FFFFFF", "outline": 10, "shadow": 3, "pos_y": 55},
    "Minimalista": {"size": 60, "bold": False, "uppercase": False, "mode": "linha",
                    "words": 5, "outline": 3, "shadow": 0, "pop": False, "pos_y": 80},
}

DEFAULT_SETTINGS = {
    "video": "", "lang": "pt", "model": "small", "out": "saida",
    "n": 10, "len": 40, "minutes": 10, "seg_len": 30,
    "layout": "blur",             # blur (vídeo inteiro + fundo desfocado) | preencher (corta o centro)
    "focus": 0.5,                 # 0 = esquerda, 0.5 = centro, 1 = direita (layout preencher)
    "zoom": True, "zoom_strength": 0.14,
    "hashtags": "live, cortes, melhoresmomentos", "description": "",
    "platforms": ["youtube"], "yt_privacy": "private", "tt_privacy": "SELF_ONLY",
    "when_mode": "now", "when_date": "", "when_time": "18:00", "step_hours": 6,
    "auto_folder": "", "auto_every": 6, "auto_delay": 10,
    "style_name": "Clássico amarelo", "style": {},
}

DEFAULT_CONFIG = {
    "settings": {},
    "custom_styles": {},
    "accounts": {"yt_client_secret": "", "tiktok_token": "", "ig_user_id": "",
                 "ig_token": "", "ig_version": "v23.0"},
}


def full_style(d=None):
    s = dict(DEFAULT_STYLE)
    s.update(d or {})
    return s


def full_settings(d=None):
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(d or {})
    s["style"] = full_style(s.get("style"))
    return s


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg["settings"] = saved.get("settings", {})
            cfg["custom_styles"] = saved.get("custom_styles", {})
            cfg["accounts"].update(saved.get("accounts", {}))
        except Exception:
            pass
    cfg["settings"] = full_settings(cfg["settings"])
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- utilitários
def run(cmd, cwd=None):
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # sem janela preta no Windows
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **kw)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg/ffprobe não encontrado. Instale (winget install ffmpeg) "
                           "e reabra o MineCortes.")
    if r.returncode != 0:
        raise RuntimeError(f"Erro no {cmd[0]}:\n{r.stderr[-1500:]}")
    return r


def video_duration(video):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)])
    return float(r.stdout.strip())


def zscore(x):
    return (x - x.mean()) / (x.std() + 1e-6)


def smooth(x, k=3):
    return np.convolve(x, np.ones(k) / k, mode="same")


def out_dir_for(out, video):
    d = Path(out) / Path(video).stem
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- análise da live
def extract_audio(video, wav):
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])


def per_second_rms(wav):
    out = []
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        while True:
            raw = w.readframes(sr)
            if len(raw) < sr * 2:
                break
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            out.append(float(np.sqrt((a ** 2).mean())))
    return np.array(out)


def transcribe(video, wav, lang, model_size, duration, cb):
    cache = Path(str(video) + ".transcript.json")
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("lang") == lang and d.get("model") == model_size:
                cb(0.9, "Transcrição encontrada no cache.")
                return d["segments"], d["words"]
        except Exception:
            pass
    from faster_whisper import WhisperModel
    cb(0.15, f"Carregando modelo '{model_size}' (na 1ª vez baixa da internet)...")
    model = WhisperModel(model_size, compute_type="int8")
    segs, _ = model.transcribe(str(wav), language=lang, word_timestamps=True, vad_filter=True)
    segments, words = [], []
    for s in segs:
        segments.append([s.start, s.end, s.text.strip()])
        for w in s.words or []:
            words.append([w.start, w.end, w.word.strip()])
        if duration:
            cb(0.15 + 0.75 * min(1.0, s.end / duration),
               f"Transcrevendo... {int(100 * min(1.0, s.end / duration))}%")
    cache.write_text(json.dumps({"lang": lang, "model": model_size, "segments": segments,
                                 "words": words}, ensure_ascii=False), encoding="utf-8")
    return segments, words


def score_seconds(rms, words):
    n = len(rms)
    wps = np.zeros(n)
    kw = np.zeros(n)
    for s, _e, w in words:
        i = int(s)
        if i >= n:
            continue
        wps[i] += 1
        if re.sub(r"\W", "", w.lower()) in KEYWORDS or "!" in w:
            kw[i] += 1
    return (zscore(smooth(np.log1p(rms)))
            + 0.5 * zscore(smooth(wps, 5))
            + 1.2 * smooth(np.minimum(kw, 3), 3))


def analyze(video, lang="pt", model="small", cb=None):
    cb = cb or (lambda f, m: None)
    video = Path(video)
    if not video.exists():
        raise RuntimeError(f"Arquivo não encontrado: {video}")
    dur = video_duration(video)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        cb(0.02, "Extraindo o áudio...")
        extract_audio(video, wav)
        cb(0.08, "Medindo o volume segundo a segundo...")
        rms = per_second_rms(wav)
        segments, words = transcribe(video, wav, lang, model, dur, cb)
    cb(0.95, "Procurando os melhores momentos...")
    return {"video": str(video), "duration": dur, "rms": rms, "segments": segments,
            "words": words, "sec": score_seconds(rms, words)}


def snap(start, end, segments, tol=6.0):
    s, e = start, end
    starts = [a for a, _b, _t in segments if start - tol <= a <= start]
    ends = [b for _a, b, _t in segments if end <= b <= end + tol]
    if starts:
        s = max(starts)
    if ends:
        e = min(ends)
    return s, e


def pick_windows(sec, segments, n, length, step=5):
    L = int(min(length, len(sec) - 1))
    if L < 5:
        return []
    cs = np.concatenate([[0], np.cumsum(sec)])
    cands = sorted(((cs[i + L] - cs[i]) / L, i) for i in range(0, max(1, len(sec) - L), step))
    cands.reverse()
    chosen = []
    for sc, i in cands:
        s, e = snap(i, i + L, segments)
        if any(not (e <= a or s >= b) for a, b, _ in chosen):
            continue
        chosen.append((s, e, float(sc)))
        if len(chosen) >= n:
            break
    return chosen


def make_title(segments, start, end):
    best, bs = None, -1.0
    for a, b, t in segments:
        if a < start - 1 or b > end + 1:
            continue
        txt = t.strip()
        if len(txt.split()) < 3:
            continue
        sc = (3 * sum(1 for w in re.findall(r"\w+", txt.lower()) if w in KEYWORDS)
              + 2 * txt.count("!") + min(len(txt), 80) / 80)
        if sc > bs:
            best, bs = txt, sc
    if not best:
        return "Melhor momento da live"
    best = best.strip(" .,;")
    if len(best) > 70:
        best = best[:70].rsplit(" ", 1)[0] + "…"
    return best[0].upper() + best[1:]


def find_clips(analysis, n, length):
    picks = pick_windows(analysis["sec"], analysis["segments"], n, length)
    return [{"id": i, "start": s, "end": e, "score": sc,
             "title": make_title(analysis["segments"], s, e),
             "selected": True, "file": None}
            for i, (s, e, sc) in enumerate(picks, 1)]


def zoom_moments(rms, words, start, end, max_zooms=5, min_gap=4.0):
    cand = []
    a, b = int(start), min(int(end), len(rms))
    if b - a > 2:
        seg = zscore(rms[a:b])
        for i in range(1, len(seg) - 1):
            if seg[i] > 1.0 and seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]:
                cand.append((float(seg[i]), i + a - start))
    for s, _e, w in words:
        if start <= s <= end and re.sub(r"\W", "", w.lower()) in KEYWORDS:
            cand.append((1.5, s - start))
    cand.sort(reverse=True)
    out = []
    for _sc, t in cand:
        if all(abs(t - o) >= min_gap for o in out):
            out.append(t)
        if len(out) >= max_zooms:
            break
    return sorted(out)


# --------------------------------------------------------------------------- legendas (ASS)
def _rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def ass_color(hex_, alpha=0):
    r, g, b = _rgb(hex_)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_tag(hex_):
    r, g, b = _rgb(hex_)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_time(t):
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(t, st):
    t = re.sub(r"[{}\\]", "", t).strip()
    return t.upper() if st["uppercase"] else t


def make_ass(words, path, style, w=1080, h=1920):
    """words = [(inicio, fim, texto)] com tempos relativos ao início do corte."""
    st = full_style(style)
    words = [(float(a), float(b), _clean(t, st)) for a, b, t in words]
    words = [x for x in words if x[2]]
    y = int(h * st["pos_y"] / 100)
    if st["box"]:
        alpha = int(round(255 * (1 - st["box_opacity"] / 100)))
        border, outline_col, outline = 3, ass_color(st["box_color"], alpha), st["box_pad"]
    else:
        border, outline_col, outline = 1, ass_color(st["outline_color"]), st["outline"]
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{st['font']},{st['size']},{ass_color(st['color'])},&H000000FF,"
        f"{outline_col},{ass_color('#000000', 110)},{-1 if st['bold'] else 0},0,0,0,100,100,0,0,"
        f"{border},{outline},{st['shadow']},5,60,60,0,1\n\n[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    pos = f"{{\\an5\\pos({w // 2},{y})}}"
    pop = "{\\fscx78\\fscy78\\t(0,110,\\fscx100\\fscy100)}" if st["pop"] else ""
    base, hl = ass_tag(st["color"]), ass_tag(st["highlight"])

    def line(a, b, txt, with_pop):
        b = max(b, a + 0.08)
        return (f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,"
                f"{pos}{pop if with_pop else ''}{txt}")

    n = 1 if st["mode"] == "palavra" else max(1, int(st["words"]))
    chunks, cur = [], []
    for wd in words:
        if cur and (len(cur) >= n or wd[0] - cur[-1][1] > 0.9):
            chunks.append(cur)
            cur = []
        cur.append(wd)
    if cur:
        chunks.append(cur)

    lines = []
    for ch in chunks:
        if st["mode"] == "linha":
            lines.append(line(ch[0][0], ch[-1][1] + 0.15, " ".join(x[2] for x in ch), True))
        elif st["mode"] == "palavra":
            lines.append(line(ch[0][0], ch[0][1] + 0.1, ch[0][2], True))
        else:  # destaque: bloco inteiro visível, palavra atual colorida
            for k, cw in enumerate(ch):
                end = ch[k + 1][0] if k + 1 < len(ch) else cw[1] + 0.25
                txt = " ".join(
                    f"{{\\1c{hl}}}{x[2]}{{\\1c{base}}}" if j == k else x[2]
                    for j, x in enumerate(ch))
                lines.append(line(cw[0], end, txt, k == 0))
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- renderização
def build_graph(layout, focus, zooms, strength, subs, pts_shift=None, final_w=None):
    if layout == "blur":
        g = ("[0:v]split[a][b];"
             "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
             "boxblur=8:2,scale=1080:1920[bg];"
             "[b]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
             "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];")
    else:
        g = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
             f"crop=1080:1920:(iw-1080)*{focus:.3f}:(ih-1920)/2[v0];")
    if zooms:
        z = "1+" + "+".join(f"{strength:.3f}*max(0,1-abs(t-{t:.2f})/0.7)" for t in zooms)
        g += f"[v0]scale=w='1080*({z})':h='1920*({z})':eval=frame,crop=1080:1920[v1];"
    else:
        g += "[v0]null[v1];"
    ops = []
    if pts_shift is not None:
        ops.append(f"setpts=PTS+{pts_shift}/TB")
    if subs:
        ops.append("subtitles=c.ass")
    if final_w:
        ops.append(f"scale={final_w}:-2")
    ops.append("format=yuv420p")
    return g + "[v1]" + ",".join(ops) + "[v]"


def render_clip(video, clip, analysis, settings, out):
    s = full_settings(settings)
    st = s["style"]
    start, end = float(clip["start"]), float(clip["end"])
    words = [(a - start, b - start, w) for a, b, w in analysis["words"]
             if a >= start - 0.05 and b <= end + 0.05 and w]
    zooms = zoom_moments(analysis["rms"], analysis["words"], start, end) if s["zoom"] else []
    use_subs = bool(st["enabled"] and words)
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if use_subs:
            make_ass(words, Path(tmp) / "c.ass", st)
        g = build_graph(s["layout"], float(s["focus"]), zooms, float(s["zoom_strength"]), use_subs)
        run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(Path(video).resolve()),
             "-t", f"{end - start:.2f}", "-filter_complex", g, "-map", "[v]", "-map", "0:a?",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], cwd=tmp)
    return out


def make_preview(video, t, style, settings, sample_words, out_png, width=360):
    """Gera um PNG com um frame do vídeo + a legenda no estilo escolhido."""
    st = full_style(style)
    s = full_settings(settings)
    texts = [x for x in (sample_words or []) if x][:6] or ["ISSO", "FOI", "INSANO", "DEMAIS"]
    while len(texts) < 2:
        texts.append("GENTE")
    words = [(i * 1.0, (i + 1) * 1.0, tx) for i, tx in enumerate(texts)]
    n = 1 if st["mode"] == "palavra" else max(1, int(st["words"]))
    k = 1 if st["mode"] == "palavra" else min(1, n - 1)
    shift = k + 0.5
    with tempfile.TemporaryDirectory() as tmp:
        make_ass(words, Path(tmp) / "c.ass", st)
        g = build_graph(s["layout"], float(s["focus"]), [], 0, True, shift, width)
        if video:
            inp = ["-ss", f"{max(0.0, t):.2f}", "-i", str(Path(video).resolve())]
        else:
            inp = ["-f", "lavfi", "-i", "color=c=0x2b2b3a:s=1280x720:d=3"]
        run(["ffmpeg", "-y", *inp, "-frames:v", "1", "-filter_complex", g, "-map", "[v]",
             str(Path(out_png).resolve())], cwd=tmp)
    return str(out_png)


def render_landscape(video, start, end, out):
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(video), "-t", f"{end - start:.2f}",
         "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])


def compile_long(video, analysis, minutes, seg_len, out_dir, cb=None):
    cb = cb or (lambda f, m: None)
    video = Path(video).resolve()
    target = minutes * 60
    picks = pick_windows(analysis["sec"], analysis["segments"],
                         math.ceil(target / seg_len) * 3, seg_len)
    chosen, total = [], 0.0
    for s, e, _sc in picks:
        if total >= target:
            break
        if total + (e - s) > target:
            e = s + (target - total)
        chosen.append((s, e))
        total += e - s
    if not chosen:
        raise RuntimeError("Não encontrei trechos suficientes para montar o vídeo.")
    chosen.sort()
    out = Path(out_dir) / f"melhores_momentos_{minutes:g}min.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        lst = []
        for i, (s, e) in enumerate(chosen):
            part = Path(tmp) / f"p{i:03d}.mp4"
            cb(i / len(chosen), f"Trecho {i + 1}/{len(chosen)} ({s:.0f}s-{e:.0f}s)")
            render_landscape(video, s, e, part)
            lst.append(f"file '{part.as_posix()}'")
        (Path(tmp) / "list.txt").write_text("\n".join(lst), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(Path(tmp) / "list.txt"),
             "-c", "copy", "-movflags", "+faststart", str(out)])
    cb(1.0, f"Vídeo longo pronto ({total / 60:.1f} min)")
    return out


# --------------------------------------------------------------------------- postagem
def build_caption(title, hashtags, description=""):
    tags = " ".join("#" + t.lstrip("#") for t in re.split(r"[,\s]+", hashtags or "") if t.strip("#"))
    parts = [title.strip()]
    if description.strip():
        parts.append(description.strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)


def youtube_service(cfg):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None
    if YT_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(YT_TOKEN_FILE), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secret = cfg["accounts"].get("yt_client_secret") or str(BASE / "client_secret.json")
            if not Path(secret).exists():
                raise RuntimeError("Falta o arquivo client_secret.json do YouTube (aba Contas).")
            creds = InstalledAppFlow.from_client_secrets_file(secret, scopes).run_local_server(port=0)
        YT_TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_youtube(path, title, caption, privacy, publish_at, cfg):
    """publish_at: datetime local (ou None). Com publish_at o vídeo sobe PRIVADO e o YouTube
    publica sozinho na hora marcada, mesmo com o programa fechado."""
    from googleapiclient.http import MediaFileUpload
    yt = youtube_service(cfg)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"snippet": {"title": f"{title[:90]} #Shorts", "description": f"{caption}\n\n#Shorts",
                        "categoryId": "20"}, "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _st, resp = req.next_chunk()
    return resp["id"]


def upload_tiktok(path, caption, privacy, cfg, log):
    import requests
    token = cfg["accounts"].get("tiktok_token") or os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Falta o token do TikTok (aba Contas).")
    size = os.path.getsize(path)
    MB = 1024 * 1024
    chunk, total = (size, 1) if size <= 64 * MB else (32 * MB, size // (32 * MB))
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    r = requests.post("https://open.tiktokapis.com/v2/post/publish/video/init/", headers=hdr,
                      timeout=60, json={
                          "post_info": {"title": caption[:2200], "privacy_level": privacy,
                                        "disable_duet": False, "disable_comment": False,
                                        "disable_stitch": False},
                          "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                          "chunk_size": chunk, "total_chunk_count": total}})
    j = r.json()
    err = j.get("error", {})
    if r.status_code >= 400 or err.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok recusou: {err.get('code')} - {err.get('message')}")
    up_url, pid = j["data"]["upload_url"], j["data"]["publish_id"]
    with open(path, "rb") as f:
        for i in range(total):
            a = i * chunk
            b = size - 1 if i == total - 1 else a + chunk - 1
            f.seek(a)
            data = f.read(b - a + 1)
            pr = requests.put(up_url, data=data, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {a}-{b}/{size}"})
            if pr.status_code not in (200, 201, 206):
                raise RuntimeError(f"Falha no envio ao TikTok (HTTP {pr.status_code}).")
    for _ in range(12):  # confere o resultado por ~1 min
        time.sleep(5)
        sr = requests.post("https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                           headers=hdr, json={"publish_id": pid}, timeout=30).json()
        stt = sr.get("data", {}).get("status", "")
        if stt == "FAILED":
            raise RuntimeError(f"TikTok falhou: {sr['data'].get('fail_reason')}")
        if stt in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return pid, stt
    return pid, "ENVIADO"


def upload_instagram(path, caption, cfg, log):
    """Reels via upload resumível (arquivo local, sem precisar de URL pública)."""
    import requests
    a = cfg["accounts"]
    uid, token, ver = a.get("ig_user_id"), a.get("ig_token"), a.get("ig_version") or "v23.0"
    if not (uid and token):
        raise RuntimeError("Faltam o ID e o token do Instagram (aba Contas).")
    size = os.path.getsize(path)
    r = requests.post(f"https://graph.facebook.com/{ver}/{uid}/media", timeout=60,
                      headers={"Authorization": f"Bearer {token}"},
                      data={"media_type": "REELS", "upload_type": "resumable",
                            "caption": caption[:2200], "access_token": token})
    j = r.json()
    if "id" not in j:
        raise RuntimeError(f"Instagram recusou: {j.get('error', j)}")
    cid = j["id"]
    with open(path, "rb") as f:
        ur = requests.post(f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}", data=f,
                           timeout=900, headers={"Authorization": f"OAuth {token}",
                                                 "offset": "0", "file_size": str(size)})
    if ur.status_code >= 400:
        raise RuntimeError(f"Falha no envio ao Instagram: {ur.text[:300]}")
    for _ in range(20):  # espera o processamento (até ~10 min)
        time.sleep(30)
        sj = requests.get(f"https://graph.facebook.com/{ver}/{cid}", timeout=30,
                          params={"fields": "status_code", "access_token": token}).json()
        code = sj.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram não conseguiu processar o vídeo ({code}).")
    else:
        raise RuntimeError("Instagram demorou demais para processar o vídeo.")
    pj = requests.post(f"https://graph.facebook.com/{ver}/{uid}/media_publish", timeout=60,
                       data={"creation_id": cid, "access_token": token}).json()
    if "id" not in pj:
        raise RuntimeError(f"Instagram não publicou: {pj.get('error', pj)}")
    return pj["id"]


# --------------------------------------------------------------------------- fila e agendamento
_LOCK = threading.RLock()
WHEN_FMT = "%Y-%m-%dT%H:%M"
LOST_AFTER = timedelta(minutes=30)


def load_queue():
    with _LOCK:
        if QUEUE_FILE.exists():
            try:
                return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []


def save_queue(q):
    with _LOCK:
        tmp = QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(QUEUE_FILE)


def add_queue_item(file, title, caption, platforms, when, privacy):
    """when: datetime local ou None (= o quanto antes)."""
    item = {"id": uuid.uuid4().hex[:8], "file": str(file), "title": title, "caption": caption,
            "platforms": list(platforms), "when": when.strftime(WHEN_FMT) if when else None,
            "privacy": privacy, "status": {p: {"state": "pendente", "detail": ""} for p in platforms}}
    with _LOCK:
        q = load_queue()
        q.append(item)
        save_queue(q)
    return item


def remove_queue_items(ids):
    with _LOCK:
        save_queue([i for i in load_queue() if i["id"] not in set(ids)])


def spread_times(first, step_hours, n):
    return [first + timedelta(hours=step_hours * i) for i in range(n)]


def process_queue(cfg, log=print, force_ids=None):
    """Um ciclo da fila. YouTube: sobe e agenda no próprio YouTube (publishAt).
    TikTok/Instagram: não têm agendamento na API, então o programa posta na hora marcada
    (precisa estar aberto)."""
    force_ids = set(force_ids or [])
    changed = False
    with _LOCK:
        q = load_queue()
        now = datetime.now()
        for item in q:
            force = item["id"] in force_ids
            when = datetime.strptime(item["when"], WHEN_FMT) if item["when"] else None
            for plat in item["platforms"]:
                st = item["status"].setdefault(plat, {"state": "pendente", "detail": ""})
                if st["state"] == "pendente" or (force and st["state"] in ("erro", "perdido")):
                    pass
                else:
                    continue
                if not Path(item["file"]).exists():
                    st.update(state="erro", detail="arquivo do corte não existe mais")
                    changed = True
                    continue
                future = when is not None and when > now
                if plat != "youtube" and future and not force:
                    continue  # ainda não chegou a hora
                if when and not future and now - when > LOST_AFTER and not force:
                    st.update(state="perdido",
                              detail="o horário passou com o programa fechado; use 'Postar agora'")
                    changed = True
                    continue
                try:
                    if plat == "youtube":
                        sched = when if (future and when > now + timedelta(minutes=2) and not force) else None
                        log(f"YouTube: enviando '{item['title']}'...")
                        vid = upload_youtube(item["file"], item["title"], item["caption"],
                                             item["privacy"].get("youtube", "private"), sched, cfg)
                        if sched:
                            st.update(state="agendado_youtube",
                                      detail=f"https://youtu.be/{vid} - o YouTube publica em "
                                             f"{sched.strftime('%d/%m %H:%M')}")
                        else:
                            st.update(state="postado", detail=f"https://youtu.be/{vid}")
                    elif plat == "tiktok":
                        log(f"TikTok: enviando '{item['title']}'...")
                        pid, stt = upload_tiktok(item["file"], item["caption"],
                                                 item["privacy"].get("tiktok", "SELF_ONLY"), cfg, log)
                        st.update(state="postado", detail=f"publish_id={pid} ({stt})")
                    elif plat == "instagram":
                        log(f"Instagram: enviando '{item['title']}'...")
                        mid = upload_instagram(item["file"], item["caption"], cfg, log)
                        st.update(state="postado", detail=f"media id {mid}")
                    log(f"{plat}: {st['state']} - {item['title']}")
                except Exception as ex:
                    st.update(state="erro", detail=str(ex)[:400])
                    log(f"{plat}: ERRO - {ex}")
                changed = True
                save_queue(q)  # grava a cada passo para nunca repetir uma postagem
        if changed:
            save_queue(q)
    return changed


# --------------------------------------------------------------------------- modo automático
def auto_process_video(video, cfg, log, cb, stop=None):
    s = full_settings(cfg["settings"])
    analysis = analyze(video, s["lang"], s["model"], cb)
    clips = find_clips(analysis, int(s["n"]), float(s["len"]))
    out_dir = out_dir_for(s["out"], video)
    first = datetime.now() + timedelta(minutes=float(s["auto_delay"]))
    times = spread_times(first, float(s["auto_every"]), len(clips))
    for i, (c, when) in enumerate(zip(clips, times), 1):
        if stop and stop.is_set():
            return
        out = out_dir / f"corte_{c['id']:02d}_{int(c['start'])}s.mp4"
        log(f"[{i}/{len(clips)}] gerando {out.name}")
        render_clip(video, c, analysis, s, out)
        if s["platforms"]:
            cap = build_caption(c["title"], s["hashtags"], s["description"])
            add_queue_item(out, c["title"], cap, s["platforms"], when,
                           {"youtube": s["yt_privacy"], "tiktok": s["tt_privacy"]})
    process_queue(cfg, log)


def watch_folder(folder, cfg, log, cb, stop):
    folder = Path(folder)
    done_file = folder / ".minecortes_done.json"
    done = set(json.loads(done_file.read_text())) if done_file.exists() else set()
    log(f"Modo automático: vigiando {folder}")
    while not stop.is_set():
        for f in sorted(folder.iterdir()):
            if stop.is_set():
                return
            if f.suffix.lower() not in VIDEO_EXT or f.name in done:
                continue
            size = f.stat().st_size
            if stop.wait(5) or f.stat().st_size != size:
                continue  # ainda gravando
            log(f"Nova live detectada: {f.name}")
            try:
                auto_process_video(f, cfg, log, cb, stop)
                done.add(f.name)
                done_file.write_text(json.dumps(sorted(done)))
            except Exception as ex:
                log(f"Erro em {f.name}: {ex}")
                done.add(f.name)
        stop.wait(60)


# --------------------------------------------------------------------------- linha de comando
def main():
    p = argparse.ArgumentParser(description="MineCortes")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("clips", "compile", "watch"):
        sp = sub.add_parser(name)
        sp.add_argument("target")
        sp.add_argument("--n", type=int)
        sp.add_argument("--len", type=float)
        sp.add_argument("--minutes", type=float)
    sub.add_parser("fila", help="processa a fila de postagens sem abrir a janela")
    a = p.parse_args()

    cfg = load_config()
    s = cfg["settings"]
    log = print
    cb = lambda f, m: print(f"  {int(f * 100):3d}% {m}")
    if a.cmd == "fila":
        log("Processando a fila a cada 20s (Ctrl+C para sair)...")
        while True:
            process_queue(cfg, log)
            time.sleep(20)
    if a.n:
        s["n"] = a.n
    if a.len:
        s["len"] = a.len
    if a.cmd == "watch":
        watch_folder(a.target, cfg, log, cb, threading.Event())
        return
    analysis = analyze(a.target, s["lang"], s["model"], cb)
    out_dir = out_dir_for(s["out"], a.target)
    if a.cmd == "clips":
        for c in find_clips(analysis, int(s["n"]), float(s["len"])):
            out = out_dir / f"corte_{c['id']:02d}_{int(c['start'])}s.mp4"
            log(f"{c['title']}  ->  {out}")
            render_clip(a.target, c, analysis, s, out)
    else:
        compile_long(a.target, analysis, a.minutes or float(s["minutes"]),
                     float(s["seg_len"]), out_dir, cb)


if __name__ == "__main__":
    main()

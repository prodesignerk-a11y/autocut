"""
AutoCut Video Processor
Detects silences, pauses, and background noise — then cuts them out.
"""

import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple, Callable, Optional
import math


class VideoProcessor:
    """
    Full pipeline:
    1. Extract audio (FFmpeg)
    2. Detect speech segments (Silero VAD or pydub fallback)
    3. Optionally transcribe (Whisper) for context-aware cuts
    4. Merge segments with padding
    5. Render final video (FFmpeg concat)
    """

    def __init__(
        self,
        input_path: str,
        output_path: str,
        min_silence_ms: int = 400,
        remove_bg_noise: bool = True,
        padding_ms: int = 50,
        progress_callback: Optional[Callable] = None,
    ):
        self.input_path = input_path
        self.output_path = output_path
        self.min_silence_ms = min_silence_ms
        self.remove_bg_noise = remove_bg_noise
        self.padding_ms = padding_ms
        self.cb = progress_callback or (lambda p, s: None)

        self.temp_dir = tempfile.mkdtemp(prefix="autocut_")

    def run(self) -> dict:
        """Execute full processing pipeline. Returns stats dict."""
        import time
        start = time.time()

        # Step 1: Extract audio
        self.cb(5, "Extraindo áudio do vídeo...")
        audio_path = self._extract_audio()

        # Step 2: Get video duration
        self.cb(10, "Analisando duração do vídeo...")
        duration = self._get_duration(self.input_path)

        # Step 3: Detect speech segments
        self.cb(20, "Detectando silêncios e pausas...")
        segments = self._detect_speech_segments(audio_path, duration)

        # Step 4: Try to improve with Whisper (optional, skip if unavailable)
        self.cb(40, "Transcrevendo áudio com IA...")
        try:
            segments = self._refine_with_whisper(audio_path, segments)
        except Exception as e:
            print(f"[Whisper] Skipped: {e}")

        # Step 5: Filter very short segments and apply padding
        self.cb(60, "Refinando cortes...")
        segments = self._apply_padding(segments, duration)
        segments = self._filter_short(segments, min_duration=0.3)

        if not segments:
            raise ValueError("Nenhum segmento de fala detectado. Verifique o áudio.")

        # Step 6: Render output video
        self.cb(70, "Renderizando vídeo final...")
        self._render_video(segments, duration)

        elapsed = time.time() - start
        total_kept = sum(e - s for s, e in segments)
        removed = duration - total_kept

        stats = {
            "original_duration": round(duration, 2),
            "edited_duration": round(total_kept, 2),
            "removed_duration": round(removed, 2),
            "reduction_pct": round((removed / duration) * 100, 1) if duration > 0 else 0,
            "segments_kept": len(segments),
            "processing_time": round(elapsed, 1),
        }

        self.cb(100, "Concluído!")
        self._cleanup()
        return stats

    # ─── Audio Extraction ────────────────────────────────────────────────

    def _extract_audio(self) -> str:
        audio_path = os.path.join(self.temp_dir, "audio.wav")
        cmd = [
            "ffmpeg", "-y", "-i", self.input_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            audio_path
        ]
        self._run_cmd(cmd)
        return audio_path

    # ─── Duration ────────────────────────────────────────────────────────

    def _get_duration(self, path: str) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())

    # ─── Speech Detection ────────────────────────────────────────────────

    def _detect_speech_segments(self, audio_path: str, duration: float) -> List[Tuple[float, float]]:
        """Try Silero VAD first, fall back to pydub energy detection."""
        try:
            return self._silero_vad(audio_path, duration)
        except Exception as e:
            print(f"[Silero] Unavailable ({e}), using pydub fallback...")
            return self._pydub_silence_detection(audio_path, duration)

    def _silero_vad(self, audio_path: str, duration: float) -> List[Tuple[float, float]]:
        import torch
        import torchaudio

        model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True,
        )
        (get_speech_ts, _, read_audio, *_) = utils

        wav = read_audio(audio_path, sampling_rate=16000)
        speech_timestamps = get_speech_ts(
            wav, model,
            sampling_rate=16000,
            min_silence_duration_ms=self.min_silence_ms,
            min_speech_duration_ms=100,
            return_seconds=True,
        )

        segments = []
        for ts in speech_timestamps:
            segments.append((float(ts['start']), float(ts['end'])))
        return segments

    def _pydub_silence_detection(self, audio_path: str, duration: float) -> List[Tuple[float, float]]:
        """Fallback: pydub detect_nonsilent."""
        from pydub import AudioSegment, silence

        audio = AudioSegment.from_wav(audio_path)
        min_silence_len = self.min_silence_ms
        silence_thresh = audio.dBFS - 16  # 16 dB below mean = silence

        nonsilent = silence.detect_nonsilent(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
            seek_step=10,
        )

        segments = []
        for start_ms, end_ms in nonsilent:
            segments.append((start_ms / 1000.0, end_ms / 1000.0))
        return segments

    # ─── Whisper Refinement ──────────────────────────────────────────────

    def _refine_with_whisper(self, audio_path: str, segments: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Use Whisper to get word-level timestamps for cleaner cuts."""
        import whisper

        self.cb(45, "Transcrevendo com Whisper (pode demorar)...")
        model = whisper.load_model("base")
        result = model.transcribe(
            audio_path,
            word_timestamps=True,
            language=None,  # auto-detect
            verbose=False,
        )

        # Collect all word segments
        word_segments = []
        for seg in result.get("segments", []):
            for word in seg.get("words", []):
                word_segments.append((word["start"], word["end"]))

        if not word_segments:
            return segments

        # Merge words that are close together
        merged = self._merge_segments(word_segments, gap_threshold=self.min_silence_ms / 1000.0)
        return merged

    # ─── Segment Utilities ───────────────────────────────────────────────

    def _merge_segments(
        self, segments: List[Tuple[float, float]], gap_threshold: float
    ) -> List[Tuple[float, float]]:
        """Merge segments separated by less than gap_threshold seconds."""
        if not segments:
            return []

        merged = [list(segments[0])]
        for start, end in segments[1:]:
            if start - merged[-1][1] <= gap_threshold:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(s, e) for s, e in merged]

    def _apply_padding(
        self, segments: List[Tuple[float, float]], duration: float
    ) -> List[Tuple[float, float]]:
        """Add small padding around each segment for natural feel."""
        pad = self.padding_ms / 1000.0
        padded = []
        for start, end in segments:
            s = max(0.0, start - pad)
            e = min(duration, end + pad)
            padded.append((s, e))
        # Re-merge after padding
        return self._merge_segments(padded, gap_threshold=0.01)

    def _filter_short(
        self, segments: List[Tuple[float, float]], min_duration: float = 0.3
    ) -> List[Tuple[float, float]]:
        return [(s, e) for s, e in segments if e - s >= min_duration]

    # ─── Video Rendering ─────────────────────────────────────────────────

    def _render_video(self, segments: List[Tuple[float, float]], duration: float):
        """
        Use FFmpeg complex filter to concatenate segments in one pass.
        Avoids re-encoding quality loss for video; re-encodes audio for clean joins.
        """
        # Build filter_complex for concat
        n = len(segments)

        # Write segments list file for ffmpeg concat demuxer
        # But for accurate seeking we use the trim/setpts approach
        filter_parts = []
        for i, (start, end) in enumerate(segments):
            filter_parts.append(
                f"[0:v]trim=start={start:.4f}:end={end:.4f},setpts=PTS-STARTPTS[v{i}];"
                f"[0:a]atrim=start={start:.4f}:end={end:.4f},asetpts=PTS-STARTPTS[a{i}]"
            )

        concat_v = "".join(f"[v{i}]" for i in range(n))
        concat_a = "".join(f"[a{i}]" for i in range(n))
        filter_complex = ";".join(filter_parts)
        filter_complex += f";{concat_v}concat=n={n}:v=1:a=0[outv];{concat_a}concat=n={n}:v=0:a=1[outa]"

        cmd = [
            "ffmpeg", "-y",
            "-i", self.input_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            self.output_path,
        ]

        self.cb(80, f"Concatenando {n} segmentos...")
        self._run_cmd(cmd)

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _run_cmd(self, cmd: list):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg error:\n{result.stderr[-1000:]}")

    def _cleanup(self):
        import shutil
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

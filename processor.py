import os
import subprocess
import tempfile
import shutil
from typing import List, Tuple, Callable, Optional
import time


class VideoProcessor:
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
        start = time.time()

        self.cb(3, "Reduzindo resolução do vídeo...")
        self.input_path = self._downscale_video()

        self.cb(10, "Extraindo áudio do vídeo...")
        audio_path = self._extract_audio()

        self.cb(15, "Analisando duração do vídeo...")
        duration = self._get_duration(self.input_path)

        self.cb(20, "Transcrevendo com Whisper (pode demorar)...")
        segments = self._whisper_segments(audio_path, duration)

        self.cb(60, "Refinando cortes...")
        segments = self._apply_padding(segments, duration)
        segments = self._filter_short(segments, min_duration=0.2)

        if not segments:
            raise ValueError("Nenhum segmento de fala detectado.")

        self.cb(70, "Cortando segmentos...")
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

    def _downscale_video(self) -> str:
        scaled_path = os.path.join(self.temp_dir, "scaled.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", self.input_path,
            "-vf", "scale=720:-2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            scaled_path
        ]
        self._run_cmd(cmd)
        return scaled_path

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

    def _get_duration(self, path: str) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())

    def _whisper_segments(self, audio_path: str, duration: float) -> List[Tuple[float, float]]:
        try:
            import whisper
            model = whisper.load_model("tiny")
            result = model.transcribe(
                audio_path,
                word_timestamps=True,
                verbose=False,
            )

            # Collect word-level segments
            word_segs = []
            for seg in result.get("segments", []):
                for word in seg.get("words", []):
                    word_segs.append((float(word["start"]), float(word["end"])))

            if not word_segs:
                # Fall back to segment-level if no words
                for seg in result.get("segments", []):
                    word_segs.append((float(seg["start"]), float(seg["end"])))

            if not word_segs:
                raise ValueError("Whisper não detectou fala")

            # Merge words close together
            return self._merge_segments(word_segs, gap_threshold=self.min_silence_ms / 1000.0)

        except Exception as e:
            print(f"[Whisper] Error: {e}, falling back to pydub")
            return self._pydub_silence_detection(audio_path, duration)

    def _pydub_silence_detection(self, audio_path: str, duration: float) -> List[Tuple[float, float]]:
        from pydub import AudioSegment, silence

        audio = AudioSegment.from_wav(audio_path)
        silence_thresh = audio.dBFS - 16

        nonsilent = silence.detect_nonsilent(
            audio,
            min_silence_len=self.min_silence_ms,
            silence_thresh=silence_thresh,
            seek_step=10,
        )

        segments = []
        for start_ms, end_ms in nonsilent:
            segments.append((start_ms / 1000.0, end_ms / 1000.0))
        return segments

    def _merge_segments(self, segments, gap_threshold):
        if not segments:
            return []
        merged = [list(segments[0])]
        for start, end in segments[1:]:
            if start - merged[-1][1] <= gap_threshold:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(s, e) for s, e in merged]

    def _apply_padding(self, segments, duration):
        pad = self.padding_ms / 1000.0
        padded = []
        for start, end in segments:
            s = max(0.0, start - pad)
            e = min(duration, end + pad)
            padded.append((s, e))
        return self._merge_segments(padded, gap_threshold=0.01)

    def _filter_short(self, segments, min_duration=0.2):
        return [(s, e) for s, e in segments if e - s >= min_duration]

    def _render_video(self, segments, duration):
        clip_paths = []
        total = len(segments)

        for i, (start, end) in enumerate(segments):
            clip_path = os.path.join(self.temp_dir, f"clip_{i:04d}.mp4")
            pct = 70 + int((i / total) * 20)
            self.cb(pct, f"Cortando segmento {i+1}/{total}...")
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-to", f"{end:.3f}",
                "-i", self.input_path,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-c:a", "aac",
                "-b:a", "128k",
                clip_path
            ]
            self._run_cmd(cmd)
            clip_paths.append(clip_path)

        list_file = os.path.join(self.temp_dir, "list.txt")
        with open(list_file, "w") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        self.cb(92, "Juntando segmentos...")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            self.output_path
        ]
        self._run_cmd(cmd)

    def _run_cmd(self, cmd: list):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg error:\n{result.stderr[-1500:]}")

    def _cleanup(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

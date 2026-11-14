"""中文 TTS：edge-tts（需联网，非官方在线接口）→ MP3；失败回退 Windows SAPI。

禁止 macOS say / Unix fcntl。生成后用 ffprobe 校验时长与可播放性；
时长异常或损坏 → 抛 TTSUnavailable，不发布。
"""
from __future__ import annotations

import os
import shutil
import subprocess


class TTSUnavailable(Exception):
    pass


def _ffprobe_duration(path: str) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            timeout=60, stderr=subprocess.STDOUT)
        return float(out.decode().strip())
    except Exception:
        return None


def _synthesize_edge(text: str, out_path: str, voice: str, rate: str) -> None:
    try:
        import asyncio
        import edge_tts
    except ImportError as e:
        raise TTSUnavailable(f"edge-tts 未安装：{e}")

    async def _run():
        c = edge_tts.Communicate(text, voice, rate=rate)
        await c.save(out_path)

    asyncio.run(_run())
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise TTSUnavailable("edge-tts 未产出音频文件（可能网络受限）。")


def _synthesize_sapi(text: str, out_path: str) -> None:
    if os.name != "nt":
        raise TTSUnavailable("SAPI 回退仅 Windows 可用。")
    wav = out_path.rsplit(".", 1)[0] + ".wav"
    ps = (
        "$ErrorActionPreference='Stop';\n"
        "Add-Type -AssemblyName System.Speech;\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;\n"
        "$zh = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' };\n"
        "if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) };\n"
        "$s.SetOutputToWaveFile('" + wav.replace("'", "''") + "');\n"
        "$s.Speak('" + text.replace("'", "''") + "')"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(wav):
        raise TTSUnavailable("SAPI 未产出 wav。")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TTSUnavailable("SAPI 转 mp3 需要 ffmpeg。")
    subprocess.run([ffmpeg, "-y", "-i", wav, "-codec:a", "libmp3lame",
                    "-qscale:a", "3", out_path], check=True, timeout=300,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(wav):
        os.unlink(wav)


def synthesize(text: str, out_path: str, voice: str = "zh-CN-YunxiNeural",
               rate: str = "+0%", min_sec: float = 10.0, max_sec: float = 1500.0) -> dict:
    """生成并校验音频，返回 {path, duration_sec, size_bytes, engine}。"""
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    engine = None
    last_err = None
    for eng, fn in (("edge-tts", lambda p: _synthesize_edge(text, p, voice, rate)),
                    ("sapi", lambda p: _synthesize_sapi(text, p))):
        try:
            fn(out_path)
            engine = eng
            break
        except Exception as e:
            last_err = f"{eng}: {e}"
    if engine is None:
        raise TTSUnavailable(f"所有 TTS 引擎都失败。最后错误：{last_err}")
    dur = _ffprobe_duration(out_path)
    size = os.path.getsize(out_path)
    if dur is None:
        raise TTSUnavailable("音频已生成但 ffprobe 无法读取时长（可能损坏），拒绝发布。")
    if not (min_sec <= dur <= max_sec):
        raise TTSUnavailable(f"音频时长异常：{dur:.1f}s（应 {min_sec}-{max_sec}s），拒绝发布。")
    return {"path": out_path, "duration_sec": round(dur, 2), "size_bytes": size, "engine": engine}

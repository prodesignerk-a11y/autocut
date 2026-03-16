#!/usr/bin/env python3
"""
autocut_cli.py — Command-line interface for AutoCut
Usage:
    python autocut_cli.py input.mp4 --mode medium
    python autocut_cli.py input.mp4 -o output.mp4 --mode aggressive --padding 80
"""

import argparse
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))
from processor import VideoProcessor


def fmt_dur(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}m {s:02d}s"


def progress(pct: int, step: str):
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {pct:3d}%  {step:<45}", end="", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="AutoCut — Remove silences and pauses from video automatically",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  aggressive  Remove pauses > 200ms  (fast-paced reels)
  medium      Remove pauses > 400ms  (natural rhythm)   [default]
  light       Remove pauses > 700ms  (preserve breathing)

Examples:
  python autocut_cli.py podcast.mp4
  python autocut_cli.py interview.mp4 --mode aggressive -o interview_cut.mp4
  python autocut_cli.py aula.mov --mode light --padding 100 --no-bg-filter
        """
    )

    parser.add_argument("input", help="Input video file (MP4, MOV, MKV)")
    parser.add_argument("-o", "--output", help="Output path (default: <input>_autocut.mp4)")
    parser.add_argument(
        "--mode", choices=["aggressive", "medium", "light"], default="medium",
        help="Cut aggressiveness (default: medium)"
    )
    parser.add_argument(
        "--silence-ms", type=int, default=None,
        help="Custom silence threshold in milliseconds (overrides --mode)"
    )
    parser.add_argument(
        "--padding", type=int, default=50,
        help="Padding in ms added before/after each speech segment (default: 50)"
    )
    parser.add_argument(
        "--no-bg-filter", action="store_true",
        help="Disable background noise filtering"
    )

    args = parser.parse_args()

    # Validate input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"✗ File not found: {input_path}")
        sys.exit(1)

    allowed = {".mp4", ".mov", ".mkv", ".webm"}
    if input_path.suffix.lower() not in allowed:
        print(f"✗ Unsupported format: {input_path.suffix}. Use: {', '.join(allowed)}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = str(input_path.parent / f"{input_path.stem}_autocut.mp4")

    # Determine silence threshold
    if args.silence_ms:
        min_silence_ms = args.silence_ms
    else:
        thresholds = {"aggressive": 200, "medium": 400, "light": 700}
        min_silence_ms = thresholds[args.mode]

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║         AutoCut  ✂️  Video Editor     ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print(f"  Input   : {input_path.name}")
    print(f"  Output  : {Path(output_path).name}")
    print(f"  Mode    : {args.mode.upper()} (silence > {min_silence_ms}ms)")
    print(f"  Padding : {args.padding}ms")
    print(f"  BG Filter: {'No' if args.no_bg_filter else 'Yes'}")
    print()
    print("  Processing:")

    start = time.time()

    try:
        processor = VideoProcessor(
            input_path=str(input_path),
            output_path=output_path,
            min_silence_ms=min_silence_ms,
            remove_bg_noise=not args.no_bg_filter,
            padding_ms=args.padding,
            progress_callback=progress,
        )
        stats = processor.run()

    except KeyboardInterrupt:
        print("\n\n  ✗ Cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n  ✗ Error: {e}")
        sys.exit(1)

    elapsed = time.time() - start
    print("\n")
    print("  ─────────────────────────────────────────")
    print(f"  ✓ Done in {elapsed:.1f}s")
    print()
    print(f"  Original duration  : {fmt_dur(stats['original_duration'])}")
    print(f"  Edited duration    : {fmt_dur(stats['edited_duration'])}")
    print(f"  Removed            : {fmt_dur(stats['removed_duration'])} ({stats['reduction_pct']}%)")
    print(f"  Segments kept      : {stats['segments_kept']}")
    print()
    print(f"  📁 Output saved to: {output_path}")
    print()


if __name__ == "__main__":
    main()

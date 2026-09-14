#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
=======================================
YouTube Audio & Video Extractor (v1.20)
=======================================

Info:
  Author: Igor Brzezek (igor.brzezek@gmail.com)
  GitHub: https://github.com/IgorBrzezek/yt-audio-download
  Version: 1.20
  Date: 08.02.2026

LICENSE:
  MIT License

CHANGELOG:
Version 1.20 (2026-02-08)

New Features

- Extended Audio Formats:
    - Added -mp3slow option for high-quality MP3 (VBR 196kbps).
    - Added -mono option for space-saving mono MP3 (VBR 96kbps).
- Extended Video Formats:
    - Introduced -mp41080 for 1080p video downloads.
    - Introduced -mp4480 for 480p video downloads.
- Enhanced Output Modes:
    - Added --compact mode for a compact view when using --list.
    - Implemented --summarize to display a comprehensive summary after all batch downloads are completed.
    - Added --title option to show the title of each downloaded item when using --list.
- Improved Concurrency & Reliability:
    - Added -t/--threads option to specify the number of threads for FFmpeg compression.
    - Implemented -ret/--retries option to allow automatic retries for failed downloads.
- Interactive Overwrite Prompt: Introduced an interactive prompt ([y/N/a/q]) to handle existing files, allowing users to choose to overwrite, skip, overwrite all, or quit.
- Global State Management: Refactored global variables to better manage download and conversion progress, including:
    - IS_COMPACT_MODE: New flag to control compact output.
    - VIDEO_PROGRESS, AUDIO_PROGRESS: Separate variables for tracking video and audio download/conversion progress, improving clarity for merged formats.
    - OVERWRITE_ALL: New flag to apply overwrite decision across all files in batch mode.
    - SUMMARY_DATA: Collects detailed information (size, time, speeds) for post-download summaries.
    - current_file_download_speed_bps, current_file_compress_speed_bps: Track real-time speeds per file for summary statistics.
- Progress Reporting:
    - show_minimal_status now intelligently adapts its output for the new --compact mode.
    - download_progress_handler and conversion_progress_handler were updated to handle video/audio stream distinctions and update new global progress variables more accurately.
- Speed and Size Calculation:
    - New utility functions speed_to_bytes_per_second and size_to_bytes were added for more robust and accurate calculation of download/compression speeds and file sizes across various formats (yt-dlp and FFmpeg outputs).
- Argument Parsing:
    - Help message descriptions for -h and --help were swapped for better clarity.
    - Grouped format options into 'Format (Audio)' and 'Format (Video)' for better organization.
- FFmpeg Audio Quality Control: Switched to using -q:a (VBR quality) for MP3 encoding instead of -b:a (CBR bitrate), allowing for more flexible and higher quality audio outputs.
- Dynamic Video Format Selection: Implemented dynamic selection of yt-dlp video formats based on -mp4fast, -mp41080, and -mp4480 options.
- Error Reporting: Improved error reporting context, especially with the introduction of retries.

Bug Fixes

- Fixed an issue where the --skip and --overwrite options might not behave as expected in all scenarios by integrating the new interactive overwrite logic and making their behavior more explicit ("without asking").

Refactoring

- Code Structure: Introduced DownloadState class to encapsulate stream-specific download state, improving modularity and reducing global state reliance for complex download scenarios.
- run_command Function: Modified to accept additional keyword arguments for custom handlers, making it more flexible.

Old versions:

v1.18: Added --showname X and fixed --skip logic to check for existing final files.
v1.18p6: Added ytextractor.err reporting for failed/incomplete downloads and enhanced summary.
v1.18p5: Fully restored original categorized --help layout and all missing options.
v1.18p4: Fixed -h to show one-line options summary; restored categorized --help.
"""

import argparse
import subprocess
import sys
import os
import logging
import re
import time
import json
import signal
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

IS_BATCH_MODE = False
IS_MINIMAL_MODE = False
IS_COMPACT_MODE = False
CURRENT_SUBPROCESS = None
VIDEO_PROGRESS = ""
AUDIO_PROGRESS = ""
current_files_to_cleanup = []
failed_urls = []
OVERWRITE_ALL = False
SUMMARY_DATA = []
current_file_download_speed_bps = 0.0
current_file_compress_speed_bps = 0.0
_COMPACT_FILE_PREFIX = "" 
_COMPACT_LAST_DOWNLOAD_MSG = "" 


try:
    from colorama import init, Fore, Style
except ImportError:
    def init(): pass
    class Colors:
        HEADER, OKBLUE, OKCYAN, OKGREEN, WARNING, FAIL, ENDC, BOLD, UNDERLINE = '', '', '', '', '', '', '', '', ''
        C_DIM, C_YELLOW, C_MAGENTA, C_WHITE = '', '', '', ''
else:
    class Colors:
        HEADER = Fore.MAGENTA; OKBLUE = Fore.BLUE; OKCYAN = Fore.CYAN; OKGREEN = Fore.GREEN
        WARNING = Fore.YELLOW; FAIL = Fore.RED; ENDC = Style.RESET_ALL; BOLD = Style.BRIGHT; UNDERLINE = '\033[4m'
        C_DIM = '\033[2m'; C_YELLOW = Fore.YELLOW; C_MAGENTA = Fore.MAGENTA; C_WHITE = Fore.WHITE

AUTHOR = 'Igor Brzezek'; VERSION = "1.20"; DATE = '08.02.2026'
USER_AGENT_HEADER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, browser: chrome) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

def _find_ytdlp():
    import shutil
    if shutil.which('yt-dlp'):
        return ['yt-dlp']
    try:
        subprocess.check_output([sys.executable, '-m', 'yt_dlp', '--version'], stderr=subprocess.DEVNULL)
        return [sys.executable, '-m', 'yt_dlp']
    except Exception:
        pass
    return ['yt-dlp']

_YTDLP = _find_ytdlp()

class DownloadState:
    def __init__(self):
        self.stream_type = "video"

def format_bytes(size_bytes_str):
    try:
        size = float(size_bytes_str)
        if size < 1024: return f"{size:.0f}B"
        elif size < 1024**2: return f"{size/1024:.1f}KiB"
        else: return f"{size/(1024**2):.1f}MiB"
    except: return "0B"

def format_ff_time(time_str):
    if '.' in time_str: return time_str.split('.')[0]
    return time_str

def speed_to_bytes_per_second(speed_str):
    if not speed_str: return 0.0
    match = re.match(r'([\d.]+)\s*([KMGT]?I?B)?/S', speed_str.upper()) # Added \s* for optional space and I?B for KiB
    if match:
        value, unit = match.groups()
        value = float(value)
        if unit and unit.startswith('K'): return value * 1024
        if unit and unit.startswith('M'): return value * 1024**2
        if unit and unit.startswith('G'): return value * 1024**3
        if unit and unit.startswith('T'): return value * 1024**4
        return value # Handles plain numbers or 'B' (no prefix)
    return 0.0

def size_to_bytes(size_str):
    if not size_str: return 0.0
    # yt-dlp might report '12.3MiB' or '12.3M'
    # ffmpeg might report 'size=   1234567kB' or 'total_size=1234567'
    # Let's handle common units and raw bytes
    
    # Try to extract number and unit from "X.YM", "X.YMiB", "X.YMB" format
    match = re.match(r'([\d.]+)([KMGT]?)(?:i?B)?', size_str.upper())
    if match:
        value, unit = match.groups()
        value = float(value)
        if unit == 'K': return value * 1024
        if unit == 'M': return value * 1024**2
        if unit == 'G': return value * 1024**3
        if unit == 'T': return value * 1024**4
        return value
    
    # Handle raw byte numbers (e.g., from ffmpeg total_size)
    try:
        return float(size_str)
    except ValueError:
        return 0.0

def cprint(text, color="", use_color_flag=False, force_print=False, **kwargs):
    if IS_BATCH_MODE and not force_print: return
    if IS_MINIMAL_MODE and not force_print: return
    if use_color_flag: print(f"{color}{text}{Colors.ENDC}", **kwargs)
    else: print(text, **kwargs)

def show_minimal_status(i, total, status_text, color_flag, color_code=None, title="", title_limit=None):
    if IS_COMPACT_MODE:
        total_digits = len(str(total))
        file_part = f"{str(i).rjust(total_digits)}/{total}"
        if color_flag:
            prefix = f"{Colors.BOLD}{Colors.OKBLUE}{file_part}{Colors.ENDC}: "
            content = f"{color_code}{status_text}{Colors.ENDC}" if color_code else status_text
        else:
            prefix = f"{file_part}: "
            content = status_text
        sys.stdout.write(f"\r\033[K{prefix}{content}")
        sys.stdout.flush()
        return prefix

    total_digits = len(str(total))
    file_part = f"File: {str(i).rjust(total_digits)}/{total}"

    if title_limit and title:
        short_title = title[:title_limit]
        file_part += f" ({short_title})"

    if color_flag:
        prefix = f"{Colors.BOLD}{Colors.OKBLUE}{file_part}{Colors.ENDC} | "
        content = f"{color_code}{status_text}{Colors.ENDC}" if color_code else status_text
    else:
        prefix = f"{file_part} | "
        content = status_text
    sys.stdout.write(f"\r\033[K{prefix}{content}")
    sys.stdout.flush()
    return prefix

def terminate_process_tree():
    global CURRENT_SUBPROCESS
    if CURRENT_SUBPROCESS:
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(CURRENT_SUBPROCESS.pid), signal.SIGTERM)
            else:
                CURRENT_SUBPROCESS.terminate()
        except: pass
    cleanup_incomplete_files()

def signal_handler(sig, frame):
    sys.stdout.write(f"\n{Colors.FAIL}{Colors.BOLD}ABORTED! Instant exit...{Colors.ENDC}\n")
    sys.stdout.flush()
    terminate_process_tree()
    os._exit(1)

signal.signal(signal.SIGINT, signal_handler)

def create_arg_parser():
    parser = argparse.ArgumentParser(description="YouTube Audio/Video Extractor", add_help=False, formatter_class=argparse.RawTextHelpFormatter)

    help_group = parser.add_argument_group('Help')
    help_group.add_argument('-h', '--short-help', action='store_true', help="Show extensive help.")
    help_group.add_argument('--help', action='store_true', help="Show short help message.")

    io_group = parser.add_argument_group('Input & Output')
    io_group.add_argument('urls', nargs='*', help="YouTube URLs.")
    io_group.add_argument('-o', '--output', type=str, help="Output filename.")
    io_group.add_argument('--list', type=str, help="File with URLs.")
    io_group.add_argument('--skip', action='store_true', help="Skip existing files without asking.")
    io_group.add_argument('-dst', type=str, help="Destination dir.")
    io_group.add_argument('--overwrite', action='store_true', help="Overwrite existing files without asking.")

    format_group = parser.add_argument_group('Format (Audio)')
    a_group = format_group.add_mutually_exclusive_group()
    a_group.add_argument('-mp3fast', action='store_true', help="MP3 stereo VBR 128kbps (fast compression).")
    a_group.add_argument('-mp3slow', action='store_true', help="MP3 stereo VBR 196kbps (high quality).")
    a_group.add_argument('-mp3high', action='store_true', default=True, help="MP3 stereo VBR 192kbps (default quality).")
    a_group.add_argument('-mono', action='store_true', help="MP3 mono VBR 96kbps (space saving).")

    video_group = parser.add_argument_group('Format (Video)')
    v_group = video_group.add_mutually_exclusive_group()
    v_group.add_argument('-mp4fast', action='store_true', help="MP4 video 720p (fastest way).")
    v_group.add_argument('-mp41080', action='store_true', help="MP4 video 1080p or best available.")
    v_group.add_argument('-mp4480', action='store_true', help="MP4 video 480p or best available.")

    ab_group = parser.add_argument_group('Anti-Blocking')
    ab_group.add_argument('--cookies', type=str, help="Browser cookies (e.g. chrome).")
    ab_group.add_argument('-r', '--limit-rate', type=str, help="Limit rate (e.g. 50K or 4.2M).")
    ab_group.add_argument('--add-header', action='store_true', help="Add UA header.")
    ab_group.add_argument('--add-android', action='store_true', help="Use android client spoofing.")

    out_group = parser.add_argument_group('Output Mode')
    o_group = out_group.add_mutually_exclusive_group()
    o_group.add_argument('--pb', action='store_true', help="Show progress bar (if supported).")
    o_group.add_argument('--min', action='store_true', help="Download with minimal, single-line progress updates.")
    o_group.add_argument('-b', '--batch', action='store_true', help="Run a batch job from a file silently.")
    o_group.add_argument('--compact', action='store_true', help="Compact view for the --list option.")
    out_group.add_argument('-sum', '--summarize', action='store_true', help="Show a summary after all downloads (for --list).")
    out_group.add_argument('--title', action='store_true', help="Show title of each downloaded item (for --list).")
    out_group.add_argument('--showname', type=int, metavar='X', help="Show X chars of title in minimal mode.")

    util_group = parser.add_argument_group('Utilities')
    util_group.add_argument('--color', action='store_true', help="Enable colored output.")
    util_group.add_argument('--log', nargs='?', const='yt-dlp.log', help="Log yt-dlp output to file.")
    util_group.add_argument('--debug', action='store_true', help="Show debug information.")
    util_group.add_argument('--verbose', action='store_true', help="Verbose output.")
    util_group.add_argument('-t', '--threads', type=int, default=1, help="Number of threads for compression (default: 1).")
    util_group.add_argument('-ret', '--retries', type=int, default=1, help="Number of retries for a failed download (default: 1).")

    ytdlp_group = parser.add_argument_group('yt-dlp Management')
    ytdlp_group.add_argument('--dlver', action='store_true', help="Show current yt-dlp version used by this script.")
    ytdlp_group.add_argument('--dlchk', action='store_true', help="Check latest yt-dlp version available online.")
    ytdlp_group.add_argument('--dlupg', action='store_true', help="Update yt-dlp to latest version (OS-specific).")

    return parser

def get_ytdlp_version():
    """Get the current installed yt-dlp version."""
    try:
        result = subprocess.check_output(list(_YTDLP) + ['--version'], stderr=subprocess.DEVNULL, universal_newlines=True)
        return result.strip()
    except Exception as e:
        return f"Error: cannot get version ({e})"

def check_ytdlp_latest_version():
    """Check the latest available yt-dlp version online."""
    try:
        import urllib.request
        url = 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get('tag_name', 'Unknown')
    except Exception as e:
        return f"Error: cannot check online version ({e})"

def update_ytdlp():
    """Update yt-dlp to the latest version (OS-specific)."""
    platform = sys.platform
    try:
        if platform == 'win32':
            # Windows: use pip or yt-dlp -U
            print("Updating yt-dlp for Windows...")
            result = subprocess.run(list(_YTDLP) + ['-U'], capture_output=True, text=True)
            if result.returncode != 0:
                # Fallback to pip
                print("Trying to update via pip...")
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'], 
                                      capture_output=True, text=True)
            print(result.stdout)
            if result.returncode == 0:
                print(f"{Colors.OKGREEN}Update completed successfully.{Colors.ENDC}")
            else:
                print(f"{Colors.FAIL}Error during update: {result.stderr}{Colors.ENDC}")
        elif platform.startswith('linux') or platform == 'darwin':
            # Linux/Mac: use pip
            print(f"Updating yt-dlp for {'Linux' if platform.startswith('linux') else 'macOS'}...")
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'], 
                                  capture_output=True, text=True)
            print(result.stdout)
            if result.returncode == 0:
                print(f"{Colors.OKGREEN}Update completed successfully.{Colors.ENDC}")
            else:
                print(f"{Colors.FAIL}Error during update: {result.stderr}{Colors.ENDC}")
        else:
            print(f"{Colors.WARNING}Unsupported operating system: {platform}{Colors.ENDC}")
    except Exception as e:
        print(f"{Colors.FAIL}Error during update: {e}{Colors.ENDC}")

def print_help(parser, detailed=False):
    if not detailed:
        print(f"YouTube Audio/Video Extractor v{VERSION} ({DATE})")
        all_options = []
        for group in parser._action_groups:
            for action in group._group_actions:
                if action.option_strings:
                    all_options.append(action.option_strings[0])
        print(f"Options: {' '.join(all_options)}")
    else:
        print(f"YouTube Audio & Video Extractor (v{VERSION})")
        print(f"Author: {AUTHOR} | Date: {DATE}\n")
        for group in parser._action_groups:
            print(f"{group.title}:")
            for action in group._group_actions:
                opts = ", ".join(action.option_strings)
                print(f"  {opts.ljust(25)} {action.help if action.help else ''}")
            print("")
        print("EXAMPLES:")
        print("  python yt_avextractor.py  -mp3fast --add-header   --log info.log --list lista.txt  --min --color --skip  --showname 20")
        print("  python yt_avextractor.py -mp3fast --add-header --log info.log  --min --color --overwrite  --showname 20 \"Here URL of YT movie do download\"")
        print("  python yt_avextractor.py --cookies chrome \"URL\"")
        print("  python yt_avextractor.py --list \"links.txt\" --min --showname 15")
        print("  python yt_avextractor.py -mono -t 4 \"URL\"")
        print("  python yt_avextractor.py -mp41080 --color \"URL\"")
    sys.exit(0)

def cleanup_temp_files(destination_dir):
    """Clean up all temporary files with ytextr_tmp_ prefix at startup."""
    try:
        for temp_file in destination_dir.glob('ytextr_tmp_*'):
            try:
                os.remove(temp_file)
            except:
                pass
    except:
        pass

def cleanup_incomplete_files():
    for filepath in current_files_to_cleanup:
        if filepath and filepath.exists():
            try: os.remove(filepath)
            except: pass

def run_command(command, args, custom_handler=None, **handler_kwargs):
    global CURRENT_SUBPROCESS
    try:
        if args.debug:
            debug_msg = f"[DEBUG] Command: {' '.join(command)}"
            if args.log: logging.debug(debug_msg)
            sys.stdout.write(f"\r\033[K{Colors.C_DIM}{debug_msg}{Colors.ENDC}\n")

        kwargs = {'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT, 'universal_newlines': True, 'encoding': 'utf-8', 'errors': 'replace', 'bufsize': 1}
        if sys.platform != "win32": kwargs['start_new_session'] = True

        CURRENT_SUBPROCESS = subprocess.Popen(command, **kwargs)

        for line in iter(CURRENT_SUBPROCESS.stdout.readline, ''):
            clean_line = line.strip()
            if not clean_line: continue

            is_progress = ("[download]" in clean_line and "%" in clean_line) or \
                          ("=" in clean_line and any(k in clean_line for k in ["out_time", "progress", "speed", "total_size"]))

            if args.log and not is_progress:
                logging.info(clean_line)

            is_error = "ERROR:" in clean_line or "WARNING:" in clean_line
            if (args.verbose or args.debug or is_error) and not is_progress:
                msg_color = Colors.FAIL if "ERROR:" in clean_line else (Colors.WARNING if "WARNING:" in clean_line else Colors.C_DIM)
                prefix = "" if is_error else "[DEBUG] "
                sys.stdout.write(f"\r\033[K{msg_color}{prefix}{clean_line}{Colors.ENDC}\n")
                sys.stdout.flush()

            if custom_handler:
                custom_handler(line, args, **handler_kwargs)
        
        ret = CURRENT_SUBPROCESS.wait()
        CURRENT_SUBPROCESS = None
        return ret == 0
    except Exception as e:
        cprint(f"Error: {e}", Colors.FAIL, args.color, force_print=True); sys.exit(1)

def download_progress_handler(line, args, i, total, title="", is_video=False, download_state=None):
    global VIDEO_PROGRESS, AUDIO_PROGRESS, _COMPACT_LAST_DOWNLOAD_MSG, _COMPACT_FILE_PREFIX
    stripped = line.strip()

    if '[download]' in stripped and '%' in stripped and 'ETA' in stripped:
        match = re.search(r'(\d+\.?\d*%) of\s+([\d\.]+\w+)(?: in ([\d:]+))? at\s+([\d\.]+\w+/s)', stripped)
        if match:
            p, size, duration, speed = match.groups()
            global current_file_download_speed_bps
            current_file_download_speed_bps = speed_to_bytes_per_second(speed)
            
            duration_str = f" in {duration}" if duration else ""
            progress = f"{p.rjust(6)} of {size.rjust(9)}{duration_str} at {speed.rjust(10)}"
            if args.color:
                colored_duration_str = f" in {Colors.OKBLUE}{duration}{Colors.ENDC}" if duration else ""
                progress = f"{Colors.BOLD}{p.rjust(6)}{Colors.ENDC} of {Colors.C_YELLOW}{size.rjust(9)}{Colors.ENDC}{colored_duration_str} at {Colors.C_MAGENTA}{speed.rjust(10)}{Colors.ENDC}"

            if is_video:
                if download_state.stream_type == "video":
                    VIDEO_PROGRESS = progress
                else: # This means it's now downloading audio for a video (merged format)
                    AUDIO_PROGRESS = progress
            else: # Audio-only download
                AUDIO_PROGRESS = progress

            # Format jednoliniowy: File M/N | Downloading: ... 
            total_digits = len(str(total))
            file_part = f"File {str(i).rjust(total_digits)}/{total}"
            
            if args.color:
                file_colored = f"{Colors.BOLD}{Colors.OKBLUE}{file_part}{Colors.ENDC}"
            else:
                file_colored = file_part
            
            if is_video:
                download_part = f"Downloading: video: {VIDEO_PROGRESS} | audio: {AUDIO_PROGRESS}"
            else:
                download_part = f"Downloading: {AUDIO_PROGRESS}"
            
            full_line = f"{file_colored} | {download_part}"
            sys.stdout.write(f"\r\033[K{full_line}")
            sys.stdout.flush()

    elif '[download]' in stripped and '100%' in stripped:
        if is_video and download_state and download_state.stream_type == "video":
            download_state.stream_type = "audio"
        
        if IS_COMPACT_MODE:
            if is_video:
                final_download_display = f"video: {VIDEO_PROGRESS} | audio: {AUDIO_PROGRESS}"
            else:
                final_download_display = f"{AUDIO_PROGRESS}"

            prefix_colored = f"{Colors.BOLD}{Colors.OKBLUE}{_COMPACT_FILE_PREFIX}{Colors.ENDC}: " if args.color else f"{_COMPACT_FILE_PREFIX}: "
            _COMPACT_LAST_DOWNLOAD_MSG = f"{prefix_colored}Downloading: {final_download_display}"


def conversion_progress_handler(line, args, total_duration, i, total, state, title=""):
    global VIDEO_PROGRESS, AUDIO_PROGRESS, _COMPACT_LAST_DOWNLOAD_MSG
    kv = line.strip().split('=')
    if len(kv) == 2: state[kv[0]] = kv[1]

    if "out_time_us=" in line:
        now = time.monotonic()
        if (args.min or IS_COMPACT_MODE) and (now - state['last_update'] < 0.1): return
        state['last_update'] = now
        
        # Zabezpieczenie przed wartością 'N/A' z FFmpeg
        try:
            us = int(state.get('out_time_us', '0'))
        except (ValueError, TypeError):
            us = 0
        
        total_bytes_converted_raw = state.get('total_size', '0')
        total_bytes_converted = size_to_bytes(total_bytes_converted_raw)
        
        conversion_duration_seconds = us / 1_000_000
        if conversion_duration_seconds > 0 and total_bytes_converted > 0:
            global current_file_compress_speed_bps
            current_file_compress_speed_bps = total_bytes_converted / conversion_duration_seconds
        else:
            current_file_compress_speed_bps = 0.0
        
        try:
            percent = min(100.0, (us / (total_duration * 1_000_000)) * 100) if total_duration > 0 else 0
        except: percent = 0

        size_display_str = format_bytes(total_bytes_converted)
        time_str = format_ff_time(state.get('out_time', '0:00:00'))

        convert_status = f"Converting: {Colors.BOLD}{percent:.1f}%{Colors.ENDC} ({Colors.C_YELLOW}{size_display_str}{Colors.ENDC}, {Colors.OKBLUE}{time_str}{Colors.ENDC})" if args.color else f"Converting: {percent:.1f}% ({size_display_str}, {time_str})"
        
        AUDIO_PROGRESS = convert_status

        # Format jednoliniowy: File M/N | Downloading: ... | Converting: ...
        total_digits = len(str(total))
        file_part = f"File {str(i).rjust(total_digits)}/{total}"
        
        if args.color:
            file_colored = f"{Colors.BOLD}{Colors.OKBLUE}{file_part}{Colors.ENDC}"
        else:
            file_colored = file_part
        
        # Zachowaj ostatni stan downloadingu
        download_part = f"Downloading: {VIDEO_PROGRESS if VIDEO_PROGRESS else 'completed'}" if VIDEO_PROGRESS else f"Downloading: completed"
        
        full_line = f"{file_colored} | {download_part} | {convert_status}"
        sys.stdout.write(f"\r\033[K{full_line}")
        sys.stdout.flush()

def main():
    parser = create_arg_parser(); args = parser.parse_args()
    global IS_BATCH_MODE, IS_MINIMAL_MODE, IS_COMPACT_MODE
    IS_BATCH_MODE = args.batch
    IS_MINIMAL_MODE = args.min
    IS_COMPACT_MODE = args.compact and args.list
    if not IS_BATCH_MODE:
        try: init()
        except: pass

    if args.log:
        logging.basicConfig(filename=args.log, level=logging.DEBUG if args.debug else logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s', force=True)

    if args.short_help: print_help(parser, detailed=False)
    if args.help: print_help(parser, detailed=True)

    # yt-dlp management options
    if args.dlver:
        version = get_ytdlp_version()
        print(f"yt-dlp version: {version}")
        sys.exit(0)
    
    if args.dlchk:
        latest = check_ytdlp_latest_version()
        current = get_ytdlp_version()
        print(f"Current version: {current}")
        print(f"Latest version available online: {latest}")
        sys.exit(0)
    
    if args.dlupg:
        update_ytdlp()
        sys.exit(0)

    if args.summarize and not args.list:
        parser.error("Option --summarize can only be used with --list.")

    urls = []
    if args.list:
        try:
            with open(args.list, 'r', encoding='utf-8') as f: urls = [l.strip() for l in f if l.strip()]
        except: cprint("File not found.", Colors.FAIL, args.color, force_print=True); sys.exit(1)
    else: urls = args.urls
    if not urls: cprint("No URLs provided.", Colors.FAIL, args.color, force_print=True); sys.exit(1)

    destination_dir = Path(args.dst) if args.dst else Path.cwd()
    destination_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean up any leftover temporary files from previous runs
    cleanup_temp_files(destination_dir)
    
    cprint(f"Found {len(urls)} file(s).", Colors.HEADER, args.color)

    error_count = 0
    for i, url in enumerate(urls, 1):
        global VIDEO_PROGRESS, AUDIO_PROGRESS, current_file_download_speed_bps, current_file_compress_speed_bps
        global _COMPACT_FILE_PREFIX, _COMPACT_LAST_DOWNLOAD_MSG
        VIDEO_PROGRESS = "" # Reset for current file
        AUDIO_PROGRESS = "" # Reset for current file
        current_file_download_speed_bps = 0.0 # Reset for current file
        current_file_compress_speed_bps = 0.0 # Reset for current file
        _COMPACT_FILE_PREFIX = f"{str(i).rjust(len(str(len(urls))))}/{len(urls)}"
        _COMPACT_LAST_DOWNLOAD_MSG = ""


        download_state = DownloadState()

        # Initialize here to prevent undefined error later
        video_title = "" 
        is_video = False 
        final_filepath = None 

        try:
            info_cmd = list(_YTDLP) + ['--no-warnings', '--dump-json', '--no-playlist', url]
            if args.cookies: info_cmd.extend(['--cookies-from-browser', args.cookies])
            if args.add_header: info_cmd.extend(['--user-agent', USER_AGENT_HEADER])
            if args.limit_rate: info_cmd.extend(['--limit-rate', args.limit_rate])

            raw_output = subprocess.check_output(info_cmd, stderr=subprocess.DEVNULL)
            video_info = json.loads(raw_output.decode('utf-8', errors='replace'))
            video_title = re.sub(r'[\\/*?:"<>|]', "", video_info.get('title', f"video_{i}"))
            
            is_video = any([args.mp4fast, args.mp41080, args.mp4480])
            ext = '.mp4' if is_video else '.mp3'
            final_filepath = destination_dir / (f"{video_title}{ext}" if not args.output else args.output)

        except Exception:
            if args.min: sys.stdout.write(f"\n{Colors.FAIL}Error: Metadata fetch failed.{Colors.ENDC}\n")
            cprint(f"Error: Metadata fetch failed for {url}", Colors.FAIL, args.color, force_print=True);
            failed_urls.append(f"{url} | REASON: Metadata fetch failed.")
            error_count += 1; continue
        
        # Check if file already exists and handle accordingly
        global OVERWRITE_ALL
        skip_this_file = False
        if final_filepath.exists():
            if args.skip:
                cprint(f"File exists, skipping: {final_filepath.name}", Colors.WARNING, args.color, force_print=True)
                continue
            elif args.overwrite or OVERWRITE_ALL:
                # Silently overwrite - delete existing file first
                try:
                    os.remove(final_filepath)
                except Exception as e:
                    cprint(f"Error removing existing file: {e}", Colors.FAIL, args.color, force_print=True)
                    continue
            else:
                # Ask user what to do
                while True:
                    response = input(f"File exists: {final_filepath.name}. Overwrite? [y/N/a/q]: ").strip().lower()
                    if response == 'y':
                        # Delete the file before overwriting
                        try:
                            os.remove(final_filepath)
                        except Exception as e:
                            cprint(f"Error removing existing file: {e}", Colors.FAIL, args.color, force_print=True)
                            skip_this_file = True
                        break  # Overwrite this file
                    elif response == 'a':
                        OVERWRITE_ALL = True
                        # Delete the file before overwriting
                        try:
                            os.remove(final_filepath)
                        except Exception as e:
                            cprint(f"Error removing existing file: {e}", Colors.FAIL, args.color, force_print=True)
                            skip_this_file = True
                        break  # Overwrite all files
                    elif response == 'q':
                        cprint("Quitting...", Colors.WARNING, args.color, force_print=True)
                        sys.exit(0)
                    elif response == 'n' or response == '':
                        cprint(f"Skipping: {final_filepath.name}", Colors.WARNING, args.color, force_print=True)
                        skip_this_file = True
                        break
                    else:
                        print("Invalid choice. Please enter y, N, a, or q.")
                
                if skip_this_file:
                    continue  # Skip this file
        
        if args.min:
            show_minimal_status(i, len(urls), "Connecting...", args.color, Colors.HEADER, title_limit=args.showname)
        elif IS_COMPACT_MODE: # No initial message for compact mode, its all handled in finish_summary
            pass
        else: # This is the verbose mode
            cprint(f"\n--- Processing {i}/{len(urls)}: {url} ---", Colors.BOLD, args.color, force_print=True)
            if args.title: # If --title is present, show title in verbose mode, regardless of --list.
                cprint(f"Title: {video_title}", Colors.OKGREEN, args.color, force_print=True)
            
            # Initialize the accumulating line with "Downloading:"


            for s in range(3, 0, -1):
                sys.stdout.write(f"\rStarting download in {s}s... "); sys.stdout.flush(); time.sleep(1)
            sys.stdout.write("\r" + " " * 40 + "\r") # Clear the countdown
            sys.stdout.flush()


        start_time = time.monotonic()

        for attempt in range(args.retries):
            if is_video:
                video_format = "bestvideo[height<=720]+bestaudio/best[height<=720]" if args.mp4fast else \
                               "bestvideo[height<=1080]+bestaudio/best[height<=1080]" if args.mp41080 else \
                               "bestvideo[height<=480]+bestaudio/best[height<=480]"
                cmd = list(_YTDLP) + ['--no-warnings', '--progress', '-f', video_format, '--merge-output-format', 'mp4', '-o', str(final_filepath), url]
                if args.add_header: cmd.extend(['--user-agent', USER_AGENT_HEADER])
                if args.limit_rate: cmd.extend(['--limit-rate', args.limit_rate])
                if args.overwrite: cmd.append('--force-overwrites')
                cmd.extend(['--retries', str(args.retries)])
                if run_command(cmd, args, custom_handler=download_progress_handler, i=i, total=len(urls), title=video_title, is_video=True, download_state=download_state):
                    finish_summary(start_time, args, i, len(urls), title=video_title, is_video=True, final_filepath=final_filepath)
                    break
                else:
                    if attempt < args.retries - 1:
                        cprint(f"Retrying ({attempt + 1}/{args.retries})...", Colors.WARNING, args.color, force_print=True)
                        time.sleep(5)
                    else:
                        failed_urls.append(f"{url} | REASON: Download failed (Video).")
                        error_count += 1
            else:
                temp_path = destination_dir / f"ytextr_tmp_{os.getpid()}_{i}.%(ext)s"
                dl_cmd = list(_YTDLP) + ['--no-warnings', '--progress', '-f', 'bestaudio', '-o', str(temp_path), url]
                if args.add_header: dl_cmd.extend(['--user-agent', USER_AGENT_HEADER])
                if args.limit_rate: dl_cmd.extend(['--limit-rate', args.limit_rate])
                if args.overwrite: dl_cmd.append('--force-overwrites')
                dl_cmd.extend(['--retries', str(args.retries)])
                if run_command(dl_cmd, args, custom_handler=download_progress_handler, i=i, total=len(urls), title=video_title, is_video=False, download_state=None):
                    actual_input = next(destination_dir.glob(f"ytextr_tmp_{os.getpid()}_{i}.*"), None)
                    if actual_input:
                        current_files_to_cleanup.append(actual_input)
                        duration = video_info.get('duration', 0)

                        cv_cmd = ['ffmpeg', '-threads', str(args.threads), '-i', str(actual_input)]

                        if args.mono:
                            cv_cmd.extend(['-vn', '-ac', '1', '-codec:a', 'libmp3lame', '-q:a', '7'])
                        elif args.mp3fast:
                            cv_cmd.extend(['-vn', '-codec:a', 'libmp3lame', '-q:a', '4'])
                        elif args.mp3slow:
                            cv_cmd.extend(['-vn', '-codec:a', 'libmp3lame', '-q:a', '2'])
                        else:  # mp3high (default)
                            cv_cmd.extend(['-vn', '-codec:a', 'libmp3lame', '-q:a', '3'])

                        cv_cmd.extend(['-progress', 'pipe:1', '-y', str(final_filepath)])

                        ff_state = {'total_size': '0', 'out_time': '0:00:00', 'out_time_us': '0', 'last_update': 0}
                        if run_command(cv_cmd, args, custom_handler=conversion_progress_handler, total_duration=duration, i=i, total=len(urls), state=ff_state, title=video_title):
                            finish_summary(start_time, args, i, len(urls), title=video_title, final_filepath=final_filepath)
                            try: os.remove(actual_input)
                            except: pass
                            break
                        else:
                            if attempt < args.retries - 1:
                                cprint(f"Retrying ({attempt + 1}/{args.retries})...", Colors.WARNING, args.color, force_print=True)
                                time.sleep(5)
                            else:
                                failed_urls.append(f"{url} | REASON: Conversion to MP3 failed.")
                                error_count += 1
                        try: os.remove(actual_input)
                        except: pass
                    else:
                        failed_urls.append(f"{url} | REASON: Downloaded temp file missing.")
                        error_count += 1
                        break # No point in retrying if the temp file is missing
                else:
                    if attempt < args.retries - 1:
                        cprint(f"Retrying ({attempt + 1}/{args.retries})...", Colors.WARNING, args.color, force_print=True)
                        time.sleep(5)
                    else:
                        failed_urls.append(f"{url} | REASON: Download failed (Audio).")
                        error_count += 1
                    break
 
    if args.list and args.summarize:
        total_succeeded = len(SUMMARY_DATA)
        total_failed = len(failed_urls)

        total_downloaded_bytes_sum = sum(d['size'] for d in SUMMARY_DATA)
        total_processing_time_sum = sum(d['total_time'] for d in SUMMARY_DATA)
        
        all_download_speeds = [s for s in [d['download_speed'] for d in SUMMARY_DATA] if s is not None and s > 0]
        avg_download_speed_bps = sum(all_download_speeds) / len(all_download_speeds) if all_download_speeds else 0

        all_compress_speeds = [s for s in [d['compress_speed'] for d in SUMMARY_DATA] if s is not None and s > 0]
        avg_compress_speed_bps = sum(all_compress_speeds) / len(all_compress_speeds) if all_compress_speeds else 0

        cprint("\n--- Download Summary ---", Colors.BOLD, args.color, force_print=True)
        cprint(f"Files processed successfully: {total_succeeded}", Colors.OKGREEN, args.color, force_print=True)
        if total_failed > 0:
            cprint(f"Files with errors: {total_failed}", Colors.FAIL, args.color, force_print=True)
        cprint(f"Total size of downloaded files: {format_bytes(total_downloaded_bytes_sum)}", Colors.OKBLUE, args.color, force_print=True)
        cprint(f"Total processing time: {total_processing_time_sum:.2f}s", Colors.OKBLUE, args.color, force_print=True)
        cprint(f"Average download speed: {format_bytes(avg_download_speed_bps)}/s", Colors.OKCYAN, args.color, force_print=True)
        if all_compress_speeds: # Only show if some compression happened
            cprint(f"Average compression speed: {format_bytes(avg_compress_speed_bps)}/s", Colors.OKCYAN, args.color, force_print=True)
        cprint("------------------------", Colors.BOLD, args.color, force_print=True)
 
    if failed_urls:
        err_file = Path("ytextractor.err")
        with open(err_file, "w", encoding="utf-8") as f:
            f.write("\n".join(failed_urls))
        cprint(f"\n{len(failed_urls)} file(s) failed or are incomplete.", Colors.FAIL, args.color, force_print=True)
        cprint(f"Details saved to: {err_file.absolute()}", Colors.WARNING, args.color, force_print=True)

    sys.exit(error_count)

def finish_summary(start_time, args, i, total, title="", is_video=False, final_filepath=None):
    elapsed = time.monotonic() - start_time
    file_size_str = ""
    file_size = 0 # Initialize file_size
    if final_filepath and final_filepath.exists():
        try:
            file_size = final_filepath.stat().st_size
            file_size_str = f"Size: {format_bytes(file_size)}"
        except: pass
    
    summary_msg = f"Completed in {elapsed:.2f}s"
    if file_size_str:
        summary_msg += f", {file_size_str}"

    # Collect data for summary if summarize option is enabled
    global SUMMARY_DATA
    if args.summarize and final_filepath and final_filepath.exists():
        elapsed_total_file_time = time.monotonic() - start_time
        SUMMARY_DATA.append({
            'size': file_size,
            'total_time': elapsed_total_file_time,
            'download_speed': current_file_download_speed_bps,
            'compress_speed': current_file_compress_speed_bps
        })

    color_summary = f"{Colors.OKGREEN}{summary_msg}{Colors.ENDC}" if args.color else summary_msg

    # Format jednoliniowy: File M/N | Downloading: ... | Converting: ... | Summary: ...
    total_digits = len(str(total))
    file_part = f"File {str(i).rjust(total_digits)}/{total}"
    
    if args.color:
        file_colored = f"{Colors.BOLD}{Colors.OKBLUE}{file_part}{Colors.ENDC}"
    else:
        file_colored = file_part
    
    # Pokaż ostatni stan downloading
    download_part = f"Downloading: completed"
    
    # Sprawdź czy była konwersja (dla audio)
    if not is_video and AUDIO_PROGRESS:
        convert_part = f"Converting: completed"
        final_line = f"{file_colored} | {download_part} | {convert_part} | {color_summary}"
    else:
        final_line = f"{file_colored} | {download_part} | {color_summary}"
    
    sys.stdout.write(f"\r\033[K{final_line}\n")
    sys.stdout.flush()

if __name__ == '__main__': main()
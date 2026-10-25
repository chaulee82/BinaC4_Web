# /root/BinaC4_Web/auto_runner.py
"""
Module: auto_runner.py
Mục đích: Process Guard điều phối phiên quét tự động định kỳ (1 giờ/lần).
Tránh xung đột tài nguyên VPS, kiểm tra tiến trình đang chạy và dọn dẹp stale lock.
"""

import os
import sys
import subprocess
import time
from typing import Optional, Dict, Any

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(WORKSPACE_DIR, "main.py")
LOCK_FILE = os.path.join(WORKSPACE_DIR, "data", "binac4_main.lock")
HTML_LOG_FILE = os.path.join(WORKSPACE_DIR, "BinaC4_latest_log.html")
TXT_LOG_FILE = os.path.join(WORKSPACE_DIR, "BinaC4_latest_log.txt")
PYTHON_BIN = sys.executable  # Tự động dùng đúng Python venv hiện hành

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)


def is_target_main_cmd(args: list) -> bool:
    """Kiểm tra chính xác tham số có nhắm tới main.py hay không (tránh trùng tên xxx_main.py)"""
    return any(arg == "main.py" or arg.endswith("/main.py") or arg.endswith("\\main.py") for arg in args)


def find_running_main_proc() -> Optional[Dict[str, Any]]:
    """
    Tìm tiến trình main.py đang chạy thực sự.
    Trả về dict {'pid': pid, 'cmd': cmdline_str} nếu có, ngược lại trả về None.
    Bỏ qua tiến trình Zombie/Defunct, pgrep và chính tiến trình auto_runner.
    """
    my_pid = os.getpid()

    # 1. Quét bằng psutil nếu có sẵn
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status']):
            try:
                pid = proc.info.get('pid')
                if not pid or pid == my_pid:
                    continue
                # Bỏ qua tiến trình Zombie
                if proc.info.get('status') == psutil.STATUS_ZOMBIE:
                    continue
                cmdline = proc.info.get('cmdline') or []
                cmd_str = " ".join(cmdline)
                # Chỉ match khi tham số nhắm tới main.py và không phải auto_runner
                if "auto_runner.py" in cmd_str:
                    continue
                if is_target_main_cmd(cmdline):
                    return {"pid": pid, "cmd": cmd_str[:120]}
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        # psutil đã quét toàn bộ hệ thống mà không có main.py -> kết luận None
        return None
    except ImportError:
        pass

    # 2. Quét /proc trên Linux VPS
    if os.path.exists('/proc'):
        try:
            for entry in os.listdir('/proc'):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == my_pid:
                    continue
                try:
                    # Kiểm tra trạng thái trong /proc/{pid}/status để loại trừ zombie
                    status_path = f"/proc/{pid}/status"
                    if os.path.exists(status_path):
                        with open(status_path, "r", encoding="utf-8", errors="ignore") as f:
                            stat_text = f.read(256)
                            if "State:\tZ" in stat_text or "State: Z" in stat_text:
                                continue

                    cmdline_path = f"/proc/{pid}/cmdline"
                    if os.path.exists(cmdline_path):
                        with open(cmdline_path, "rb") as f:
                            raw = f.read().decode("utf-8", errors="ignore")
                            # /proc/pid/cmdline phân tách các đối số bằng ký tự null \0
                            args = [a for a in raw.split("\0") if a]
                            cmd_str = " ".join(args)
                            if "auto_runner.py" in cmd_str:
                                continue
                            if is_target_main_cmd(args):
                                if "python" in cmd_str or "venv" in cmd_str:
                                    return {"pid": pid, "cmd": cmd_str[:120]}
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
            # Đã quét hết /proc mà không thấy main.py -> kết luận None
            return None
        except Exception:
            pass

    # 3. Fallback trên Windows (wmic)
    if sys.platform == 'win32':
        try:
            out = subprocess.check_output(
                ['wmic', 'process', 'where', 'name like "%python%"', 'get', 'CommandLine,ProcessId'],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.strip().splitlines():
                if "main.py" in line and "auto_runner.py" not in line and str(my_pid) not in line:
                    parts = line.strip().rsplit(None, 1)
                    found_pid = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 0
                    return {"pid": found_pid, "cmd": line.strip()[:120]}
        except Exception:
            pass

    return None


def is_main_running() -> bool:
    """Trả về True nếu có tiến trình main.py thực sự đang chạy"""
    return find_running_main_proc() is not None


def clean_stale_lock():
    """Dọn dẹp file lock cũ nếu tiến trình trước đó đã kết thúc bất thường."""
    if os.path.exists(LOCK_FILE):
        try:
            if not is_main_running():
                os.remove(LOCK_FILE)
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🧹 [AUTO-RUNNER] Đã dọn dẹp file lock cũ (stale lock): {LOCK_FILE}")
        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ [AUTO-RUNNER] Không thể dọn dẹp lock file: {e}")


def auto_scan():
    """Hàm kích hoạt quét tự động định kỳ, đồng thời ghi log ra file cho Web UI."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Kiểm tra an toàn tiến trình (Process Guard): Chỉ cảnh báo, vẫn ưu tiên tiếp tục chạy
    running_proc = find_running_main_proc()
    warn_banner = ""
    if running_proc:
        pid_info = f"PID: {running_proc['pid']} | {running_proc['cmd']}"
        warn_msg = (
            f"[{timestamp}] ⚠️ [AUTO-RUNNER] CẢNH BÁO TÀI NGUYÊN: Phát hiện main.py đang chạy ngầm ({pid_info}).\n"
            f"[{timestamp}] ⚡ [AUTO-RUNNER] ƯU TIÊN TIẾP TỤC: Vẫn kích hoạt phiên quét tự động mới theo yêu cầu..."
        )
        print(warn_msg)
        warn_banner = (
            f"⚠️ [CẢNH BÁO TÀI NGUYÊN] Đang có tiến trình main.py chạy ngầm ({pid_info})\n"
            f"⚡ Ưu tiên tiếp tục phiên quét tự động định kỳ...\n"
            + "-" * 60 + "\n"
        )
    else:
        # Dọn dẹp stale lock nếu hệ thống hoàn toàn rảnh
        clean_stale_lock()
        print(f"[{timestamp}] 🚀 [AUTO-RUNNER] Hệ thống rảnh. Bắt đầu kích hoạt phiên quét tự động...")

    # Header log cho file và UI
    header_banner = (
        f"🤖 [AUTO-RUNNER] PHIÊN QUÉT TỰ ĐỘNG ĐỊNH KỲ\n"
        f"⏰ Khởi động lúc: {timestamp}\n"
        + warn_banner
        + "=" * 60 + "\n"
    )

    html_header = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BinaC4 Log (Auto Runner)</title>
    <style>
        body { background-color: #1e1e1e; padding: 20px; margin: 0; }
        pre { font-family: Consolas, 'Courier New', monospace; white-space: pre; font-size: 14px; color: #d4d4d4; line-height: 1.5; }
    </style>
</head>
<body>
<pre>"""

    # 2. Gọi main.py độc lập qua subprocess và stream log
    f_txt = None
    f_html = None
    process = None
    try:
        try:
            f_txt = open(TXT_LOG_FILE, 'w', encoding='utf-8')
            f_html = open(HTML_LOG_FILE, 'w', encoding='utf-8')
            f_html.write(html_header)
            f_txt.write(header_banner)
            f_html.write(header_banner.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            f_txt.flush()
            f_html.flush()
        except Exception as err_file:
            print(f"[{timestamp}] ⚠️ [AUTO-RUNNER] Không thể mở file log: {err_file}")

        env = os.environ.copy()
        env["AUTO_SCAN_MODE"] = "1"  # Cờ đánh dấu chạy tự động nếu cần
        env["PYTHONIOENCODING"] = "utf-8"

        # Khởi chạy main.py với unbuffered I/O
        process = subprocess.Popen(
            [PYTHON_BIN, "-u", MAIN_SCRIPT],
            cwd=WORKSPACE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace'
        )

        # Ghi nhận lock file cho tiến trình mới
        try:
            os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
            with open(LOCK_FILE, "w", encoding="utf-8") as lf:
                lf.write(str(process.pid))
        except Exception:
            pass

        # Stream từng dòng với cơ chế Throttled Batch Flush (tối đa 2 lần/giây)
        # Giúp tránh nghẽn I/O đĩa cứng khi in hàng nghìn dòng ma trận và bảng biểu
        last_flush = time.time()
        FLUSH_INTERVAL = 0.5  # giây

        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                sys.stdout.write(line)
                if f_txt:
                    f_txt.write(line)
                if f_html:
                    escaped = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    f_html.write(escaped)

                # Chỉ flush đĩa cứng định kỳ để tiến trình con main.py chạy tốc độ tối đa
                now = time.time()
                if now - last_flush >= FLUSH_INTERVAL:
                    sys.stdout.flush()
                    if f_txt:
                        f_txt.flush()
                    if f_html:
                        f_html.flush()
                    last_flush = now

            # Đảm bảo xả hết toàn bộ buffer còn lại khi kết thúc đọc
            sys.stdout.flush()
            if f_txt:
                f_txt.flush()
            if f_html:
                f_html.flush()
            process.stdout.close()

        retcode = process.wait()
        end_time = time.strftime("%Y-%m-%d %H:%M:%S")

        if retcode == 0:
            finish_msg = f"\n" + "=" * 60 + f"\n[{end_time}] ✅ [AUTO-RUNNER] Hoàn tất phiên quét tự động thành công.\n"
        else:
            finish_msg = f"\n" + "=" * 60 + f"\n[{end_time}] ❌ [AUTO-RUNNER] Phiên quét kết thúc với mã lỗi: {retcode}\n"

        sys.stdout.write(finish_msg)
        sys.stdout.flush()
        if f_txt:
            f_txt.write(finish_msg)
            f_txt.flush()
        if f_html:
            f_html.write(finish_msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            f_html.flush()

        # Cập nhật snapshot tĩnh (danh sách mã + hồ sơ mã) cho Web UI, tránh truy vấn DB mỗi lần load trang
        try:
            if WORKSPACE_DIR not in sys.path:
                sys.path.insert(0, WORKSPACE_DIR)
            from core.symbol_inspector import save_web_snapshot, SNAPSHOT_FILE
            
            # Nếu main.py đã chạy thành công và file snapshot vừa được cập nhật, tái sử dụng trực tiếp
            already_updated = False
            if retcode == 0 and os.path.exists(SNAPSHOT_FILE):
                age = time.time() - os.path.getmtime(SNAPSHOT_FILE)
                if age < 180:  # Tạo trong vòng 3 phút qua bởi phiên main.py hiện tại
                    already_updated = True
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🗂️ [AUTO-RUNNER] Snapshot Web UI đã được cập nhật bởi main.py ({age:.1f}s trước).")

            if not already_updated:
                if save_web_snapshot():
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🗂️ [AUTO-RUNNER] Đã cập nhật snapshot Web UI (fallback).")
        except Exception as e_snap:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ [AUTO-RUNNER] Không thể cập nhật snapshot Web UI: {e_snap}")

    except Exception as e:
        err_msg = f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 💥 [AUTO-RUNNER] Lỗi ngoại lệ khi chạy main.py: {e}\n"
        sys.stdout.write(err_msg)
        sys.stdout.flush()
        if f_txt:
            f_txt.write(err_msg)
        if f_html:
            f_html.write(err_msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    finally:
        # Dọn dẹp lock file
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
        if f_html:
            try:
                f_html.write("</pre>\n</body>\n</html>\n")
                f_html.close()
            except Exception:
                pass
        if f_txt:
            try:
                f_txt.close()
            except Exception:
                pass


if __name__ == "__main__":
    auto_scan()

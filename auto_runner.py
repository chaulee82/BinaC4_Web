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
PYTHON_BIN = sys.executable  # Tự động dùng đúng Python venv hiện hành

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)


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
                if any(arg.endswith("main.py") or arg == "main.py" for arg in cmdline):
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
                            if any(arg.endswith("main.py") or arg == "main.py" for arg in args):
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
    """Hàm kích hoạt quét tự động định kỳ"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Kiểm tra an toàn tiến trình
    running_proc = find_running_main_proc()
    if running_proc:
        pid_info = f" (PID: {running_proc['pid']} | {running_proc['cmd']})"
        print(f"[{timestamp}] ⚠️ [AUTO-RUNNER] Phát hiện main.py đang chạy{pid_info}. Bỏ qua phiên quét này để tránh xung đột tài nguyên!")
        return

    # Dọn dẹp stale lock nếu có
    clean_stale_lock()

    print(f"[{timestamp}] 🚀 [AUTO-RUNNER] Hệ thống rảnh. Bắt đầu kích hoạt phiên quét tự động...")

    # 2. Gọi main.py độc lập qua subprocess
    try:
        env = os.environ.copy()
        env["AUTO_SCAN_MODE"] = "1"  # Cờ đánh dấu chạy tự động nếu cần
        env["PYTHONIOENCODING"] = "utf-8"

        # Chạy main.py và chờ hoàn tất
        result = subprocess.run(
            [PYTHON_BIN, MAIN_SCRIPT],
            cwd=WORKSPACE_DIR,
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )

        if result.returncode == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ [AUTO-RUNNER] Hoàn tất phiên quét tự động thành công.")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ [AUTO-RUNNER] Phiên quét kết thúc với mã lỗi: {result.returncode}")

    except Exception as e:
        print(f"[{timestamp}] 💥 [AUTO-RUNNER] Lỗi ngoại lệ khi gọi main.py: {e}")


if __name__ == "__main__":
    auto_scan()

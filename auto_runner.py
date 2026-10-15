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

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(WORKSPACE_DIR, "main.py")
LOCK_FILE = os.path.join(WORKSPACE_DIR, "data", "binac4_main.lock")
PYTHON_BIN = sys.executable  # Tự động dùng đúng Python venv hiện hành

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)


def is_main_running() -> bool:
    """Kiểm tra xem có tiến trình main.py nào khác đang thực thi không.
    Ưu tiên dùng psutil nếu có; tự động fallback sang /proc (Linux VPS) hoặc pgrep / wmic.
    """
    current_pid = os.getpid()

    # 1. Thử dùng psutil nếu đã cài đặt
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                cmdline = proc.info.get('cmdline') or []
                # Kiểm tra nếu lệnh chạy chứa main.py
                if any("main.py" in str(arg) for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False
    except ImportError:
        pass

    # 2. Fallback quét /proc trên Linux VPS
    if os.path.exists('/proc'):
        try:
            for entry in os.listdir('/proc'):
                if entry.isdigit():
                    pid = int(entry)
                    if pid == current_pid:
                        continue
                    try:
                        with open(f"/proc/{pid}/cmdline", "rb") as f:
                            raw = f.read().decode("utf-8", errors="ignore")
                            if "main.py" in raw and ("python" in raw or "venv" in raw):
                                return True
                    except (FileNotFoundError, PermissionError, ProcessLookupError):
                        continue
        except Exception:
            pass

    # 3. Fallback pgrep trên Unix/Linux
    if sys.platform != 'win32':
        try:
            out = subprocess.check_output(['pgrep', '-f', 'main.py'], text=True)
            for line in out.strip().splitlines():
                if line.isdigit() and int(line) != current_pid:
                    return True
        except Exception:
            pass
    else:
        # Fallback trên Windows (wmic)
        try:
            out = subprocess.check_output(
                ['wmic', 'process', 'where', 'name like "%python%"', 'get', 'CommandLine,ProcessId'],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.strip().splitlines():
                if "main.py" in line and str(current_pid) not in line:
                    return True
        except Exception:
            pass

    return False


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
    if is_main_running():
        print(f"[{timestamp}] ⚠️ [AUTO-RUNNER] Phát hiện main.py đang chạy. Bỏ qua phiên quét này để tránh xung đột tài nguyên!")
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

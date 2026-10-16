import gradio as gr
import subprocess
import sys
import os
import queue
import threading
import time

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(WORKSPACE_DIR, 'main.py')
HTML_LOG_FILE = os.path.join(WORKSPACE_DIR, 'BinaC4_latest_log.html')
TXT_LOG_FILE = os.path.join(WORKSPACE_DIR, 'BinaC4_latest_log.txt')

# Quản lý tiến trình duy nhất (Single Instance Subprocess)
_current_process = None
_proc_lock = threading.Lock()

def get_latest_log_data():
    """Tải nội dung log gần nhất từ file để hiển thị ngay khi mở Web (0s chờ)."""
    if os.path.exists(TXT_LOG_FILE) and os.path.getsize(TXT_LOG_FILE) > 0:
        try:
            with open(TXT_LOG_FILE, 'r', encoding='utf-8') as f:
                return f.read(), TXT_LOG_FILE
        except Exception:
            pass
    if os.path.exists(HTML_LOG_FILE) and os.path.getsize(HTML_LOG_FILE) > 0:
        try:
            with open(HTML_LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if '<pre>' in content:
                    content = content.split('<pre>', 1)[1]
                    if '</pre>' in content:
                        content = content.split('</pre>', 1)[0]
                return content.replace('&lt;', '<').replace('&gt;', '>'), HTML_LOG_FILE
        except Exception:
            pass
    return "ℹ️ Chưa có dữ liệu phiên quét nào. Vui lòng bấm [▶️ Thực thi Bot] để bắt đầu.", None

def run_binac4():
    """Executes main.py and yields its output in throttled batches to the Gradio UI."""
    global _current_process
    
    # ── 1. Kiểm tra tiến trình cũ (Chỉ cảnh báo, vẫn ưu tiên tiếp tục chạy dù chậm) ──
    prev_pid = None
    with _proc_lock:
        if _current_process is not None and _current_process.poll() is None:
            prev_pid = _current_process.pid

    # Prepare environment variables to force UTF-8 encoding for prints
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    html_header = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BinaC4 Log</title>
    <style>
        body { background-color: #1e1e1e; padding: 20px; margin: 0; }
        pre { font-family: Consolas, 'Courier New', monospace; white-space: pre; font-size: 14px; color: #d4d4d4; line-height: 1.5; }
    </style>
</head>
<body>
<pre>"""
    html_footer = """</pre>
</body>
</html>"""

    try:
        process = subprocess.Popen(
            [sys.executable, "-u", SCRIPT_PATH],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
            encoding='utf-8'
        )
        with _proc_lock:
            _current_process = process
    except Exception as e:
        yield f"❌ Lỗi khi khởi động main.py: {e}", None
        return
    
    output_str = ""
    if prev_pid:
        output_str += (
            f"⚠️ [CẢNH BÁO TÀI NGUYÊN] Tiến trình quét trước (PID: {prev_pid}) vẫn đang chạy ngầm!\n"
            f"⚡ Ưu tiên tiếp tục khởi tạo phiên quét mới này (PID: {process.pid})...\n"
            + "="*60 + "\n"
        )
    output_str += f"🚀 Đang khởi động BinaC4 (PID: {process.pid})...\n" + "="*50 + "\n"
    yield output_str, None
    
    q = queue.Queue()
    def enqueue_output(out, q):
        for line in iter(out.readline, ''):
            q.put(line)
        out.close()
        
    t = threading.Thread(target=enqueue_output, args=(process.stdout, q))
    t.daemon = True
    t.start()
    
    last_yield_time = time.time()
    has_new_data = False
    
    try:
        with open(HTML_LOG_FILE, 'w', encoding='utf-8') as f_html, \
             open(TXT_LOG_FILE, 'w', encoding='utf-8') as f_txt:
            
            f_html.write(html_header)
            f_txt.write(output_str)
            f_html.write(output_str.replace('<', '&lt;').replace('>', '&gt;'))
            
            while True:
                try:
                    # Timeout ngắn để nhanh chóng gom các dòng
                    line = q.get(timeout=0.2)
                    if "✅ AN TOÀN" not in line:
                        output_str += line
                        has_new_data = True
                        f_txt.write(line)
                        f_html.write(line.replace('<', '&lt;').replace('>', '&gt;'))
                    
                    # Rút hết các dòng đang chờ sẵn trong Queue mà không chờ timeout
                    while not q.empty():
                        try:
                            extra_line = q.get_nowait()
                            if "✅ AN TOÀN" not in extra_line:
                                output_str += extra_line
                                has_new_data = True
                                f_txt.write(extra_line)
                                f_html.write(extra_line.replace('<', '&lt;').replace('>', '&gt;'))
                        except queue.Empty:
                            break
                    
                    now = time.time()
                    # Throttle: Chỉ yield ra giao diện mỗi tối thiểu 0.5 giây để tránh nghẽn WebSocket & DOM Freeze
                    if has_new_data and (now - last_yield_time >= 0.5):
                        f_html.flush()
                        f_txt.flush()
                        last_yield_time = now
                        has_new_data = False
                        yield output_str, None

                except queue.Empty:
                    now = time.time()
                    if has_new_data:
                        f_html.flush()
                        f_txt.flush()
                        last_yield_time = now
                        has_new_data = False
                        yield output_str, None
                    elif process.poll() is not None:
                        # Tiến trình con đã kết thúc và không còn dữ liệu trong queue
                        break
                    else:
                        # Gửi heartbeat định kỳ 2s nếu không có output để giữ kết nối không bị timeout
                        if now - last_yield_time >= 2.0:
                            last_yield_time = now
                            yield output_str, None
            
            return_code = process.wait()
            
            if return_code != 0:
                final_msg = f"\n\n[ERROR] Quá trình kết thúc với mã lỗi {return_code}\n"
            else:
                final_msg = f"\n\n[SUCCESS] Hoàn tất quá trình quét.\n"
            
            output_str += final_msg
            f_txt.write(final_msg)
            f_html.write(final_msg.replace('<', '&lt;').replace('>', '&gt;'))
            f_html.write(html_footer)
            f_txt.flush()
            f_html.flush()
            
        yield output_str, TXT_LOG_FILE
    finally:
        with _proc_lock:
            if _current_process == process:
                _current_process = None

# Custom CSS to make the Textbox behave like a Terminal (Monospace, no wrapping)
css = """
#terminal-output textarea {
    font-family: 'Consolas', 'Courier New', 'Monaco', monospace !important;
    font-size: 13px !important;
    white-space: pre !important;
    overflow-wrap: normal !important;
    overflow-x: auto !important;
    line-height: 1.4 !important;
    background-color: #1e1e1e !important;
    color: #d4d4d4 !important;
}
"""

# Build the Gradio interface
with gr.Blocks(title="BinaC4 Web Interface", css=css) as demo:
    initial_text, initial_file = get_latest_log_data()

    gr.Markdown("# 🚀 BinaC4 Trading Bot Web Interface")
    gr.Markdown("Giao diện này cho phép bạn kích hoạt bot `main.py` và theo dõi quá trình quét thị trường trực tiếp.")
    
    with gr.Row():
        run_btn = gr.Button("▶️ Thực thi Bot (Run main.py)", variant="primary")
    
    with gr.Row():
        copy_btn = gr.Button("📋 Copy Kết Quả", size="sm")
    
    with gr.Row():
        output_box = gr.Textbox(
            value=initial_text,
            label="Terminal Output / Logs", 
            lines=30, 
            max_lines=50, 
            interactive=False,
            elem_id="terminal-output",
            autoscroll=True
        )
        
    with gr.Row():
        file_download = gr.File(
            value=initial_file,
            label="Tải xuống File Log để gửi cho Gemini (.txt)", 
            interactive=False
        )
    
    # Tự động tải log gần nhất mỗi khi mở hoặc làm mới trang web (F5 / tải lại)
    demo.load(fn=get_latest_log_data, inputs=None, outputs=[output_box, file_download])

    # Connect run button
    run_btn.click(fn=run_binac4, inputs=None, outputs=[output_box, file_download])
    
    # Javascript fallback for copying on HTTP (non-secure) connections
    js_copy_text = """
    (text) => {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text);
            alert('Đã copy kết quả!');
        } else {
            let textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            document.execCommand('copy');
            textArea.remove();
            alert('Đã copy kết quả (Chế độ tương thích)!');
        }
    }
    """
    
    # Connect copy button using Javascript
    copy_btn.click(
        fn=None,
        inputs=[output_box],
        outputs=[],
        js=js_copy_text
    )
    


if __name__ == "__main__":
    demo.queue() # Enable queuing for streaming outputs
    # Mặc định share=False để loại bỏ triệt để các tiến trình frpc zombie gây ngốn RAM trên VPS.
    # Muốn bật public link, hãy thêm GRADIO_SHARE=true trong file .env hoặc khi chạy script.
    share_mode = os.getenv("GRADIO_SHARE", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("GRADIO_PORT", 7860))
    print(f"🚀 BinaC4 Web Server đang khởi chạy trên cổng {port} (share={share_mode})...")
    demo.launch(server_name="0.0.0.0", server_port=port, share=share_mode, inbrowser=False)

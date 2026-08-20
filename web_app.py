import gradio as gr
import subprocess
import sys
import os

def run_binac4():
    """Executes main.py and yields its output line by line to the Gradio UI."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')
    log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'BinaC4_latest_log.txt')
    
    # Prepare environment variables to force UTF-8 encoding for prints
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    # Clear previous log file
    with open(log_file_path, 'w', encoding='utf-8') as f:
        f.write("")
        
    process = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Redirect stderr to stdout
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=env,
        encoding='utf-8' # Ensure we read it as utf-8
    )
    
    output_str = ""
    yield "Đang khởi động BinaC4...\n" + "="*50 + "\n", None
    
    # Read output line by line as it is generated
    for line in iter(process.stdout.readline, ''):
        # Lọc bớt các dòng không quan trọng để kết quả ngắn gọn hơn
        if "✅ AN TOÀN" in line:
            continue
            
        output_str += line
        
        # Ghi vào file log
        with open(log_file_path, 'a', encoding='utf-8') as f:
            f.write(line)
            
        yield output_str, None
        
    process.stdout.close()
    return_code = process.wait()
    
    if return_code != 0:
        output_str += f"\n\n[ERROR] Quá trình kết thúc với mã lỗi {return_code}"
    else:
        output_str += f"\n\n[SUCCESS] Hoàn tất quá trình quét."
    
    with open(log_file_path, 'a', encoding='utf-8') as f:
        f.write(f"\nReturn code: {return_code}")
        
    yield output_str, log_file_path

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
    gr.Markdown("# 🚀 BinaC4 Trading Bot Web Interface")
    gr.Markdown("Giao diện này cho phép bạn kích hoạt bot `main.py` và theo dõi quá trình quét thị trường trực tiếp.")
    
    with gr.Row():
        run_btn = gr.Button("▶️ Thực thi Bot (Run main.py)", variant="primary", size="lg")
    
    with gr.Row():
        copy_btn = gr.Button("📋 Copy Kết Quả", size="sm")
        share_btn = gr.Button("🔗 Copy Link Trang Web (Chia Sẻ)", size="sm")
    
    with gr.Row():
        output_box = gr.Textbox(
            label="Terminal Output / Logs", 
            lines=30, 
            max_lines=50, 
            interactive=False,
            elem_id="terminal-output"
        )
        
    with gr.Row():
        file_download = gr.File(label="Tải xuống File Log để gửi cho Gemini (.txt)", interactive=False)
    
    # Connect run button to the generator function
    run_btn.click(fn=run_binac4, inputs=None, outputs=[output_box, file_download])
    
    # Connect copy button using Javascript
    copy_btn.click(
        fn=None,
        inputs=[output_box],
        outputs=[],
        js="(text) => { navigator.clipboard.writeText(text); alert('Đã copy kết quả vào khay nhớ tạm!'); }"
    )
    
    # Connect share button using Javascript
    share_btn.click(
        fn=None,
        inputs=[],
        outputs=[],
        js="() => { navigator.clipboard.writeText(window.location.href); alert('Đã copy link trang web! Bạn có thể dán (Paste) để gửi cho người khác.'); }"
    )

if __name__ == "__main__":
    # Launch with share=True to attempt generating a public link via Gradio (if server is up)
    demo.queue() # Enable queuing for streaming outputs
    demo.launch(share=True, inbrowser=False, theme=gr.themes.Monochrome())

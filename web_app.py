import gradio as gr
import subprocess
import sys
import os

def run_binac4():
    """Executes main.py and yields its output line by line to the Gradio UI."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')
    html_log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'BinaC4_latest_log.html')
    
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

    # Clear previous HTML log file and write header
    with open(html_log_file_path, 'w', encoding='utf-8') as f:
        f.write(html_header)
        
    process = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=env,
        encoding='utf-8'
    )
    
    output_str = ""
    yield "Đang khởi động BinaC4...\n" + "="*50 + "\n", None
    
    # Read output line by line as it is generated
    for line in iter(process.stdout.readline, ''):
        if "✅ AN TOÀN" in line:
            continue
            
        output_str += line
        
        # Append line to HTML file (escape < and > just in case)
        safe_line = line.replace('<', '&lt;').replace('>', '&gt;')
        with open(html_log_file_path, 'a', encoding='utf-8') as f:
            f.write(safe_line)
            
        yield output_str, None
        
    process.stdout.close()
    return_code = process.wait()
    
    if return_code != 0:
        output_str += f"\n\n[ERROR] Quá trình kết thúc với mã lỗi {return_code}"
    else:
        output_str += f"\n\n[SUCCESS] Hoàn tất quá trình quét."
    
    # Write footer to HTML file
    with open(html_log_file_path, 'a', encoding='utf-8') as f:
        f.write(f"\nReturn code: {return_code}")
        f.write(html_footer)
        
    yield output_str, html_log_file_path

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
        summary_copy_btn = gr.Button("✂️ Copy Tóm Tắt (Gửi Điện Thoại)", size="sm")
    
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
    
    js_copy_summary = """
    (text) => {
        let lines = text.split('\\n');
        let summaryLines = [];
        let captureMode = '';
        
        for (let i = 0; i < lines.length; i++) {
            let line = lines[i].trim();
            if (!line) continue;
            
            if (line.includes('⏰ Thời điểm cập nhật')) {
                summaryLines.push(line);
                continue;
            }
            if (line.includes('ĐỘNG CƠ 1: DARVAS GRID')) {
                captureMode = 'DARVAS';
                summaryLines.push('');
                summaryLines.push('📦 ĐỘNG CƠ 1: DARVAS GRID');
                summaryLines.push('Mã | Điểm | Trạng Thái | Hành Động');
                continue;
            }
            if (line.includes('ĐỘNG CƠ 2 - TÌM LỆNH THỰC THI CHÍNH XÁC')) {
                captureMode = 'SNIPER';
                summaryLines.push('');
                summaryLines.push('🎯 ĐỘNG CƠ 2: PULLBACK SNIPER');
                summaryLines.push('Mã | Điểm | Trạng Thái | Hành Động');
                continue;
            }
            if (line.includes('ĐỘNG CƠ 3 - SĂN BỨT PHÁ ĐỘNG LƯỢNG')) {
                captureMode = 'BREAKOUT';
                summaryLines.push('');
                summaryLines.push('🚀 ĐỘNG CƠ 3: MOMENTUM BREAKOUT');
                summaryLines.push('Mã | Điểm | Hành Động');
                continue;
            }
            if (line.includes('ĐỘNG CƠ 4 - SĂN ĐIỂM VÀO LỆNH PULLBACK')) {
                captureMode = 'HOT_TREND';
                summaryLines.push('');
                summaryLines.push('🔥 ĐỘNG CƠ 4: HOT TREND PULLBACK');
                summaryLines.push('Mã | Điểm | Hành Động');
                continue;
            }
            
            if (line.startsWith('====') || line.startsWith('----') && captureMode) {
                continue;
            }
            if (line.includes('KẾT QUẢ PHÂN TÍCH THỊ TRƯỜNG')) {
                captureMode = '';
                continue;
            }
            
            if (captureMode === 'DARVAS') {
                if (line.includes('|') && !line.includes('Mã (Symbol)')) {
                    let parts = line.split('|').map(s => s.trim());
                    if (parts.length >= 4) {
                        summaryLines.push(`${parts[0]} | ${parts[1]} | ${parts[2].replace(/✅ |⚠️ /g, '')} | ${parts[3]}`);
                    }
                }
            } else if (captureMode === 'SNIPER') {
                if (line.includes('|') && !line.includes('Mã (Symbol)')) {
                    let parts = line.split('|').map(s => s.trim());
                    if (parts.length >= 8) {
                        summaryLines.push(`${parts[0]} | ${parts[1]} | ${parts[2].replace(/✅ |⚠️ /g, '')} | ${parts[7]}`);
                    }
                } else if (line.includes('↳ ⚙️ SETUP:')) {
                    summaryLines.push('  ' + line.replace('↳ ⚙️ SETUP:', '↳').replace(/\\s+/g, ' '));
                }
            } else if (captureMode === 'BREAKOUT') {
                if (line.includes('|') && !line.includes('Mã (Symbol)')) {
                    let parts = line.split('|').map(s => s.trim());
                    if (parts.length >= 6) {
                        let hanhDong = '';
                        if (i + 1 < lines.length && lines[i+1].includes('↳ Hành Động:')) {
                            hanhDong = lines[i+1].split('↳ Hành Động:')[1].trim();
                        }
                        summaryLines.push(`${parts[0]} | ${parts[1]} | ${hanhDong}`);
                    }
                }
            } else if (captureMode === 'HOT_TREND') {
                if (line.includes('|') && !line.includes('Mã (Symbol)')) {
                    let parts = line.split('|').map(s => s.trim());
                    if (parts.length >= 8) {
                        // Col 0: Mã, Col 1: Điểm, Col 7: Hành Động
                        let hanhDong = parts[7].replace(/✅ |⚠️ |🟡 |🛑 |🚀 |⏳ /g, '');
                        summaryLines.push(`${parts[0]} | ${parts[1]} | ${hanhDong}`);
                    }
                } else if (line.includes('↳ ⚙️ SETUP:')) {
                    summaryLines.push('  ' + line.replace('↳ ⚙️ SETUP:', '↳').replace(/\\s+/g, ' '));
                }
            }
        }
        
        let summaryText = summaryLines.join('\\n');
        
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(summaryText.trim());
            alert('Đã copy Tóm Tắt Ngắn Gọn!');
        } else {
            let textArea = document.createElement("textarea");
            textArea.value = summaryText.trim();
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            document.execCommand('copy');
            textArea.remove();
            alert('Đã copy Tóm Tắt Ngắn Gọn bằng chế độ tương thích!');
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
    
    # Connect summary copy button using Javascript
    summary_copy_btn.click(
        fn=None,
        inputs=[output_box],
        outputs=[],
        js=js_copy_summary
    )

if __name__ == "__main__":
    # Launch with share=True to attempt generating a public link via Gradio
    # Bind to 0.0.0.0 so it can be accessed directly via VPS IP
    demo.queue() # Enable queuing for streaming outputs
    demo.launch(server_name="0.0.0.0", share=True, inbrowser=False)

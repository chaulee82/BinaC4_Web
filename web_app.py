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
    
    # Javascript fallback for copying summary text (filtering important lines)
    js_copy_summary = """
    (text) => {
        // Dời bảng Rebalance xuống cuối
        let warningIndex = text.indexOf("🚨 HỆ THỐNG CẢNH BÁO SỚM");
        let rebalanceIndex = text.indexOf("🏆 BẢNG CHẤM ĐIỂM REBALANCE");

        if (rebalanceIndex !== -1 && warningIndex !== -1 && rebalanceIndex < warningIndex) {
            let startOfRebalance = text.lastIndexOf("=", rebalanceIndex);
            startOfRebalance = text.lastIndexOf("\\n", startOfRebalance) + 1;
            if (startOfRebalance === -1 || startOfRebalance === 0) startOfRebalance = text.lastIndexOf("=", rebalanceIndex - 1) !== -1 ? text.lastIndexOf("\\n", text.lastIndexOf("=", rebalanceIndex - 1)) + 1 : 0;
            
            let startOfWarning = text.lastIndexOf("!", warningIndex);
            startOfWarning = text.lastIndexOf("\\n", startOfWarning) + 1;
            
            let part1 = text.substring(0, startOfRebalance); 
            let part2 = text.substring(startOfRebalance, startOfWarning); // Bảng Rebalance
            let part3 = text.substring(startOfWarning); // Các bảng còn lại
            
            // Tách dòng thời gian để đưa lên đầu
            let timeMatch = part2.match(/⏰ Thời điểm cập nhật.*?\\n/);
            if (timeMatch) {
                part1 = timeMatch[0] + "\\n" + part1;
            }
            
            text = part1 + part3 + "\\n" + part2;
        }

        // Tối ưu hóa các đường kẻ dài để tiết kiệm không gian trên màn hình điện thoại
        let summaryText = text.replace(/={50,}/g, '==================================================');
        summaryText = summaryText.replace(/-{50,}/g, '--------------------------------------------------');
        summaryText = summaryText.replace(/!{50,}/g, '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!');
        
        // Xóa bớt dòng trống thừa
        summaryText = summaryText.replace(/\\n{3,}/g, '\\n\\n');
        
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(summaryText.trim());
            alert('Đã copy Tóm Tắt (Đã chuyển Rebalance xuống cuối)!');
        } else {
            let textArea = document.createElement("textarea");
            textArea.value = summaryText.trim();
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            document.execCommand('copy');
            textArea.remove();
            alert('Đã copy Tóm Tắt bằng chế độ tương thích!');
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

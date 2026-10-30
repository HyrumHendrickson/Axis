# Troubleshooting

Find the symptom in the left column. If nothing here helps, please [open an issue](https://github.com/HyrumHendrickson/Axis/issues/new/choose) with the details the bug report form asks for.

| Symptom | Fix |
|---------|-----|
| `axis` or `pipx` not found | See [Fixing PATH problems](installation.md#fixing-path-problems). Open a new terminal first. |
| `py` not recognized (Windows) | Reinstall Python with **Add python.exe to PATH** checked, or use `python` instead of `py`. |
| `ollama` not recognized | Open a new terminal after installing. On Windows, confirm Ollama appears in the system tray. |
| Status: **No model server** | Open **Models**: click **Start Ollama**, or **Start** a llama.cpp model. For a server on a non-default port, start Axis with `--url`. |
| Status: **No model is on** | Open **Models** and click **Start** on a model. |
| Install: "No model named ... was found" (Ollama) | Copy the exact name from ollama.com/library, including the part after the colon. |
| Install: "not found, or it is private or gated" (Hugging Face) | Check the spelling (`owner/name`). For a gated model, accept its terms on its page, set `HF_TOKEN`, and restart Axis. |
| Install: "no GGUF files" | llama.cpp needs GGUF. Search Hugging Face for the model's GGUF version (often `...-GGUF`), or install it with Ollama. |
| Install: "Not enough disk space" | Free space on the drive holding `Axis/models`, or set `AXIS_HOME` to another drive. |
| Start (llama.cpp): "did not fit in memory" | Choose a smaller model or quantization, lower **Context size** under Engine settings, or close other programs. |
| Start (llama.cpp): "llama-server was not found" | Click **Install llama.cpp** on the Models page, or set its path under **Engine settings**. |
| Installing llama.cpp or stable-diffusion.cpp fails: "does not run on this computer" | Choose another build in the menu next to **Install** (CPU always works), or update your GPU driver. |
| Installing an engine fails on Windows: "Visual C++ runtime" | Install <https://aka.ms/vs/17/release/vc_redist.x64.exe> and click **Install** again. |
| Install by name: "no longer has ..." | The file was renamed on Hugging Face. Install the parts with **Install one file from Hugging Face instead**, or update Axis. |
| Start (image): "sd-server was not found" | Update stable-diffusion.cpp (recent releases include `sd-server`), or set the sd-server path under Image models. |
| Start (image): extra arguments not accepted | Edit the model and remove options only `sd-cli` understands. |
| Model shows "Started by an earlier Axis session" | Axis was closed without stopping it. Click **Stop** if you do not need it. |
| Page says **Axis has stopped** | The terminal running `axis` was closed. Run `axis` again and reload the page. |
| Browser did not open | Open the address printed in the terminal (normally `http://127.0.0.1:8765`). |
| Axis opens in a tab, not its own window | No Chrome, Edge, Brave or Chromium was found. Install one, or set `AXIS_BROWSER` to a Chromium-based browser. |
| Axis stopped when I closed its window | That is intended in 4.0. To keep Axis running without a window, start it with `axis --tab` and close the tab instead. |
| "This agent prefers X, which is not on" | Turn that model on, or clear the agent's preferred model in **Edit agent**. |
| Reply stops with an error | Your message is restored in the box. Check the status panel and send again. |
| Replies are very slow | Use a smaller model or close other programs to free memory. |
| First reply after loading a model is slow | Normal. Ollama reloads the model with the larger context Axis requests. |
| Agent forgets instructions mid-task | The conversation outgrew the model's context. Start a **New chat**, or raise `AXIS_NUM_CTX` (Ollama) or `-c` (llama.cpp). |
| Agent describes an action but nothing happens | Confirm the skill is ticked in the agent's settings and use a tool-capable model such as `llama3.1:8b`. Direct requests help: "Save this as notes.md". |
| `Unknown tool '...'` in a step | The agent tried an action whose skill is not enabled. Tick it in **Edit agent**, or ignore it. |
| Run Python reports a missing package | `pipx inject axis-agent-manager <package>` |
| `axis update` fails | Check your connection, or reinstall with the `pipx install --force` command in [Commands](configuration.md#commands). |
| Images: **Set up Stable Diffusion** | Install an image model by name on the Models page; Axis installs stable-diffusion.cpp with it. |
| Image model shows **Files missing** | A path is wrong or relative paths start in an unexpected folder. Use full paths, or set the **Working folder**. |
| Picture fails with `sd-cli failed` | Run the same command in a terminal. Out-of-memory errors usually need a smaller size, a smaller quantization, or `--offload-to-cpu` in Extra arguments. |
| OCR: "Tesseract is not installed" | Click **Install Tesseract** under **Models**, **OCR models**, or set its path under **OCR settings** on the **OCR** page. |
| OCR: "The Tesseract language ... is not installed" | Install it under **OCR models** (for example `lat`), then read the picture again. |
| Installing PyTorch: "This Python cannot make environments" | On Debian or Ubuntu run `sudo apt install python3-venv`, or set another Python under **OCR settings** on the **OCR** page. |
| Installing PyTorch: "pip found no PyTorch for this Python" | PyTorch supports recent 64-bit Python versions only. Set another Python under **OCR settings** on the **OCR** page and install the PyTorch environment again. |
| A PyTorch OCR model runs out of memory | Set **Device** to **CPU only** under **OCR settings** on the **OCR** page, or stop other models. |
| Chats disappeared | Use the same `AXIS_HOME`. Chats written by very old versions were stored per browser and port; use the same browser and default port once to migrate them. |

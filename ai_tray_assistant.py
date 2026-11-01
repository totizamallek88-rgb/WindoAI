"""
AI Tray Assistant
=================
• Slides up from taskbar, flush right edge, full height
• Drag title bar to move, resize handles on all edges/corners
• Position + size saved in config, restored on next launch
• Gemini streaming (text appears word-by-word, near-instant)
• Config → Documents/Better_AI_windows/config.json

Requirements:
    pip install pystray pillow google-genai openai
"""

import sys, json, ctypes, queue, shutil, threading, subprocess
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from datetime import datetime

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
except ImportError:
    print("Run:  pip install pystray pillow"); sys.exit(1)

try:
    from google import genai as genai_new
    from google.genai import types as genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
APP_DIR     = Path.home() / "Documents" / "Better_AI_windows"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"

ANIM_STEPS = 20
ANIM_MS    = 7
MIN_W      = 320
MIN_H      = 400
RESIZE_HIT = 7          # px hit zone for resize handles
PH_TEXT    = "Type a message..."

DEFAULT_CONFIG = {
    "provider":  "gemini",
    "api_key":   "",
    "model":     "gemini-2.0-flash",
    "work_dir":  str(Path.home()),
    "theme":     "dark",
    # layout — saved automatically on move/resize
    "panel_x":   None,
    "panel_y":   None,
    "panel_w":   440,
    "panel_h":   None,   # None = full work area height
}

def _work_area():
    """Return (width, height, work_width, work_height, taskbar_height) of primary monitor."""
    try:
        r = (ctypes.c_long * 4)()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, r, 0)
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        work_w = r[2] - r[0]
        work_h = r[3] - r[1]
        tb_h   = sh - work_h
        return sw, sh, work_w, work_h, tb_h
    except Exception:
        return 1920, 1080, 1920, 1032, 48

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRAY ICON
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def make_icon(size=64):
    img = Image.new("RGBA", (size, size), (0,0,0,0))
    d   = ImageDraw.Draw(img)
    d.ellipse([1,1,size-2,size-2], fill=(18,18,30,255))
    cx, cy = size//2, size//2
    d.line([cx-15,cy+13, cx-2,cy-13],  fill=(90,170,255), width=4)
    d.line([cx-2, cy-13, cx+11,cy+13], fill=(90,170,255), width=4)
    d.line([cx-9, cy+3,  cx+4, cy+3],  fill=(90,170,255), width=3)
    d.line([cx+20,cy-13, cx+20,cy+13], fill=(60,220,160), width=4)
    return img

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILESYSTEM TOOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class FSTools:
    @staticmethod
    def read_file(p):
        try:    return Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def write_file(p, content):
        try:
            pp = Path(p); pp.parent.mkdir(parents=True, exist_ok=True)
            pp.write_text(content, encoding="utf-8")
            return f"OK: {pp.stat().st_size} bytes → {p}"
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def list_dir(p):
        try:
            out = []
            for e in sorted(Path(p).iterdir()):
                t = "📁" if e.is_dir() else "📄"
                s = "" if e.is_dir() else f"  {e.stat().st_size:>10} B"
                out.append(f"{t} {e.name}{s}")
            return "\n".join(out) or "(empty)"
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def create_dir(p):
        try:    Path(p).mkdir(parents=True, exist_ok=True); return f"OK: {p}"
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def delete_path(p):
        try:
            pp = Path(p)
            shutil.rmtree(pp) if pp.is_dir() else pp.unlink()
            return f"OK: deleted {p}"
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def run_command(cmd, cwd=None):
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd,
                               capture_output=True, text=True, timeout=30)
            return (r.stdout+r.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired: return "ERROR: timed out"
        except Exception as e: return f"ERROR: {e}"
    @staticmethod
    def search_files(root, pattern):
        try:
            m = list(Path(root).rglob(pattern))[:60]
            return "\n".join(str(x) for x in m) or "(no matches)"
        except Exception as e: return f"ERROR: {e}"

def dispatch_tool(name, args, wd):
    fs  = FSTools()
    def r(p): return str(Path(wd)/p) if p and not Path(p).is_absolute() else p
    if name == "read_file":    return fs.read_file(r(args.get("path","")))
    if name == "write_file":   return fs.write_file(r(args.get("path","")), args.get("content",""))
    if name == "list_dir":     return fs.list_dir(r(args.get("path",wd)))
    if name == "create_dir":   return fs.create_dir(r(args.get("path","")))
    if name == "delete_path":  return fs.delete_path(r(args.get("path","")))
    if name == "run_command":  return fs.run_command(args.get("cmd",""), cwd=args.get("cwd") or wd)
    if name == "search_files": return fs.search_files(r(args.get("root",wd)), args.get("pattern","*"))
    return f"ERROR: unknown tool '{name}'"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI BACKENDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SYS = ("You are an AI assistant with full access to the user's filesystem and shell.\n"
       "You can read, write, create, delete files/dirs, and run shell commands.\n"
       "Ask before destructive operations. Be concise.\n"
       "Working dir: {wd}\nTime: {now}\n")

_DECLS = [
    ("read_file",    "Read a file.",                      {"path":"STRING"},                    ["path"]),
    ("write_file",   "Write/overwrite a file.",           {"path":"STRING","content":"STRING"}, ["path","content"]),
    ("list_dir",     "List a directory.",                  {"path":"STRING"},                    ["path"]),
    ("create_dir",   "Create a directory.",                {"path":"STRING"},                    ["path"]),
    ("delete_path",  "Delete a file or directory.",        {"path":"STRING"},                    ["path"]),
    ("run_command",  "Run a shell command.",               {"cmd":"STRING","cwd":"STRING"},      ["cmd"]),
    ("search_files", "Glob-search files.",                 {"root":"STRING","pattern":"STRING"}, ["root","pattern"]),
]

GEMINI_DECLS = [
    genai_types.FunctionDeclaration(
        name=n, description=d,
        parameters=genai_types.Schema(
            type="OBJECT",
            properties={k: genai_types.Schema(type=v) for k,v in p.items()},
            required=req
        )
    ) for n,d,p,req in _DECLS
] if HAS_GEMINI else []

OAI_TOOLS = [
    {"type":"function","function":{
        "name":n,"description":d,
        "parameters":{"type":"object",
                      "properties":{k:{"type":v.lower()} for k,v in p.items()},
                      "required":req}
    }} for n,d,p,req in _DECLS
]

def _sys_txt(wd):
    return SYS.format(wd=wd, now=datetime.now().strftime("%Y-%m-%d %H:%M"))

def ask_gemini_stream(api_key, model, history, wd, on_token, on_tool):
    """Stream tokens; on_token called with cumulative text each chunk."""
    if not HAS_GEMINI:
        return "ERROR: pip install google-genai"
    client  = genai_new.Client(api_key=api_key)
    contents = [
        genai_types.Content(
            role="user" if m["role"]=="user" else "model",
            parts=[genai_types.Part(text=m["content"])]
        ) for m in history
    ]
    cfg = genai_types.GenerateContentConfig(
        system_instruction=_sys_txt(wd),
        tools=[genai_types.Tool(function_declarations=GEMINI_DECLS)],
    )
    for _ in range(12):
        text_buf   = ""
        fn_calls_buf = []
        cand_content_obj = None
        try:
            for chunk in client.models.generate_content_stream(
                    model=model, contents=contents, config=cfg):
                cand = chunk.candidates[0] if chunk.candidates else None
                if not cand:
                    continue
                if cand.content:
                    cand_content_obj = cand.content
                for part in cand.content.parts:
                    if part.text:
                        text_buf += part.text
                        on_token(text_buf)
                    elif part.function_call and part.function_call.name:
                        fn_calls_buf.append(part.function_call)
        except Exception:
            resp = client.models.generate_content(
                model=model, contents=contents, config=cfg)
            cand = resp.candidates[0]
            cand_content_obj = cand.content
            fn_calls_buf = [p.function_call for p in cand.content.parts
                            if p.function_call and p.function_call.name]
            if not fn_calls_buf:
                t = resp.text or ""
                on_token(t)
                return t
            contents.append(cand_content_obj)
            parts = []
            for fc in fn_calls_buf:
                on_tool(fc.name, dict(fc.args))
                res = dispatch_tool(fc.name, dict(fc.args), wd)
                parts.append(genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        name=fc.name, response={"result": res})))
            contents.append(genai_types.Content(role="user", parts=parts))
            continue

        if not fn_calls_buf:
            return text_buf

        # Append the raw candidate content object containing the thought_signature
        if cand_content_obj:
            contents.append(cand_content_obj)
        else:
            model_parts = []
            if text_buf:
                model_parts.append(genai_types.Part(text=text_buf))
            for fc in fn_calls_buf:
                model_parts.append(genai_types.Part(function_call=fc))
            contents.append(genai_types.Content(role="model", parts=model_parts))

        fn_parts = []
        for fc in fn_calls_buf:
            on_tool(fc.name, dict(fc.args))
            res = dispatch_tool(fc.name, dict(fc.args), wd)
            fn_parts.append(genai_types.Part(
                function_response=genai_types.FunctionResponse(
                    name=fc.name, response={"result": res})))
        contents.append(genai_types.Content(role="user", parts=fn_parts))

    return "ERROR: loop limit"

def ask_openai(api_key, model, history, wd, on_token, on_tool):
    if not HAS_OPENAI:
        return "ERROR: pip install openai"
    import json as _j
    client   = OpenAI(api_key=api_key)
    messages = [{"role":"system","content":_sys_txt(wd)}] + history
    for _ in range(12):
        text_buf   = ""
        tool_calls = {}
        with client.chat.completions.create(
                model=model, messages=messages,
                tools=OAI_TOOLS, tool_choice="auto", stream=True) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                if delta.content:
                    text_buf += delta.content
                    on_token(text_buf)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls:
                            tool_calls[idx] = {"id": tc.id or "",
                                               "name": tc.function.name or "",
                                               "args": ""}
                        if tc.function.arguments:
                            tool_calls[idx]["args"] += tc.function.arguments
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function.name:
                            tool_calls[idx]["name"] = tc.function.name

        if not tool_calls:
            return text_buf

        from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
        from openai.types.chat.chat_completion_message_tool_call import Function
        tcs = [ChatCompletionMessageToolCall(
                   id=v["id"], type="function",
                   function=Function(name=v["name"], arguments=v["args"]))
               for v in tool_calls.values()]
        messages.append(ChatCompletionMessage(role="assistant",
                                              content=text_buf or None,
                                              tool_calls=tcs))
        for tc in tcs:
            args = _j.loads(tc.function.arguments)
            on_tool(tc.function.name, args)
            res = dispatch_tool(tc.function.name, args, wd)
            messages.append({"role":"tool","tool_call_id":tc.id,"content":res})
    return "ERROR: loop limit"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# THEMES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THEMES = {
    "dark": {
        "bg":          "#0b0b12",
        "topbar":      "#0f0f1a",
        "sidebar":     "#10101a",
        "input_bg":    "#181826",
        "border":      "#252538",
        "text":        "#dde2f2",
        "text_dim":    "#6a6a95",
        "accent":      "#4f7ef8",
        "accent2":     "#34c98a",
        "user_bubble": "#171f3c",
        "user_label":  "#4f7ef8",
        "ai_bubble":   "#111824",
        "ai_label":    "#34c98a",
        "tool_bubble": "#0e1a16",
        "tool_label":  "#2db87a",
        "error":       "#f05555",
        "send_btn":    "#4f7ef8",
        "send_fg":     "#ffffff",
        "divider":     "#1e1e30",
        "thinking":    "#888ab0",
        "resize_bg":   "#1a1a2c",
    },
    "light": {
        "bg":          "#f2f4fb",
        "topbar":      "#e8eaf6",
        "sidebar":     "#eaecf7",
        "input_bg":    "#ffffff",
        "border":      "#d0d4ea",
        "text":        "#1a1a2e",
        "text_dim":    "#5c5c80",
        "accent":      "#2c5ee8",
        "accent2":     "#1a9a68",
        "user_bubble": "#dde5ff",
        "user_label":  "#2c5ee8",
        "ai_bubble":   "#f6f8ff",
        "ai_label":    "#1a9a68",
        "tool_bubble": "#e8f7f0",
        "tool_label":  "#1a9a68",
        "error":       "#cc2020",
        "send_btn":    "#2c5ee8",
        "send_fg":     "#ffffff",
        "divider":     "#dde0f0",
        "thinking":    "#8888a0",
        "resize_bg":   "#dde0f0",
    }
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESIZE MIXIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ResizableMixin:
    def _init_resize(self, win, on_geometry_changed=None):
        self._rwin        = win
        self._on_geom_cb  = on_geometry_changed
        self._resize_edge = None
        self._drag_start  = None
        win.bind("<Motion>",           self._resize_motion,  "+")
        win.bind("<ButtonPress-1>",    self._resize_press,   "+")
        win.bind("<B1-Motion>",        self._resize_drag,    "+")
        win.bind("<ButtonRelease-1>",  self._resize_release, "+")

    def _edge_at(self, x, y):
        w = self._rwin.winfo_width()
        h = self._rwin.winfo_height()
        H = RESIZE_HIT
        l = x < H; r = x > w - H; t = y < H; b = y > h - H
        if t and l:  return "nw"
        if t and r:  return "ne"
        if b and l:  return "sw"
        if b and r:  return "se"
        if t:        return "n"
        if b:        return "s"
        if l:        return "w"
        if r:        return "e"
        return None

    _CURSORS = {"n":"top_side","s":"bottom_side","e":"right_side","w":"left_side",
                "ne":"top_right_corner","nw":"top_left_corner",
                "se":"bottom_right_corner","sw":"bottom_left_corner"}

    def _resize_motion(self, e):
        edge = self._edge_at(e.x, e.y)
        self._rwin.configure(cursor=self._CURSORS.get(edge, ""))

    def _resize_press(self, e):
        self._resize_edge = self._edge_at(e.x, e.y)
        if self._resize_edge:
            geo = self._rwin.geometry().split("+")
            wh  = geo[0].split("x")
            self._drag_start = (
                e.x_root, e.y_root,
                int(geo[1]), int(geo[2]),
                int(wh[0]), int(wh[1])
            )

    def _resize_drag(self, e):
        if not self._resize_edge or not self._drag_start:
            return
        mx, my, ox, oy, ow, oh = self._drag_start
        dx = e.x_root - mx
        dy = e.y_root - my
        edge = self._resize_edge

        nx, ny, nw, nh = ox, oy, ow, oh
        if "e" in edge: nw = max(MIN_W, ow + dx)
        if "s" in edge: nh = max(MIN_H, oh + dy)
        if "w" in edge:
            nw = max(MIN_W, ow - dx)
            nx = ox + (ow - nw)
        if "n" in edge:
            nh = max(MIN_H, oh - dy)
            ny = oy + (oh - nh)

        # Constrain bottom edge to not exceed work_h
        if ny + nh > self._work_h:
            nh = self._work_h - ny

        self._rwin.geometry(f"{nw}x{nh}+{nx}+{ny}")
        if self._on_geom_cb:
            self._on_geom_cb(nx, ny, nw, nh)

    def _resize_release(self, e):
        self._resize_edge = None
        self._drag_start  = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN APP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AIPanelApp(ResizableMixin):
    def __init__(self):
        self.cfg           = load_config()
        self.history       = []
        self.busy          = False
        self.panel_visible = False
        self._anim_job     = None
        self._think_job    = None
        self._think_dots   = 0
        self._drag_win     = None
        self.has_placeholder = True

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._screen_w, self._screen_h, self._work_w, self._work_h, self._tb_h = _work_area()
        self._resolve_layout()
        self._build_panel()
        self._start_tray()

        self._ui_queue = queue.Queue()
        self.root.after(30, self._drain_ui_queue)

    def _resolve_layout(self):
        sw, wh = self._work_w, self._work_h
        w  = self.cfg.get("panel_w") or 440
        h  = self.cfg.get("panel_h") or wh
        px = self.cfg.get("panel_x")
        py = self.cfg.get("panel_y")
        
        if px is None: px = sw - w
        if py is None: py = 0
        
        w  = max(MIN_W, min(w,  sw))
        h  = max(MIN_H, min(h,  wh))
        px = max(0,     min(px, sw - w))
        py = max(0,     min(py, wh - MIN_H))
        
        # Ensure panel height doesn't push past work area
        if py + h > wh:
            h = wh - py

        self._pw  = w;  self._ph  = h
        self._px  = px; self._py  = py
        self._py_hide = self._screen_h  # Hide below visible screen bounds

    def _save_layout(self, x=None, y=None, w=None, h=None):
        self._px = x if x is not None else self._px
        self._py = y if y is not None else self._py
        self._pw = w if w is not None else self._pw
        self._ph = h if h is not None else self._ph
        self.cfg.update(panel_x=self._px, panel_y=self._py,
                        panel_w=self._pw, panel_h=self._ph)
        save_config(self.cfg)
        self._update_wrap()

    def _update_wrap(self):
        wl = self._pw - 48
        def walk(widget):
            try:
                widget.configure(wraplength=wl)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                walk(child)
        walk(self._chat_inner)

    def _build_panel(self):
        c = THEMES[self.cfg.get("theme","dark")]
        self._c = c

        self.panel = tk.Toplevel(self.root)
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.configure(bg=c["bg"])
        self.panel.geometry(
            f"{self._pw}x{self._ph}+{self._px}+{self._py_hide}"
        )
        self.panel.withdraw()
        self._build_ui(c)
        self._init_resize(self.panel, on_geometry_changed=self._save_layout)

    def _build_ui(self, c):
        p = self.panel

        topbar = tk.Frame(p, bg=c["topbar"], height=54)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        left = tk.Frame(topbar, bg=c["topbar"])
        left.pack(side="left", padx=14, fill="y")
        tk.Label(left, text="◈", bg=c["topbar"], fg=c["accent"],
                 font=("Segoe UI",16)).pack(side="left")
        col = tk.Frame(left, bg=c["topbar"])
        col.pack(side="left", padx=(6,0), fill="y")
        tk.Label(col, text="AI Assistant", bg=c["topbar"], fg=c["text"],
                 font=("Segoe UI",12,"bold")).pack(anchor="w")
        self._badge_var = tk.StringVar(value=self._badge_text())
        tk.Label(col, textvariable=self._badge_var,
                 bg=c["topbar"], fg=c["accent2"],
                 font=("Segoe UI",8)).pack(anchor="w")

        topbar.bind("<ButtonPress-1>",   self._drag_press)
        topbar.bind("<B1-Motion>",       self._drag_move)
        topbar.bind("<ButtonRelease-1>", self._drag_end)
        left.bind("<ButtonPress-1>",   self._drag_press)
        left.bind("<B1-Motion>",       self._drag_move)
        left.bind("<ButtonRelease-1>", self._drag_end)
        col.bind("<ButtonPress-1>",   self._drag_press)
        col.bind("<B1-Motion>",       self._drag_move)
        col.bind("<ButtonRelease-1>", self._drag_end)

        right = tk.Frame(topbar, bg=c["topbar"])
        right.pack(side="right", padx=8, fill="y")
        for txt, fn, hov in [
            ("✕", self.hide_panel,      c["error"]),
            ("⚙", self._open_settings,  c["accent"]),
            ("🗑", self._clear_chat,    c["text_dim"]),
        ]:
            b = tk.Label(right, text=txt, bg=c["topbar"], fg=c["text_dim"],
                         font=("Segoe UI",13), cursor="hand2", padx=6, pady=4)
            b.pack(side="right")
            b.bind("<Enter>",     lambda e, w=b, h=hov: w.configure(fg=h))
            b.bind("<Leave>",     lambda e, w=b:        w.configure(fg=c["text_dim"]))
            b.bind("<Button-1>",  lambda e, f=fn:       f())

        tk.Frame(p, bg=c["divider"], height=1).pack(fill="x")

        chat_wrap = tk.Frame(p, bg=c["bg"])
        chat_wrap.pack(fill="both", expand=True)

        self.chat_canvas = tk.Canvas(chat_wrap, bg=c["bg"],
                                     highlightthickness=0, bd=0)
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        vsb = tk.Scrollbar(chat_wrap, orient="vertical",
                           command=self.chat_canvas.yview, width=5)
        vsb.pack(side="right", fill="y")
        self.chat_canvas.configure(yscrollcommand=vsb.set)

        self._chat_inner  = tk.Frame(self.chat_canvas, bg=c["bg"])
        self._chat_window = self.chat_canvas.create_window(
            (0,0), window=self._chat_inner, anchor="nw")
        self._chat_inner.bind("<Configure>",  self._on_chat_cfg)
        self.chat_canvas.bind("<Configure>",  self._on_canvas_resize)
        
        self.chat_canvas.bind("<MouseWheel>", self._on_wheel)
        self.chat_canvas.bind("<Button-4>",   self._on_wheel)
        self.chat_canvas.bind("<Button-5>",   self._on_wheel)
        
        self._welcome()

        tk.Frame(p, bg=c["divider"], height=1).pack(fill="x")
        bot = tk.Frame(p, bg=c["sidebar"], pady=10)
        bot.pack(fill="x", side="bottom")

        wd_row = tk.Frame(bot, bg=c["sidebar"])
        wd_row.pack(fill="x", padx=12, pady=(0,6))
        tk.Label(wd_row, text="📁", bg=c["sidebar"], fg=c["text_dim"],
                 font=("Segoe UI",9)).pack(side="left")
        self._wd_var = tk.StringVar(value=self.cfg["work_dir"])
        tk.Label(wd_row, textvariable=self._wd_var, bg=c["sidebar"],
                 fg=c["text_dim"], font=("Segoe UI",8),
                 wraplength=self._pw-90, justify="left").pack(side="left", padx=4)
        pick_btn = tk.Label(wd_row, text="…", bg=c["input_bg"], fg=c["text"],
                            font=("Segoe UI",9), cursor="hand2", padx=6, pady=1)
        pick_btn.pack(side="right")
        pick_btn.bind("<Button-1>", lambda e: self._pick_wd())

        inp_row = tk.Frame(bot, bg=c["sidebar"])
        inp_row.pack(fill="x", padx=12)

        wrap = tk.Frame(inp_row, bg=c["input_bg"],
                        highlightthickness=1,
                        highlightbackground=c["border"],
                        highlightcolor=c["accent"])
        wrap.pack(side="left", fill="x", expand=True)
        
        self.input_box = tk.Text(
            wrap, height=3, wrap="word",
            bg=c["input_bg"], fg=c["text"], insertbackground=c["text"],
            relief="flat", bd=0, font=("Segoe UI",10),
            padx=10, pady=8, undo=True
        )
        self.input_box.pack(fill="x")
        
        self.input_box.bind("<Return>",       self._on_enter)
        self.input_box.bind("<Shift-Return>", lambda e: None)
        self.input_box.bind("<FocusIn>",      self._on_focus_in)
        self.input_box.bind("<FocusOut>",     self._on_focus_out)
        self.input_box.bind("<KeyPress>",     self._on_key_press)
        
        self.input_box.bind("<MouseWheel>",   self._on_wheel)
        self.input_box.bind("<Button-4>",     self._on_wheel)
        self.input_box.bind("<Button-5>",     self._on_wheel)

        self._init_placeholder()

        self.send_btn = tk.Label(
            inp_row, text="↑", bg=c["send_btn"], fg=c["send_fg"],
            font=("Segoe UI",14,"bold"), cursor="hand2",
            width=3, pady=8, relief="flat"
        )
        self.send_btn.pack(side="right", padx=(6,0))
        self.send_btn.bind("<Button-1>", lambda e: self._send())

        self._think_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self._think_var,
                 bg=c["sidebar"], fg=c["thinking"],
                 font=("Segoe UI",8,"italic")).pack(anchor="w", padx=14)

    def _drag_press(self, e):
        if self._edge_at(e.x_root - self.panel.winfo_rootx(),
                         e.y_root - self.panel.winfo_rooty()):
            self._drag_win = None
            return
        self._drag_win = (e.x_root, e.y_root,
                          self.panel.winfo_x(), self.panel.winfo_y())

    def _drag_move(self, e):
        if not self._drag_win:
            return
        mx, my, ox, oy = self._drag_win
        nx = ox + (e.x_root - mx)
        ny = oy + (e.y_root - my)
        sw, wh = self._work_w, self._work_h
        nx = max(0, min(nx, sw - self._pw))
        ny = max(0, min(ny, wh - self._ph))
        self.panel.geometry(f"{self._pw}x{self._ph}+{nx}+{ny}")
        self._px, self._py = nx, ny

    def _drag_end(self, e):
        if self._drag_win:
            self._save_layout(x=self._px, y=self._py)
        self._drag_win = None

    def _init_placeholder(self):
        self.has_placeholder = True
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", PH_TEXT)
        self.input_box.configure(fg=self._c["text_dim"])

    def _on_key_press(self, e):
        if self.has_placeholder and e.keysym not in ('Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Caps_Lock', 'Tab'):
            self.input_box.delete("1.0", "end")
            self.input_box.configure(fg=self._c["text"])
            self.has_placeholder = False

    def _on_focus_in(self, e):
        if self.has_placeholder:
            self.input_box.delete("1.0", "end")
            self.input_box.configure(fg=self._c["text"])
            self.has_placeholder = False

    def _on_focus_out(self, e):
        content = self.input_box.get("1.0", "end-1c").strip()
        if not content:
            self._init_placeholder()

    def _get_clean_input_text(self):
        if self.has_placeholder:
            return ""
        return self.input_box.get("1.0", "end-1c").strip()

    def _welcome(self):
        c = self._c
        f = tk.Frame(self._chat_inner, bg=c["bg"], pady=24)
        f.pack(fill="x")
        tk.Label(f, text="◈", bg=c["bg"], fg=c["accent"],
                 font=("Segoe UI",30)).pack()
        tk.Label(f, text="AI Assistant", bg=c["bg"], fg=c["text"],
                 font=("Segoe UI",14,"bold")).pack()
        tk.Label(f, text="Ask me anything. I can read, write\nand manage files on your PC.",
                 bg=c["bg"], fg=c["text_dim"],
                 font=("Segoe UI",9), justify="center").pack(pady=(4,0))
        tk.Frame(self._chat_inner, bg=c["divider"], height=1).pack(
            fill="x", padx=20, pady=12)

    def _badge_text(self):
        p = self.cfg.get("provider","gemini").upper()
        m = self.cfg.get("model","")
        d = "●" if self.cfg.get("api_key") else "○"
        return f"{d} {p} · {m}"

    def _bubble(self, role, text, tag=None):
        c  = self._c
        wl = self._pw - 48
        if role == "user":
            bg, lbl, lc = c["user_bubble"], "You",      c["user_label"]
        elif role == "tool":
            bg, lbl, lc = c["tool_bubble"], f"⚙ {tag}", c["tool_label"]
        elif role == "error":
            bg, lbl, lc = c["ai_bubble"],  "Error",     c["error"]
        else:
            bg, lbl, lc = c["ai_bubble"],  "AI",        c["ai_label"]

        outer = tk.Frame(self._chat_inner, bg=c["bg"], pady=2)
        outer.pack(fill="x", padx=8)
        frame = tk.Frame(outer, bg=bg, padx=12, pady=8)
        frame.pack(fill="x")
        tk.Label(frame, text=lbl, bg=bg, fg=lc,
                 font=("Segoe UI",8,"bold")).pack(anchor="w", pady=(0,3))
        body = tk.Label(frame, text=text, bg=bg, fg=c["text"],
                        font=("Segoe UI",10), wraplength=wl,
                        justify="left", anchor="w")
        body.pack(fill="x", anchor="w")
        self.chat_canvas.update_idletasks()
        self.chat_canvas.yview_moveto(1.0)
        return body

    def _bubble_ai(self):
        return self._bubble("ai", "…")

    def _on_chat_cfg(self, e):
        self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self.chat_canvas.itemconfig(self._chat_window, width=e.width)

    def _on_wheel(self, e):
        if e.num == 4 or getattr(e, 'delta', 0) > 0:
            self.chat_canvas.yview_scroll(-1, "units")
        elif e.num == 5 or getattr(e, 'delta', 0) < 0:
            self.chat_canvas.yview_scroll(1, "units")

    def _clear_chat(self):
        self.history.clear()
        for w in self._chat_inner.winfo_children():
            w.destroy()
        self._welcome()

    def _on_enter(self, e):
        if not (e.state & 0x1):
            self._send(); return "break"

    def _send(self):
        if self.busy: return
        raw = self._get_clean_input_text()
        if not raw: return
        
        self._init_placeholder()

        if not self.cfg.get("api_key"):
            self._bubble("error","No API key — click ⚙"); return

        self._bubble("user", raw)
        self.history.append({"role":"user","content":raw})
        ai_lbl = self._bubble_ai()

        def on_token(text):
            self._queue(lambda: ai_lbl.configure(text=text))

        def on_tool(name, args):
            short = ", ".join(f"{k}={repr(v)[:35]}" for k,v in args.items())
            self._queue(lambda: self._bubble("tool", short, tag=name))

        def worker():
            err = None
            try:
                prov = self.cfg.get("provider","gemini")
                fn   = ask_gemini_stream if prov=="gemini" else ask_openai
                result = fn(self.cfg["api_key"], self.cfg["model"],
                            list(self.history), self.cfg["work_dir"],
                            on_token, on_tool)
                self.history.append({"role":"assistant","content":result})
            except Exception as ex:
                err = str(ex)
            finally:
                if err:
                    _e, _l, _col = err, ai_lbl, self._c["error"]
                    self._queue(lambda: _l.configure(text=f"Error: {_e}", fg=_col))
                
                def cleanup():
                    self._set_busy(False)
                    self.input_box.focus_set()
                self._queue(cleanup)

        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, b):
        self.busy = b
        self.send_btn.configure(
            bg=self._c["border"] if b else self._c["send_btn"])
        if b:
            self._tick()
        else:
            if self._think_job:
                self.root.after_cancel(self._think_job)
                self._think_job = None
            self._think_var.set("")

    def _tick(self):
        bars = ["▏","▎","▍","▌","▋","▊","▉","█","▉","▊","▋","▌","▍","▎","▏"]
        self._think_var.set("Thinking  " + bars[self._think_dots % len(bars)])
        self._think_dots += 1
        self._think_job = self.root.after(80, self._tick)

    def _queue(self, fn):
        self._ui_queue.put(fn)

    def _drain_ui_queue(self):
        try:
            while True: self._ui_queue.get_nowait()()
        except queue.Empty: pass
        self.root.after(30, self._drain_ui_queue)

    def toggle_panel(self):
        self.hide_panel() if self.panel_visible else self.show_panel()

    def show_panel(self):
        if self.panel_visible: return
        self.panel_visible = True
        self.panel.geometry(f"{self._pw}x{self._ph}+{self._px}+{self._py_hide}")
        self.panel.deiconify()
        self._animate(self._py_hide, self._py)

    def hide_panel(self):
        if not self.panel_visible: return
        self._animate(self._py, self._py_hide,
                      on_done=lambda: (self.panel.withdraw(),
                                       setattr(self,"panel_visible",False)))

    def _animate(self, start, end, on_done=None):
        if self._anim_job: self.root.after_cancel(self._anim_job)
        def ease(t): return 1-(1-t)**3
        frames = [int(start+(end-start)*ease(i/ANIM_STEPS))
                  for i in range(1, ANIM_STEPS+1)]
        def step(i=0):
            if i >= len(frames):
                if on_done: on_done()
                return
            self.panel.geometry(f"{self._pw}x{self._ph}+{self._px}+{frames[i]}")
            self._anim_job = self.root.after(ANIM_MS, step, i+1)
        step()

    def _open_settings(self):
        c = self._c
        win = tk.Toplevel(self.panel)
        win.title("Settings")
        win.configure(bg=c["bg"])
        win.geometry("430x530")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        tk.Label(win, text="⚙  Configuration", bg=c["bg"], fg=c["accent"],
                 font=("Segoe UI",14,"bold")).pack(pady=(20,14))

        def row(lbl, fn, pady=8):
            f = tk.Frame(win, bg=c["bg"])
            f.pack(fill="x", padx=24, pady=pady)
            tk.Label(f, text=lbl, bg=c["bg"], fg=c["text_dim"],
                     font=("Segoe UI",9), width=12, anchor="w").pack(side="left")
            w = fn(f); w.pack(side="left", fill="x", expand=True)
            return w

        pv = tk.StringVar(value=self.cfg.get("provider","gemini"))
        kv = tk.StringVar(value=self.cfg.get("api_key",""))
        mv = tk.StringVar(value=self.cfg.get("model", DEFAULT_CONFIG["model"]))
        tv = tk.StringVar(value=self.cfg.get("theme","dark"))
        wv = tk.StringVar(value=self.cfg.get("work_dir", str(Path.home())))

        row("Provider",  lambda f: ttk.Combobox(f, textvariable=pv,
            values=["gemini","openai"], state="readonly", width=16))
        ke = row("API Key", lambda f: tk.Entry(f, textvariable=kv, show="●",
            bg=c["input_bg"], fg=c["text"], insertbackground=c["text"],
            relief="flat", bd=4, font=("Segoe UI",10)))
        sv = tk.BooleanVar()
        sf = tk.Frame(win, bg=c["bg"]); sf.pack(fill="x", padx=36)
        tk.Checkbutton(sf, text="Show key", variable=sv,
                       command=lambda: ke.configure(show="" if sv.get() else "●"),
                       bg=c["bg"], fg=c["text_dim"], selectcolor=c["input_bg"],
                       activebackground=c["bg"],
                       font=("Segoe UI",8)).pack(side="left")
        row("Model",    lambda f: tk.Entry(f, textvariable=mv,
            bg=c["input_bg"], fg=c["text"], insertbackground=c["text"],
            relief="flat", bd=4, font=("Segoe UI",10)))
        row("Theme",    lambda f: ttk.Combobox(f, textvariable=tv,
            values=["dark","light"], state="readonly", width=10))
        row("Work dir", lambda f: tk.Entry(f, textvariable=wv,
            bg=c["input_bg"], fg=c["text"], insertbackground=c["text"],
            relief="flat", bd=4, font=("Segoe UI",9)))
        tk.Button(win, text="Browse…", bg=c["input_bg"], fg=c["text"],
                  bd=0, relief="flat", font=("Segoe UI",9), cursor="hand2",
                  command=lambda: wv.set(
                      filedialog.askdirectory(initialdir=wv.get()) or wv.get()
                  )).pack(pady=(0,4))

        tk.Label(win,
                 text=("Gemini:  gemini-2.0-flash  ·  gemini-2.0-flash-lite  ·  gemini-1.5-pro\n"
                       "OpenAI:  gpt-4o  ·  gpt-4o-mini  ·  gpt-4-turbo\n\n"
                       f"Config: {APP_DIR}"),
                 bg=c["bg"], fg=c["text_dim"],
                 font=("Segoe UI",8), justify="left").pack(padx=24, anchor="w")

        def save():
            self.cfg.update(provider=pv.get(), api_key=kv.get().strip(),
                            model=mv.get().strip(), theme=tv.get(),
                            work_dir=wv.get().strip() or str(Path.home()))
            save_config(self.cfg)
            self._badge_var.set(self._badge_text())
            self._wd_var.set(self.cfg["work_dir"])
            win.destroy()

        tk.Button(win, text="  Save  ", bg=c["send_btn"], fg=c["send_fg"],
                  relief="flat", bd=0, font=("Segoe UI",11,"bold"),
                  padx=28, pady=8, cursor="hand2", command=save).pack(pady=16)

    def _pick_wd(self):
        d = filedialog.askdirectory(initialdir=self.cfg["work_dir"])
        if d:
            self.cfg["work_dir"] = d
            self._wd_var.set(d)
            save_config(self.cfg)

    def _start_tray(self):
        menu = pystray.Menu(
            item("Show / Hide", lambda: self.root.after(0, self.toggle_panel), default=True),
            pystray.Menu.SEPARATOR,
            item("Settings",    lambda: self.root.after(0, self._open_settings)),
            item("Clear chat",  lambda: self.root.after(0, self._clear_chat)),
            pystray.Menu.SEPARATOR,
            item("Quit",        lambda: self.root.after(0, self._quit)),
        )
        self._tray = pystray.Icon("AI Assistant", make_icon(64), "AI Assistant", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _on_close(self): self.root.withdraw()

    def _quit(self):
        try: self._tray.stop()
        except Exception: pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AIPanelApp().run()

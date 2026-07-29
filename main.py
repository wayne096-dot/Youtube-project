import sys
import os
import ctypes
import subprocess
import unicodedata
import math
import threading
import json
import socket
import time
import urllib.request

import sdl2
import sdl2.ext
import sdl2.sdlttf as ttf
import sdl2.sdlimage as img

import youtube
import re
import pytchat

import yt_dlp

FONT_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), "font.ttf"),
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/DroidSansFallback.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf"
]

EMOJI_FONT_PATHS = [
    os.path.join(os.path.dirname(__file__), "emoji.ttf"),
    "/system/fonts/NotoColorEmoji.ttf",
    "/system/fonts/AndroidEmoji.ttf"
]

IPC_SOCKET_PATH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "mpv_socket")

# --- 設定檔 (config.cfg) 讀寫邏輯 ---
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.cfg")

DEFAULT_CONFIG = {
    "search_num_results": 10,
    "cache_time_seconds": 3,
    "show_thumbnails": True,
    "enable_comments": True  # 【新增】預設開啟留言功能
}


def load_config():
    """讀取 config.cfg，若不存在或損壞則返回預設值"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception as e:
            print(f"[Warning] Failed to load config.cfg, using default: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(config_data):
    """將設定寫入 config.cfg"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[Error] Failed to save config.cfg: {e}")


def is_emoji(char):
    cp = ord(char)
    category = unicodedata.category(char)
    if category in ('So', 'Sk'):
        return True
    return (
        0x1F600 <= cp <= 0x1F64F or
        0x1F300 <= cp <= 0x1F5FF or
        0x1F680 <= cp <= 0x1F6FF or
        0x1F1E6 <= cp <= 0x1F1FF or
        0x1F900 <= cp <= 0x1F9FF or
        0x1FA70 <= cp <= 0x1FAFF or
        0x2600  <= cp <= 0x26FF  or
        0x2700  <= cp <= 0x27BF  or
        0x2300  <= cp <= 0x23FF  or
        0xFE00  <= cp <= 0xFE0F  or
        0x200D == cp
    )

def extract_video_id(url):
    """從 YouTube URL 提取 11 位數的 Video ID"""
    pattern = r'(?:v=|\/|be\/|embed\/|live\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def fetch_normal_comments(url, max_comments=20):
    """使用 yt-dlp 抓取一般 YouTube 影片留言"""
    ydl_opts = {
        'skip_download': True,
        'getcomments': True,
        'max_comments': max_comments,
        'extract_flat': 'in_playlist',
        'playlistend': 1,
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
    }
    comments_list = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and 'comments' in info and info['comments']:
                for c in info['comments'][:max_comments]:
                    author = c.get('author') or c.get('author_id') or '匿名'
                    text = c.get('text') or ''
                    time_text = c.get('_time_text') or ''
                    like_count = c.get('like_count') or 0
                    
                    comments_list.append({
                        "depth": 0,
                        "is_more_btn": False,
                        "node": {
                            "author": author,
                            "text": text,
                            "time_text": time_text,
                            "like_count": like_count,
                            "replies": [],
                            "is_expanded": False
                        }
                    })
    except Exception as e:
        print(f"[Comments Error] 抓取一般留言失敗: {e}")
    return comments_list



class MultiFontManager:
    def __init__(self, size=20):
        self.size = size
        self.main_font = None
        self.emoji_font = None
        self.init_fonts()

    def init_fonts(self):
        for path in FONT_SEARCH_PATHS:
            if os.path.exists(path):
                self.main_font = ttf.TTF_OpenFont(path.encode('utf-8'), self.size)
                if self.main_font:
                    break

        for path in EMOJI_FONT_PATHS:
            if os.path.exists(path):
                self.emoji_font = ttf.TTF_OpenFont(path.encode('utf-8'), self.size)
                if self.emoji_font:
                    break

    def get_text_size(self, text):
        if not text: return 0, 0
        total_w = 0
        max_h = 0
        segments = self._split_segments(text)

        for chunk, is_emo in segments:
            font = self.emoji_font if (is_emo and self.emoji_font) else self.main_font
            if not font: font = self.main_font
            if not font: continue

            w, h = ctypes.c_int(), ctypes.c_int()
            ttf.TTF_SizeUTF8(font, chunk.encode('utf-8'), ctypes.byref(w), ctypes.byref(h))
            total_w += w.value
            max_h = max(max_h, h.value)

        return total_w, max_h

    def _split_segments(self, text):
        segments = []
        current_segment = ""
        current_is_emoji = False

        for char in text:
            char_is_emoji = is_emoji(char)
            if not current_segment:
                current_segment = char
                current_is_emoji = char_is_emoji
            elif char_is_emoji == current_is_emoji:
                current_segment += char
            else:
                segments.append((current_segment, current_is_emoji))
                current_segment = char
                current_is_emoji = char_is_emoji

        if current_segment:
            segments.append((current_segment, current_is_emoji))
        return segments

    def draw_text_with_emoji(self, renderer, text, x, y, color=(255, 255, 255, 255)):
        if not text: return 0
        current_x = x
        sdl_color = sdl2.SDL_Color(color[0], color[1], color[2], color[3])
        segments = self._split_segments(text)

        for chunk, is_emo in segments:
            text_bytes = chunk.encode('utf-8')
            surface = None

            fonts_to_try = []
            if is_emo and self.emoji_font:
                fonts_to_try.append(self.emoji_font)
            if self.main_font:
                fonts_to_try.append(self.main_font)

            for font in fonts_to_try:
                surface = ttf.TTF_RenderUTF8_Blended(font, text_bytes, sdl_color)
                if surface and surface.contents.w > 0 and surface.contents.h > 0:
                    break
                if surface:
                    sdl2.SDL_FreeSurface(surface)
                    surface = None

            if not surface:
                continue

            texture = sdl2.SDL_CreateTextureFromSurface(renderer, surface)
            if texture:
                rect = sdl2.SDL_Rect(current_x, y, surface.contents.w, surface.contents.h)
                sdl2.SDL_RenderCopy(renderer, texture, None, ctypes.byref(rect))
                current_x += surface.contents.w
                sdl2.SDL_DestroyTexture(texture)

            sdl2.SDL_FreeSurface(surface)
            
        return current_x - x

    def close(self):
        if self.main_font: ttf.TTF_CloseFont(self.main_font)
        if self.emoji_font: ttf.TTF_CloseFont(self.emoji_font)


class App:
    def __init__(self):
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
        os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
        ttf.TTF_Init()
        img.IMG_Init(img.IMG_INIT_JPG | img.IMG_INIT_PNG)

        bounds = sdl2.SDL_Rect()
        sdl2.SDL_GetDisplayBounds(0, ctypes.byref(bounds))
        screen_w = bounds.w if bounds.w > 0 else 1280
        
        self.video_height = int(screen_w * 9 / 16)
        self.width = screen_w
        # 修改：預設選單階段保持 1 倍高度
        self.height = self.video_height

        # 1. 主視窗 (搜尋與選單階段僅顯示 1 倍高度)
        self.window = sdl2.SDL_CreateWindow(
            b"PySDL2 YouTube Player (X11)",
            0, 0,
            self.width, self.height,
            sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_BORDERLESS | sdl2.SDL_WINDOW_ALWAYS_ON_TOP
        )


        # 2. 建立專屬的 MPV 子視窗 (加入 ALWAYS_ON_TOP 屬性)
        self.child_window = sdl2.SDL_CreateWindow(
            b"MPV Subwindow",
            0, 0,
            self.width, self.video_height,
            sdl2.SDL_WINDOW_HIDDEN | sdl2.SDL_WINDOW_BORDERLESS | sdl2.SDL_WINDOW_ALWAYS_ON_TOP
        )


        self.renderer = sdl2.SDL_CreateRenderer(
            self.window, -1, sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC
        )
        sdl2.SDL_SetRenderDrawBlendMode(self.renderer, sdl2.SDL_BLENDMODE_BLEND)

        self.font_mgr = MultiFontManager(size=20)

        # 3. 取得子視窗 (child_window) 的 X11 Window ID 給 MPV 嵌入
        wm_info = sdl2.SDL_SysWMinfo()
        sdl2.SDL_VERSION(wm_info.version)
        sdl2.SDL_GetWindowWMInfo(self.child_window, ctypes.byref(wm_info))
        self.xid = wm_info.info.x11.window

        self.state = "SEARCH_INPUT"
        self.input_text = ""
        self.cursor_pos = 0
        self.videos = []
        self.selected_video_idx = 0

        self.thumb_textures = {}
        self.thumb_queue = []
        self.thumb_lock = threading.Lock()

        self.current_video_title = ""
        self.current_url = ""
        self.video_formats = []
        self.audio_formats = []
        self.selected_fmt_idx = 0
        self.chosen_video_id = ""
        self.chosen_audio_id = ""
        self.active_fmt_str = ""

        self.mpv_process = None
        self.is_pure_audio = False
        self.playback_time = 0.0
        self.duration = 0.0
        self.is_paused = False

        self.scroll_start_time = time.time()
        self.list_scroll_y = 0

        self.config = load_config()
        self.settings_selected_idx = 0

        # --- 留言 / 聊天室相關變數 ---
        self.comments_tree = []
        self.flat_visible_comments = []
        self.selected_comment_idx = 0
        self.comments_loading = False
        self.current_comment_limit = 20
        self.stop_comment_thread = False  # 用於停止背景聊天室線程

        # 【新增】直播聊天室自動置底/滾動控制
        self.is_live_stream = False                 # 是否為直播聊天室模式
        self.auto_scroll_live_chat = True          # 是否正在自動鎖定最新留言
        self.last_comment_user_action_time = time.time()  # 上次手動操作 (Up/Down) 的時間

        # 【新增】控制 IPC 查詢頻率（避免每 16ms 建立一次 Socket）
        self.last_ipc_query_time = 0.0

    def draw_loading_media_screen(self):
        """繪製媒體載入中的動畫與資訊卡片 (影片與純音訊共用)"""
        mode_text = "🎵 純音訊載入中..." if self.is_pure_audio else "🎬 影片載入中..."
        self.draw_text(f"{mode_text}", 40, 20, (0, 229, 255, 255))

        # 資訊卡片 (標題與格式 ID)
        card_rect = sdl2.SDL_Rect(40, 55, self.width - 80, 110)
        self.draw_rounded_rect(card_rect, (40, 42, 54, 230), radius=10)

        self.draw_scrolling_text(f"📌 標題: {self.current_video_title}", 60, 75, self.width - 120, True, (255, 255, 255, 255))
        self.draw_text(f"🎧 格式 ID: {self.active_fmt_str}", 60, 115, (180, 190, 210, 255))

        # 動態 Spinner 與提示文字
        cx = self.width // 2
        cy = 55 + 110 + (self.video_height - 165) // 2 - 10
        self.draw_spinner(cx, cy - 15)
        self.draw_text("正在解析與快取串流媒體中 (請稍候)...", cx - 150, cy + 20, (0, 229, 255, 255))

    def clear_thumb_cache(self):
        with self.thumb_lock:
            for tex in self.thumb_textures.values():
                if tex:
                    sdl2.SDL_DestroyTexture(tex)
            self.thumb_textures.clear()
            self.thumb_queue.clear()

    def rebuild_visible_comments(self):
        """將樹狀結構 comments_tree 遞迴展平為帶有層級 (depth) 的清單，供 UI 繪製與展開"""
        flat = []

        def flatten(nodes, depth=0):
            for n in nodes:
                flat.append({
                    "node": n,
                    "depth": depth,
                    "is_more_btn": False
                })
                # 若該留言已被展開 (is_expanded=True)，且含有子回覆，則遞迴展開子留言
                if n.get("is_expanded") and n.get("replies"):
                    flatten(n["replies"], depth + 1)

        flatten(self.comments_tree)

        # 在列表最下方加入 "載入更多留言" 按鈕
        if flat:
            flat.append({
                "is_more_btn": True,
                "depth": 0,
                "node": {
                    "text": "▼ 按 Enter 鍵載入更多留言..."
                }
            })

        self.flat_visible_comments = flat

    def load_texture_from_bytes(self, raw_bytes):
        if not raw_bytes:
            return None
        buf = ctypes.create_string_buffer(raw_bytes)
        rw = sdl2.SDL_RWFromConstMem(buf, len(raw_bytes))
        if not rw:
            return None
        surface = img.IMG_Load_RW(rw, 1)
        if not surface:
            return None
        texture = sdl2.SDL_CreateTextureFromSurface(self.renderer, surface)
        sdl2.SDL_FreeSurface(surface)
        return texture

    def process_thumb_queue(self):
        with self.thumb_lock:
            for item in self.videos:
                url = item.get("thumb_url")
                raw_data = item.get("thumb_raw")
                if url and raw_data and url not in self.thumb_textures:
                    tex = self.load_texture_from_bytes(raw_data)
                    if tex:
                        self.thumb_textures[url] = tex
                    item["thumb_raw"] = None

    def start_thumbnail_download_thread(self):
        def task():
            headers = {'User-Agent': 'Mozilla/5.0'}
            for item in self.videos:
                if self.state != "SELECT_VIDEO":
                    break
                url = item.get("thumb_url")
                if not url or url in self.thumb_textures or item.get("thumb_raw"):
                    continue
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=3) as response:
                        item["thumb_raw"] = response.read()
                except Exception:
                    pass
        threading.Thread(target=task, daemon=True).start()

    def start_fetch_comments_thread(self, url, limit=20):
        """啟動背景 Thread 抓取直播聊天室或影片留言"""
        # 1. 標記舊線程停止
        self.stop_comment_thread = True
        
        # 2. 開啟新線程執行
        threading.Thread(
            target=self._fetch_chat_or_comments_worker, 
            args=(url, limit), 
            daemon=True
        ).start()

    def _fetch_chat_or_comments_worker(self, url, limit):
        """背景 Worker：自動判斷直播聊天室或一般留言"""
        self.stop_comment_thread = False
        self.comments_loading = True
        self.flat_visible_comments = []
        self.comments_tree = []
        self.is_live_stream = False
        
        video_id = extract_video_id(url)
        is_live_chat_success = False

        # --- 1. 優先嘗試連接直播聊天室 ---
        if video_id:
            try:
                chat = pytchat.create(video_id=video_id, interruptable=False)
                if chat.is_alive():
                    print(f"[Live Chat] 成功連接至直播聊天室 (ID: {video_id})，等待訊息中...")
                    is_live_chat_success = True
                    self.is_live_stream = True
                    self.auto_scroll_live_chat = True
                    self.last_comment_user_action_time = time.time()
                    
                    retry_count = 0
                    has_received_first_msg = False
                    start_wait_time = time.time()

                    while chat.is_alive() and not self.stop_comment_thread:
                        try:
                            chat_data = chat.get()
                            items = chat_data.items
                            
                            if items:
                                for c in items:
                                    if self.stop_comment_thread:
                                        break
                                    
                                    author = getattr(c.author, 'name', '匿名') if hasattr(c, 'author') else '匿名'
                                    msg = getattr(c, 'message', '') or ''
                                    timestamp = getattr(c, 'datetime', '') or ''
                                    amount = getattr(c, 'amountString', '') or ''
                                    
                                    text_display = f"[{amount}] {author}: {msg}" if amount else f"{author}: {msg}"

                                    chat_item = {
                                        "author": author,
                                        "text": text_display,
                                        "comment": text_display,
                                        "message": msg,
                                        "time": timestamp,
                                        "depth": 0,
                                        "is_more_btn": False,
                                        "node": {
                                            "author": author,
                                            "text": f"[{amount}] {msg}" if amount else msg,
                                            "time_text": timestamp,
                                            "like_count": 0,
                                            "replies": [],
                                            "is_expanded": False
                                        }
                                    }
                                    self.flat_visible_comments.append(chat_item)
                                
                                has_received_first_msg = True
                                self.comments_loading = False
                                
                                if len(self.flat_visible_comments) > 60:
                                    self.flat_visible_comments = self.flat_visible_comments[-60:]
                            
                            elif not has_received_first_msg and (time.time() - start_wait_time > 3):
                                chat.terminate()
                                is_live_chat_success = False
                                self.is_live_stream = False
                                break

                            retry_count = 0
                            
                        except Exception as e:
                            print(f"[Live Chat 警告] 抓取訊息異常: {e}")
                            retry_count += 1
                            if retry_count >= 3:
                                chat.terminate()
                                break
                        
                        time.sleep(1)

                    if self.stop_comment_thread:
                        chat.terminate()

            except Exception as e:
                print(f"[Live Chat] 連接失敗或非直播: {e}")

        # --- 2. 非直播影片，使用 youtube.get_comments_tree 抓取樹狀留言 ---
        if not is_live_chat_success and not self.stop_comment_thread:
            print("[Comments] 正在透過 yt-dlp 抓取樹狀留言...")
            
            # 呼叫 youtube.py 中的 get_comments_tree
            tree = youtube.get_comments_tree(url, max_comments=limit)
            
            if not self.stop_comment_thread:
                self.comments_tree = tree
                self.rebuild_visible_comments()  # 展開留言清單
                self.comments_loading = False


    def process_input_submission(self):
        query = self.input_text.strip()
        if not query:
            return

        is_url = (
            query.startswith("http://")
            or query.startswith("https://")
            or "youtube.com/" in query
            or "youtu.be/" in query
        )

        if is_url:
            self.current_video_title = query
            self.current_url = query
            self.start_load_formats_thread(self.current_url)
        else:
            self.start_search_thread(query)

    def send_mpv_ipc(self, command):
        if not os.path.exists(IPC_SOCKET_PATH):
            return None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.1)
            client.connect(IPC_SOCKET_PATH)
            req = json.dumps({"command": command}) + "\n"
            client.sendall(req.encode('utf-8'))
            resp = client.recv(1024).decode('utf-8')
            client.close()
            if resp:
                data = json.loads(resp.split('\n')[0])
                return data.get("data")
        except Exception:
            pass
        return None

    def update_mpv_status(self):
        if self.state in ("LOADING_MEDIA", "PLAYING") and self.mpv_process:
            now = time.time()
            if now - self.last_ipc_query_time < 0.2:
                return
            self.last_ipc_query_time = now

            pos = self.send_mpv_ipc(["get_property", "time-pos"])
            dur = self.send_mpv_ipc(["get_property", "duration"])
            paused = self.send_mpv_ipc(["get_property", "pause"])

            if pos is not None:
                try: self.playback_time = float(pos)
                except (ValueError, TypeError): pass

            if dur is not None:
                try: self.duration = float(dur)
                except (ValueError, TypeError): pass

            if paused is not None:
                self.is_paused = bool(paused)

            if self.state == "LOADING_MEDIA":
                # 當檢測到已開始輸出時間 (代表載入完成)
                if pos is not None or dur is not None or self.playback_time > 0:
                    self.state = "PLAYING"
                    if not self.is_pure_audio:
                        enable_comments = self.config.get("enable_comments", True)
                        if enable_comments:
                            # 【關鍵修復】切換至播放狀態時，將主視窗移至下方 (留言區)，露出上方 MPV 畫面
                            comment_h = self.height - self.video_height
                            sdl2.SDL_SetWindowPosition(self.window, 0, self.video_height)
                            sdl2.SDL_SetWindowSize(self.window, self.width, comment_h)
                            sdl2.SDL_RaiseWindow(self.child_window)
                            sdl2.SDL_RaiseWindow(self.window)
                        else:
                            # 關閉留言：升起 MPV 並隱藏主視窗
                            sdl2.SDL_RaiseWindow(self.child_window)
                            sdl2.SDL_HideWindow(self.window)








    def draw_text(self, text, x, y, color=(255, 255, 255, 255)):
        return self.font_mgr.draw_text_with_emoji(self.renderer, text, x, y, color)

    def draw_rounded_rect(self, rect, color, radius=8):
        sdl2.SDL_SetRenderDrawColor(self.renderer, color[0], color[1], color[2], color[3])
        inner_rect = sdl2.SDL_Rect(rect.x + radius, rect.y, rect.w - 2 * radius, rect.h)
        sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(inner_rect))
        inner_rect2 = sdl2.SDL_Rect(rect.x, rect.y + radius, rect.w, rect.h - 2 * radius)
        sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(inner_rect2))

        for dev_x in range(radius):
            for dev_y in range(radius):
                if (dev_x - radius) ** 2 + (dev_y - radius) ** 2 <= radius ** 2:
                    sdl2.SDL_RenderDrawPoint(self.renderer, rect.x + dev_x, rect.y + dev_y)
                    sdl2.SDL_RenderDrawPoint(self.renderer, rect.x + rect.w - radius + dev_x, rect.y + dev_y)
                    sdl2.SDL_RenderDrawPoint(self.renderer, rect.x + dev_x, rect.y + rect.h - radius + dev_y)
                    sdl2.SDL_RenderDrawPoint(self.renderer, rect.x + rect.w - radius + dev_x, rect.y + rect.h - radius + dev_y)

    def draw_scrolling_text(self, text, x, y, max_width, is_selected, color=(255, 255, 255, 255)):
        text_w, text_h = self.font_mgr.get_text_size(text)

        clip_rect = sdl2.SDL_Rect(x, y - 2, max_width, text_h + 8)
        sdl2.SDL_RenderSetClipRect(self.renderer, ctypes.byref(clip_rect))

        draw_x = x
        if is_selected and text_w > max_width:
            overflow_w = text_w - max_width + 40
            speed = 40
            scroll_duration = overflow_w / speed
            pause_duration = 1.2
            total_cycle = scroll_duration + (pause_duration * 2)

            elapsed = (time.time() - self.scroll_start_time) % total_cycle

            if elapsed < pause_duration:
                offset = 0
            elif elapsed < pause_duration + scroll_duration:
                offset = int((elapsed - pause_duration) * speed)
            else:
                offset = overflow_w

            draw_x = x - offset

        self.draw_text(text, draw_x, y, color)
        sdl2.SDL_RenderSetClipRect(self.renderer, None)

    def draw_scrollable_list(self, items, selected_idx, start_y, card_h, gap, get_title_func, show_thumb=False):
        total_items = len(items)
        if total_items == 0: return

        visible_height = self.video_height - start_y - 20
        max_visible_items = max(1, visible_height // (card_h + gap))

        top_idx = getattr(self, "_list_top_idx", 0)
        if selected_idx < top_idx:
            top_idx = selected_idx
        elif selected_idx >= top_idx + max_visible_items:
            top_idx = selected_idx - max_visible_items + 1
        
        self._list_top_idx = top_idx

        clip_rect = sdl2.SDL_Rect(40, start_y, self.width - 80, visible_height)
        sdl2.SDL_RenderSetClipRect(self.renderer, ctypes.byref(clip_rect))

        thumb_w = 64 if show_thumb else 0
        text_start_x = 58 + (thumb_w + 10 if show_thumb else 0)
        max_text_w = self.width - 120 - (thumb_w + 10 if show_thumb else 0)

        for idx in range(top_idx, min(total_items, top_idx + max_visible_items + 1)):
            y_pos = start_y + (idx - top_idx) * (card_h + gap)
            is_sel = (idx == selected_idx)
            card_rect = sdl2.SDL_Rect(40, y_pos, self.width - 80, card_h)

            item = items[idx]
            title_text = get_title_func(item)

            if is_sel:
                self.draw_rounded_rect(card_rect, (45, 52, 70, 255), radius=6)
                indicator = sdl2.SDL_Rect(40, y_pos, 6, card_h)
                self.draw_rounded_rect(indicator, (0, 229, 255, 255), radius=3)
            else:
                self.draw_rounded_rect(card_rect, (30, 33, 44, 180), radius=6)

            if show_thumb:
                thumb_x = 54
                thumb_y = y_pos + 4
                thumb_h = card_h - 8
                thumb_rect = sdl2.SDL_Rect(thumb_x, thumb_y, thumb_w, thumb_h)

                sdl2.SDL_SetRenderDrawColor(self.renderer, 20, 22, 30, 255)
                sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(thumb_rect))

                url = item.get("thumb_url")
                tex = self.thumb_textures.get(url)
                if tex:
                    sdl2.SDL_RenderCopy(self.renderer, tex, None, ctypes.byref(thumb_rect))

            text_color = (255, 255, 255, 255) if is_sel else (160, 170, 185, 255)
            self.draw_scrolling_text(title_text, text_start_x, y_pos + (card_h - 22) // 2, max_text_w, is_selected=is_sel, color=text_color)

        sdl2.SDL_RenderSetClipRect(self.renderer, None)

        if total_items > max_visible_items:
            bar_w = 6
            bar_area_h = visible_height
            bar_h = max(20, int(bar_area_h * (max_visible_items / total_items)))
            bar_y = start_y + int((bar_area_h - bar_h) * (top_idx / max(1, total_items - max_visible_items)))
            
            scrollbar_rect = sdl2.SDL_Rect(self.width - 32, bar_y, bar_w, bar_h)
            self.draw_rounded_rect(scrollbar_rect, (0, 229, 255, 180), radius=3)

    def draw_spinner(self, center_x, center_y, radius=24, dots=8):
        ticks = sdl2.SDL_GetTicks()
        angle_offset = (ticks / 1000.0) * (2 * math.pi)

        for i in range(dots):
            angle = angle_offset + (i * 2 * math.pi / dots)
            x = int(center_x + radius * math.cos(angle))
            y = int(center_y + radius * math.sin(angle))
            alpha = int(255 * (i + 1) / dots)
            
            sdl2.SDL_SetRenderDrawColor(self.renderer, 0, 229, 255, alpha)
            rect = sdl2.SDL_Rect(x - 3, y - 3, 6, 6)
            sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(rect))

    def format_time(self, seconds):
        if not seconds or seconds < 0: return "00:00"
        secs = int(seconds)
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def draw_audio_visualizer(self):
        ticks = sdl2.SDL_GetTicks()
        
        status_text = "⏸️ 已暫停" if self.is_paused else "▶️ 播放中"
        self.draw_text(f"🎵 純音訊模式 [{status_text}]", 40, 25, (0, 229, 255, 255))
        
        card_rect = sdl2.SDL_Rect(40, 65, self.width - 80, 130)
        self.draw_rounded_rect(card_rect, (40, 42, 54, 230), radius=10)
        
        self.draw_scrolling_text(f"📌 標題: {self.current_video_title}", 60, 85, self.width - 120, True, (255, 255, 255, 255))
        self.draw_text(f"🎧 格式 ID: {self.active_fmt_str}", 60, 125, (180, 190, 210, 255))
        
        bars = 32
        bar_w = (self.width - 80) // bars
        for i in range(bars):
            height_var = int(math.sin(ticks / 150.0 + i * 0.3) * 35 + 40) if not self.is_paused else 8
            x = 40 + i * bar_w
            y = self.video_height - 140 - height_var
            
            sdl2.SDL_SetRenderDrawColor(self.renderer, 0, 215, 255, 220)
            rect = sdl2.SDL_Rect(x + 2, y, bar_w - 4, height_var)
            sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(rect))

        progress_x = 40
        progress_y = self.video_height - 85
        progress_w = self.width - 80
        progress_h = 8

        ratio = (self.playback_time / self.duration) if self.duration > 0 else 0.0
        ratio = min(max(ratio, 0.0), 1.0)
        filled_w = int(progress_w * ratio)

        percent_str = f"({int(ratio * 100)}%)" if self.duration > 0 else ""
        time_str = f"{self.format_time(self.playback_time)} / {self.format_time(self.duration)}  {percent_str}"
        self.draw_text(time_str, progress_x, progress_y - 25, (255, 220, 100, 255))

        sdl2.SDL_SetRenderDrawColor(self.renderer, 60, 65, 80, 255)
        bg_rect = sdl2.SDL_Rect(progress_x, progress_y, progress_w, progress_h)
        sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(bg_rect))

        if filled_w > 0:
            sdl2.SDL_SetRenderDrawColor(self.renderer, 0, 230, 150, 255)
            fill_rect = sdl2.SDL_Rect(progress_x, progress_y, filled_w, progress_h)
            sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(fill_rect))

            sdl2.SDL_SetRenderDrawColor(self.renderer, 255, 255, 255, 255)
            handle_rect = sdl2.SDL_Rect(progress_x + filled_w - 3, progress_y - 4, 6, progress_h + 8)
            sdl2.SDL_RenderFillRect(self.renderer, ctypes.byref(handle_rect))

        self.draw_text("【操作指南】 [Space] 暫停  |  [←/→] 快退/快進  |  [Esc/Q] 返回清單", 40, self.video_height - 35, (140, 150, 165, 255))

    def render_comments_section(self):
        if not self.config.get("enable_comments", True):
            return
        if self.state not in ("PLAYING", "LOADING_MEDIA"):
            return

        # -------------------------------------------------------------
        # 【新增】直播聊天室 10s 自動跳轉與持續追蹤 logic
        # -------------------------------------------------------------
        if self.is_live_stream and self.flat_visible_comments:
            now = time.time()
            # 若手動停止中，且已經 10 秒未操作，自動恢復置底模式
            if not self.auto_scroll_live_chat and (now - self.last_comment_user_action_time >= 10.0):
                self.auto_scroll_live_chat = True

            # 若處於自動置底模式，強制選取 index 鎖定在最新一筆
            if self.auto_scroll_live_chat:
                self.selected_comment_idx = len(self.flat_visible_comments) - 1
        # -------------------------------------------------------------

        if self.state == "PLAYING" and not self.is_pure_audio:
            start_y = 0
            section_h = self.height - self.video_height
        else:
            start_y = self.video_height
            section_h = self.height - self.video_height

        bg_rect = sdl2.SDL_Rect(0, start_y, self.width, section_h)
        self.draw_rounded_rect(bg_rect, (20, 22, 28, 255), radius=0)

        # 提示標籤 (可顯示當前是否自動鎖定最新)
        if self.is_live_stream:
            if self.auto_scroll_live_chat:
                title_status = "💬 直播聊天室 [📌 自動鎖定最新]"
            else:
                countdown = max(0, int(10 - (time.time() - self.last_comment_user_action_time)))
                title_status = f"💬 直播聊天室 [⏸️ 手動瀏覽中 ({countdown}s 後恢復自動置底)]"
        else:
            title_status = "💬 留言區 [↓/↑: 選取留言 | Enter: 展開層級/載入更多]"

        self.draw_text(title_status, 30, start_y + 15, (0, 229, 255, 255))

        if self.comments_loading:
            self.draw_spinner(self.width // 2, start_y + (section_h // 2) - 20)
            self.draw_text("正在載入留言...", self.width // 2 - 60, start_y + (section_h // 2) + 15, (180, 190, 200, 255))
            return

        if not self.flat_visible_comments:
            self.draw_text("⚠️ 本影片暫無留言或留言功能已關閉。", 40, start_y + 60, (150, 160, 175, 255))
            return

        list_start_y = start_y + 45
        card_h = 38
        gap = 6
        visible_count = max(1, (section_h - 60) // (card_h + gap))
        
        top_idx = max(0, self.selected_comment_idx - visible_count + 1) if self.selected_comment_idx >= visible_count else 0

        for i in range(top_idx, min(len(self.flat_visible_comments), top_idx + visible_count)):
            item = self.flat_visible_comments[i]
            node = item.get("node")
            depth = item.get("depth", 0)
            is_more_btn = item.get("is_more_btn", False)

            y_pos = list_start_y + (i - top_idx) * (card_h + gap)
            is_sel = (i == self.selected_comment_idx)

            indent_x = 30 + depth * 20
            card_w = self.width - indent_x - 30
            card_rect = sdl2.SDL_Rect(indent_x, y_pos, card_w, card_h)

            if is_sel:
                self.draw_rounded_rect(card_rect, (45, 52, 70, 255), radius=4)
                indicator = sdl2.SDL_Rect(indent_x, y_pos, 4, card_h)
                self.draw_rounded_rect(indicator, (0, 229, 255, 255), radius=2)
            else:
                self.draw_rounded_rect(card_rect, (32, 35, 46, 180), radius=4)

            if is_more_btn:
                btn_text = node["text"] if node else item.get("text", "載入更多...")
                self.draw_scrolling_text(btn_text, indent_x + 12, y_pos + (card_h - 20) // 2, card_w - 24, is_selected=is_sel, color=(0, 229, 255, 255))
            elif node:
                prefix = "└── " if depth > 0 else ""
                has_replies = len(node.get("replies", [])) > 0
                expand_icon = "[-] " if node.get("is_expanded") else "[+] " if has_replies else "    "

                time_str = f" ({node['time_text']})" if node.get("time_text") else ""
                like_str = f" 👍{node['like_count']}" if node.get("like_count") else ""
                display_text = f"{prefix}{expand_icon}[{node.get('author', '匿名')}]{time_str}{like_str}: {node.get('text', '')}"
                
                text_color = (255, 255, 255, 255) if is_sel else (170, 180, 195, 255)
                self.draw_scrolling_text(display_text, indent_x + 12, y_pos + (card_h - 20) // 2, card_w - 24, is_selected=is_sel, color=text_color)
            else:
                # 舊格式或純扁平字典的退回顯示
                author = item.get("author", "匿名")
                msg_text = item.get("text") or item.get("message") or ""
                display_text = f"[{author}]: {msg_text}"
                
                text_color = (255, 255, 255, 255) if is_sel else (170, 180, 195, 255)
                self.draw_scrolling_text(display_text, indent_x + 12, y_pos + (card_h - 20) // 2, card_w - 24, is_selected=is_sel, color=text_color)



    def start_search_thread(self, query):
        def task():
            self.clear_thumb_cache()
            num = self.config.get("search_num_results", 10)
            self.videos = youtube.search(query, limit=num)
            self.selected_video_idx = 0
            self._list_top_idx = 0
            self.scroll_start_time = time.time()
            if self.videos:
                self.state = "SELECT_VIDEO"
                if self.config.get("show_thumbnails", True):
                    self.start_thumbnail_download_thread()
            else:
                self.state = "SEARCH_INPUT"

        self.state = "SEARCHING"
        threading.Thread(target=task, daemon=True).start()

    def start_load_formats_thread(self, url):
        def task():
            info = youtube.get_info(url)
            if not info:
                self.state = "SEARCH_INPUT"
                return

            is_playlist = (
                info.get("_type") == "playlist"
                or "entries" in info
                or "playlist" in url.lower()
            )

            target_info = info
            is_live = target_info.get("is_live") is True or target_info.get("live_status") == "is_live"
            live_prefix = "🔴 [LIVE] " if is_live else ""

            if is_playlist and "entries" in info and len(info["entries"]) > 0:
                first_entry = info["entries"][0]
                if "formats" not in first_entry and "url" in first_entry:
                    first_entry = youtube.get_info(first_entry["url"]) or first_entry
                target_info = first_entry
                self.current_video_title = f"📋 [播放清單] {info.get('title', '未命名清單')} (共 {len(info['entries'])} 部)"
            else:
                self.current_video_title = f"{live_prefix}{target_info.get('title', url)}"

            formats = target_info.get("formats", [])
            
            v_fmts = [{"format_id": "SKIP", "resolution": "[0] 跳過 (無影像 / 純音訊)"}]
            a_fmts = [
                {"format_id": "BACK", "abr": "⬅️ [返回上一頁] 重新選擇畫質"},
                {"format_id": "SKIP", "abr": "[0] 跳過 (無聲音 / 純影片)"}
            ]

            for f in formats:
                vcodec = f.get("vcodec", "none")
                acodec = f.get("acodec", "none")
                if vcodec and vcodec != "none" and f.get("height"):
                    v_fmts.append(f)
                if vcodec == "none" and acodec and acodec != "none":
                    a_fmts.append(f)

            self.video_formats = v_fmts
            self.audio_formats = a_fmts
            self.selected_fmt_idx = 0
            self._list_top_idx = 0
            self.scroll_start_time = time.time()
            self.state = "SELECT_VIDEO_FORMAT"

        self.state = "LOADING_FORMAT"
        threading.Thread(target=task, daemon=True).start()

    def play_video(self, url, fmt, is_pure_audio=False):
        enable_comments = self.config.get("enable_comments", True)

        if enable_comments:
            self.height = int(self.video_height * 1.75)
        else:
            self.height = self.video_height

        sdl2.SDL_SetWindowPosition(self.window, 0, 0)
        sdl2.SDL_SetWindowSize(self.window, self.width, self.height)

        if os.path.exists(IPC_SOCKET_PATH):
            try: os.remove(IPC_SOCKET_PATH)
            except Exception as e: print(f"[Warning] Failed to remove old socket: {e}")

        self.state = "LOADING_MEDIA"
        self.active_fmt_str = fmt
        self.is_pure_audio = is_pure_audio
        self.playback_time = 0.0
        self.duration = 0.0

        # --- 視窗同步與 X11 Mapping 防護 ---
        if not is_pure_audio:
            sdl2.SDL_ShowWindow(self.child_window)
            sdl2.SDL_SetWindowPosition(self.child_window, 0, 0)
            sdl2.SDL_SetWindowSize(self.child_window, self.width, self.video_height)
            sdl2.SDL_RaiseWindow(self.window)
            
            # 【關鍵修復 1】強制刷新 SDL 事件佇列，確保 X11 完成 Window Map 操作
            sdl2.SDL_PumpEvents()
        else:
            sdl2.SDL_HideWindow(self.child_window)
            sdl2.SDL_RaiseWindow(self.window)
            sdl2.SDL_PumpEvents()

        if fmt:
            if "[" in fmt:
                fmt_no_av1 = fmt.replace("[", "[vcodec!=av1][", 1)
            else:
                fmt_no_av1 = f"{fmt}[vcodec!=av1]"
            smart_fmt = f"{fmt_no_av1}/{fmt}/best[vcodec!=av1]/best"
        else:
            smart_fmt = "bestvideo[vcodec!=av1][height<=360]+bestaudio/best[height<=360]/best"

        cmd = [
            "mpv",
            f"--wid={self.xid}",
            # 【關鍵修復 2】強制指定 --vo=x11，防止 gpu-next 建立 EGL surface 失敗崩潰
            "--vo=x11",
            f"--ytdl-format={smart_fmt}",
            f"--input-ipc-server={IPC_SOCKET_PATH}",
            "--autofit=100%x100%",
            "--no-border",
            "--keep-open=no",
            "--cache=yes",
            "--cache-secs=15",
            "--demuxer-max-bytes=50M",
            "--demuxer-readahead-secs=15",
            "--hr-seek=no",
            "--profile=fast",
            "--framedrop=vo",
            "--vd-lavc-skiploopfilter=all",
            "--demuxer-lavf-o=timeout=10000000",
            "--input-default-bindings=yes",
            url
        ]
        if is_pure_audio:
            cmd.append("--vid=no")

        self.mpv_process = subprocess.Popen(cmd)
        
        if enable_comments:
            self.current_comment_limit = 20
            self.start_fetch_comments_thread(url, limit=self.current_comment_limit)
        else:
            self.comments_tree = []
            self.flat_visible_comments = []








    def stop_video(self):
        self.stop_comment_thread = True
        self.is_live_stream = False
        self.auto_scroll_live_chat = True
        self.last_comment_user_action_time = time.time()
        

        # 1. 安全關閉 MPV 子進程
        if self.mpv_process:
            if self.mpv_process.poll() is None:
                self.mpv_process.terminate()
                try:
                    self.mpv_process.wait(timeout=1.0)
                except Exception:
                    self.mpv_process.kill()
            self.mpv_process = None

        # 2. 隱藏 MPV 子視窗
        sdl2.SDL_HideWindow(self.child_window)

        # 3. 恢復主視窗至預設尺寸與頂部位置
        self.height = self.video_height
        sdl2.SDL_SetWindowPosition(self.window, 0, 0)
        sdl2.SDL_SetWindowSize(self.window, self.width, self.height)

        # 4. 強制向 X11 奪回鍵盤輸入焦點（Hide -> Show -> Raise）
        sdl2.SDL_HideWindow(self.window)
        sdl2.SDL_ShowWindow(self.window)
        sdl2.SDL_RaiseWindow(self.window)

        # 若系統 SDL2 版本支援，補上顯式焦點設定
        if hasattr(sdl2, 'SDL_SetWindowInputFocus'):
            try:
                sdl2.SDL_SetWindowInputFocus(self.window)
            except Exception:
                pass

        # 5. 清除切換期間積壓的舊按鍵事件（避免退出時誤觸選單按鈕）
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            pass

        # 6. 清空歷史留言快取與重置狀態
        self.flat_visible_comments = []
        self.scroll_start_time = time.time()
        self.state = "SELECT_VIDEO"



    def run(self):
        running = True
        event = sdl2.SDL_Event()
        sdl2.SDL_StartTextInput()

        while running:
            if self.state == "SELECT_VIDEO":
                self.process_thumb_queue()

            if self.state in ("LOADING_MEDIA", "PLAYING") and self.mpv_process:
                if self.mpv_process.poll() is not None:
                    self.stop_video()
                else:
                    self.update_mpv_status()

            while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
                if event.type == sdl2.SDL_QUIT:
                    running = False

                elif event.type == sdl2.SDL_KEYDOWN:
                    key = event.key.keysym.sym

                    if key in (sdl2.SDLK_s, ord('s'), ord('S')) and self.state not in ("SEARCH_INPUT", "LOADING_MEDIA", "PLAYING", "SEARCHING", "LOADING_FORMAT"):
                        self._previous_state = self.state
                        self.state = "SETTINGS"
                        continue

                    if self.state == "SETTINGS":
                        if key in (sdl2.SDLK_ESCAPE, sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            save_config(self.config)
                            self.state = getattr(self, "_previous_state", "SEARCH_INPUT")
                        elif key == sdl2.SDLK_UP:
                            self.settings_selected_idx = max(0, self.settings_selected_idx - 1)
                        elif key == sdl2.SDLK_DOWN:
                            # 修改最大索引值為 4
                            self.settings_selected_idx = min(4, self.settings_selected_idx + 1)
                        elif key == sdl2.SDLK_LEFT:
                            if self.settings_selected_idx == 0:
                                self.config["search_num_results"] = max(1, self.config["search_num_results"] - 1)
                            elif self.settings_selected_idx == 1:
                                self.config["cache_time_seconds"] = max(1, self.config["cache_time_seconds"] - 1)
                            elif self.settings_selected_idx == 2:
                                self.config["show_thumbnails"] = not self.config.get("show_thumbnails", True)
                            elif self.settings_selected_idx == 3:
                                # 【新增】切換啟用留言開關
                                self.config["enable_comments"] = not self.config.get("enable_comments", True)
                        elif key == sdl2.SDLK_RIGHT:
                            if self.settings_selected_idx == 0:
                                self.config["search_num_results"] = min(50, self.config["search_num_results"] + 1)
                            elif self.settings_selected_idx == 1:
                                self.config["cache_time_seconds"] += 1
                            elif self.settings_selected_idx == 2:
                                self.config["show_thumbnails"] = not self.config.get("show_thumbnails", True)
                            elif self.settings_selected_idx == 3:
                                # 【新增】切換啟用留言開關
                                self.config["enable_comments"] = not self.config.get("enable_comments", True)
                        continue


                    if self.state in ("LOADING_MEDIA", "PLAYING"):
                        if key in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
                            self.stop_video()
                        elif key == sdl2.SDLK_SPACE:
                            self.send_mpv_ipc(["cycle", "pause"])
                        elif key == sdl2.SDLK_LEFT:
                            # 關鍵修正：採用相對時間 + 關鍵幀快速跳轉
                            self.send_mpv_ipc(["seek", -5, "relative+keyframes"])
                        elif key == sdl2.SDLK_RIGHT:
                            # 關鍵修正：採用相對時間 + 關鍵幀快速跳轉
                            self.send_mpv_ipc(["seek", 5, "relative+keyframes"])

                        elif key == sdl2.SDLK_UP:
                            # 留言區向上瀏覽焦點
                            if self.flat_visible_comments:
                                self.selected_comment_idx = max(0, self.selected_comment_idx - 1)
                                # 【新增】使用者操作：關閉自動鎖定，記錄最後操作時間
                                if self.is_live_stream:
                                    self.auto_scroll_live_chat = False
                                    self.last_comment_user_action_time = time.time()

                        elif key == sdl2.SDLK_DOWN:
                            # 留言區向下瀏覽焦點
                            if self.flat_visible_comments:
                                self.selected_comment_idx = min(len(self.flat_visible_comments) - 1, self.selected_comment_idx + 1)
                                # 【新增】使用者操作：關閉自動鎖定，記錄最後操作時間
                                if self.is_live_stream:
                                    self.auto_scroll_live_chat = False
                                    self.last_comment_user_action_time = time.time()

                        elif key in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            if self.flat_visible_comments:
                                selected_item = self.flat_visible_comments[self.selected_comment_idx]
                                if selected_item.get("is_more_btn"):
                                    self.current_comment_limit += 20
                                    self.start_fetch_comments_thread(self.current_url, limit=self.current_comment_limit)
                                else:
                                    node = selected_item.get("node")
                                    if node and node.get("replies"):
                                        node["is_expanded"] = not node["is_expanded"]
                                        if hasattr(self, "rebuild_visible_comments"):
                                            self.rebuild_visible_comments()

                        elif key == sdl2.SDLK_LESS or key == ord("<"):
                            self.send_mpv_ipc(["playlist-prev"])
                        elif key == sdl2.SDLK_GREATER or key == ord(">"):
                            self.send_mpv_ipc(["playlist-next"])
                        continue

                    if self.state == "SEARCHING":
                        if key in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
                            self.state = "SEARCH_INPUT"
                        continue

                    if self.state == "LOADING_FORMAT":
                        if key in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
                            self.state = "SELECT_VIDEO"
                        continue

                    elif self.state == "SEARCH_INPUT":
                        if key in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            self.process_input_submission()
                        elif key == sdl2.SDLK_BACKSPACE:
                            if self.cursor_pos > 0:
                                self.input_text = self.input_text[:self.cursor_pos - 1] + self.input_text[self.cursor_pos:]
                                self.cursor_pos -= 1
                        elif key == sdl2.SDLK_LEFT:
                            self.cursor_pos = max(0, self.cursor_pos - 1)
                        elif key == sdl2.SDLK_RIGHT:
                            self.cursor_pos = min(len(self.input_text), self.cursor_pos + 1)

                    elif key in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
                        if self.state == "SELECT_AUDIO_FORMAT":
                            self.state = "SELECT_VIDEO_FORMAT"
                        elif self.state == "SELECT_VIDEO_FORMAT":
                            self.state = "SELECT_VIDEO"
                        elif self.state == "SELECT_VIDEO":
                            self.clear_thumb_cache()
                            self.state = "SEARCH_INPUT"
                        else:
                            running = False

                    elif self.state == "SELECT_VIDEO":
                        if key == sdl2.SDLK_UP:
                            self.selected_video_idx = max(0, self.selected_video_idx - 1)
                            self.scroll_start_time = time.time()
                        elif key == sdl2.SDLK_DOWN:
                            self.selected_video_idx = min(len(self.videos) - 1, self.selected_video_idx + 1)
                            self.scroll_start_time = time.time()
                        elif key in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            if self.videos:
                                item = self.videos[self.selected_video_idx]
                                self.current_video_title = item["title"]
                                self.current_url = item["url"]
                                self.start_load_formats_thread(self.current_url)

                    elif self.state == "SELECT_VIDEO_FORMAT":
                        if key == sdl2.SDLK_UP:
                            self.selected_fmt_idx = max(0, self.selected_fmt_idx - 1)
                            self.scroll_start_time = time.time()
                        elif key == sdl2.SDLK_DOWN:
                            self.selected_fmt_idx = min(len(self.video_formats) - 1, self.selected_fmt_idx + 1)
                            self.scroll_start_time = time.time()
                        elif key in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            if self.video_formats:
                                chosen = self.video_formats[self.selected_fmt_idx]
                                self.chosen_video_id = chosen.get("format_id", "SKIP")
                                has_audio = chosen.get("acodec") and chosen.get("acodec") != "none"

                                if has_audio and self.chosen_video_id != "SKIP":
                                    self.play_video(self.current_url, self.chosen_video_id, is_pure_audio=False)
                                else:
                                    self.selected_fmt_idx = 0
                                    self._list_top_idx = 0
                                    self.scroll_start_time = time.time()
                                    self.state = "SELECT_AUDIO_FORMAT"

                    elif self.state == "SELECT_AUDIO_FORMAT":
                        if key == sdl2.SDLK_UP:
                            self.selected_fmt_idx = max(0, self.selected_fmt_idx - 1)
                            self.scroll_start_time = time.time()
                        elif key == sdl2.SDLK_DOWN:
                            self.selected_fmt_idx = min(len(self.audio_formats) - 1, self.selected_fmt_idx + 1)
                            self.scroll_start_time = time.time()
                        elif key in (sdl2.SDLK_RETURN, sdl2.SDLK_KP_ENTER):
                            if self.audio_formats:
                                chosen = self.audio_formats[self.selected_fmt_idx]
                                self.chosen_audio_id = chosen.get("format_id", "SKIP")

                                if self.chosen_audio_id == "BACK":
                                    self.selected_fmt_idx = 0
                                    self._list_top_idx = 0
                                    self.scroll_start_time = time.time()
                                    self.state = "SELECT_VIDEO_FORMAT"
                                elif self.chosen_video_id == "SKIP" and self.chosen_audio_id == "SKIP":
                                    self.state = "SELECT_VIDEO"
                                elif self.chosen_video_id == "SKIP":
                                    self.play_video(self.current_url, self.chosen_audio_id, is_pure_audio=True)
                                elif self.chosen_audio_id == "SKIP":
                                    self.play_video(self.current_url, self.chosen_video_id, is_pure_audio=False)
                                else:
                                    combined = f"{self.chosen_video_id}+{self.chosen_audio_id}"
                                    self.play_video(self.current_url, combined, is_pure_audio=False)

                elif event.type == sdl2.SDL_TEXTINPUT and self.state == "SEARCH_INPUT":
                    try:
                        raw_bytes = event.text.text
                        text = raw_bytes.decode('utf-8', errors='ignore').split('\x00')[0]
                        
                        if text:
                            clean_text = "".join(ch for ch in text if ch.isprintable())
                            if clean_text:
                                self.cursor_pos = max(0, min(self.cursor_pos, len(self.input_text)))
                                self.input_text = (
                                    self.input_text[:self.cursor_pos] 
                                    + clean_text 
                                    + self.input_text[self.cursor_pos:]
                                )
                                self.cursor_pos += len(clean_text)
                    except Exception as e:
                        print(f"[Warning] TextInput error ignored: {e}")

            self.render()
            sdl2.SDL_Delay(16)

        self.clear_thumb_cache()
        self.stop_video()
        sdl2.SDL_StopTextInput()
        self.font_mgr.close()
        img.IMG_Quit()
        ttf.TTF_Quit()
        sdl2.SDL_DestroyRenderer(self.renderer)
        if hasattr(self, 'child_window') and self.child_window:
            sdl2.SDL_DestroyWindow(self.child_window)
        sdl2.SDL_DestroyWindow(self.window)
        sdl2.SDL_Quit()

    def render_settings(self):
        self.draw_text("⚙️ 設定選單 (↑/↓ 選擇, ←/→ 調整數字或開關, Enter/Esc 儲存離開)", 40, 25, (230, 235, 245, 255))

        items = [
            ("搜尋結果數量", f"{self.config['search_num_results']} 筆"),
            ("快取時間", f"{self.config['cache_time_seconds']} 秒"),
            ("顯示縮圖", "開 (ON)" if self.config.get('show_thumbnails', True) else "關 (OFF)"),
            ("啟用影片留言", "開 (ON)" if self.config.get('enable_comments', True) else "關 (OFF)"),
            ("儲存並返回", "[ Enter 確定 ]")
        ]

        start_y = 80
        card_h = 44
        gap = 12

        for idx, (label, val_str) in enumerate(items):
            y_pos = start_y + idx * (card_h + gap)
            is_sel = (idx == self.settings_selected_idx)

            card_rect = sdl2.SDL_Rect(40, y_pos, self.width - 80, card_h)
            if is_sel:
                self.draw_rounded_rect(card_rect, (45, 52, 70, 255), radius=6)
                indicator = sdl2.SDL_Rect(40, y_pos, 6, card_h)
                self.draw_rounded_rect(indicator, (0, 229, 255, 255), radius=3)
            else:
                self.draw_rounded_rect(card_rect, (30, 33, 44, 180), radius=6)

            text_color = (255, 255, 255, 255) if is_sel else (160, 170, 185, 255)
            self.draw_text(f"{label}:", 60, y_pos + (card_h - 22) // 2, text_color)
            
            val_color = (0, 229, 255, 255) if is_sel else (180, 190, 205, 255)
            self.draw_text(val_str, self.width - 240, y_pos + (card_h - 22) // 2, val_color)


    def render(self):
        sdl2.SDL_SetRenderDrawColor(self.renderer, 24, 25, 32, 255)
        sdl2.SDL_RenderClear(self.renderer)

        if self.state == "SEARCH_INPUT":
            self.draw_text(
                "🔍 輸入關鍵字或直接貼上 YouTube 網址 (Enter: 確認, Esc: 離開):",
                40,
                35,
                (230, 235, 245, 255),
            )
            box_rect = sdl2.SDL_Rect(40, 80, self.width - 80, 52)
            self.draw_rounded_rect(box_rect, (38, 42, 54, 255), radius=8)

            self.cursor_pos = max(0, min(self.cursor_pos, len(self.input_text)))
            left_part = self.input_text[:self.cursor_pos]
            right_part = self.input_text[self.cursor_pos:]
            
            x_pos = 55
            x_pos += self.draw_text(left_part, x_pos, 93, (0, 229, 255, 255))
            x_pos += self.draw_text("▌", x_pos, 93, (255, 255, 255, 255))
            self.draw_text(right_part, x_pos, 93, (0, 229, 255, 255))

        elif self.state == "SEARCHING":
            cx, cy = self.width // 2, self.video_height // 2
            self.draw_spinner(cx, cy - 20)
            self.draw_text("正在搜尋 YouTube 影片...", cx - 110, cy + 30, (0, 229, 255, 255))

        elif self.state == "SELECT_VIDEO":
            self.draw_text("🎬 選擇影片 (方向鍵: 上/下選取, Enter: 確認, Esc: 返回, S: 設定):", 40, 25, (220, 225, 235, 255))
            
            def format_video_item_title(item):
                prefix = "🔴 [LIVE] " if item.get("is_live") else ""
                return f"{prefix}{item['title']}"

            show_thumb_cfg = self.config.get("show_thumbnails", True)

            self.draw_scrollable_list(
                items=self.videos,
                selected_idx=self.selected_video_idx,
                start_y=65,
                card_h=48 if show_thumb_cfg else 38,
                gap=8,
                get_title_func=format_video_item_title,
                show_thumb=show_thumb_cfg
            )

        elif self.state == "LOADING_FORMAT":
            cx, cy = self.width // 2, self.video_height // 2
            self.draw_spinner(cx, cy - 20)
            self.draw_text("正在解析影片畫質與格式資訊...", cx - 140, cy + 30, (0, 229, 255, 255))

        elif self.state == "SELECT_VIDEO_FORMAT":
            self.draw_text("📺 步驟 1/2: 請選擇畫質 (Enter 下一步, Esc 取消):", 40, 25, (220, 225, 235, 255))
            
            def get_fmt_title(f):
                if f.get("format_id") == "SKIP":
                    return f.get("resolution")
                audio_tag = " (含聲音)" if f.get("acodec") and f.get("acodec") != "none" else ""
                return f"{f.get('format_id')} | {f.get('resolution','')} | {f.get('fps','')}fps | {f.get('vcodec','')}{audio_tag}"

            self.draw_scrollable_list(
                items=self.video_formats,
                selected_idx=self.selected_fmt_idx,
                start_y=65,
                card_h=38,
                gap=6,
                get_title_func=get_fmt_title,
                show_thumb=False
            )

        elif self.state == "SELECT_AUDIO_FORMAT":
            self.draw_text("🎵 步驟 2/2: 請選擇音質 (Enter 開始播放, Esc 取消):", 40, 25, (220, 225, 235, 255))
            
            def get_audio_title(f):
                if f.get("format_id") in ("SKIP", "BACK"):
                    return f.get("abr")
                return f"{f.get('format_id')} | {f.get('abr','')}kbps | {f.get('acodec','')}"

            self.draw_scrollable_list(
                items=self.audio_formats,
                selected_idx=self.selected_fmt_idx,
                start_y=65,
                card_h=38,
                gap=6,
                get_title_func=get_audio_title,
                show_thumb=False
            )

        elif self.state == "LOADING_MEDIA":
            # 繪製頂部載入動畫與資訊卡片
            self.draw_loading_media_screen()
            # 同時繪製下方留言區
            self.render_comments_section()

        elif self.state == "PLAYING":
            if self.is_pure_audio:
                self.draw_audio_visualizer()

            # 播放狀態下持續繪製下方留言區
            self.render_comments_section()


        elif self.state == "SETTINGS":
            self.render_settings()

        sdl2.SDL_RenderPresent(self.renderer)

if __name__ == "__main__":
    app = App()
    app.run()

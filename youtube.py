import subprocess
import json

YTDLP = "yt-dlp"

import json
import time
from datetime import datetime

def parse_time_text(c):
    """
    優先使用 yt-dlp 的 time_text，若無則將 timestamp 轉換為相對時間
    """
    # 1. 如果 yt-dlp 有直接提供相對時間文字
    time_text = c.get("time_text")
    if time_text:
        return time_text

    # 2. 如果沒有，改用 UNIX timestamp 計算
    ts = c.get("timestamp")
    if not ts:
        return ""

    try:
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        diff = now - dt

        if diff.days >= 365:
            return f"{diff.days // 365} 年前"
        elif diff.days >= 30:
            return f"{diff.days // 30} 個月前"
        elif diff.days >= 7:
            return f"{diff.days // 7} 週前"
        elif diff.days > 0:
            return f"{diff.days} 天前"
        elif diff.seconds >= 3600:
            return f"{diff.seconds // 3600} 小時前"
        elif diff.seconds >= 60:
            return f"{diff.seconds // 60} 分鐘前"
        else:
            return "剛剛"
    except Exception:
        return ""


def run_yt_dlp(args):
    cmd = [
        YTDLP,
        "--js-runtimes", "deno",
        "--remote-components", "ejs:github",
    ] + args

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        return None
    return result.stdout

def search(keyword, limit=10):
    out = run_yt_dlp([
        "--flat-playlist",
        "--dump-json",
        "--skip-download",
        f"ytsearch{limit}:{keyword}"
    ])

    if not out:
        return []

    result = []
    for line in out.splitlines():
        try:
            data = json.loads(line)
            vid = data.get("id")
            
            # 判斷是否為直播
            is_live = (
                data.get("is_live") is True 
                or data.get("live_status") == "is_live"
            )

            # 提取 120x90 (~3.6KB) 微型縮圖網址
            thumb_url = f"https://i.ytimg.com/vi/{vid}/default.jpg" if vid else None

            result.append({
                "title": data.get("title", "未知影片"),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "is_live": is_live,
                "thumb_url": thumb_url
            })
        except:
            pass
    return result


def get_info(url):
    out = run_yt_dlp(["-J", url])
    if not out:
        return None
    return json.loads(out)


def get_comments_tree(url, max_comments=20):
    """
    擷取指定影片的留言
    """
    out = run_yt_dlp([
        "-J",
        "--write-comments",
        "--extractor-args", f"youtube:max_comments={max_comments}", 
        "--skip-download",
        url
    ])
    
    if not out:
        return []

    try:
        data = json.loads(out)
        raw_comments = data.get("comments", [])
        if not raw_comments:
            return []

        comment_map = {}
        root_comments = []

        # 第一階段：建立所有 node 字典
        for c in raw_comments:
            c_id = c.get("id")
            parent_id = c.get("parent")

            node = {
                "id": c_id,
                "author": c.get("author", "匿名"),
                "text": c.get("text", "").strip(),
                "like_count": c.get("like_count", 0),
                "time_text": parse_time_text(c),  # 【修改】雙重支援：time_text 與 timestamp 補全
                "parent": parent_id,
                "replies": [],
                "is_expanded": False
            }
            comment_map[c_id] = node


        # 第二階段：建立父子樹狀關係
        for c_id, node in comment_map.items():
            parent_id = node["parent"]
            if parent_id == "root" or parent_id not in comment_map:
                root_comments.append(node)
            else:
                comment_map[parent_id]["replies"].append(node)

        if raw_comments:
            print("留言範例資料:", raw_comments[0].get("time_text"), raw_comments[0].get("timestamp"))

        return root_comments
    except Exception as e:
        print(f"[Error] Failed to parse comments: {e}")
        return []


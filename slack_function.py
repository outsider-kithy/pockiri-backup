import os
import re
import json
import time
import requests
from flask import render_template
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime
from dotenv import load_dotenv
from google.cloud import storage

# OSの環境変数や引数で環境を指定
env_mode = os.getenv("ENV_MODE", "development")
dotenv_file = f".env.{env_mode}"
load_dotenv(dotenv_path=dotenv_file)

slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
ARCHIVE_ROOT = os.getenv("ARCHIVE_ROOT")

# Cloud Storage設定
BUCKET_NAME = os.getenv("BUCKET_NAME")
JOINED_CHANNELS_FILE = os.getenv("JOINED_CHANNELS_FILE")
# ローカル開発時のみキーを使う
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path and os.path.exists(credentials_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
    print(f"[INFO] Using local credentials from {credentials_path}")
else:
    print("[INFO] Using default Cloud credentials (Cloud Run mode)")
storage_client = storage.Client()

# ユーザーidとユーザー名の辞書を作成
def get_user_map():
    users = slack.users_list()["members"]
    return {u["id"]: u["profile"]["display_name"] or u["name"] for u in users}

# チャンネルidとチャンネル名の辞書を作成
def get_channel_map():
    channels = slack.conversations_list(limit=100)["channels"]
    return {c["id"]: c["name"] for c in channels}

# Cloud Storageにjoined_channels.jsonを保存
def save_joined_channels_to_gcs(joined_channels):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(JOINED_CHANNELS_FILE)
    blob.upload_from_string(
        json.dumps(joined_channels, ensure_ascii=False, indent=2),
        content_type="application/json"
    )
    print(f"{JOINED_CHANNELS_FILE} を GCS に保存しました")

def load_joined_channels_from_gcs():
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(JOINED_CHANNELS_FILE)
    if blob.exists():
        data = blob.download_as_text()
        return json.loads(data)
    return []

#GCSのJSONファイルを空にする
def clear_joined_channels_in_gcs(bucket_name, blob_name):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json.dumps([], ensure_ascii=False, indent=2), content_type="application/json")
        print(f"{blob_name} を空にしました")
    except Exception as e:
        print(f"GCSクリア失敗: {e}")

# Slackの絵文字一覧を取得
def get_emoji_map():
    try:
        response = slack.emoji_list()
        emoji_map = response["emoji"]
        return emoji_map  # 例: {"uoo": "https://emoji.slack-edge.com/T123/uoo/abcd.png", "party": "https://..."}
    except SlackApiError as e:
        print(f"⚠️ Failed to fetch emoji list: {e.response['error']}")
        return {}

# 絵文字を画像に変換
def replace_emoji(text, emoji_map):
    def repl(match):
        name = match.group(1)
        url = emoji_map.get(name)
        if url:
            # カスタム絵文字画像を表示
            return f'<img src="{url}" alt=":{name}:" class="emoji">'
        else:
            # 標準絵文字ならUnicodeのまま表示
            return f":{name}:"
    return re.sub(r":([a-zA-Z0-9_\-\+]+):", repl, text)

# チャンネルに投稿されたファイルをダウンロード・保存
def download_file(url, dest_path, headers=None):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        r = requests.get(url, headers=headers, stream=True)
        if r.status_code == 200:
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            return True
    except Exception as e:
        print(f"❌ Failed to download {url}: {e}")
    return False


# 取得したチャンネルの情報をHTMLに出力
def export_channel_to_html(channel_id, channel_name, workspace, channels, archive_dir, all_histories):
    """
    チャンネル履歴を取得してHTML出力。
    all_histories: fetch_all_channel_histories() の戻り値
    """

    # --- 今回のチャンネルのメッセージを抽出 ---
    messages = []
    for ch in all_histories:
        if ch["channel_id"] == channel_id:
            messages = ch["messages"]
            break

    # --- HTMLレンダリング ---
    html = render_template(
        "slack_view.html",
        channel_name=channel_name,
        messages=messages,
        workspace=workspace,
        channels=channels,
        date=datetime.now().strftime("%Y-%m-%d"),
    )

    filepath = f"{archive_dir}/{channel_name}.html"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"📝 Exported {filepath}")



# すべてのチャンネルの履歴を取得
def fetch_all_channel_histories():

    user_map = get_user_map()
    channel_map = get_channel_map()
    emoji_map = get_emoji_map() 
    joined_channels = load_joined_channels_from_gcs()
    user_cache = {}
    all_histories = []

    try:
        # --- 1. 全チャンネルを取得 ---
        cursor = None
        channels = []
        while True:
            channels_response = slack.conversations_list(types="public_channel", limit=100, cursor=cursor)
            channels.extend(channels_response["channels"])
            cursor = channels_response["response_metadata"].get("next_cursor")
            if not cursor:
                break

        # --- 2. 参加済みチャンネルのキャッシュを読み込み ---
        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel.get("name")

            print(f"\n=== {channel_name} ({channel_id}) ===")

            # --- 3. Botが未参加なら join ---
            if channel_id not in joined_channels:
                try:
                    slack.conversations_join(channel=channel_id)
                    print(f"✅ Joined channel: {channel_name}")
                    joined_channels.append(channel_id)
                    time.sleep(1.5)  # ✅ Rate Limit緩和のため待機
                except SlackApiError as e:
                    err = e.response["error"]
                    if err == "already_in_channel":
                        print(f"⏩ Already in {channel_name}")
                        joined_channels.append(channel_id)
                    elif err == "method_not_supported_for_channel_type":
                        print(f"⚠️ {channel_name} は特殊チャンネルのためスキップ")
                        continue
                    elif err == "not_in_channel":
                        print(f"⚠️ {channel_name} に参加できませんでした")
                        continue
                    else:
                        print(f"⚠️ Failed to join {channel_name}: {err}")
                        continue
            else:
                print(f"⏩ Skipping join (already known): {channel_name}")

            # --- 3. 履歴を取得 ---
            try:
                history_response = slack.conversations_history(channel=channel_id, limit=10)
                time.sleep(1) 
                messages = history_response["messages"]
                formatted = []

                for msg in messages:
                    user_id = msg.get("user")
                    user_name = user_map.get(user_id, "Unknown")
                    user_icon = "/static/default_avatar.png"  # デフォルトアイコン

                    # --- ユーザー情報を取得 --- #
                    if user_id:
                        if user_id not in user_cache:
                            try:
                                user_info = slack.users_info(user=user_id)
                                profile = user_info["user"]["profile"]
                                user_name = profile.get("display_name") or profile.get("real_name") or "Unknown"
                                user_icon = profile.get("image_48")
                                user_cache[user_id] = (user_name, user_icon)
                            except SlackApiError as e:
                                print(f"⚠️ Failed to fetch user info for {user_id}: {e.response['error']}")
                    else:
                        user_name, user_icon = user_cache[user_id]

                    # --- メッセージ本文をHTML整形 --- #
                    raw_text = msg.get("text", "")
                    # 🔽 メンションやチャンネル名を置換
                    text_html = replace_mentions(raw_text, user_map, channel_map)
                    # 絵文字を画像に変換
                    text_html = replace_emoji(text_html, emoji_map)
                    text_html = text_html.replace("\n", "<br>")

                    # -- アバター -- #
                    today = datetime.now().strftime("%Y-%m-%d")
                    avatar_dir = os.path.join(ARCHIVE_ROOT, today, "avatar")

                    if user_id in user_cache:
                        user_name, user_icon_url = user_cache[user_id]
                    else:
                        user_icon_url = "/static/img/default_avatar.png"

                    if user_icon_url.startswith("http"):
                        avatar_filename = f"{user_id}.png"
                        avatar_dest = os.path.join(avatar_dir, avatar_filename)
                        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                        success = download_file(user_icon_url, avatar_dest, headers=headers)
                        if success:
                            print(f"✅ Downloaded avatar for {user_name} ({user_id})")
                            user_icon = os.path.join("avatar", avatar_filename)
                        else:
                            user_icon = "/static/img/default_avatar.png"
                    else:
                        user_icon = user_icon_url

                    # --- 添付ファイル --- #
                    files = []
                    if "files" in msg:
                        for f in msg.get("files", []):
                            filename = f.get("name")
                            mimetype = f.get("mimetype")
                            url_private = f.get("url_private")

                            # Slack APIトークンを認証ヘッダーとして渡す
                            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

                            # ファイルをダウンロード
                            # 保存先パス（例: archive/YYYY-MM-DD/media/<channel_name>/<filename>）
                            dest_path = os.path.join(ARCHIVE_ROOT, today, "media", channel_id, filename)
                            success = download_file(url_private, dest_path, headers=headers)
                            
                            if success:
                                print(f"✅ Downloaded {filename} to {dest_path}")
                            else:
                                print(f"⚠️ Failed to download {filename}")

                            # ファイル情報に local_path を追加してテンプレートで利用
                            files.append({
                                "name": filename,
                                "mimetype": mimetype,
                                "url_private": url_private,
                                "local_path": os.path.join("media", channel_id, filename),  
                            })

                    # --- リアクション --- #
                    reactions = []
                    for r in msg.get("reactions", []):
                        name = r.get("name")
                        if name in emoji_map:
                            emoji_html = f'<img src="{emoji_map[name]}" alt=":{name}:" class="emoji">'
                        else:
                            emoji_html = f":{name}:"

                        reactions.append({
                            "emoji_html": emoji_html,
                            "count": r.get("count")
                        })

                    # --- スレッド（リプライ）取得 --- #
                    replies = []
                    if "reply_count" in msg and "thread_ts" in msg and msg["reply_count"] <= 5:
                        try:
                            thread_resp = slack.conversations_replies(channel=channel_id, ts=msg["thread_ts"])
                            time.sleep(1) 
                            for reply in thread_resp["messages"][1:]:  # 0番目は親メッセージ
                                r_user = reply.get("user")
                                r_user_name = "Unknown"
                                if r_user:
                                    try:
                                        uinfo = slack.users_info(user=r_user)
                                        r_user_name = uinfo["user"]["profile"].get("display_name") or uinfo["user"]["name"]
                                    except SlackApiError:
                                        pass
                                replies.append({
                                    "user_name": r_user_name,
                                    "text_html": reply.get("text", "").replace("\n", "<br>"),
                                    "timestamp": datetime.fromtimestamp(float(reply["ts"].split(".")[0])).strftime("%Y-%m-%d %H:%M:%S")
                                })
                        except SlackApiError as e:
                            print(f"⚠️ Failed to fetch thread for {msg['ts']}: {e.response['error']}")

                    # --- タイムスタンプ変換 --- #
                    ts_str = msg.get("ts", "0").split(".")[0]
                    timestamp = datetime.fromtimestamp(float(ts_str)).strftime("%Y-%m-%d %H:%M:%S")

                    # --- まとめて整形 --- #
                    formatted.append({
                        "user_name": user_name,
                        "user_icon": user_icon,
                        "text_html": text_html,
                        "timestamp": timestamp,
                        "files": files,
                        "reactions": reactions,
                        "replies": replies,
                    })

                all_histories.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "messages": list(reversed(formatted))
                })

            except SlackApiError as e:
                print(f"⚠️  Failed to get history for {channel_name}: {e.response['error']}")
                return []

    except SlackApiError as e:
        print(f"❌ conversations.list failed: {e.response['error']}")

    save_joined_channels_to_gcs(joined_channels)
    return all_histories


#  本文内のチャンネルidとユーザーidを実際のユーザー名・チャンネル名に置き換える
def replace_mentions(text, user_map, channel_map):
    if not text:
        return text

    # ユーザーIDを@ユーザー名に変換
    for uid, uname in user_map.items():
        text = text.replace(f"<@{uid}>", f'<span class="mention">@{uname}</span>')

    # チャンネルIDを#チャンネル名に変換
    for cid, cname in channel_map.items():
        text = text.replace(f"<#{cid}>", f'<span class="channel-mention">#{cname}</span>')

    return text

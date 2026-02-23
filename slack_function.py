import os
import re
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google.cloud import storage
import re, html
from jinja2 import Environment, FileSystemLoader

# OSの環境変数や引数で環境を指定
env_mode = os.getenv("ENV_MODE", "development")
dotenv_file = f".env.{env_mode}"
load_dotenv(dotenv_path=dotenv_file)

slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
ARCHIVE_ROOT = os.getenv("ARCHIVE_ROOT")
ARCHIVE_DOMAIN = os.getenv("ARCHIVE_DOMAIN")

# Cloud Storage設定
STORAGE_DOMAIN=os.getenv("STORAGE_DOMAIN")
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

# すでにGCS上に画像があるかどうかをチェック
def gcs_file_exists(bucket_name, object_path):

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_path)

    return blob.exists()

# 添付ファイルをGCSにダウンロード
def download_file_to_gcs(url, bucket_name, gcs_object_path, headers=None):
    """
    URLのファイルをダウンロードし、GCS に保存する。
    ローカルには /tmp 内に一時保存する。
    """
    # --- 一時ファイルのローカルパス ---
    local_tmp_path = f"/tmp/{os.path.basename(gcs_object_path)}"
    os.makedirs(os.path.dirname(local_tmp_path), exist_ok=True)

    # --- ダウンロード ---
    try:
        r = requests.get(url, headers=headers, stream=True)
        if r.status_code == 200:
            with open(local_tmp_path, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
        else:
            print(f"❌ Failed to download: {url} (status {r.status_code})")
            return False
    except Exception as e:
        print(f"❌ Exception downloading {url}: {e}")
        return False

    # --- GCS にアップロード ---
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(gcs_object_path)

        blob.upload_from_filename(local_tmp_path)
        print(f"📤 Uploaded to GCS: gs://{bucket_name}/{gcs_object_path}")

        return True

    except Exception as e:
        print(f"❌ GCS upload failed: {e}")
        return False

# Botが参加していないチャンネルの場合は参加させる
def ensure_bot_joined(slack_client, channel_id):

    try:
        slack_client.conversations_join(channel=channel_id)

    except SlackApiError as e:

        error_code = e.response["error"]

        if error_code == "already_in_channel":
            pass

        elif error_code == "method_not_supported_for_channel_type":
            print(f"Private channel: {channel_id}")

        elif error_code == "not_in_channel":
            print(f"Bot cannot join: {channel_id}")

        else:
            print(f"Unexpected error: {error_code}")


# 取得したチャンネルの情報をHTMLに出力
def export_channel_to_html(channel_name, workspace, channels, all_histories):

    # --- 今回のチャンネルのメッセージを抽出 ---
    messages = all_histories

    user_cache = {}
    formatted_messages = format_messages(
        messages,
        slack_client=slack,
        user_cache=user_cache
    )

    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("slack_view.html")

    html = template.render(
        channel_name=channel_name,
        messages = formatted_messages,
        workspace=workspace,
        channels = channels,
        date = datetime.now().strftime("%Y-%m-%d"),
    )

    # --- 置き換え後（GCS 保存） ---
    date_str = datetime.now().strftime("%Y-%m-%d")
    object_name = f"{date_str}/{channel_name}.html"

    bucket = storage.Client().bucket(BUCKET_NAME)
    blob = bucket.blob(object_name)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    print(f"📝 Exported to GCS: gs://{BUCKET_NAME}/{object_name}")



# チャンネル内の全てのメッセージをリプライ込みで取得
def fetch_all_messages_with_threads(client, channel_id):

    all_messages = []
    cursor = None

    while True:

        params = {
            "channel": channel_id,
            "limit": 200
        }

        if cursor:
            params["cursor"] = cursor

        res = client.conversations_history(**params)

        messages = res.get("messages", [])

        for m in messages:

            # --- スレッド有無判定 ---
            if m.get("reply_count", 0) > 0:

                thread_ts = m["thread_ts"]

                replies = fetch_thread_replies(
                    client,
                    channel_id,
                    thread_ts
                )

                m["replies_full"] = replies

            else:
                m["replies_full"] = []

        print(messages)
        all_messages.extend(messages)

        cursor = res.get("response_metadata", {}).get("next_cursor")

        if not cursor:
            break

    return all_messages



# ワークスペース内の全てのチャンネルを取得
def get_all_channels():

    all_channels = []
    cursor = None

    while True:

        params = {
            "types": "public_channel,private_channel",
            "limit": 200
        }

        if cursor:
            params["cursor"] = cursor

        result = slack.conversations_list(**params)

        all_channels.extend(result["channels"])

        cursor = result.get("response_metadata", {}).get("next_cursor")

        if not cursor:
            break

    return all_channels

# メッセージを正規化
def format_messages(messages, slack_client, user_cache):

    formatted = []

    for m in messages:

        msg = {}

        # --- ユーザー名変換 ---
        user_id = m.get("user") or m.get("bot_id")

        user_info = None

        if user_id:
            user_info = user_cache.get(user_id)

            if not user_info:
                try:
                    res = slack_client.users_info(user=user_id)
                    user_info = res["user"]
                except Exception:
                    # Botや削除ユーザー対応
                    user_info = {
                        "real_name": m.get("username", "Unknown"),
                        "name": m.get("username", "Unknown"),
                        "profile": {}
                    }

                user_cache[user_id] = user_info

        else:
            user_info = {
                "real_name": "Unknown",
                "name": "Unknown",
                "profile": {}
            }

        avatar_url = format_avatars(
            user_info,
            bucket_name=BUCKET_NAME,
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        )
        msg["user_icon"] = avatar_url

        # --- 時刻 ---
        msg["ts"] = format_ts(m.get("ts"))

        # --- 本文整形 ---
        msg["text"] = format_slack_text(m.get("text", ""))

        # --- ファイル ---
        msg["files"] = format_files(m.get("files", []))

        # --- リアクション ---
        msg["reactions"] = format_reactions(m.get("reactions", []))

        # --- リプライ ---
        replies = []

        for r in m.get("replies_full", []):
            reply_user_id = r.get("user")
            reply_user_info = None

            if reply_user_id:
                reply_user_info = user_cache.get(reply_user_id)

                if not reply_user_info:
                    try:
                        res = slack_client.users_info(user=reply_user_id)
                        reply_user_info = res["user"]
                    except Exception:
                        reply_user_info = {
                            "real_name": "Unknown",
                            "name": "Unknown",
                            "profile": {}
                        }

                    user_cache[reply_user_id] = reply_user_info

            else:
                reply_user_info = {
                    "real_name": "Unknown",
                    "name": "Unknown",
                    "profile": {}
                }

            replies.append({
                "user_name": reply_user_info.get("name", "Unknown"),
                "timestamp": format_ts(r.get("ts")),
                "text": format_slack_text(r.get("text", ""))
            })

        msg["replies"] = replies

        formatted.append(msg)

    return formatted


# メッセージ本文の正規化
def format_slack_text(text, user_map=None):

    if not text:
        return ""

    # --- HTMLエスケープ ---
    text = html.escape(text)

    # --- <url|label> ---
    text = re.sub(
        r'&lt;(https?://[^|]+)\|([^&]+)&gt;',
        r'<a href="\1" target="_blank">\2</a>',
        text
    )

    # --- <url> ---
    text = re.sub(
        r'&lt;(https?://[^&]+)&gt;',
        r'<a href="\1" target="_blank">\1</a>',
        text
    )

    # --- mailto ---
    text = re.sub(
        r'&lt;mailto:([^|]+)\|([^&]+)&gt;',
        r'<a href="mailto:\1">\2</a>',
        text
    )

    # --- 改行 ---
    text = text.replace("\n", "<br>")

    return text

# アバターのダウンロード・正規化
def format_avatars(user_info, bucket_name, headers=None):

    if not user_info:
        return ""

    profile = user_info.get("profile", {})
    avatar_url = profile.get("image_72") or profile.get("image_48")

    if not avatar_url:
        return ""

    user_id = user_info.get("id", "unknown")
    filename = f"{user_id}.jpg"
    gcs_object_path = f"avatars/{filename}"

    public_url = f"https://storage.cloud.google.com/{bucket_name}/{gcs_object_path}"

    # --- 既存チェック ---
    if not gcs_file_exists(bucket_name, gcs_object_path):

        download_file_to_gcs(
            avatar_url,
            bucket_name,
            gcs_object_path,
            headers=headers
        )

    return public_url


# 添付ファイルの正規化
def format_files(files):

    formatted = []

    today = datetime.now().strftime("%Y-%m-%d")

    for f in files:
        filename = (
            f.get("name")
            or f.get("title")
            or f"{f.get('id', 'file')}.dat"
        )
        if not isinstance(filename, str):
            filename = str(filename)

        mimetype = f.get("mimetype") or ""
        url_private = f.get("url_private")
        url_public = f"{STORAGE_DOMAIN}/{BUCKET_NAME}/{today}/media/{filename}"

         # 外部ファイルはスキップ
        if not url_private:
            print(f"⚠️ Skipping external file: {filename}")
            formatted.append({
                "name": filename,
                "mimetype": mimetype,
                "url_private": None,
                "url_public": None,
            })
            continue

        # Slack APIトークンを認証ヘッダーとして渡す
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

        # ファイルをダウンロード
        gcs_object_path = f"{today}/media/{filename}"
        success = download_file_to_gcs(url_private, BUCKET_NAME, gcs_object_path, headers=headers)

        if success:
            print(f"✅ Downloaded {filename} to {url_private}")
        else:
            print(f"⚠️ Failed to download {filename}")

        formatted.append({
            "name": filename,
            "mimetype": mimetype,
            "url_private": url_private,
            "url_public": url_public,
        })

    return formatted

# リアクションの変換
def format_reactions(reactions):

    formatted = []

    for r in reactions:

        emoji_name = r["name"]

        emoji_html = f"""
        <img src="https://emoji.slack-edge.com/{emoji_name}.png"
             class="emoji">
        """

        formatted.append({
            "name": emoji_name,
            "count": r["count"],
            "emoji_html": emoji_html
        })

    return formatted

# タイムスタンプの変換
JST = timezone(timedelta(hours=9))

def format_ts(ts):

    if not ts:
        return ""

    ts_float = float(ts)

    dt = datetime.fromtimestamp(ts_float, JST)

    return dt.strftime("%Y/%m/%d %H:%M")

# リプライを全件取得
def fetch_thread_replies(client, channel_id, thread_ts):

    replies = []
    cursor = None

    while True:

        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": 200
        }

        if cursor:
            params["cursor"] = cursor

        res = client.conversations_replies(**params)

        messages = res.get("messages", [])

        # 1件目は親なので除外
        replies.extend(messages[1:])

        cursor = res.get("response_metadata", {}).get("next_cursor")

        if not cursor:
            break

    return replies
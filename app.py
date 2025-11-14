import os
import re
import json
from flask import Flask, Response, url_for
from flask_httpauth import HTTPBasicAuth
from slack_sdk import WebClient
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError
from slack_function import export_channel_to_html, fetch_all_channel_histories
from datetime import datetime
from google.cloud import storage

# OSの環境変数や引数で環境を指定
env_mode = os.getenv("ENV_MODE", "development")
dotenv_file = f".env.{env_mode}"
load_dotenv(dotenv_path=dotenv_file)

app = Flask(__name__)
slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# BASIC認証
auth = HTTPBasicAuth()

def load_users():
    with open("users.json", "r", encoding="utf-8") as f:
        return json.load(f)

users = load_users()

@auth.get_password
def get_pw(username):
    if username in users:
        return users.get(username)
    return None

ARCHIVE_DOMAIN = os.getenv("ARCHIVE_DOMAIN")
ARCHIVE_ROOT = os.getenv("ARCHIVE_ROOT")
REPORT_CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID")

BUCKET_NAME = os.getenv("BUCKET_NAME")

PORT = os.getenv("PORT")

# Jinja2環境設定
env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template("slack_view.html")

# /captureルート
@app.route("/capture", methods=["POST"])
def capture_channels():
    """全チャンネルを自動参加 → 履歴取得 → HTML書き出し"""

    # --- 日付ディレクトリを準備 --- #
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir = f'archive/{date_str}'
    
    # --- ワークスペース情報を取得 --- #
    workspace_info = slack.team_info()
    workspace = workspace_info["team"]["name"]

    # --- チャンネル一覧を取得 --- #
    try:
        channels_response = slack.conversations_list(types="public_channel,private_channel", limit=1000)
        channels = channels_response["channels"]
    except SlackApiError as e:
        return f"❌ Failed to list channels: {e.response['error']}", 500

    # --- 1. すべてのチャンネルの履歴を1回だけ取得 --- #
    all_histories = fetch_all_channel_histories()

    # --- 2. 各チャンネルをHTMLに出力 --- #
    for ch in channels:
        channel_id = ch["id"]
        channel_name = ch.get("name")

        try:
            export_channel_to_html(
                channel_id,
                channel_name,
                workspace,
                channels,
                archive_dir,
                all_histories  # 👈 ここで全履歴を渡す
            )
        except SlackApiError as e:
            if e.response["error"] == "not_in_channel":
                print(f"⚠️ Skipping {channel_name} (bot not in channel)")
                continue
            else:
                print(f"⚠️ Error in {channel_name}: {e.response['error']}")
                continue

    slack.chat_postMessage(
        channel=REPORT_CHANNEL_ID,
        text=f"過去90日の履歴をバックアップしました。\n {ARCHIVE_DOMAIN}view/{date_str} で閲覧できます。"
    )

    return "Archived."

# /viewルート
@app.route("/view")
def view_list():
    client = storage.Client()

    # archive 下のオブジェクト一覧を取得
    blobs = client.list_blobs(BUCKET_NAME)

    # YYYY-mm-dd に一致するフォルダ名を抽出
    date_dirs = set()

    pattern = re.compile(r"(\d{4}-\d{2}-\d{2})/")

    for blob in blobs:
        m = pattern.match(blob.name)
        if m:
            date_dirs.add(m.group(1))

    # ソート（新しい日付が上）
    sorted_dates = sorted(date_dirs, reverse=True)

    # HTML生成
    html = "<h1>バックアップ一覧</h1><ul>"
    for d in sorted_dates:
        html += f'<li><a href="/view/{d}">{d}</a></li>'
    html += "</ul>"

    return Response(html, mimetype="text/html")

# /view/YYYY-mm-dd ルート
@app.route("/view/<date>", methods=["GET"])
def view_date(date):
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)

    # 例: "2025-11-14"
    prefix = f"{date}/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    # .html のみ抽出
    html_files = [b.name for b in blobs if b.name.endswith(".html")]

    if not html_files:
        return f"<h2>{date} のHTMLファイルはありません</h2>"

    # シンプルなHTML生成
    links = []
    for file_name in html_files:
        # /view/<path> 側の既存のビューアにリンクさせる
        url = url_for("view_file", object_name=file_name)
        links.append(f'<li><a href="{url}">{file_name}</a></li>')

    html = f"""
        <h2>{date} のアーカイブ一覧</h2>
        <ul>
            {''.join(links)}
        </ul>
        <p><a href="/view">← 日付一覧に戻る</a></p>
    """

    return html


# /view/YYYY-mm-dd/hoge.html ルート
@app.route("/view/<path:object_name>",  strict_slashes=True)
@auth.login_required
def view_file(object_name):

    # GCS から該当ファイルを取得
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(object_name)

    html = blob.download_as_text()

    return Response(html, mimetype="text/html")

# 起動
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(PORT))
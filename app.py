import os
import json
from flask import Flask, send_from_directory, abort
from flask_httpauth import HTTPBasicAuth
from slack_sdk import WebClient
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError
from slack_function import export_channel_to_html, fetch_all_channel_histories
from datetime import datetime

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

PORT = os.getenv("PORT")

# Jinja2環境設定
env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template("slack_view.html")

# /captureルート
@app.route("/capture", methods=["GET"])
def capture_channels():
    """全チャンネルを自動参加 → 履歴取得 → HTML書き出し"""

    # --- 日付ディレクトリを準備 --- #
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir = f'archive/{date_str}'
    os.makedirs(f"{archive_dir}/avatar", exist_ok=True)
    os.makedirs(f"{archive_dir}/media", exist_ok=True)

    ARCHIVE_URL = os.path.join(ARCHIVE_DOMAIN, archive_dir)

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
        text=f"過去90日の履歴をバックアップしました。\n {ARCHIVE_URL} で閲覧できます。"
    )

    return "Archived."


# /archiveルート
@app.route("/archive", strict_slashes=True)
@auth.login_required
def archive_root():
    if not os.path.exists(ARCHIVE_ROOT):
        return "<h1>アーカイブが存在しません</h1>", 404

    # archive/ 内のディレクトリを取得
    dates = [d for d in os.listdir(ARCHIVE_ROOT) if os.path.isdir(os.path.join(ARCHIVE_ROOT, d))]
    dates.sort(reverse=True)  # 新しい順に表示

    # HTMLを簡易生成（クリックで各日付ページへ）
    html = "<h1>アーカイブ一覧</h1><ul>"
    for date in dates:
        html += f'<li><a href="/archive/{date}">{date}</a></li>'
    html += "</ul>"
    return html

# /archive/YYYY-mm-ddルート（チャンネル一覧）
@app.route("/archive/<date>", strict_slashes=True)
@auth.login_required
def archive_index(date):
    archive_dir = os.path.join(ARCHIVE_ROOT, date)
    if not os.path.exists(archive_dir):
        return f"<h1>{date} のアーカイブは存在しません</h1>", 404

    # ディレクトリ内のHTMLファイルをリスト化
    files = [f for f in os.listdir(archive_dir) if f.endswith(".html")]
    html = "<h1>{}のアーカイブ</h1><ul>".format(date)
    for f in files:
        html += f'<li><a href="/archive/{date}/{f}">{f}</a></li>'
    html += "</ul>"
    return html

# /archive/YYYY-mm-dd/channelルート（チャンネル詳細）
@app.route("/archive/<date>/<path:filename>", strict_slashes=True)
@auth.login_required
def serve_archive(date, filename):
    archive_dir = os.path.join(ARCHIVE_ROOT, date)
    if not os.path.exists(archive_dir):
        abort(404)
    return send_from_directory(archive_dir, filename)

# 起動
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(PORT))
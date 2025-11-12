import os
from flask import Flask, send_from_directory, abort
from slack_sdk import WebClient
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError
from slack_function import export_channel_to_html, get_channel_list

load_dotenv()

app = Flask(__name__)
slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

ARCHIVE_ROOT = "archive"

# Jinja2環境設定
env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template("slack_view.html")

# /captureルート
@app.route("/capture", methods=["GET"])
def capture_channels():

    # --- チャンネル情報 --- #
    channels = get_channel_list()

    # --- ワークスペース情報 ---#
    workspace_info = slack.team_info()
    team = workspace_info["team"]
    workspace = team["name"]

    for ch in channels:
        try:
            export_channel_to_html(ch["id"], ch["name"], workspace)
        except SlackApiError as e:
            if e.response["error"] == "not_in_channel":
                print(f"⚠️ Skipping {ch['name']} (bot not in channel)")
                continue
        
    return "Archived."


# /archiveルート
@app.route("/archive/")
def archive_root():
    if not os.path.exists(ARCHIVE_ROOT):
        return "<h1>アーカイブが存在しません</h1>", 404

    # archive/ 内のディレクトリを取得
    dates = [d for d in os.listdir(ARCHIVE_ROOT) if os.path.isdir(os.path.join(ARCHIVE_ROOT, d))]
    dates.sort(reverse=True)  # 新しい順に表示

    # HTMLを簡易生成（クリックで各日付ページへ）
    html = "<h1>アーカイブ一覧</h1><ul>"
    for date in dates:
        html += f'<li><a href="/archive/{date}/">{date}</a></li>'
    html += "</ul>"
    return html

# /archive/YYYY-mm-ddルート（チャンネル一覧）
@app.route("/archive/<date>/")
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
@app.route("/archive/<date>/<path:filename>")
def serve_archive(date, filename):
    archive_dir = os.path.join(ARCHIVE_ROOT, date)
    if not os.path.exists(archive_dir):
        abort(404)
    return send_from_directory(archive_dir, filename)
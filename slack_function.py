import os
import requests
from flask import render_template
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime
import markdown2

slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

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

#チャンネル内の全投稿・リアクションなどのデータを取得
def fetch_channel_history(channel_id, archive_dir):
    messages_data = []
    try:
        response = slack.conversations_history(channel=channel_id)
       
        for msg in response["messages"]:
            # --- ユーザー情報 ---
            user_info = slack.users_info(user=msg.get("user", ""))["user"]
            user_name = user_info["profile"]["real_name"]
            user_icon_url = user_info["profile"]["image_72"]

            # --- アイコンをローカル保存 ---
            icon_filename = f"{user_info['id']}.png"
            local_icon_path = os.path.join(archive_dir, "avatars", icon_filename)
            if not os.path.exists(local_icon_path):
                download_file(
                    user_icon_url,
                    local_icon_path,
                )
            user_icon_local = f"avatars/{icon_filename}"

            # --- 投稿本文 ---
            text_html = markdown2.markdown(msg.get("text", ""))

            # --- ファイル添付処理 ---
            files_local = []
            for f in msg.get("files", []):
                file_url = f.get("url_private", "")
                file_name = f.get("name", "")
                mimetype = f.get("mimetype", "")
                local_path = os.path.join(archive_dir, "media", file_name)

                if file_url:
                    # Slack APIの認証トークンをヘッダーに付けてダウンロード
                    download_file(
                        file_url,
                        local_path,
                        headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
                    )

                files_local.append({
                    "name": file_name,
                    "mimetype": mimetype,
                    "local_path": f"media/{file_name}"
                })

            # --- スレッド取得 ---
            replies = []
            if msg.get("reply_count", 0) > 0:
                thread = slack.conversations_replies(channel=channel_id, ts=msg["ts"])
                for reply in thread["messages"][1:]:
                    uinfo = slack.users_info(user=reply.get("user", ""))["user"]
                    replies.append({
                        "user_name": uinfo["profile"]["real_name"],
                        "text_html": markdown2.markdown(reply.get("text", "")),
                        "timestamp": datetime.fromtimestamp(float(reply["ts"])).strftime("%H:%M")
                    })

            messages_data.append({
                "user_name": user_name,
                "user_icon": user_icon_local,
                "timestamp": datetime.fromtimestamp(float(msg["ts"])).strftime("%Y-%m-%d %H:%M"),
                "text_html": text_html,
                "reactions": msg.get("reactions", []),
                "files": files_local,
                "replies": replies
            })

    except SlackApiError as e:
        print(f"Slack API error: {e}")

    return messages_data


# 取得したチャンネルの情報をHTMLに出力
def export_channel_to_html(channel_id, channel_name, workspace):
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir = f"archive/{date_str}"
    os.makedirs(f"{archive_dir}/avatars", exist_ok=True)
    os.makedirs(f"{archive_dir}/media", exist_ok=True)

    messages = fetch_channel_history(channel_id, archive_dir)

    channels = get_channel_list()

    html = render_template("slack_view.html", channel_name=channel_name, messages=messages, workspace=workspace, channels=channels, date=date_str)
    with open(f"{archive_dir}/{channel_name}.html", "w", encoding="utf-8") as f:
        f.write(html)


#チャンネル一覧を取得
def get_channel_list():
    try:
        response = slack.conversations_list(limit=100)
        channels = response["channels"]
        # チャンネル名とIDのリストに整形
        return [{"id": c["id"], "name": c["name"]} for c in channels]
    except SlackApiError as e:
        print(f"Slack API error: {e}")
        return []
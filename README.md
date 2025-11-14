# pockiri-backup
指定したSlackのワークスペースにある全チャンネルのスクリーンショットを自動定期実行するbot

## ローカルでの起動
```sh
# ビルド
docker build -t pockiri-backup .
# 起動
docker run -d -p 8080:8080 -e PORT=8080 --rm pockiri-backup
```

## 開発と本番で環境変数を分ける
```sh
# 開発環境
docker compose up --build

# 本番環境
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

## Botを実行するには

```sh
# 開発環境
curl -X GET http://127.0.0.1:5000/capture
# 本番環境
curl -X GET https://pockiri-backup-slackbot-898485337484.asia-northeast1.run.app/capture

```

## Cloud Runへのデプロイ
```sh
# タグ付け
docker tag pockiri-backup-slackbot asia-northeast1-docker.pkg.dev/slack-bot-464701/slack-backup-bot/pockiri-backup-slackbot
# プッシュ
docker push asia-northeast1-docker.pkg.dev/slack-bot-464701/slack-backup-bot/pockiri-backup-slackbot
# Google Cloud上でビルド
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/slack-bot-464701/slack-bot/pockiri-backup-slackbot
```

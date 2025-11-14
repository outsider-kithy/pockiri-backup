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
curl -X GET http://127.0.0.1:8080/capture

```

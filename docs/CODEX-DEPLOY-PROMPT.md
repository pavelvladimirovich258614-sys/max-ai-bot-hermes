# PROMPT FOR CODEX: Production Deploy of max-ai-bot на VPS

> Скопируй ВСЁ ниже строки `=== COPY START ===` в Codex. Codex выполнит шаги. Pavel вставит реальные credentials в указанных местах (плейсхолдеры `<...>`).

---

## === COPY START ===

### Контекст

**max-ai-bot** — AI-бот для мессенджера MAX (Российский аналог Telegram). Production-deploy на Ubuntu VPS.

**Что деплоим:** `D:\hermes-multi-agent-setup\max-ai-bot\` (Python 3.12+, FastAPI, maxapi-sdk, aiosqlite, Docker).

**Сервер:**
- IP: `<SERVER_IP>` (Pavel заменит перед запуском)
- SSH user: `<SSH_USER>` (обычно `root` или `deploy`)
- SSH password: `<SSH_PASSWORD>` (или SSH-ключ)
- OS: Ubuntu 24.04 LTS (или 22.04)
- Домен: `ai-agent-paul.ru` (уже настроен у регистратора reg.ru)
- Поддомен: `max.ai-agent-paul.ru` → `<SERVER_IP>` (уже A-запись)
- Сертификат: Let's Encrypt через certbot (бесплатно) или Минцифры CA (если нужна совместимость с platform-api2.max.ru)

**Credentials для вставки Pavel'я (НЕ выдумывай — спроси у Pavel'я):**
- `MAX_BOT_TOKEN` — токен MAX-бота (получен через business.max.ru или partner@max.ru)
- `LLM_PRIMARY_API_KEY` — MiniMax API ключ (из https://platform.minimax.io/user-center/payment/token-plan, префикс `sk-cp-…`)
- `LLM_FALLBACK_API_KEY` — StepFun API ключ (из https://platform.stepfun.ai)
- `MAX_ADMIN_USER_IDS` — Pavel's user_id в MAX (числовой)
- `MINIMAX_BASE_URL` = `https://api.minimax.io/anthropic`
- `STEPFUN_BASE_URL` = `https://api.stepfun.ai/v1`
- `HERMES_RZA_URL` = `http://host.docker.internal:9119/api/hermes/route` (если есть RZA рядом, иначе `http://localhost:9119`)

### Все ссылки (для Codex)

**Проект:**
- `D:\hermes-multi-agent-setup\max-ai-bot\` (локально у Pavel'я на Windows 10)
- `D:\hermes-multi-agent-setup\max-ai-bot\docs\HANDOFF.md` — контекст
- `D:\hermes-multi-agent-setup\max-ai-bot\docs\FEATURES-V2-PLAN.md` — что сделано
- `D:\hermes-multi-agent-setup\max-ai-bot\docs\CODEX-REFACTOR-PROMPT.md` — рефакторинг (если ещё не сделан — СНАЧАЛА рефакторинг, потом deploy)
- `D:\hermes-multi-agent-setup\max-ai-bot\Dockerfile` (если есть)
- `D:\hermes-multi-agent-setup\max-ai-bot\docker-compose.yml` (если есть)

**MAX Bot API:**
- https://dev.max.ru/docs — главная документация
- https://dev.max.ru/docs-api — API reference
- https://dev.max.ru/docs-api/changelog-api — changelog
- https://platform-api2.max.ru — production API
- https://business.max.ru или partner@max.ru — для получения bot token

**MiniMax (LLM + image):**
- https://platform.minimax.io/docs — главная
- https://platform.minimax.io/docs/guides/image-generation — image guide
- https://platform.minimax.io/docs/api-reference/image-generation-t2i
- https://platform.minimax.io/docs/api-reference/image-generation-i2i
- https://platform.minimax.io/docs/api-reference/text-anthropic-api
- https://platform.minimax.io/user-center/payment/token-plan — API key

**StepFun (fallback LLM):**
- https://platform.stepfun.ai/docs/en/step-plan/quick-start

**MAX SDK:**
- https://pypi.org/project/maxapi-sdk/ — production-ready
- https://github.com/max-messenger/max-botapi-python — исходники

**SSL / Сервер:**
- https://certbot.eff.org/ — Let's Encrypt
- https://www.gosuslugi.ru/crt — Минцифры CA (если нужна совместимость)
- https://www.digitalocean.com/community/tutorials — gRPC tutorials (для production)

**Nginx / Systemd:**
- https://nginx.org/en/docs/ — nginx docs
- https://www.freedesktop.org/software/systemd/man/systemd.service.html — systemd

### Задача

#### Фаза 0: Предусловия

Перед деплоем убедись:
- [ ] На сервере есть Python 3.12 (`python3.12 --version`)
- [ ] Docker установлен (`docker --version`, `docker compose version`)
- [ ] Домен `ai-agent-paul.ru` резолвится в IP сервера (`dig max.ai-agent-paul.ru`)
- [ ] Открыты порты 80, 443, 8080 (или закрой 8080 наружу, оставь только через nginx)
- [ ] SSH доступ работает (`ssh <SSH_USER>@<SERVER_IP>`)
- [ ] `D:\hermes-multi-agent-setup\max-ai-bot\` содержит Dockerfile + docker-compose.yml (если нет — создай)

Если чего-то нет — выполни установку (apt update && apt install -y python3.12 python3.12-venv nginx certbot python3-certbot-nginx docker.io docker-compose-plugin).

#### Фаза 1: Копирование проекта на сервер

С локальной машины Pavel'я (или через Codex с доступом к сети):

**Вариант A: rsync через SSH**
```bash
rsync -avz --exclude '.env' --exclude 'data/' --exclude 'logs/' --exclude '__pycache__/' \
  D:/hermes-multi-agent-setup/max-ai-bot/ <SSH_USER>@<SERVER_IP>:/opt/max-ai-bot/
```

**Вариант B: git push → clone на сервере**
- Pavel инициирует git в D:\hermes-multi-agent-setup\max-ai-bot\ (если ещё не)
- Push в репо (например GitHub приватный)
- На сервере: `git clone <repo_url> /opt/max-ai-bot`

**Вариант C: scp (если проект маленький)**
```powershell
scp -r D:\hermes-multi-agent-setup\max-ai-bot <SSH_USER>@<SERVER_IP>:/opt/
```

На сервере:
```bash
sudo mkdir -p /opt/max-ai-bot
sudo chown -R <SSH_USER>:<SSH_USER> /opt/max-ai-bot
cd /opt/max-ai-bot
ls -la  # должен быть Dockerfile, docker-compose.yml, app/, requirements.txt
```

#### Фаза 2: Создание .env на сервере

На сервере:
```bash
cd /opt/max-ai-bot
nano .env
```

Скопируй ВСЕ переменные из локального `.env` (Pavel'я) — вставь реальные credentials:
```env
# MAX Bot
MAX_BOT_TOKEN=<MAX_BOT_TOKEN от Pavel'я>
MAX_API_BASE=https://platform-api2.max.ru
MAX_WEBHOOK_URL=https://max.ai-agent-paul.ru/webhook/max
MAX_USE_POLLING=false
MAX_ADMIN_USER_IDS=<Pavel's user_id>

# Hermes
HERMES_MODE=auto
HERMES_RZA_URL=http://host.docker.internal:9119/api/hermes/route
HERMES_RZA_CLI=hermes peer dm rza

# LLM (primary) — MiniMax
LLM_PRIMARY_PROVIDER=minimax
LLM_PRIMARY_API_KEY=<MiniMax sk-cp-… от Pavel'я>
LLM_PRIMARY_BASE_URL=https://api.minimax.io/anthropic
LLM_PRIMARY_MODEL=MiniMax-M3

# LLM (fallback) — StepFun
LLM_FALLBACK_PROVIDER=stepfun
LLM_FALLBACK_API_KEY=<StepFun sk-… от Pavel'я>
LLM_FALLBACK_BASE_URL=https://api.stepfun.ai/v1
LLM_FALLBACK_MODEL=step-3.7-flash

# Web search
SEARCH_BACKEND=duckduckgo
SEARXNG_URL=
WHOOGLE_URL=
LIBREX_URL=
SEARX_SPACE_URL=https://searx.space
SCRAPLING_ENABLED=true
CRAWLEE_ENABLED=false

# Image generation
IMAGE_BACKEND=minimax
IMAGE_API_BASE=https://api.minimax.io/v1/image_generation
IMAGE_MODEL=image-01
IMAGE_MAX_RETRIES=2
IMAGE_ASPECT_DEFAULT=1:1
IMAGE_STORAGE_DIR=/app/data/images

# App
LOG_LEVEL=INFO
DATABASE_URL=sqlite+aiosqlite:////app/data/bot.db
MESSAGE_FORMAT=markdown
SECRET_KEY=<сгенерируй: openssl rand -hex 32>
```

```bash
chmod 600 .env
sudo chown <SSH_USER>:<SSH_USER> .env
```

#### Фаза 3: SSL сертификат

**Вариант A: Let's Encrypt (рекомендую)**
```bash
sudo certbot certonly --standalone -d max.ai-agent-paul.ru --non-interactive --agree-tos -m <Pavel's email>
```
Сертификаты будут в `/etc/letsencrypt/live/max.ai-agent-paul.ru/`.

**Вариант B: Минцифры CA (если нужен для platform-api2.max.ru)**
```bash
sudo mkdir -p /usr/local/share/ca-certificates
# Скачай CA с https://www.gosuslugi.ru/crt
sudo curl -o /usr/local/share/ca-certificates/minstroy_ca.crt <URL от Pavel'я>
sudo update-ca-certificates
```

Автопродление (для Let's Encrypt):
```bash
sudo certbot renew --dry-run
```

#### Фаза 4: Nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/max-ai-bot
```

Содержимое:
```nginx
# Rate limit
limit_req_zone $binary_remote_addr zone=max_bot:10m rate=30r/s;

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name max.ai-agent-paul.ru;
    return 301 https://$host$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name max.ai-agent-paul.ru;

    ssl_certificate /etc/letsencrypt/live/max.ai-agent-paul.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/max.ai-agent-paul.ru/privkey.pem;

    # SSL optimization
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Logging
    access_log /var/log/nginx/max-ai-bot.access.log;
    error_log /var/log/nginx/max-ai-bot.error.log;

    # MAX size for attachments
    client_max_body_size 25M;

    # Webhook endpoint
    location /webhook/max {
        limit_req zone=max_bot burst=10 nodelay;

        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        access_log off;
    }

    # Block everything else
    location / {
        return 403;
    }
}
```

Активируй:
```bash
sudo ln -s /etc/nginx/sites-available/max-ai-bot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### Фаза 5: Docker setup

Проверь/создай `Dockerfile` (если нет):
```dockerfile
FROM python:3.12-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY app/ ./app/
COPY data/ ./data/
COPY logs/ ./logs/

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health').read()" || exit 1

# Запуск
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Проверь/создай `docker-compose.yml`:
```yaml
version: '3.8'

services:
  max-ai-bot:
    build: .
    container_name: max-ai-bot
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health').read()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # Опционально: RZA/Hermes (если деплоишь рядом)
  # hermes-rza:
  #   image: your-registry/hermes-rza:latest
  #   container_name: hermes-rza
  #   restart: unless-stopped
  #   ports:
  #     - "127.0.0.1:9119:9119"
```

Запуск:
```bash
cd /opt/max-ai-bot
sudo docker compose up -d --build
sudo docker compose logs -f max-ai-bot
```

Должен быть лог:
```
Application startup complete
Бот: @id752703975446_3_bot
17 обработчиков зарегистрировано
Slash-команды зарегистрированы
```

Проверка:
```bash
curl -i http://127.0.0.1:8080/health
# Должен вернуть 200 OK
```

#### Фаза 6: Регистрация webhook в MAX

Только после того как бот запущен и SSL настроен:
```bash
curl -X POST https://platform-api2.max.ru/subscriptions \
  -H "Authorization: $MAX_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://max.ai-agent-paul.ru/webhook/max"}'
```

Или через Python:
```python
import asyncio
from maxapi import Bot

async def main():
    bot = Bot(token="<MAX_BOT_TOKEN>")
    await bot.subscribe_webhook("https://max.ai-agent-paul.ru/webhook/max")
    print("Webhook зарегистрирован")
    await bot.close()

asyncio.run(main())
```

Проверка:
```bash
curl -X GET https://platform-api2.max.ru/subscriptions \
  -H "Authorization: $MAX_BOT_TOKEN"
# Должен вернуть ваш webhook URL
```

Переключите polling → webhook в .env:
```env
MAX_USE_POLLING=false
```
Перезапустите:
```bash
sudo docker compose restart max-ai-bot
```

#### Фаза 7: Systemd auto-restart

Docker `restart: unless-stopped` уже даёт автозапуск. Но для надёжности — добавь systemd unit:
```bash
sudo nano /etc/systemd/system/max-ai-bot.service
```

```ini
[Unit]
Description=max-ai-bot (MAX Messenger AI bot)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/max-ai-bot
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable max-ai-bot
sudo systemctl start max-ai-bot
sudo systemctl status max-ai-bot
```

#### Фаза 8: Мониторинг и логи

**Systemd journal:**
```bash
sudo journalctl -u max-ai-bot -f
```

**Docker logs:**
```bash
sudo docker compose logs -f max-ai-bot
```

**Health check endpoint:**
```bash
curl http://127.0.0.1:8080/health
```

**Nginx logs:**
```bash
sudo tail -f /var/log/nginx/max-ai-bot.access.log
sudo tail -f /var/log/nginx/max-ai-bot.error.log
```

**Disk usage:**
```bash
df -h /opt/max-ai-bot/data
du -sh /opt/max-ai-bot/data/*
```

**Backup:**
```bash
# Cron: backup SQLite + images daily
sudo crontab -e
# Add line:
0 3 * * * tar -czf /backup/max-ai-bot-$(date +\%Y\%m\%d).tar.gz -C /opt/max-ai-bot data/
```

#### Фаза 9: Проверка от Pavel'я (acceptance)

1. **Бот в MAX:**
   - Открыть @id752703975446_3_bot
   - `/start` → баннер + 10 кнопок
   - `/copy тема` → результат с эмодзи-структурой
   - `/ideate тема` → 10 идей
   - `/research тема` → research
   - `/post` → подменю 5 кнопок

2. **Webhook работает:**
   - В MAX написать боту → бот отвечает (значит webhook дошёл)
   - В MAX добавить бота в канал → бот видит канал (getSubscriptions)

3. **Картинки:**
   - `/post` → 🎨 Сгенерировать → Свой → "космонавт" → 1:1 → бот шлёт картинку

4. **Hermes-кнопка (если сделана):**
   - В /start найти 🤖 Hermes → подменю → выбрать сценарий → ждать ответ

5. **Сервер стабилен:**
   - `sudo docker ps` — `max-ai-bot` в статусе `Up` (healthy)
   - `sudo systemctl status max-ai-bot` — `active (running)`
   - `sudo journalctl -u max-ai-bot --since "1 hour ago"` — нет ERROR

### Acceptance (Pavel'у нужно проверить)

| # | Что | Как |
|---|-----|-----|
| 1 | Сервер отвечает | `curl -i https://max.ai-agent-paul.ru/health` → 200 |
| 2 | HTTP→HTTPS редирект | `curl -I http://max.ai-agent-paul.ru` → 301 |
| 3 | SSL валиден | `https://www.ssllabs.com/ssltest/analyze.html?d=max.ai-agent-paul.ru` → A или A+ |
| 4 | Бот стартует | `sudo docker compose ps` → Up + healthy |
| 5 | Slash-команды | В MAX ввести `/` → подсказки |
| 6 | Webhook доходит | Написать боту в MAX → бот отвечает |
| 7 | Картинки | `/post` → 🎨 → Свой → ... |
| 8 | Автозапуск | `sudo reboot` → после ребута бот живой |

### Финальный отчёт Codex (формат)

```
## Deploy V1 — отчёт

### Что сделано
- [✅/❌] Предусловия (Python 3.12, Docker, домен)
- [✅/❌] Копирование проекта (rsync/git/scp)
- [✅/❌] .env с реальными credentials
- [✅/❌] SSL (Let's Encrypt или Минцифры)
- [✅/❌] Nginx reverse proxy + rate limit
- [✅/❌] Docker build + run
- [✅/❌] Webhook регистрация в MAX
- [✅/❌] Systemd auto-restart
- [✅/❌] Cron backup
- [✅/❌] Мониторинг (health endpoint, journal, docker logs)

### Acceptance (8 пунктов)
1. ✅/❌ + кратко
...

### Что осталось
- [список]
```

### Жёсткие правила

- **НЕ выдумывай credentials** — Pavel вставит реальные сам
- **НЕ выкатывай .env в git** — добавь в .gitignore
- **НЕ открывай порт 8080 наружу** — только через nginx (127.0.0.1:8080)
- **НЕ используй HTTP webhook** — только HTTPS
- **НЕ передавай токен в query-параметре** — только `Authorization` header
- **НЕ запускай бот от root** — создай отдельного user'а `maxbot`
- **НЕ храни SQLite в контейнере** — монтируй volume `./data:/app/data`
- **НЕ забудь backup** — cron ежедневно
- **НЕ ставь рестарт слишком часто** — `Restart=on-failure` + `RestartSec=10` хватит

### Начни с

1. Проверь предусловия (Фаза 0)
2. Скопируй проект (Фаза 1)
3. Создай .env — Pavel вставит credentials (Фаза 2)
4. SSL (Фаза 3)
5. Nginx (Фаза 4)
6. Docker (Фаза 5)
7. Webhook (Фаза 6)
8. Systemd (Фаза 7)
9. Мониторинг (Фаза 8)
10. Acceptance с Pavel'я (Фаза 9)

Когда закончишь — дай финальный отчёт.

## === COPY END ===

# Демо Stand Manager

Этот сценарий поднимает настоящий стек: один Telegram-бот, один PostgreSQL и
отдельный Workspace для каждого группового чата. Моковая база и тестовые
пользователи не нужны: демонстрация показывает реальные Telegram ID,
конкуренцию за стенд и сохранение состояния после рестарта бота.

## Что подготовить

- запущенный Docker Desktop;
- интернет на компьютере и телефонах;
- токен бота от [@BotFather](https://t.me/BotFather);
- два или три Telegram-аккаунта для ролей ADMIN, User A и User B;
- отсутствие другого запущенного процесса с тем же токеном бота.

Для надёжности добавьте бота администратором группы. Тогда Telegram точно
доставляет ему групповые команды и Reply-контекст.

## Как запустить

В корне проекта создайте `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA_ТВОЙ_ТОКЕН
POSTGRES_HOST_PORT=55432
```

Больше ничего в `.env` не требуется. Затем выполните:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs --tail=50 bot
```

Ожидаемое состояние:

```text
stand-manager-postgres-1   Up (healthy)
stand-manager-bot-1        Up
stand-manager-wow-1        Up (healthy)
```

В логах бота должны появиться строки `Схема PostgreSQL готова` и
`Запускается Telegram long polling` без traceback.

## Скрытый WOW-слой

После запуска Compose откройте на компьютере:

```text
http://127.0.0.1:8088/boss-mode/
```

Страница намеренно не связана с корневым экраном. Нажмите «Активировать
режим»: запустятся локальный видеолооп и голос браузера. Чек-лист сохраняет
прогресс в `localStorage`, а после четырёх пунктов показывает финальный эффект.
Для озвучки нужен один пользовательский клик — это ограничение браузеров.

После публикации репозитория та же страница доступна извне:

```text
https://mlops-summer-day-2026.github.io/team-01/boss-mode/
```

Пасхалка открывается скрытой Telegram-командой `/bali`. Команда не показывается
в `/help` и меню бота: она отправляет кнопку с публичной ссылкой. При переносе
страницы задайте новый адрес через `WOW_PUBLIC_URL` в `.env`.

## PostgreSQL

База доступна только с локального компьютера и специально использует порт
`55432`, чтобы не конфликтовать с установленным PostgreSQL:

```text
Host:     127.0.0.1
Port:     55432
Database: standbot
User:     standbot
Password: standbot
```

Подключиться можно через DBeaver или из контейнера:

```powershell
docker compose exec postgres psql -U standbot -d standbot
```

В `psql` команда `\dt` покажет таблицы. Бот создаёт их на старте через
`Base.metadata.create_all`, поэтому вручную запускать миграции не нужно.

## Подготовка Telegram-группы

1. Сначала запустите Compose.
2. Создайте новую группу для сценического показа — это даст чистый Workspace
   без удаления репетиционных данных.
3. Добавьте бота в группу и сделайте его администратором.
4. Выполните `/start`. Это повторно синхронизирует владельца группы как ADMIN,
   а остальных Telegram-администраторов как MODERATOR, даже если событие
   добавления бота было пропущено.
5. User A и User B должны написать в группу хотя бы по одному сообщению, чтобы
   администратор мог отвечать на них командами.

## Сценарий показа на 4–5 минут

### 1. Создать независимые Team

От имени владельца группы:

```text
/create_team backend Backend
/create_team mobile Mobile
/teams
```

### 2. Добавить реальных пользователей

ADMIN или MODERATOR отвечает Reply на сообщение User A:

```text
/add_user backend
```

Затем такой же Reply на сообщение User B:

```text
/add_user backend
```

Проверка:

```text
/team_users backend
```

Если доступно только два Telegram-аккаунта, владелец группы может ответить
`/add_user backend` на собственное предыдущее сообщение и выступить как User A.

### 3. Создать стенды

```text
/create_stand backend dev-1
/create_stand backend dev-2
/stands backend
```

### 4. Показать конфликт

User A:

```text
/take_stand backend dev-1
```

User B повторяет ту же команду и видит имя текущего владельца:

```text
/take_stand backend dev-1
```

После этого User B занимает второй стенд:

```text
/take_stand backend dev-2
/stands backend
```

Дополнительно можно показать:

```text
/my_stands
/free_stands backend
```

### 5. Показать moderator override

ADMIN или MODERATOR освобождает чужой стенд:

```text
/untake_stand backend dev-1
/stands backend
```

### 6. Доказать сохранение состояния

На компьютере перезапустите только процесс бота:

```powershell
docker compose restart bot
docker compose logs --tail=30 bot
```

После появления сообщения о polling снова выполните в Telegram:

```text
/stands backend
```

Team, пользователи и занятый `dev-2` останутся в PostgreSQL.

### 7. При наличии времени показать изоляцию чатов

Добавьте того же бота во вторую группу и выполните там:

```text
/start
/create_team backend Другой Backend
/stands backend
```

Одинаковый slug `backend` разрешён, потому что у каждой Telegram-группы свой
Workspace. Один bot container обслуживает все группы одновременно.

## Управление данными

Обычная остановка сохраняет volume:

```powershell
docker compose stop
docker compose start
```

`docker compose down` также оставляет именованный volume. Команда ниже удаляет
всю локальную базу без возможности восстановления и для обычного демо не нужна:

```powershell
docker compose down -v
```

Для чистого повторного показа безопаснее создать новую Telegram-группу: новый
`telegram_chat_id` автоматически создаст новый Workspace.

## Если что-то не стартовало

Главная диагностическая команда:

```powershell
docker compose logs --tail=100 bot postgres
```

Частые причины:

- `Conflict: terminated by other getUpdates request` — с этим токеном уже
  запущен другой экземпляр бота; остановите его;
- `Unauthorized` — неверный или отозванный `TELEGRAM_BOT_TOKEN`;
- `postgres` не становится `healthy` — проверьте Docker Desktop и место на
  диске;
- бот не прислал welcome после добавления — сделайте его администратором и
  выполните `/start`;
- контейнер постоянно перезапускается — смотрите первый traceback через
  `docker compose logs --tail=100 bot`.

Перед выступлением один раз пройдите сценарий целиком и запишите экран. Видео —
резерв на случай проблем с Wi-Fi, Telegram или Docker Desktop на площадке.

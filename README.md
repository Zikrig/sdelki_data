## Склад-бот (aiogram3 + Postgres + SQLAlchemy)

### Запуск
- Установите и запустите Postgres через Docker:
  ```bash
  docker compose up -d
  ```
- Скопируйте `.env.example` в `.env` и заполните `BOT_TOKEN`.
- Создайте виртуальное окружение и установите зависимости:
  ```bash
  python -m venv .venv
  .venv\\Scripts\\activate
  pip install -r requirements.txt
  ```
- Запустите бота:
  ```bash
  python main.py
  ```

### Переменные окружения
- **BOT_TOKEN**: токен Telegram-бота
- **DATABASE_URL**: строка подключения, по умолчанию `postgresql+asyncpg://postgres:postgres@localhost:5432/warehouse`

### Функционал
- Инлайн-меню для оформления отгрузки
- /admin для редактирования контрагентов и товаров
- PDF по каждой сделке с прибылью (продажа - закупка)


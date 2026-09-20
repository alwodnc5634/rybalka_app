-- MVP-схема для дисциплины "Лодки" (режим "Вес", один тур).
-- v2: поддержка иерархии Команда (опционально) -> Пара -> Спортсмен,
-- дата рождения, очерёдность старта, полный официальный протокол
-- (по образцу листа "ПТР ком 1-туровые" из официального Excel-пакета).
-- v7: evsk_tier (статус соревнования для расчёта разрядов, см. backend/evsk.py)
-- и auto_draw_enabled (жеребьёвка очерёдности старта по кнопке, вместо
-- только вручную) — оба поля вводятся в общих данных турнира.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tournament (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    name_full     TEXT NOT NULL,   -- А2: полное название соревнования
    status        TEXT,            -- напр. "Чемпионат Вологодской области" (статус для шапки)
    venue         TEXT,
    date_tour1    TEXT,
    discipline    TEXT,            -- А3: "Ловля спиннингом с лодок — ..."
    gender_group  TEXT,            -- "мужчины, женщины" и т.п.
    evsk_tier     TEXT NOT NULL DEFAULT '',   -- статус соревнования по ЕВСК (см. evsk.TIER_ORDER)
    auto_draw_enabled INTEGER NOT NULL DEFAULT 0 CHECK (auto_draw_enabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS judges_signoff (
    id        INTEGER PRIMARY KEY,
    role      TEXT NOT NULL,   -- "Главный судья", "Главный секретарь", "Судья контролер" ...
    category  TEXT,            -- "судья 1К", "судья 2К", "б/р" ...
    full_name TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS athletes (
    id              INTEGER PRIMARY KEY,
    full_name       TEXT NOT NULL,
    rank_category   TEXT,     -- 'б/р','3ю','2ю','1ю','3','2','1','КМС','МС'
    birth_date      TEXT      -- ISO дата, может быть пустой
);

-- Команда — ОПЦИОНАЛЬНАЯ группировка нескольких пар. Если у пары нет
-- team_id — она standalone (ровно текущий реальный случай пользователя:
-- "Команда" в протоколе = сама пара, без вложенности).
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    reg_number  INTEGER,
    name        TEXT NOT NULL,
    region      TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id               INTEGER PRIMARY KEY,
    reg_number       INTEGER,           -- рег. номер пары (если standalone) либо порядковый номер внутри команды
    name             TEXT NOT NULL,     -- название пары/команды-как-пары (напр. "GLS 360 FISHING")
    team_id          INTEGER REFERENCES teams(id),   -- NULL, если пара сама по себе
    pair_index_in_team INTEGER,         -- 1,2,3... порядок пары внутри команды (для "1-1","1-2")
    start_order      INTEGER            -- "Очерёдность старта" — из жеребьёвки, вводится вручную
);

CREATE TABLE IF NOT EXISTS entry_members (
    id          INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    athlete_id  INTEGER NOT NULL REFERENCES athletes(id),
    slot_order  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (entry_id, slot_order)
);

CREATE TABLE IF NOT EXISTS pair_weigh_ins (
    id              INTEGER PRIMARY KEY,
    entry_id        INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
    weight_score    REAL NOT NULL DEFAULT 0,
    fish_count      INTEGER
);

-- ----------------------------------------------------------------------------
-- VIEW: место пары (H "Баллы", I "Место пары" официального протокола).
-- Ранжирование строго по весу (по убыванию); тай-брейк — ПРЕДПОЛОЖЕНИЕ
-- (больше рыб = выше место), не подтверждено реальными данными.
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_pair_results AS
SELECT
    e.id AS entry_id,
    e.reg_number,
    e.name AS entry_name,
    e.team_id,
    e.pair_index_in_team,
    e.start_order,
    w.weight_score,
    w.fish_count,
    ROW_NUMBER() OVER (
        ORDER BY w.weight_score DESC, COALESCE(w.fish_count, 0) DESC
    ) AS pair_place
FROM entries e
JOIN pair_weigh_ins w ON w.entry_id = e.id;

-- ----------------------------------------------------------------------------
-- VIEW: агрегация по командам (J "Сумма баллов", K "Сумма мест", L "Место ком").
-- ПОДТВЕРЖДЕНО по формулам официального Excel (J10=SUM Баллы пар,
-- K10=SUM Место пары): сумма мест команды = сумма мест ВСЕХ её пар,
-- сумма баллов = сумма баллов всех её пар. Тай-брейк места команды —
-- по аналогии с остальной системой: сумма мест (меньше=лучше) -> сумма
-- баллов (больше=лучше).
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_team_results AS
SELECT
    t.id AS team_id,
    t.reg_number,
    t.name AS team_name,
    t.region,
    SUM(pr.pair_place) AS sum_places,
    SUM(pr.weight_score) AS sum_weight,
    ROW_NUMBER() OVER (
        ORDER BY SUM(pr.pair_place) ASC, SUM(pr.weight_score) DESC
    ) AS team_place
FROM teams t
JOIN v_pair_results pr ON pr.team_id = t.id
GROUP BY t.id;

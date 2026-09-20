-- ============================================================================
-- Рыбалка — «Форель» (спиннинг с берега). v1.
--
-- Логика (реверс-инжиниринг реальных формул пакета «Подсчёт Хоменко форель»
-- + уточнение пользователя):
--   - Тур делится на ЗОНЫ (А..Д, до 5) и ПЕРИОДЫ (3 или 4 в туре).
--   - Каждый спортсмен на каждый период получает бумажную «личную карту»:
--     туда судья и сам спортсмен подписывают КАЖДУЮ пойманную рыбу с её
--     длиной. Карта переносится в программу вручную после периода.
--   - Баллы периода = СУММА ДЛИН всех пойманных рыб (не количество, не
--     балл-за-рыбу — это уточнение пользователя, отличается от того, что
--     буквально названо "баллы" в старых формулах, но арифметически то же
--     место занимает).
--   - Тай-брейк при равной сумме длины: больше рыб — выше место (в отличие
--     от старой Excel-версии, где просто складывались баллы периода как
--     секунд. критерий; тут по прямому указанию пользователя явно "количество
--     рыб" — используем его как явный второй критерий).
--   - Место в периоде считается ВНУТРИ ЗОНЫ (условия ловли разные по зонам).
--   - Снятие («дисквалификация») с периода, в котором её зафиксировали, и
--     до конца тура — фиксированное худшее место (число участников зоны + 3),
--     а не ранжирование.
--   - Место в туре: сумма мест по периодам → место в зоне за тур (это и
--     есть главный критерий для сравнения МЕЖДУ зонами) → сумма длины →
--     сумма рыб.
--   - Если 2 тура: место в зоне (сумма за оба тура) → сумма мест по периодам
--     (оба тура) → сумма длины (оба тура) → лучший результат одного тура →
--     результат именно 2-го тура.
--   - При полном совпадении всех перечисленных показателей — это НЕ решается
--     жребием (для этой дисциплины такого правила нет вообще, уточнение
--     пользователя): места делятся, всем в группе присваивается среднее
--     арифметическое подряд идущих мест (напр. двое делят 5-6 место -> обоим
--     5.5; трое делят 3-4-5 -> всем 4, т.к. (3+4+5)/3=4; следующий за ними
--     получает 6). Это и есть окончательное правило, а не временная замена
--     жребия — реализовано ниже через RANK()+((tie_n-1)/2.0), проверено на
--     всех трёх примерах пользователя.
--
-- v7: три ранее отложенные возможности (см. СПРАВОЧНИК §17), теперь как
-- ВЫБОР в общих данных турнира (не меняют поведение, если выключены):
--   - evsk_tier: статус соревнования для расчёта разрядов/ЕВСК (evsk.py).
--   - half_zones_enabled: зона делится на 2 подзоны с НЕЗАВИСИМЫМ рейтингом
--     (независимо ранжируются периоды и место в зоне за тур — см. rank_zone
--     в представлениях ниже); итоговый протокол дальше не меняется, просто
--     "место в зоне" теперь фактически "место в подзоне", если включено.
--   - auto_draw_enabled: показывает кнопку "Провести жеребьёвку" (случайно
--     распределяет зону/подзону на тур и очерёдность старта), не убирает
--     ручной ввод — можно и дальше редактировать зону/старт вручную.
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tournament (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    name_full       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    venue           TEXT NOT NULL DEFAULT '',
    date_tour1      TEXT NOT NULL DEFAULT '',
    date_tour2      TEXT NOT NULL DEFAULT '',
    discipline      TEXT NOT NULL DEFAULT 'Ловля спиннингом с берега (форель)',
    gender_group    TEXT NOT NULL DEFAULT '',
    tours_count     INTEGER NOT NULL DEFAULT 2 CHECK (tours_count IN (1,2)),
    periods_count   INTEGER NOT NULL DEFAULT 3 CHECK (periods_count IN (3,4)),
    zones           TEXT NOT NULL DEFAULT 'А,Б,В',  -- список зон через запятую, напр. "А,Б,В,Г,Д"
    evsk_tier       TEXT NOT NULL DEFAULT '',
    half_zones_enabled INTEGER NOT NULL DEFAULT 0 CHECK (half_zones_enabled IN (0,1)),
    auto_draw_enabled  INTEGER NOT NULL DEFAULT 0 CHECK (auto_draw_enabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS judges_signoff (
    id INTEGER PRIMARY KEY, role TEXT NOT NULL, category TEXT,
    full_name TEXT, sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS athletes (
    id INTEGER PRIMARY KEY, full_name TEXT NOT NULL,
    rank_category TEXT, birth_date TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY, reg_number INTEGER, name TEXT NOT NULL, region TEXT
);

-- Один участник = один спортсмен (в отличие от лодок, тут не пары).
-- team_id — опционально (личник в команде или чисто личный зачёт).
CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY,
    reg_number      INTEGER,
    athlete_id      INTEGER NOT NULL REFERENCES athletes(id),
    team_id         INTEGER REFERENCES teams(id),
    start_order     INTEGER
);

-- Зона спортсмена на тур (по жребию — вводится вручную, автоматической
-- жеребьёвки в программе пока нет ни для одной дисциплины).
CREATE TABLE IF NOT EXISTS entry_zones (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    zone            TEXT NOT NULL,
    half_zone       TEXT,     -- '1'/'2' — используется только если half_zones_enabled=1
    PRIMARY KEY (entry_id, tour_number)
);

-- Одна строка = одна рыба (ровно как в бумажной личной карте спортсмена).
CREATE TABLE IF NOT EXISTS catches (
    id              INTEGER PRIMARY KEY,
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    period_number   INTEGER NOT NULL CHECK (period_number BETWEEN 1 AND 4),
    length_cm       REAL NOT NULL CHECK (length_cm > 0)
);

-- Снятие спортсмена, действует с указанного периода до конца тура.
CREATE TABLE IF NOT EXISTS sanctions (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    from_period     INTEGER NOT NULL CHECK (from_period BETWEEN 1 AND 4),
    reason          TEXT,
    PRIMARY KEY (entry_id, tour_number)
);

-- ----------------------------------------------------------------------------
-- VIEWS
-- ----------------------------------------------------------------------------

-- Итог по периоду: сколько рыб и какая суммарная длина у спортсмена в этом
-- периоде этого тура (0/0, если ничего не поймал — это НЕ дисквалификация).
CREATE VIEW IF NOT EXISTS v_period_totals AS
SELECT
    e.id AS entry_id, e.tour_number, e.period_number,
    COUNT(c.id) AS fish_count,
    COALESCE(SUM(c.length_cm), 0) AS total_length
FROM (
    SELECT en.id, t.tour_number, p.period_number
    FROM entries en
    CROSS JOIN (SELECT 1 AS tour_number UNION SELECT 2) t
    CROSS JOIN (SELECT 1 AS period_number UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) p
    CROSS JOIN tournament tr
    WHERE t.tour_number <= tr.tours_count AND p.period_number <= tr.periods_count
) e
LEFT JOIN catches c ON c.entry_id = e.id AND c.tour_number = e.tour_number AND c.period_number = e.period_number
GROUP BY e.id, e.tour_number, e.period_number;

-- Место в периоде (внутри зоны). Дисквалифицированные (снятие действует
-- с this-period включительно) -> фиксированное место = кол-во в зоне + 3.
-- Остальные -> RANK.AVG по (сумма длины DESC, кол-во рыб DESC).
-- rank_zone: реальная зона, либо "зона/подзона", если в турнире включено
-- деление на полузоны (half_zones_enabled) и у записи задана подзона —
-- используется ТОЛЬКО для группировки ранжирования (место в подзоне
-- становится тем, что дальше используется как "место в зоне"); в
-- протоколе показывается настоящая zone (+ half_zone отдельно).
CREATE VIEW IF NOT EXISTS v_period_places AS
WITH base AS (
    SELECT
        pt.entry_id, pt.tour_number, pt.period_number,
        pt.fish_count, pt.total_length,
        ez.zone, ez.half_zone,
        CASE WHEN (SELECT half_zones_enabled FROM tournament) = 1
                  AND ez.half_zone IS NOT NULL AND ez.half_zone <> ''
             THEN ez.zone || '/' || ez.half_zone ELSE ez.zone END AS rank_zone,
        (s.entry_id IS NOT NULL AND s.from_period <= pt.period_number) AS disqualified
    FROM v_period_totals pt
    JOIN entry_zones ez ON ez.entry_id = pt.entry_id AND ez.tour_number = pt.tour_number
    LEFT JOIN sanctions s ON s.entry_id = pt.entry_id AND s.tour_number = pt.tour_number
),
zone_counts AS (
    SELECT tour_number, period_number, rank_zone, COUNT(*) AS n
    FROM base GROUP BY tour_number, period_number, rank_zone
),
ranked AS (
    SELECT b.*,
        RANK() OVER (
            PARTITION BY b.tour_number, b.period_number, b.rank_zone
            ORDER BY (CASE WHEN b.disqualified THEN 1 ELSE 0 END) ASC,
                     b.total_length DESC, b.fish_count DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY b.tour_number, b.period_number, b.rank_zone,
                         (CASE WHEN b.disqualified THEN 1 ELSE 0 END),
                         b.total_length, b.fish_count
        ) AS tie_n
    FROM base b
)
SELECT
    r.entry_id, r.tour_number, r.period_number, r.zone, r.half_zone,
    r.fish_count, r.total_length, r.disqualified,
    CASE WHEN r.disqualified THEN zc.n + 3 ELSE r.rnk + (r.tie_n - 1) / 2.0 END AS period_place
FROM ranked r
JOIN zone_counts zc ON zc.tour_number = r.tour_number AND zc.period_number = r.period_number
                    AND zc.rank_zone = r.rank_zone;

-- Итог по туру: сумма мест по периодам, сумма длины, сумма рыб.
CREATE VIEW IF NOT EXISTS v_tour_totals AS
SELECT
    entry_id, tour_number, zone, half_zone,
    SUM(period_place) AS sum_period_places,
    SUM(total_length) AS sum_length,
    SUM(fish_count) AS sum_fish,
    MAX(CASE WHEN disqualified THEN 1 ELSE 0 END) AS disqualified_ever
FROM v_period_places
GROUP BY entry_id, tour_number, zone, half_zone;

-- Место в зоне за тур (главный критерий для сравнения между зонами):
-- RANK.AVG по (сумма мест ASC, сумма длины DESC, сумма рыб DESC). Группировка
-- по rank_zone (зона либо зона/подзона, см. v_period_places) — при
-- включённых полузонах это фактически место В ПОДЗОНЕ.
CREATE VIEW IF NOT EXISTS v_tour_zone_place AS
WITH base AS (
    SELECT t.*,
        CASE WHEN (SELECT half_zones_enabled FROM tournament) = 1
                  AND t.half_zone IS NOT NULL AND t.half_zone <> ''
             THEN t.zone || '/' || t.half_zone ELSE t.zone END AS rank_zone
    FROM v_tour_totals t
),
zone_counts AS (
    SELECT tour_number, rank_zone, COUNT(*) AS n FROM base GROUP BY tour_number, rank_zone
),
ranked AS (
    SELECT b.*,
        RANK() OVER (
            PARTITION BY b.tour_number, b.rank_zone
            ORDER BY b.sum_period_places ASC, b.sum_length DESC, b.sum_fish DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY b.tour_number, b.rank_zone, b.sum_period_places, b.sum_length, b.sum_fish
        ) AS tie_n
    FROM base b
)
SELECT r.entry_id, r.tour_number, r.zone, r.half_zone, r.sum_period_places, r.sum_length, r.sum_fish,
       r.disqualified_ever, r.rnk + (r.tie_n - 1) / 2.0 AS zone_place
FROM ranked r
JOIN zone_counts zc ON zc.tour_number = r.tour_number AND zc.rank_zone = r.rank_zone;

-- Место в туре по ВСЕМ зонам: приоритет — место в зоне, затем сумма мест,
-- затем сумма длины, затем сумма рыб.
CREATE VIEW IF NOT EXISTS v_tour_overall_place AS
WITH ranked AS (
    SELECT z.*,
        RANK() OVER (
            PARTITION BY z.tour_number
            ORDER BY z.zone_place ASC, z.sum_period_places ASC, z.sum_length DESC, z.sum_fish DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY z.tour_number, z.zone_place, z.sum_period_places, z.sum_length, z.sum_fish
        ) AS tie_n,
        COUNT(*) OVER (PARTITION BY z.tour_number) AS total_n
    FROM v_tour_zone_place z
)
SELECT entry_id, tour_number, zone, sum_period_places, sum_length, sum_fish, disqualified_ever,
       zone_place, rnk + (tie_n - 1) / 2.0 AS tour_place, total_n
FROM ranked;

-- Финальное место за все туры (если тур один — совпадает с v_tour_overall_place).
CREATE VIEW IF NOT EXISTS v_final_place AS
WITH agg AS (
    SELECT
        entry_id,
        SUM(zone_place) AS sum_zone_place,
        SUM(sum_period_places) AS sum_period_places_all,
        SUM(sum_length) AS sum_length_all,
        MAX(sum_length) AS best_tour_length,
        MAX(CASE WHEN tour_number = 2 THEN sum_length ELSE NULL END) AS tour2_length,
        MAX(disqualified_ever) AS disqualified_ever,
        COUNT(*) AS tours_played
    FROM v_tour_zone_place
    GROUP BY entry_id
),
ranked AS (
    SELECT a.*,
        RANK() OVER (
            ORDER BY a.sum_zone_place ASC, a.sum_period_places_all ASC, a.sum_length_all DESC,
                     a.best_tour_length DESC, COALESCE(a.tour2_length, -1) DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY a.sum_zone_place, a.sum_period_places_all, a.sum_length_all,
                         a.best_tour_length, a.tour2_length
        ) AS tie_n
    FROM agg a
)
SELECT entry_id, sum_zone_place, sum_period_places_all, sum_length_all, tours_played,
       disqualified_ever, rnk + (tie_n - 1) / 2.0 AS final_place
FROM ranked;

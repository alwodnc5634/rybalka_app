-- ============================================================================
-- Рыбалка — «Ловля спиннингом с берега». v2 (v8 программы).
--
-- ВАЖНО (v8): структура переделана по реальному протоколу Чемпионата
-- Вологодской области 28.09.2025 и прямым указаниям пользователя:
--   - ТУРОВ НЕТ ВООБЩЕ. Соревнование состоит только из ПЕРИОДОВ (обычно 3).
--     Раньше (v5-v7) здесь были туры, внутри туров периоды — это неверно.
--   - ЗОНА назначается НА КАЖДЫЙ ПЕРИОД отдельно (в протоколе реального
--     турнира так и есть: "Зона 1пер", "Зона 2пер", "Зона 3пер"), а не одна
--     зона на весь тур. Спортсмен переходит из зоны в зону между периодами.
--   - ЗОНА НЕ ВВОДИТСЯ ПРИ РЕГИСТРАЦИИ: регистрация идёт до соревнования,
--     когда жеребьёвки ещё не было. Зоны появляются позже, на отдельной
--     вкладке «Жеребьёвка» (вручную или автоматически).
--
-- Логика подсчёта (без изменений с v5, подтверждена пользователем повторно):
--   - Баллы периода = СУММА ДЛИН всех пойманных рыб этого периода.
--   - Место в периоде считается ВНУТРИ ЗОНЫ этого периода: RANK.AVG по
--     (сумма длины DESC, количество рыб DESC). Нулевой улов — это НЕ
--     дисквалификация, такой спортсмен участвует в обычном ранжировании
--     (подтверждено пользователем в v8: заполненный лист «Личка» в
--     присланном файле был от других соревнований, сбивал с толку).
--   - Снятие («дисквалификация») действует с указанного периода и до конца
--     соревнования — фиксированное худшее место (число участников зоны + 3).
--   - ИТОГОВОЕ место = сумма мест по периодам (ASC). Тай-брейки по порядку:
--       1) сумма мест по периодам (меньше — лучше);
--       2) суммарная длина всех рыб (больше — лучше);
--       3) суммарное количество рыб (больше — лучше);
--       4) КОЛИЧЕСТВО РЫБ В ПОСЛЕДНЕМ ПЕРИОДЕ (больше — лучше), при равенстве
--          — в предпоследнем, и так далее назад до первого периода
--          (правило названо пользователем в v8);
--       5) если совпало вообще всё — жребия нет: группа делит среднее
--          арифметическое подряд идущих мест (двое на 5-6 → обоим 5.5).
--   - КОМАНДНЫЙ зачёт (v8, есть в реальном протоколе — листы «Команда» и
--     «Командно-Личный», команды по 3 человека): баллы команды = СУММА мест
--     всех её участников по всем периодам; место команды — по этой сумме
--     (ASC), тай-брейки: суммарная длина команды DESC, количество рыб DESC,
--     дальше — то же среднее при полном равенстве. Отбора "лучших N" нет:
--     считаются ВСЕ отмеченные участники команды (как и в зонно-весовом
--     движке).
--
-- Возможности из v7 сохранены:
--   - evsk_tier: статус соревнования для расчёта разрядов (evsk.py);
--   - half_zones_enabled: зона делится на 2 подзоны с НЕЗАВИСИМЫМ рейтингом
--     (через rank_zone в представлениях ниже);
--   - auto_draw_enabled: показывает кнопку автоматической жеребьёвки
--     (ручной ввод зон при этом остаётся доступным).
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tournament (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    name_full       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    venue           TEXT NOT NULL DEFAULT '',
    date_tour1      TEXT NOT NULL DEFAULT '',   -- дата соревнования (тур один)
    date_tour2      TEXT NOT NULL DEFAULT '',   -- не используется с v8, оставлено для совместимости
    discipline      TEXT NOT NULL DEFAULT 'Ловля спиннингом с берега',
    gender_group    TEXT NOT NULL DEFAULT '',
    periods_count   INTEGER NOT NULL DEFAULT 3 CHECK (periods_count BETWEEN 1 AND 4),
    zones           TEXT NOT NULL DEFAULT 'А,Б,В',  -- список зон через запятую
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

-- Зона спортсмена НА КАЖДЫЙ ПЕРИОД (заполняется на вкладке «Жеребьёвка»
-- после регистрации — вручную или автоматической жеребьёвкой).
CREATE TABLE IF NOT EXISTS entry_zones (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    period_number   INTEGER NOT NULL CHECK (period_number BETWEEN 1 AND 4),
    zone            TEXT NOT NULL,
    half_zone       TEXT,     -- '1'/'2' — используется только если half_zones_enabled=1
    PRIMARY KEY (entry_id, period_number)
);

-- Одна строка = одна рыба (ровно как в бумажной личной карте спортсмена).
CREATE TABLE IF NOT EXISTS catches (
    id              INTEGER PRIMARY KEY,
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    period_number   INTEGER NOT NULL CHECK (period_number BETWEEN 1 AND 4),
    length_cm       REAL NOT NULL CHECK (length_cm > 0)
);

-- Снятие спортсмена, действует с указанного периода до конца соревнования.
CREATE TABLE IF NOT EXISTS sanctions (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    from_period     INTEGER NOT NULL CHECK (from_period BETWEEN 1 AND 4),
    reason          TEXT,
    PRIMARY KEY (entry_id)
);

-- ----------------------------------------------------------------------------
-- VIEWS
-- ----------------------------------------------------------------------------

-- Итог по периоду: сколько рыб и какая суммарная длина (0/0, если ничего не
-- поймал — это НЕ дисквалификация).
CREATE VIEW IF NOT EXISTS v_period_totals AS
SELECT
    e.id AS entry_id, e.period_number,
    COUNT(c.id) AS fish_count,
    COALESCE(SUM(c.length_cm), 0) AS total_length
FROM (
    SELECT en.id, p.period_number
    FROM entries en
    CROSS JOIN (SELECT 1 AS period_number UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) p
    CROSS JOIN tournament tr
    WHERE p.period_number <= tr.periods_count
) e
LEFT JOIN catches c ON c.entry_id = e.id AND c.period_number = e.period_number
GROUP BY e.id, e.period_number;

-- Место в периоде (внутри зоны ЭТОГО периода). Дисквалифицированные (снятие
-- действует с этого периода включительно) -> фиксированное место = кол-во в
-- зоне + 3. Остальные -> RANK.AVG по (сумма длины DESC, кол-во рыб DESC).
-- rank_zone: зона, либо "зона/подзона" при half_zones_enabled — используется
-- ТОЛЬКО для группировки ранжирования; в протоколе показывается zone.
CREATE VIEW IF NOT EXISTS v_period_places AS
WITH base AS (
    SELECT
        pt.entry_id, pt.period_number,
        pt.fish_count, pt.total_length,
        ez.zone, ez.half_zone,
        CASE WHEN (SELECT half_zones_enabled FROM tournament) = 1
                  AND ez.half_zone IS NOT NULL AND ez.half_zone <> ''
             THEN ez.zone || '/' || ez.half_zone ELSE ez.zone END AS rank_zone,
        (s.entry_id IS NOT NULL AND s.from_period <= pt.period_number) AS disqualified
    FROM v_period_totals pt
    JOIN entry_zones ez ON ez.entry_id = pt.entry_id AND ez.period_number = pt.period_number
    LEFT JOIN sanctions s ON s.entry_id = pt.entry_id
),
zone_counts AS (
    SELECT period_number, rank_zone, COUNT(*) AS n
    FROM base GROUP BY period_number, rank_zone
),
ranked AS (
    SELECT b.*,
        RANK() OVER (
            PARTITION BY b.period_number, b.rank_zone
            ORDER BY (CASE WHEN b.disqualified THEN 1 ELSE 0 END) ASC,
                     b.total_length DESC, b.fish_count DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY b.period_number, b.rank_zone,
                         (CASE WHEN b.disqualified THEN 1 ELSE 0 END),
                         b.total_length, b.fish_count
        ) AS tie_n
    FROM base b
)
SELECT
    r.entry_id, r.period_number, r.zone, r.half_zone,
    r.fish_count, r.total_length, r.disqualified,
    CASE WHEN r.disqualified THEN zc.n + 3 ELSE r.rnk + (r.tie_n - 1) / 2.0 END AS period_place
FROM ranked r
JOIN zone_counts zc ON zc.period_number = r.period_number AND zc.rank_zone = r.rank_zone;

-- Сводка по спортсмену за всё соревнование + количество рыб по каждому
-- периоду отдельно (нужно и для протокола, и для тай-брейка "по рыбам с
-- последнего периода назад").
CREATE VIEW IF NOT EXISTS v_entry_totals AS
SELECT
    entry_id,
    SUM(period_place) AS sum_period_places,
    SUM(total_length) AS sum_length,
    SUM(fish_count)   AS sum_fish,
    MAX(CASE WHEN disqualified THEN 1 ELSE 0 END) AS disqualified_ever,
    MAX(CASE WHEN period_number = 1 THEN fish_count END) AS fish_p1,
    MAX(CASE WHEN period_number = 2 THEN fish_count END) AS fish_p2,
    MAX(CASE WHEN period_number = 3 THEN fish_count END) AS fish_p3,
    MAX(CASE WHEN period_number = 4 THEN fish_count END) AS fish_p4
FROM v_period_places
GROUP BY entry_id;

-- Итоговое личное место. Тай-брейки — см. шапку файла (п.1-5).
-- Периоды, которых нет в турнире, дают NULL -> COALESCE(-1) у всех одинаково
-- и на порядок не влияют.
CREATE VIEW IF NOT EXISTS v_final_place AS
WITH ranked AS (
    SELECT t.*,
        RANK() OVER (
            ORDER BY t.sum_period_places ASC, t.sum_length DESC, t.sum_fish DESC,
                     COALESCE(t.fish_p4, -1) DESC, COALESCE(t.fish_p3, -1) DESC,
                     COALESCE(t.fish_p2, -1) DESC, COALESCE(t.fish_p1, -1) DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY t.sum_period_places, t.sum_length, t.sum_fish,
                         t.fish_p4, t.fish_p3, t.fish_p2, t.fish_p1
        ) AS tie_n
    FROM v_entry_totals t
)
SELECT entry_id, sum_period_places, sum_length, sum_fish, disqualified_ever,
       fish_p1, fish_p2, fish_p3, fish_p4,
       rnk + (tie_n - 1) / 2.0 AS final_place
FROM ranked;

-- ----------------------------------------------------------------------------
-- КОМАНДНЫЙ ЗАЧЁТ (v8): сумма по ВСЕМ отмеченным участникам команды.
-- ----------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_team_totals AS
SELECT
    e.team_id,
    COUNT(DISTINCT e.id) AS members_count,
    SUM(t.sum_period_places) AS team_sum_places,
    SUM(t.sum_length)        AS team_sum_length,
    SUM(t.sum_fish)          AS team_sum_fish
FROM entries e
JOIN v_entry_totals t ON t.entry_id = e.id
WHERE e.team_id IS NOT NULL
GROUP BY e.team_id;

CREATE VIEW IF NOT EXISTS v_team_final AS
WITH ranked AS (
    SELECT tt.*,
        RANK() OVER (
            ORDER BY tt.team_sum_places ASC, tt.team_sum_length DESC, tt.team_sum_fish DESC
        ) AS rnk,
        COUNT(*) OVER (
            PARTITION BY tt.team_sum_places, tt.team_sum_length, tt.team_sum_fish
        ) AS tie_n
    FROM v_team_totals tt
)
SELECT team_id, members_count, team_sum_places, team_sum_length, team_sum_fish,
       rnk + (tie_n - 1) / 2.0 AS team_place
FROM ranked;

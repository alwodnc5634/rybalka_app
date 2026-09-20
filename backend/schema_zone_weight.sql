-- ============================================================================
-- Рыбалка — «зонный весовой» движок. v1.
-- Общий движок для ЧЕТЫРЁХ дисциплин: донка, поплавок, блесна (со льда),
-- мормышка (со льда). Различия между ними — только название дисциплины
-- в шапке/протоколе, сама логика подсчёта идентична (подтверждено на
-- реальном примере 4-Подсчет.xlsx, лист «Зона 1-А» — см. СПРАВОЧНИК §5).
-- Одна БД на турнир, один файл схемы на все четыре дисциплины — какая
-- именно дисциплина у конкретного турнира, хранится в tournament.discipline.
--
-- Логика (реверс-инжиниринг реальных формул):
--   - Один спортсмен (личник, либо личник — член команды) взвешивается
--     ОДИН РАЗ за тур (без периодов, в отличие от форели) — вес улова
--     целиком за тур, в конкретной зоне.
--   - Санкции — ИНДИВИДУАЛЬНЫЕ, два вида («страшные санкции», Вспом!C56:C58):
--       * «Снятие до взвешивания» (before_weigh): зачётный балл = -1
--         (штрафное последнее место при подсчёте зонного места).
--       * «Снятие после взвешивания» (after_weigh): зачётный балл
--         остаётся РЕАЛЬНЫМ весом (формула проверяет только «до
--         взвешивания», не «любое снятие») — то есть для расчёта места
--         используется фактический вес, а НЕ штраф. Но зонное место всё
--         равно принудительно выставляется фиксированным (см. ниже) —
--         в протоколе вес всегда показывается как 0 при ЛЮБОМ снятии.
--   - Место в зоне (v_zone_places): подтверждено формулой M10 реального
--     файла — RANK.AVG (среднее место при точном совпадении баллов) среди
--     ВСЕХ участников зоны по зачётному баллу; но у любого снятого
--     (до или после взвешивания) место ВСЕГДА принудительно = (число
--     участников зоны) + 3, а не через ранжирование. Побочный эффект
--     буквальной формулы: реальный вес «снятого после взвешивания» всё
--     равно участвует в вычислении RANK.AVG для ОСТАЛЬНЫХ участников зоны
--     (как и в самом Excel — RANK.AVG считается по всему диапазону, не
--     исключая снятых) — сознательно оставлено как есть, это не баг, а
--     точное соответствие проверенной формуле.
--   - Команда — это НЕ отдельная сущность со своим взвешиванием, а просто
--     признак у личника (entries.team_id): команда — это сумма её
--     участников. Флаг «в команде?» — это признак активного участника
--     ростера (не отбор «топ-N»); баллы/места команды = SUM по ВСЕМ
--     отмеченным участникам без отбора лучших (см. СПРАВОЧНИК §8, п.4).
--   - Командное снятие (санкция всей команде целиком, отдельно от личных
--     санкций её участников) в проверенных формулах зоны НЕ участвует —
--     оно наступает уже на уровне итогового протокола, но точная формула
--     не была подтверждена реальными данными (см. СПРАВОЧНИК §8, TODO).
--     В v1 отдельная командная санкция НЕ реализована: если нужно снять
--     команду целиком — снимите персонально каждого её участника, тогда
--     командная сумма автоматически отразит это (через их собственные
--     личные санкции). Это сознательное ограничение первой версии.
--   - Итоговый протокол (сумма мест/баллов за оба тура): подтверждено
--     формулами ПТР(Ч), столбцы AI/AK — это RANK.EQ (обычное спортивное
--     ранжирование: у равных — ОДИНАКОВЫЙ ранг, следующий занимает место
--     через пропуск, средних дробных мест здесь НЕТ), а не RANK.AVG,
--     который используется ТОЛЬКО для места внутри зоны. Это два разных,
--     оба подтверждённых правила на разных уровнях — не путать между
--     собой (в отличие от форели, где сумма и то, и другое приводит к
--     среднему дробному месту).
--     Личный итог (3 уровня): сумма мест за оба тура (меньше=лучше) →
--       сумма веса за оба тура (больше=лучше) → вес именно 2-го тура
--       (больше=лучше).
--     Командный итог (4 уровня): сумма мест команды за оба тура →
--       сумма баллов команды за оба тура → баллы команды именно во 2-м
--       туре → наибольший индивидуальный улов внутри команды.
--   - НЕ реализовано в v1 (сознательно отложено, см. СПРАВОЧНИК §8):
--     разряды/ЕВСК, полузоны (деление зоны на 2 подзоны), жеребьёвка как
--     генератор (зона вводится вручную, как и везде в этой программе).
--
-- v7: все три пункта выше теперь РЕАЛИЗОВАНЫ как выбор в общих данных
-- турнира (см. комментарий в schema_forel.sql — то же самое устройство:
-- evsk_tier / half_zones_enabled / auto_draw_enabled / entry_zones.half_zone
-- / rank_zone в представлениях ниже).
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tournament (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    name_full       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    venue           TEXT NOT NULL DEFAULT '',
    date_tour1      TEXT NOT NULL DEFAULT '',
    date_tour2      TEXT NOT NULL DEFAULT '',
    discipline      TEXT NOT NULL DEFAULT '',   -- полное название дисциплины (для шапки протокола)
    gender_group    TEXT NOT NULL DEFAULT '',
    tours_count     INTEGER NOT NULL DEFAULT 2 CHECK (tours_count IN (1,2)),
    zones           TEXT NOT NULL DEFAULT 'А,Б,В',   -- список зон через запятую
    evsk_tier       TEXT NOT NULL DEFAULT '',
    half_zones_enabled INTEGER NOT NULL DEFAULT 0 CHECK (half_zones_enabled IN (0,1)),
    auto_draw_enabled  INTEGER NOT NULL DEFAULT 0 CHECK (auto_draw_enabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS judges_signoff (
    id INTEGER PRIMARY KEY, role TEXT NOT NULL, category TEXT,
    full_name TEXT, sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY, reg_number INTEGER, name TEXT NOT NULL, region TEXT
);

CREATE TABLE IF NOT EXISTS athletes (
    id INTEGER PRIMARY KEY, full_name TEXT NOT NULL,
    rank_category TEXT, birth_date TEXT
);

-- Один участник = один спортсмен (личник, либо личник-член команды).
-- team_id NULL -> чисто личный зачёт; team_id задан -> ещё и в командном.
CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY,
    reg_number      INTEGER,
    athlete_id      INTEGER NOT NULL REFERENCES athletes(id),
    team_id         INTEGER REFERENCES teams(id),
    start_order     INTEGER
);

-- Зона спортсмена на тур (вводится вручную по жребию/протоколу зон, либо
-- автоматически кнопкой "Провести жеребьёвку", если auto_draw_enabled=1).
CREATE TABLE IF NOT EXISTS entry_zones (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    zone            TEXT NOT NULL,
    half_zone       TEXT,     -- '1'/'2' — используется только если half_zones_enabled=1
    PRIMARY KEY (entry_id, tour_number)
);

-- Взвешивание: один вес на спортсмена на тур (без периодов).
CREATE TABLE IF NOT EXISTS weigh_ins (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    weight_score    REAL NOT NULL DEFAULT 0,
    fish_count      INTEGER,     -- справочное поле для печати, не участвует в ранжировании
    PRIMARY KEY (entry_id, tour_number)
);

-- Личная санкция спортсмену на конкретный тур ("до" или "после" взвешивания).
CREATE TABLE IF NOT EXISTS sanctions (
    entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tour_number     INTEGER NOT NULL CHECK (tour_number IN (1,2)),
    sanction_type   TEXT NOT NULL CHECK (sanction_type IN ('before_weigh','after_weigh')),
    reason          TEXT,
    PRIMARY KEY (entry_id, tour_number)
);

-- ----------------------------------------------------------------------------
-- VIEWS
-- ----------------------------------------------------------------------------

-- Зачётный балл и отображаемый вес с учётом санкций (аналог столбцов AB/AD
-- реального файла). individual_score используется для ранжирования;
-- displayed_weight — что показывается в протоколе.
CREATE VIEW IF NOT EXISTS v_entry_tour_score AS
SELECT
    w.entry_id, w.tour_number, w.fish_count,
    ez.zone, ez.half_zone,
    CASE WHEN (SELECT half_zones_enabled FROM tournament) = 1
              AND ez.half_zone IS NOT NULL AND ez.half_zone <> ''
         THEN ez.zone || '/' || ez.half_zone ELSE ez.zone END AS rank_zone,
    s.sanction_type,
    CASE WHEN s.sanction_type = 'before_weigh' THEN -1 ELSE w.weight_score END AS individual_score,
    CASE WHEN s.sanction_type IN ('before_weigh','after_weigh') THEN 0 ELSE w.weight_score END AS displayed_weight,
    CASE WHEN s.sanction_type IN ('before_weigh','after_weigh') THEN 1 ELSE 0 END AS is_disqualified
FROM weigh_ins w
JOIN entry_zones ez ON ez.entry_id = w.entry_id AND ez.tour_number = w.tour_number
LEFT JOIN sanctions s ON s.entry_id = w.entry_id AND s.tour_number = w.tour_number;

-- Место в зоне: НЕ дисквалифицированные — RANK.AVG по individual_score DESC
-- (среди ВСЕХ участников зоны, включая снятых — буквально как Excel);
-- дисквалифицированные (любой вид снятия) — фиксированное место
-- (число участников зоны + 3), не через ранжирование. Группировка — по
-- rank_zone (зона либо зона/подзона, см. v_entry_tour_score); при
-- включённых полузонах это фактически место В ПОДЗОНЕ.
CREATE VIEW IF NOT EXISTS v_zone_places AS
WITH zone_counts AS (
    SELECT tour_number, rank_zone, COUNT(*) AS n FROM v_entry_tour_score GROUP BY tour_number, rank_zone
),
ranked AS (
    SELECT s.*,
        (SELECT COUNT(*) FROM v_entry_tour_score s2
          WHERE s2.tour_number = s.tour_number AND s2.rank_zone = s.rank_zone
            AND s2.individual_score > s.individual_score) + 1
        +
        (SELECT COUNT(*) FROM v_entry_tour_score s2
          WHERE s2.tour_number = s.tour_number AND s2.rank_zone = s.rank_zone
            AND s2.individual_score >= s.individual_score)
        AS rank_avg_x2
    FROM v_entry_tour_score s
)
SELECT
    r.entry_id, r.tour_number, r.zone, r.half_zone, r.fish_count, r.displayed_weight, r.is_disqualified,
    CASE WHEN r.is_disqualified THEN zc.n + 3 ELSE r.rank_avg_x2 / 2.0 END AS zone_place
FROM ranked r
JOIN zone_counts zc ON zc.tour_number = r.tour_number AND zc.rank_zone = r.rank_zone;

-- ----------------------------------------------------------------------------
-- Личный итог за оба тура. Тай-брейк — RANK.EQ (обычное ранжирование,
-- НЕ усреднение) по (сумма мест ASC, сумма веса DESC, вес 2 тура DESC) —
-- подтверждено формулами ПТР(Ч), столбцы AI/AK.
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_individual_final AS
WITH agg AS (
    SELECT
        z.entry_id,
        SUM(z.zone_place) AS sum_places,
        SUM(z.displayed_weight) AS sum_weight,
        MAX(CASE WHEN z.tour_number = 2 THEN z.displayed_weight END) AS tour2_weight,
        MAX(z.is_disqualified) AS disqualified_ever,
        COUNT(*) AS tours_played
    FROM v_zone_places z
    GROUP BY z.entry_id
)
SELECT entry_id, sum_places, sum_weight, tour2_weight, disqualified_ever, tours_played,
    RANK() OVER (
        ORDER BY sum_places ASC, sum_weight DESC, COALESCE(tour2_weight, -1) DESC
    ) AS final_place
FROM agg;

-- ----------------------------------------------------------------------------
-- Командный итог. Команда = SUM по ВСЕМ отмеченным участникам (team_id),
-- без отбора "топ-N". Тай-брейк (4 уровня, RANK.EQ) — подтверждено
-- формулами ПТР(ком)(Ч): сумма мест команды -> сумма баллов команды ->
-- баллы команды 2 тура -> наибольший индивидуальный улов в команде.
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_team_tour_score AS
SELECT
    e.team_id, z.tour_number,
    SUM(z.zone_place) AS team_sum_places,
    SUM(z.displayed_weight) AS team_sum_weight,
    MAX(z.displayed_weight) AS team_max_individual_weight
FROM entries e
JOIN v_zone_places z ON z.entry_id = e.id
WHERE e.team_id IS NOT NULL
GROUP BY e.team_id, z.tour_number;

CREATE VIEW IF NOT EXISTS v_team_final AS
WITH agg AS (
    SELECT
        team_id,
        SUM(team_sum_places) AS sum_places,
        SUM(team_sum_weight) AS sum_weight,
        MAX(CASE WHEN tour_number = 2 THEN team_sum_weight END) AS tour2_weight,
        MAX(team_max_individual_weight) AS best_individual_catch,
        COUNT(*) AS tours_played
    FROM v_team_tour_score
    GROUP BY team_id
)
SELECT team_id, sum_places, sum_weight, tour2_weight, best_individual_catch, tours_played,
    RANK() OVER (
        ORDER BY sum_places ASC, sum_weight DESC, COALESCE(tour2_weight, -1) DESC,
                 COALESCE(best_individual_catch, -1) DESC
    ) AS final_place
FROM agg;

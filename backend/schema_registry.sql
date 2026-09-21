-- ============================================================================
-- Реестр турниров ("лаунчер"). Отдельный маленький файл рядом с папкой data/
-- tournaments/ — аналог старого frmLauncher: список турниров + куда указывает
-- каждый (какая дисциплина, в какой подпапке лежит его собственный файл БД).
-- Сама регистрация/подсчёт каждого турнира — в ЕГО ОТДЕЛЬНОМ sqlite-файле
-- (как и в Excel-версии — один турнир = свой набор файлов в своей папке).
-- ============================================================================

CREATE TABLE IF NOT EXISTS tournaments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,     -- имя подпапки в data/tournaments/
    name_full           TEXT NOT NULL,
    discipline_code     TEXT NOT NULL,            -- 'lodki','donka','poplavok','blesna','mormyshka'
    venue               TEXT,
    date_tour1          TEXT,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    -- v8: «весовой режим» для дисциплины «Ловля спиннингом с берега».
    -- На соревнованиях уровня муниципального образования рыбу не измеряют
    -- сантиметрами, а ВЗВЕШИВАЮТ, и подсчёт идёт один в один как в донке
    -- (туры, зона на тур, одно взвешивание за тур, периодов нет). Если флаг
    -- включён, турнир этой дисциплины заводится и считается "зонным весовым"
    -- движком (schema_zone_weight.sql) — название дисциплины при этом
    -- остаётся прежним. Для остальных дисциплин флаг не используется.
    weight_mode         INTEGER NOT NULL DEFAULT 0 CHECK (weight_mode IN (0,1))
);

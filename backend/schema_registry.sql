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
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

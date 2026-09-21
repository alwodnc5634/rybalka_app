"""
Рыбалка — общий лаунчер турниров + модуль «Лодки» (v4).
Локальный сервер без внешних зависимостей.

Запуск (разработка): python3 server.py
Запуск (собранный .exe / бинарник macOS): двойной клик, ничего доп. не нужно.

Откроется на http://127.0.0.1:8642 — интерфейс в frontend/.

---------------------------------------------------------------------------
v4 — добавлен общий лаунчер турниров (аналог старого frmLauncher):
  - один общий файл-реестр data/реестр.sqlite: список турниров (название,
    дисциплина, место, дата, статус) и указатель, в какой подпапке лежит
    файл БД именно этого турнира;
  - каждый турнир — своя папка data/tournaments/<slug>/data.sqlite, со
    своей схемой (как в Excel-версии — один турнир = свой набор файлов);
  - из 5 запланированных дисциплин полноценно реализована пока только
    «Лодки»; остальные 4 можно создать в лаунчере (папка/файл заводятся
    заранее, чтобы данные не потерялись, когда модуль будет готов), но при
    открытии показывается страница-заглушка «модуль в разработке» —
    честно, а не фальшивый интерфейс, который ничего не считает;
  - при обновлении с версии v2/v3 (где был только один турнир в
    data/лодки.sqlite) этот файл автоматически переносится в реестр как
    первый турнир — данные пользователя не пропадают.

v3 — исправления «не сохраняется / не выдаёт варианты» (см. server.py из
предыдущей версии / СПРАВОЧНИК проекта): утечка соединений с БД на статике,
однопоточный сервер, молчаливые ошибки без обратной связи, отсутствие
автоподсказки спортсменов. Все три исправления сохранены и здесь.

v5 — добавлена дисциплина «Форель» (спиннинг с берега), возвращена в скоуп
по запросу пользователя. Баллы периода = СУММА ДЛИН пойманных рыб (не
количество, не вес); тай-брейк при равной сумме длины — больше рыб выше.
Каждый спортсмен на каждый период заполняет бумажную «личную карту» (длина
каждой рыбы), судьи переносят в программу вручную. Свой файл БД на турнир
(`schema_forel.sql`), свой набор эндпоинтов `/api/forel/*` — код лодок не
трогали. Подробности логики — в шапке `schema_forel.sql`.
---------------------------------------------------------------------------
"""
import http.server
import json
import os
import random
import shutil
import socketserver
import sqlite3
import sys
import threading
import traceback
import webbrowser
from urllib.parse import urlparse, parse_qs

import evsk

# ---------------------------------------------------------------------------
# Пути. Данные пишутся рядом с РЕАЛЬНЫМ .exe (sys.executable), ресурсы
# (frontend, schema) читаются из временной папки распаковки PyInstaller.
# ---------------------------------------------------------------------------
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
    FRONTEND_DIR = os.path.join(BUNDLE_DIR, "frontend")
    SCHEMA_LODKI_PATH = os.path.join(BUNDLE_DIR, "schema_lodki_mvp.sql")
    SCHEMA_FOREL_PATH = os.path.join(BUNDLE_DIR, "schema_forel.sql")
    SCHEMA_ZONE_WEIGHT_PATH = os.path.join(BUNDLE_DIR, "schema_zone_weight.sql")
    SCHEMA_REGISTRY_PATH = os.path.join(BUNDLE_DIR, "schema_registry.sql")
    EVSK_STANDARDS_PATH = os.path.join(BUNDLE_DIR, "evsk_standards.json")
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
    SCHEMA_LODKI_PATH = os.path.join(BASE_DIR, "backend", "schema_lodki_mvp.sql")
    SCHEMA_FOREL_PATH = os.path.join(BASE_DIR, "backend", "schema_forel.sql")
    SCHEMA_ZONE_WEIGHT_PATH = os.path.join(BASE_DIR, "backend", "schema_zone_weight.sql")
    SCHEMA_REGISTRY_PATH = os.path.join(BASE_DIR, "backend", "schema_registry.sql")
    EVSK_STANDARDS_PATH = os.path.join(BASE_DIR, "backend", "evsk_standards.json")

EVSK_STANDARDS = evsk.load_standards(EVSK_STANDARDS_PATH)

DATA_DIR = os.path.join(BASE_DIR, "data")
TOURNAMENTS_DIR = os.path.join(DATA_DIR, "tournaments")
REGISTRY_DB_PATH = os.path.join(DATA_DIR, "реестр.sqlite")
LEGACY_DB_PATH = os.path.join(DATA_DIR, "лодки.sqlite")  # из v1-v3
PORT = 8642

# Справочник дисциплин (эталон — schema.sql, раздел 1). "implemented" — есть
# ли уже рабочий модуль регистрации/подсчёта; сейчас только лодки.
DISCIPLINES = {
    "lodki":     {"name": "Ловля спиннингом с лодок",        "engine": "fish_measurement", "implemented": True},
    "forel":     {"name": "Ловля спиннингом с берега",        "engine": "zone_length_sum", "implemented": True},
    "donka":     {"name": "Ловля донной удочкой",            "engine": "zone_weight",      "implemented": True},
    "poplavok":  {"name": "Ловля поплавочной удочкой",       "engine": "zone_weight",      "implemented": True},
    "blesna":    {"name": "Ловля на блесну со льда",         "engine": "zone_weight",      "implemented": True},
    "mormyshka": {"name": "Ловля на мормышку со льда",       "engine": "zone_weight",      "implemented": True},
}
ZONE_WEIGHT_DISCIPLINES = {"donka", "poplavok", "blesna", "mormyshka"}

# Список статусов соревнований для выпадающего списка ЕВСК в интерфейсе
# (см. evsk.TIER_ORDER — тот же порядок, от высшего статуса к низшему).
EVSK_TIER_OPTIONS = evsk.TIER_ORDER

WRITE_LOCK = threading.Lock()  # сериализация записей (турнир + реестр)


def _evsk_gender_from_group(gender_group):
    """Программа не хранит пол каждого спортсмена отдельно — только общий
    состав турнира (tournament.gender_group, свободный текст вроде
    "мужчины, женщины"). Разбираем его в 'М'/'Ж'/'МЖ' и применяем как общий
    контекст пола ко ВСЕМ записям турнира при расчёте разрядов (осознанное
    ограничение v7 — если в одном турнире реально соревнуются оба пола с
    разными нормативами, точнее посчитать нельзя без отдельного поля пола
    у спортсмена)."""
    g = (gender_group or "").lower()
    has_m, has_f = "муж" in g, "жен" in g
    if has_m and has_f:
        return "МЖ"
    if has_m:
        return "М"
    if has_f:
        return "Ж"
    return "МЖ"


def _evsk_tournament_year(date_tour1):
    m = (date_tour1 or "")[:4]
    try:
        return int(m)
    except ValueError:
        return None


def _run_zone_draw(conn):
    """Общая жеребьёвка для «Форели» и «зонного весового» движка: случайно
    назначает ЗОНУ (и подзону, если half_zones_enabled) на каждый тур —
    равномерно (round-robin по перемешанному списку зон) — и ОДИН РАЗ,
    независимо от тура, очерёдность старта всем участникам. Не блокирует
    последующую ручную правку отдельных записей (кнопка "Провести
    жеребьёвку" просто перезаписывает текущие значения)."""
    t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
    zones = [z.strip() for z in (t["zones"] or "").split(",") if z.strip()]
    if not zones:
        raise ApiError("не заданы зоны турнира — заполните список зон в общих данных")
    tours_count = t["tours_count"]
    half_enabled = bool(t["half_zones_enabled"])

    entry_ids = [r["id"] for r in conn.execute("SELECT id FROM entries").fetchall()]
    if not entry_ids:
        return 0

    # очерёдность старта — один раз для всех участников
    shuffled = entry_ids[:]
    random.shuffle(shuffled)
    for i, eid in enumerate(shuffled, start=1):
        conn.execute("UPDATE entries SET start_order=? WHERE id=?", (i, eid))

    for tn in range(1, tours_count + 1):
        pool = entry_ids[:]
        random.shuffle(pool)
        zone_groups = {z: [] for z in zones}
        for i, eid in enumerate(pool):
            zone_groups[zones[i % len(zones)]].append(eid)
        for zone, members in zone_groups.items():
            if half_enabled:
                random.shuffle(members)
                for i, eid in enumerate(members):
                    half = "1" if i % 2 == 0 else "2"
                    conn.execute(
                        "INSERT INTO entry_zones (entry_id, tour_number, zone, half_zone) VALUES (?,?,?,?) "
                        "ON CONFLICT(entry_id, tour_number) DO UPDATE SET zone=excluded.zone, half_zone=excluded.half_zone",
                        (eid, tn, zone, half),
                    )
            else:
                for eid in members:
                    conn.execute(
                        "INSERT INTO entry_zones (entry_id, tour_number, zone, half_zone) VALUES (?,?,?,NULL) "
                        "ON CONFLICT(entry_id, tour_number) DO UPDATE SET zone=excluded.zone, half_zone=NULL",
                        (eid, tn, zone),
                    )
    conn.commit()
    return len(entry_ids)


def _run_forel_draw(conn):
    """Жеребьёвка для «Ловли спиннингом с берега» (v8): туров нет, зона
    назначается НА КАЖДЫЙ ПЕРИОД, причём с РОТАЦИЕЙ — спортсмены переходят
    из зоны в зону между периодами (так это и делается на реальных
    соревнованиях, и так устроен реальный протокол: "Зона 1пер", "Зона
    2пер", "Зона 3пер" у одного человека разные).

    Как считается: участники случайно делятся на N примерно равных групп
    (N = число зон); в периоде p группа i уходит в зону с номером
    (i + p - 1) mod N. Это гарантирует и равномерность в каждом периоде, и
    то, что одна и та же группа не сидит всё соревнование в одной зоне.
    Ручная правка отдельных записей после жеребьёвки остаётся доступной."""
    t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
    zones = [z.strip() for z in (t["zones"] or "").split(",") if z.strip()]
    if not zones:
        raise ApiError("не заданы зоны турнира — заполните список зон в общих данных")
    periods_count = t["periods_count"]
    half_enabled = bool(t["half_zones_enabled"])

    entry_ids = [r["id"] for r in conn.execute("SELECT id FROM entries").fetchall()]
    if not entry_ids:
        return 0

    shuffled = entry_ids[:]
    random.shuffle(shuffled)
    for i, eid in enumerate(shuffled, start=1):
        conn.execute("UPDATE entries SET start_order=? WHERE id=?", (i, eid))

    # деление на группы по числу зон (группа = те, кто вместе кочует по зонам)
    groups = [[] for _ in zones]
    for i, eid in enumerate(shuffled):
        groups[i % len(zones)].append(eid)

    for p in range(1, periods_count + 1):
        for gi, members in enumerate(groups):
            zone = zones[(gi + p - 1) % len(zones)]
            members = members[:]
            random.shuffle(members)
            for i, eid in enumerate(members):
                half = ("1" if i % 2 == 0 else "2") if half_enabled else None
                conn.execute(
                    "INSERT INTO entry_zones (entry_id, period_number, zone, half_zone) VALUES (?,?,?,?) "
                    "ON CONFLICT(entry_id, period_number) DO UPDATE SET zone=excluded.zone, "
                    "half_zone=excluded.half_zone",
                    (eid, p, zone, half),
                )
    conn.commit()
    return len(entry_ids)

# Открытый сейчас турнир (одна программа = один активный турнир одновременно,
# как и было решено: один ноутбук, одна база). Меняется через /api/launcher/open.
CURRENT = {"id": None, "slug": None, "discipline_code": None, "name_full": None, "db_path": None,
           "weight_mode": 0}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Реестр турниров
# ---------------------------------------------------------------------------
def registry_db():
    conn = sqlite3.connect(REGISTRY_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def init_registry():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = registry_db()
    with open(SCHEMA_REGISTRY_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    # v8: реестр, созданный прежней версией, не знает про weight_mode —
    # CREATE TABLE IF NOT EXISTS столбец не добавит, поэтому добавляем явно.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "weight_mode" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN weight_mode INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def tournament_slug(tid):
    return f"t{tid:04d}"


def tournament_db_path(slug):
    return os.path.join(TOURNAMENTS_DIR, slug, "data.sqlite")


def migrate_legacy_if_needed():
    """v1-v3 хранили один турнир в data/лодки.sqlite. Если реестр пуст, а
    такой файл есть — переносим его как первый турнир в реестре, чтобы уже
    введённые пользователем данные не потерялись при обновлении программы."""
    if not os.path.exists(LEGACY_DB_PATH):
        return
    conn = registry_db()
    count = conn.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0]
    if count > 0:
        conn.close()
        return

    name_full, venue, date_tour1 = "Турнир (перенесён автоматически)", "", ""
    try:
        old = sqlite3.connect(LEGACY_DB_PATH)
        row = old.execute("SELECT name_full, venue, date_tour1 FROM tournament WHERE id=1").fetchone()
        if row:
            name_full = row[0] or name_full
            venue, date_tour1 = row[1] or "", row[2] or ""
        old.close()
    except Exception:
        pass

    cur = conn.execute(
        "INSERT INTO tournaments (slug, name_full, discipline_code, venue, date_tour1) VALUES (?,?,?,?,?)",
        ("__pending__", name_full, "lodki", venue, date_tour1),
    )
    tid = cur.lastrowid
    slug = tournament_slug(tid)
    conn.execute("UPDATE tournaments SET slug=? WHERE id=?", (slug, tid))
    conn.commit()
    conn.close()

    new_dir = os.path.join(TOURNAMENTS_DIR, slug)
    os.makedirs(new_dir, exist_ok=True)
    shutil.move(LEGACY_DB_PATH, tournament_db_path(slug))
    print(f"[миграция] найден старый файл лодки.sqlite (версия v1-v3) — перенесён в {new_dir} как турнир #{tid}")


def init_tournament_db(db_path, discipline_code, name_full, venue, date_tour1, weight_mode=0):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    # v8: «спиннинг с берега» в весовом режиме считается зонно-весовым движком
    # (правила муниципальных соревнований — один в один как в донке).
    if discipline_code == "forel" and weight_mode:
        with open(SCHEMA_ZONE_WEIGHT_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        row = conn.execute("SELECT id FROM tournament WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tournament (id, name_full, venue, date_tour1, discipline) VALUES (1, ?, ?, ?, ?)",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES["forel"]["name"]),
            )
        else:
            conn.execute(
                "UPDATE tournament SET name_full=?, venue=?, date_tour1=?, discipline=? WHERE id=1",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES["forel"]["name"]),
            )
        conn.commit()
        conn.close()
        return
    if discipline_code == "lodki":
        with open(SCHEMA_LODKI_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        row = conn.execute("SELECT id FROM tournament WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tournament (id, name_full, status, venue, date_tour1, discipline, gender_group) "
                "VALUES (1, ?, '', ?, ?, ?, '')",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
        else:
            conn.execute(
                "UPDATE tournament SET name_full=?, venue=?, date_tour1=?, discipline=? WHERE id=1",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
    elif discipline_code == "forel":
        with open(SCHEMA_FOREL_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        row = conn.execute("SELECT id FROM tournament WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tournament (id, name_full, venue, date_tour1, discipline) VALUES (1, ?, ?, ?, ?)",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
        else:
            conn.execute(
                "UPDATE tournament SET name_full=?, venue=?, date_tour1=?, discipline=? WHERE id=1",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
    elif discipline_code in ZONE_WEIGHT_DISCIPLINES:
        with open(SCHEMA_ZONE_WEIGHT_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        row = conn.execute("SELECT id FROM tournament WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tournament (id, name_full, venue, date_tour1, discipline) VALUES (1, ?, ?, ?, ?)",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
        else:
            conn.execute(
                "UPDATE tournament SET name_full=?, venue=?, date_tour1=?, discipline=? WHERE id=1",
                (name_full, venue or "", date_tour1 or "", DISCIPLINES[discipline_code]["name"]),
            )
    else:
        # Модуль этой дисциплины пока не реализован — заводим папку/файл
        # заранее (честно, без фиктивных данных), чтобы ничего не потерять,
        # когда движок будет готов.
        conn.execute("CREATE TABLE IF NOT EXISTS _module_status (discipline_code TEXT, note TEXT)")
        conn.execute("DELETE FROM _module_status")
        conn.execute(
            "INSERT INTO _module_status (discipline_code, note) VALUES (?, ?)",
            (discipline_code, "модуль пока в разработке"),
        )
    conn.commit()
    conn.close()


def _evsk_rank_after(evsk_data, key_fn):
    """Словарь {ключ: разряд ПОСЛЕ турнира} по результату расчёта ЕВСК —
    для столбца «Разряд после» в протоколе (в реальных протоколах он есть
    рядом с «Разряд до»).

    Берётся ЛУЧШИЙ из личного и командного нормативов. Если нового разряда
    нет (в том числе когда имеющийся просто подтверждён) — ключа в словаре
    не будет, и «разряд после» останется равным «разряду до»."""
    best = {}
    for r in list(evsk_data.get("individuals", [])) + list(evsk_data.get("teams", [])):
        label = r.get("app_label")
        if not label:
            continue
        key = key_fn(r)
        if key is None:
            continue
        if key not in best or evsk.ladder_index(label) > evsk.ladder_index(best[key]):
            best[key] = label
    return best


def _discipline_page(discipline_code, weight_mode=0):
    """Какая страница интерфейса ведёт этот турнир. Отдельная функция, чтобы
    правило жило в одном месте: «спиннинг с берега» в весовом режиме ведётся
    экраном зонно-весового движка (правила как в донке)."""
    if discipline_code == "forel":
        return "zone_weight.html" if weight_mode else "forel.html"
    if discipline_code == "lodki":
        return "lodki.html"
    if discipline_code in ZONE_WEIGHT_DISCIPLINES:
        return "zone_weight.html"
    return "stub.html"


def _migrate_forel_to_periods(db_path):
    """v8: приводит турнир «спиннинга с берега», заведённый прежней версией
    (туры + одна зона на весь тур), к новой структуре — только периоды, зона
    на КАЖДЫЙ период.

    Что сохраняется: всё, что было в 1-м туре — его зона ставится всем
    периодам, его карты (пойманные рыбы) и снятие переносятся как есть.
    Данные 2-го тура отбрасываются: туров в дисциплине больше нет вообще
    (прямое указание пользователя — «тур будет всегда один, его можно убрать
    вообще, оставляем только периоды»). Регистрация, судьи, команды и
    спортсмены не затрагиваются."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(entry_zones)").fetchall()}
        if not cols or "tour_number" not in cols:
            return  # уже новая структура
        t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
        t = dict(t) if t else {}
        periods_count = t.get("periods_count") or 3
        zones_old = conn.execute("SELECT * FROM entry_zones WHERE tour_number=1").fetchall()
        catches_old = conn.execute("SELECT * FROM catches WHERE tour_number=1").fetchall()
        sanc_old = conn.execute("SELECT * FROM sanctions WHERE tour_number=1").fetchall()

        for v in ("v_period_totals", "v_period_places", "v_tour_totals", "v_tour_zone_place",
                  "v_tour_overall_place", "v_final_place", "v_entry_totals",
                  "v_team_totals", "v_team_final"):
            conn.execute("DROP VIEW IF EXISTS " + v)
        for tbl in ("entry_zones", "catches", "sanctions", "tournament"):
            conn.execute("DROP TABLE IF EXISTS " + tbl)
        with open(SCHEMA_FOREL_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())

        conn.execute(
            "INSERT INTO tournament (id, name_full, status, venue, date_tour1, date_tour2, discipline, "
            "gender_group, periods_count, zones, evsk_tier, half_zones_enabled, auto_draw_enabled) "
            "VALUES (1,?,?,?,?,'',?,?,?,?,?,?,?)",
            (t.get("name_full", ""), t.get("status", ""), t.get("venue", ""), t.get("date_tour1", ""),
             t.get("discipline") or DISCIPLINES["forel"]["name"], t.get("gender_group", ""),
             periods_count, t.get("zones") or "А,Б,В", t.get("evsk_tier", ""),
             t.get("half_zones_enabled", 0), t.get("auto_draw_enabled", 0)),
        )
        for z in zones_old:      # зона 1-го тура -> всем периодам
            for p in range(1, periods_count + 1):
                conn.execute(
                    "INSERT OR REPLACE INTO entry_zones (entry_id, period_number, zone, half_zone) VALUES (?,?,?,?)",
                    (z["entry_id"], p, z["zone"], z["half_zone"] if "half_zone" in z.keys() else None),
                )
        for c in catches_old:
            conn.execute("INSERT INTO catches (entry_id, period_number, length_cm) VALUES (?,?,?)",
                         (c["entry_id"], c["period_number"], c["length_cm"]))
        for s in sanc_old:
            conn.execute("INSERT OR REPLACE INTO sanctions (entry_id, from_period, reason) VALUES (?,?,?)",
                         (s["entry_id"], s["from_period"], s["reason"] if "reason" in s.keys() else ""))
        conn.commit()
        print("[миграция] турнир «спиннинг с берега» переведён на структуру без туров "
              "(зоны по периодам): " + db_path)
    finally:
        conn.close()


def require_current():
    if not CURRENT["db_path"]:
        raise ApiError("нет открытого турнира — откройте или создайте турнир в лаунчере", status=409)


def require_current_lodki():
    require_current()
    if CURRENT["discipline_code"] != "lodki":
        raise ApiError(
            "модуль дисциплины «" + DISCIPLINES.get(CURRENT["discipline_code"], {}).get("name", CURRENT["discipline_code"])
            + "» пока в разработке — регистрация и подсчёт для лодок готовы, для этой дисциплины ещё нет",
            status=409,
        )


def get_db():
    require_current_lodki()
    conn = sqlite3.connect(CURRENT["db_path"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def require_current_forel():
    require_current()
    if CURRENT["discipline_code"] != "forel":
        raise ApiError(
            "модуль дисциплины «" + DISCIPLINES.get(CURRENT["discipline_code"], {}).get("name", CURRENT["discipline_code"])
            + "» пока в разработке — регистрация и подсчёт для форели готовы, для этой дисциплины ещё нет",
            status=409,
        )
    if CURRENT.get("weight_mode"):
        raise ApiError(
            "у этого турнира включён подсчёт ПО ВЕСУ (правила как в донке) — "
            "он ведётся на другом экране; вернитесь в список турниров и откройте турнир заново",
            status=409,
        )


def get_forel_db():
    require_current_forel()
    conn = sqlite3.connect(CURRENT["db_path"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def require_current_zw():
    require_current()
    # v8: «спиннинг с берега» с включённым весовым режимом считается этим же
    # движком (правила муниципальных соревнований — как в донке).
    if CURRENT["discipline_code"] == "forel" and CURRENT.get("weight_mode"):
        return
    if CURRENT["discipline_code"] not in ZONE_WEIGHT_DISCIPLINES:
        raise ApiError(
            "модуль дисциплины «" + DISCIPLINES.get(CURRENT["discipline_code"], {}).get("name", CURRENT["discipline_code"])
            + "» пока в разработке — регистрация и подсчёт для этой дисциплины ещё нет",
            status=409,
        )


def get_zw_db():
    require_current_zw()
    conn = sqlite3.connect(CURRENT["db_path"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ---------------- API: GET ----------------
    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()  # статика — БД тут не нужна
        try:
            self._handle_api_get(parsed.path)
        except ApiError as e:
            print(f"[API GET {parsed.path}] {e}")
            self._send_json({"error": str(e)}, status=e.status)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": f"Внутренняя ошибка сервера: {e}"}, status=500)

    def _handle_api_get(self, path):
        # ---------- Лаунчер ----------
        if path == "/api/info":
            return self._send_json({
                "db_path": CURRENT["db_path"],
                "registry_path": REGISTRY_DB_PATH,
                "frontend_dir": FRONTEND_DIR,
                "frozen": FROZEN,
                "base_dir": BASE_DIR,
                "current": dict(CURRENT),
            })

        if path == "/api/launcher/disciplines":
            return self._send_json([
                {"code": code, "name": d["name"], "implemented": d["implemented"]}
                for code, d in DISCIPLINES.items()
            ])

        if path == "/api/evsk_tiers":
            return self._send_json(EVSK_TIER_OPTIONS)

        if path == "/api/launcher/tournaments":
            conn = registry_db()
            try:
                rows = conn.execute("SELECT * FROM tournaments ORDER BY created_at DESC, id DESC").fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    disc = DISCIPLINES.get(d["discipline_code"], {})
                    d["discipline_name"] = disc.get("name", d["discipline_code"])
                    d["implemented"] = disc.get("implemented", False)
                    d["page"] = _discipline_page(d["discipline_code"], d.get("weight_mode"))
                    result.append(d)
                return self._send_json(result)
            finally:
                conn.close()

        if path == "/api/launcher/current":
            if not CURRENT["id"]:
                return self._send_json({})
            disc = DISCIPLINES.get(CURRENT["discipline_code"], {})
            return self._send_json({**CURRENT, "implemented": disc.get("implemented", False),
                                     "discipline_name": disc.get("name", CURRENT["discipline_code"]),
                                     "page": _discipline_page(CURRENT["discipline_code"],
                                                              CURRENT.get("weight_mode"))})

        # ---------- Модуль «Форель» ----------
        if path.startswith("/api/forel/"):
            return self._handle_forel_get(path)

        # ---------- Модуль «зонный весовой» (донка/поплавок/блесна/мормышка) ----------
        if path.startswith("/api/zw/"):
            return self._handle_zw_get(path)

        # ---------- Модуль «Лодки» ----------
        conn = get_db()
        try:
            if path == "/api/tournament":
                row = conn.execute("SELECT * FROM tournament WHERE id = 1").fetchone()
                return self._send_json(dict(row) if row else {})

            if path == "/api/judges":
                rows = conn.execute("SELECT * FROM judges_signoff ORDER BY sort_order, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/teams":
                rows = conn.execute("SELECT * FROM teams ORDER BY reg_number, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/athletes":
                rows = conn.execute(
                    "SELECT DISTINCT full_name, rank_category, birth_date FROM athletes ORDER BY full_name"
                ).fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/entries":
                entries = conn.execute(
                    "SELECT e.*, t.name AS team_name FROM entries e "
                    "LEFT JOIN teams t ON t.id = e.team_id "
                    "ORDER BY e.team_id, e.pair_index_in_team, e.reg_number"
                ).fetchall()
                result = []
                for e in entries:
                    members = conn.execute(
                        "SELECT a.id, a.full_name, a.rank_category, a.birth_date "
                        "FROM entry_members em JOIN athletes a ON a.id = em.athlete_id "
                        "WHERE em.entry_id = ? ORDER BY em.slot_order",
                        (e["id"],),
                    ).fetchall()
                    weigh = conn.execute(
                        "SELECT weight_score, fish_count FROM pair_weigh_ins WHERE entry_id = ?",
                        (e["id"],),
                    ).fetchone()
                    result.append({
                        "id": e["id"], "reg_number": e["reg_number"], "name": e["name"],
                        "team_id": e["team_id"], "team_name": e["team_name"],
                        "pair_index_in_team": e["pair_index_in_team"], "start_order": e["start_order"],
                        "members": [dict(m) for m in members],
                        "weight_score": weigh["weight_score"] if weigh else None,
                        "fish_count": weigh["fish_count"] if weigh else None,
                    })
                return self._send_json(result)

            if path == "/api/results":
                pairs = conn.execute(
                    "SELECT * FROM v_pair_results ORDER BY "
                    "COALESCE(team_id, 999999999), pair_index_in_team, pair_place"
                ).fetchall()
                teams = {t["team_id"]: dict(t) for t in conn.execute("SELECT * FROM v_team_results").fetchall()}
                # «Разряд до» / «Разряд после»: у лодок разряд считается на
                # каждого спортсмена пары отдельно, поэтому ключ — пара + ФИО
                rank_after = _evsk_rank_after(self._lodki_build_evsk(conn),
                                              lambda r: (r.get("entry_id"), r.get("full_name")))
                result = []
                for p in pairs:
                    pd = dict(p)
                    members = conn.execute(
                        "SELECT a.full_name, a.rank_category, a.birth_date "
                        "FROM entry_members em JOIN athletes a ON a.id = em.athlete_id "
                        "WHERE em.entry_id = ? ORDER BY em.slot_order",
                        (pd["entry_id"],),
                    ).fetchall()
                    pd["members"] = []
                    for m in members:
                        md = dict(m)
                        md["rank_before"] = md.get("rank_category") or ""
                        md["rank_after"] = rank_after.get((pd["entry_id"], md["full_name"]),
                                                          md.get("rank_category") or "")
                        pd["members"].append(md)
                    pd["team"] = teams.get(pd["team_id"])
                    result.append(pd)
                return self._send_json(result)

            if path == "/api/evsk":
                return self._send_json(self._lodki_build_evsk(conn))

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def _lodki_build_evsk(self, conn):
        """Расчёт разрядов/ЕВСК для «Лодок» (см. evsk.py). Место = место пары
        (v_pair_results); для команды — доп. условие по месту КАЖДОЙ пары
        команды (худшее из них) относительно личного/парного распределения.
        Разряд рассчитывается на КАЖДОГО спортсмена пары отдельно (у них
        может быть разный уже имеющийся разряд -> разный итоговый)."""
        t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
        tier = (t["evsk_tier"] or "") if t else ""
        gender = _evsk_gender_from_group(t["gender_group"] if t else "")
        year = _evsk_tournament_year(t["date_tour1"] if t else "")
        pairs = conn.execute("SELECT * FROM v_pair_results").fetchall()
        field_size = len(pairs)

        individuals = []
        for p in pairs:
            has_catch = (p["weight_score"] or 0) > 0
            members = conn.execute(
                "SELECT a.full_name, a.rank_category, a.birth_date "
                "FROM entry_members em JOIN athletes a ON a.id = em.athlete_id "
                "WHERE em.entry_id = ? ORDER BY em.slot_order",
                (p["entry_id"],),
            ).fetchall()
            for m in members:
                age = evsk.age_from_birth_date(m["birth_date"], year)
                r = evsk.compute_entry_rank(
                    EVSK_STANDARDS, discipline_key="lodki", is_team=False, gender=gender,
                    tier_text=tier, competitor_count=field_size, place=p["pair_place"],
                    has_catch=has_catch, current_rank_label=m["rank_category"], age=age,
                )
                individuals.append({
                    "entry_id": p["entry_id"], "pair_name": p["entry_name"],
                    "full_name": m["full_name"], "current_rank": m["rank_category"],
                    "place": p["pair_place"], **r,
                })

        teams = []
        team_rows = {tm["id"]: dict(tm) for tm in conn.execute("SELECT * FROM teams").fetchall()}
        team_final = {r["team_id"]: dict(r) for r in conn.execute("SELECT * FROM v_team_results").fetchall()}
        for tid, tm in team_rows.items():
            tf = team_final.get(tid)
            if not tf:
                continue
            team_pairs = [p for p in pairs if p["team_id"] == tid]
            if not team_pairs:
                continue
            worst_pair_place = max(p["pair_place"] for p in team_pairs)
            has_catch = (tf.get("sum_weight") or 0) > 0
            for p in team_pairs:
                members = conn.execute(
                    "SELECT a.full_name, a.rank_category, a.birth_date "
                    "FROM entry_members em JOIN athletes a ON a.id = em.athlete_id "
                    "WHERE em.entry_id = ? ORDER BY em.slot_order",
                    (p["entry_id"],),
                ).fetchall()
                for m in members:
                    age = evsk.age_from_birth_date(m["birth_date"], year)
                    r = evsk.compute_entry_rank(
                        EVSK_STANDARDS, discipline_key="lodki", is_team=True, gender=gender,
                        tier_text=tier, competitor_count=len(team_rows), place=tf["final_place"],
                        has_catch=has_catch, current_rank_label=m["rank_category"], age=age,
                        individual_place=worst_pair_place, individual_field_size=field_size,
                    )
                    teams.append({
                        "entry_id": p["entry_id"], "team_id": tid, "team_name": tm["name"],
                        "pair_name": p["entry_name"], "full_name": m["full_name"],
                        "current_rank": m["rank_category"], "place": tf["final_place"], **r,
                    })
        return {"individuals": individuals, "teams": teams, "tier": tier, "field_size": field_size}

    # ---------------- API: POST ----------------
    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            data = self._read_json_body()
        except Exception as e:
            return self._send_json({"error": f"Некорректный JSON от формы: {e}"}, status=400)
        try:
            with WRITE_LOCK:
                self._handle_api_post(parsed.path, data)
        except ApiError as e:
            print(f"[API POST {parsed.path}] {e}")
            self._send_json({"error": str(e)}, status=e.status)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": f"Внутренняя ошибка сервера: {e}"}, status=500)

    def _handle_api_post(self, path, data):
        # ---------- Лаунчер ----------
        if path == "/api/launcher/tournaments":
            name_full = (data.get("name_full") or "").strip()
            if not name_full:
                raise ApiError("не указано название турнира")
            discipline_code = data.get("discipline_code")
            if discipline_code not in DISCIPLINES:
                raise ApiError("неизвестная дисциплина: " + str(discipline_code))
            venue = data.get("venue", "")
            date_tour1 = data.get("date_tour1", "")
            # v8: весовой режим имеет смысл только для «спиннинга с берега»
            weight_mode = 1 if (discipline_code == "forel" and data.get("weight_mode")) else 0

            conn = registry_db()
            cur = conn.execute(
                "INSERT INTO tournaments (slug, name_full, discipline_code, venue, date_tour1, weight_mode) "
                "VALUES (?,?,?,?,?,?)",
                ("__pending__", name_full, discipline_code, venue, date_tour1, weight_mode),
            )
            tid = cur.lastrowid
            slug = tournament_slug(tid)
            conn.execute("UPDATE tournaments SET slug=? WHERE id=?", (slug, tid))
            conn.commit()
            conn.close()

            init_tournament_db(tournament_db_path(slug), discipline_code, name_full, venue, date_tour1,
                               weight_mode=weight_mode)
            return self._send_json({"ok": True, "id": tid, "slug": slug, "weight_mode": weight_mode})

        if path == "/api/launcher/weight_mode":
            # v8: переключение «подсчёт по длине» <-> «подсчёт по весу (как в
            # донке)» для открытого турнира «спиннинга с берега». Движки
            # хранят данные по-разному, поэтому переключение возможно только
            # пока в турнире ещё нет участников — иначе результаты пришлось
            # бы выдумывать. Программа честно об этом говорит.
            require_current()
            if CURRENT["discipline_code"] != "forel":
                raise ApiError("подсчёт по весу переключается только у дисциплины «Ловля спиннингом с берега»")
            want = 1 if data.get("enabled") else 0
            if want == (CURRENT.get("weight_mode") or 0):
                return self._send_json({"ok": True, "weight_mode": want, "changed": False})
            db_path = CURRENT["db_path"]
            if os.path.exists(db_path):
                c = sqlite3.connect(db_path, timeout=10)
                try:
                    n = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                finally:
                    c.close()
                if n:
                    raise ApiError(
                        "нельзя переключить способ подсчёта: в турнире уже зарегистрированы участники (%d). "
                        "Удалите их или создайте отдельный турнир с нужным способом подсчёта." % n)
                os.remove(db_path)
            conn = registry_db()
            conn.execute("UPDATE tournaments SET weight_mode=? WHERE id=?", (want, CURRENT["id"]))
            row = conn.execute("SELECT * FROM tournaments WHERE id=?", (CURRENT["id"],)).fetchone()
            conn.commit()
            conn.close()
            init_tournament_db(db_path, "forel", row["name_full"], row["venue"], row["date_tour1"],
                               weight_mode=want)
            CURRENT["weight_mode"] = want
            return self._send_json({"ok": True, "weight_mode": want, "changed": True,
                                    "page": "zone_weight.html" if want else "forel.html"})

        if path == "/api/launcher/open":
            tid = data.get("id")
            conn = registry_db()
            row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
            conn.close()
            if row is None:
                raise ApiError("турнир не найден", status=404)
            CURRENT["id"] = row["id"]
            CURRENT["slug"] = row["slug"]
            CURRENT["discipline_code"] = row["discipline_code"]
            CURRENT["name_full"] = row["name_full"]
            CURRENT["db_path"] = tournament_db_path(row["slug"])
            CURRENT["weight_mode"] = row["weight_mode"] if "weight_mode" in row.keys() else 0
            disc = DISCIPLINES.get(row["discipline_code"], {})
            # v8: турнир «спиннинга с берега», заведённый прежней версией
            # (туры + зона на тур), приводится к новой структуре при открытии.
            if row["discipline_code"] == "forel" and not CURRENT["weight_mode"]:
                _migrate_forel_to_periods(CURRENT["db_path"])
            return self._send_json({**CURRENT, "implemented": disc.get("implemented", False),
                                     "discipline_name": disc.get("name", row["discipline_code"]),
                                     "page": _discipline_page(row["discipline_code"], CURRENT["weight_mode"])})

        # ---------- Модуль «Форель» ----------
        if path.startswith("/api/forel/"):
            return self._handle_forel_post(path, data)

        # ---------- Модуль «зонный весовой» (донка/поплавок/блесна/мормышка) ----------
        if path.startswith("/api/zw/"):
            return self._handle_zw_post(path, data)

        # ---------- Модуль «Лодки» ----------
        conn = get_db()
        try:
            if path == "/api/tournament":
                conn.execute(
                    "UPDATE tournament SET name_full=?, status=?, venue=?, date_tour1=?, "
                    "discipline=?, gender_group=?, evsk_tier=?, auto_draw_enabled=? WHERE id=1",
                    (
                        data.get("name_full", ""), data.get("status", ""),
                        data.get("venue", ""), data.get("date_tour1", ""),
                        data.get("discipline", ""), data.get("gender_group", ""),
                        data.get("evsk_tier", ""), 1 if data.get("auto_draw_enabled") else 0,
                    ),
                )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/judges":
                conn.execute("DELETE FROM judges_signoff")
                for i, j in enumerate(data.get("judges", [])):
                    conn.execute(
                        "INSERT INTO judges_signoff (role, category, full_name, sort_order) VALUES (?,?,?,?)",
                        (j.get("role", ""), j.get("category", ""), j.get("full_name", ""), i),
                    )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/teams":
                name = (data.get("name") or "").strip()
                if not name:
                    raise ApiError("не указано название команды")
                max_reg = conn.execute("SELECT COALESCE(MAX(reg_number), 0) FROM teams").fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO teams (reg_number, name, region) VALUES (?, ?, ?)",
                    (max_reg + 1, name, data.get("region", "")),
                )
                conn.commit()
                return self._send_json({"ok": True, "id": cur.lastrowid})

            if path == "/api/entries":
                name = (data.get("name") or "").strip()
                if not name:
                    raise ApiError("не указано название пары")
                team_id = data.get("team_id")
                pair_index = None
                if team_id:
                    max_idx = conn.execute(
                        "SELECT COALESCE(MAX(pair_index_in_team), 0) FROM entries WHERE team_id = ?",
                        (team_id,),
                    ).fetchone()[0]
                    pair_index = max_idx + 1

                cur = conn.execute(
                    "INSERT INTO entries (reg_number, name, team_id, pair_index_in_team, start_order) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (data.get("reg_number"), name, team_id, pair_index, data.get("start_order")),
                )
                entry_id = cur.lastrowid
                for i, m in enumerate(data.get("members", []), start=1):
                    full_name = (m.get("full_name") or "").strip()
                    if not full_name:
                        continue
                    a_cur = conn.execute(
                        "INSERT INTO athletes (full_name, rank_category, birth_date) VALUES (?, ?, ?)",
                        (full_name, m.get("rank_category", ""), m.get("birth_date", "")),
                    )
                    conn.execute(
                        "INSERT INTO entry_members (entry_id, athlete_id, slot_order) VALUES (?, ?, ?)",
                        (entry_id, a_cur.lastrowid, i),
                    )
                conn.commit()
                return self._send_json({"ok": True, "id": entry_id})

            if path.startswith("/api/entries/") and path.endswith("/weigh"):
                entry_id = int(path.split("/")[3])
                conn.execute(
                    "INSERT INTO pair_weigh_ins (entry_id, weight_score, fish_count) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(entry_id) DO UPDATE SET weight_score=excluded.weight_score, "
                    "fish_count=excluded.fish_count",
                    (entry_id, data.get("weight_score", 0), data.get("fish_count")),
                )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/draw":
                ids = [r["id"] for r in conn.execute("SELECT id FROM entries").fetchall()]
                random.shuffle(ids)
                for i, eid in enumerate(ids, start=1):
                    conn.execute("UPDATE entries SET start_order=? WHERE id=?", (i, eid))
                conn.commit()
                return self._send_json({"ok": True, "count": len(ids)})

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            with WRITE_LOCK:
                self._handle_api_delete(parsed.path)
        except ApiError as e:
            self._send_json({"error": str(e)}, status=e.status)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": f"Внутренняя ошибка сервера: {e}"}, status=500)

    def _handle_api_delete(self, path):
        if path.startswith("/api/launcher/tournaments/"):
            tid = int(path.split("/")[4])
            conn = registry_db()
            row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
            if row is None:
                conn.close()
                raise ApiError("турнир не найден", status=404)
            conn.execute("DELETE FROM tournaments WHERE id = ?", (tid,))
            conn.commit()
            conn.close()
            folder = os.path.join(TOURNAMENTS_DIR, row["slug"])
            shutil.rmtree(folder, ignore_errors=True)
            if CURRENT["id"] == tid:
                CURRENT.update({"id": None, "slug": None, "discipline_code": None, "name_full": None, "db_path": None})
            return self._send_json({"ok": True})

        # ---------- Модуль «Форель» ----------
        if path.startswith("/api/forel/"):
            return self._handle_forel_delete(path)

        # ---------- Модуль «зонный весовой» (донка/поплавок/блесна/мормышка) ----------
        if path.startswith("/api/zw/"):
            return self._handle_zw_delete(path)

        conn = get_db()
        try:
            if path.startswith("/api/entries/"):
                entry_id = int(path.split("/")[3])
                conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
                conn.commit()
                return self._send_json({"ok": True})
            if path.startswith("/api/teams/"):
                team_id = int(path.split("/")[3])
                conn.execute("UPDATE entries SET team_id = NULL, pair_index_in_team = NULL WHERE team_id = ?", (team_id,))
                conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
                conn.commit()
                return self._send_json({"ok": True})
            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    # ================= Модуль «Форель» =================

    def _forel_entry_row(self, conn, e):
        return {
            "id": e["id"], "reg_number": e["reg_number"],
            "full_name": e["full_name"], "rank_category": e["rank_category"], "birth_date": e["birth_date"],
            "team_id": e["team_id"], "team_name": e["team_name"], "start_order": e["start_order"],
        }

    def _forel_entries_query(self, conn):
        return conn.execute(
            "SELECT e.id, e.reg_number, e.start_order, e.team_id, t.name AS team_name, "
            "a.full_name, a.rank_category, a.birth_date "
            "FROM entries e JOIN athletes a ON a.id = e.athlete_id "
            "LEFT JOIN teams t ON t.id = e.team_id "
            "ORDER BY e.reg_number, e.id"
        ).fetchall()

    def _handle_forel_get(self, path):
        conn = get_forel_db()
        try:
            if path == "/api/forel/tournament":
                row = conn.execute("SELECT * FROM tournament WHERE id = 1").fetchone()
                return self._send_json(dict(row) if row else {})

            if path == "/api/forel/judges":
                rows = conn.execute("SELECT * FROM judges_signoff ORDER BY sort_order, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/forel/teams":
                rows = conn.execute("SELECT * FROM teams ORDER BY reg_number, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/forel/athletes":
                rows = conn.execute(
                    "SELECT DISTINCT full_name, rank_category, birth_date FROM athletes ORDER BY full_name"
                ).fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/forel/entries":
                entries = self._forel_entries_query(conn)
                zrows = conn.execute("SELECT * FROM entry_zones").fetchall()
                zones = {(z["entry_id"], z["period_number"]): z["zone"] for z in zrows}
                halves = {(z["entry_id"], z["period_number"]): z["half_zone"] for z in zrows}
                sanc = {s["entry_id"]: dict(s) for s in conn.execute("SELECT * FROM sanctions").fetchall()}
                result = []
                for e in entries:
                    d = self._forel_entry_row(conn, e)
                    # зоны по периодам (v8): {"1": "А", "2": "Б", ...}
                    d["zones"] = {str(p): zones.get((e["id"], p)) for p in range(1, 5)}
                    d["half_zones"] = {str(p): halves.get((e["id"], p)) for p in range(1, 5)}
                    d["sanction"] = sanc.get(e["id"])
                    result.append(d)
                return self._send_json(result)

            if path.startswith("/api/forel/card"):
                q = parse_qs(urlparse(self.path).query)
                period_number = int(q.get("period_number", ["1"])[0])
                entries = self._forel_entries_query(conn)
                zones = {z["entry_id"]: z["zone"] for z in
                          conn.execute("SELECT * FROM entry_zones WHERE period_number = ?", (period_number,)).fetchall()}
                sanc = {s["entry_id"]: s["from_period"] for s in
                        conn.execute("SELECT * FROM sanctions").fetchall()}
                catches = {}
                for c in conn.execute(
                    "SELECT * FROM catches WHERE period_number = ? ORDER BY id", (period_number,)
                ).fetchall():
                    catches.setdefault(c["entry_id"], []).append(c["length_cm"])
                result = []
                for e in entries:
                    d = self._forel_entry_row(conn, e)
                    d["zone"] = zones.get(e["id"])
                    from_period = sanc.get(e["id"])
                    d["disqualified"] = bool(from_period and from_period <= period_number)
                    d["disqualified_from_period"] = from_period
                    d["lengths"] = catches.get(e["id"], [])
                    result.append(d)
                return self._send_json(result)

            if path == "/api/forel/evsk":
                return self._send_json(self._forel_build_evsk(conn))

            if path == "/api/forel/results":
                return self._send_json(self._forel_build_results(conn))

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def _forel_build_results(self, conn):
        """Протокол (v8): туров нет — только периоды. У каждого периода своя
        зона, своё количество рыб, своя сумма длины и своё место в зоне.
        Дополнительно возвращается командный зачёт (листы «Команда» и
        «Командно-Личный» реального протокола)."""
        tournament = conn.execute("SELECT * FROM tournament WHERE id = 1").fetchone()
        periods_count = tournament["periods_count"] if tournament else 3
        entries = self._forel_entries_query(conn)

        periods_by_entry = {}
        for p in conn.execute("SELECT * FROM v_period_places").fetchall():
            periods_by_entry.setdefault(p["entry_id"], []).append(dict(p))

        final_rows = {f["entry_id"]: dict(f) for f in conn.execute("SELECT * FROM v_final_place").fetchall()}
        # «Разряд до» / «Разряд после» — как в реальном протоколе
        rank_after = _evsk_rank_after(self._forel_build_evsk(conn), lambda r: r.get("entry_id"))

        individuals = []
        for e in entries:
            d = self._forel_entry_row(conn, e)
            d["rank_before"] = e["rank_category"] or ""
            d["rank_after"] = rank_after.get(e["id"], e["rank_category"] or "")
            periods = sorted(periods_by_entry.get(e["id"], []), key=lambda p: p["period_number"])
            d["periods"] = [{
                "period_number": p["period_number"], "zone": p["zone"], "half_zone": p["half_zone"],
                "fish_count": p["fish_count"], "total_length": p["total_length"],
                "disqualified": bool(p["disqualified"]), "period_place": p["period_place"],
            } for p in periods]
            final = final_rows.get(e["id"])
            d["sum_period_places"] = final["sum_period_places"] if final else None
            d["sum_length"] = final["sum_length"] if final else None
            d["sum_fish"] = final["sum_fish"] if final else None
            d["final_place"] = final["final_place"] if final else None
            individuals.append(d)

        individuals.sort(key=lambda d: (d["final_place"] is None,
                                        d["final_place"] if d["final_place"] is not None else 0))

        teams_meta = {t["id"]: dict(t) for t in conn.execute("SELECT * FROM teams").fetchall()}
        members = {}
        for d in individuals:
            if d.get("team_id"):
                members.setdefault(d["team_id"], []).append(d)
        teams = []
        for tr in conn.execute("SELECT * FROM v_team_final ORDER BY team_place").fetchall():
            meta = teams_meta.get(tr["team_id"], {})
            teams.append({
                "team_id": tr["team_id"], "name": meta.get("name", ""), "region": meta.get("region", ""),
                "members_count": tr["members_count"], "team_sum_places": tr["team_sum_places"],
                "team_sum_length": tr["team_sum_length"], "team_sum_fish": tr["team_sum_fish"],
                "team_place": tr["team_place"],
                "members": [{"entry_id": m["id"], "full_name": m["full_name"],
                             "sum_period_places": m["sum_period_places"],
                             "final_place": m["final_place"]} for m in members.get(tr["team_id"], [])],
            })

        return {"individuals": individuals, "teams": teams, "periods_count": periods_count}

    def _forel_build_evsk(self, conn):
        t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
        tier = (t["evsk_tier"] or "") if t else ""
        gender = _evsk_gender_from_group(t["gender_group"] if t else "")
        year = _evsk_tournament_year(t["date_tour1"] if t else "")
        entries = self._forel_entries_query(conn)
        field_size = len(entries)
        field_quality_ok = evsk.field_quality_ok_for_i_razryad([e["rank_category"] for e in entries])
        final_rows = {f["entry_id"]: dict(f) for f in conn.execute("SELECT * FROM v_final_place").fetchall()}

        individuals = []
        for e in entries:
            final = final_rows.get(e["id"])
            if not final:
                continue
            has_catch = (final.get("sum_length") or 0) > 0
            age = evsk.age_from_birth_date(e["birth_date"], year)
            r = evsk.compute_entry_rank(
                EVSK_STANDARDS, discipline_key="forel", is_team=False, gender=gender,
                tier_text=tier, competitor_count=field_size, place=final["final_place"],
                has_catch=has_catch, current_rank_label=e["rank_category"], age=age,
                field_quality_ok_for_i=field_quality_ok,
            )
            individuals.append({
                "entry_id": e["id"], "full_name": e["full_name"], "current_rank": e["rank_category"],
                "place": final["final_place"], **r,
            })

        # Командные нормативы (v8): у дисциплины ЕСТЬ командный зачёт — это
        # видно в реальном протоколе (листы «Команда» и «Командно-Личный»),
        # поэтому заглушка v7 («командных разрядов нет») снята.
        teams = []
        team_rows = {tm["id"]: dict(tm) for tm in conn.execute("SELECT * FROM teams").fetchall()}
        team_final = {f["team_id"]: dict(f) for f in conn.execute("SELECT * FROM v_team_final").fetchall()}
        by_team = {}
        for e in entries:
            if e["team_id"]:
                by_team.setdefault(e["team_id"], []).append(e)
        for tid, members_e in by_team.items():
            tm, tf = team_rows.get(tid), team_final.get(tid)
            if not tm or not tf:
                continue
            has_catch = (tf.get("team_sum_length") or 0) > 0
            for e in members_e:
                own = final_rows.get(e["id"])
                age = evsk.age_from_birth_date(e["birth_date"], year)
                r = evsk.compute_entry_rank(
                    EVSK_STANDARDS, discipline_key="forel", is_team=True, gender=gender,
                    tier_text=tier, competitor_count=len(team_rows), place=tf["team_place"],
                    has_catch=has_catch, current_rank_label=e["rank_category"], age=age,
                    individual_place=(own["final_place"] if own else None),
                    individual_field_size=field_size,
                    field_quality_ok_for_i=field_quality_ok,
                )
                teams.append({
                    "entry_id": e["id"], "team_id": tid, "team_name": tm["name"],
                    "full_name": e["full_name"], "current_rank": e["rank_category"],
                    "place": tf["team_place"], **r,
                })
        return {"individuals": individuals, "teams": teams, "tier": tier, "field_size": field_size}

    def _handle_forel_post(self, path, data):
        conn = get_forel_db()
        try:
            if path == "/api/forel/tournament":
                conn.execute(
                    "UPDATE tournament SET name_full=?, status=?, venue=?, date_tour1=?, "
                    "gender_group=?, periods_count=?, zones=?, evsk_tier=?, "
                    "half_zones_enabled=?, auto_draw_enabled=? WHERE id=1",
                    (
                        data.get("name_full", ""), data.get("status", ""), data.get("venue", ""),
                        data.get("date_tour1", ""), data.get("gender_group", ""),
                        int(data.get("periods_count", 3)),
                        data.get("zones", "А,Б,В"), data.get("evsk_tier", ""),
                        1 if data.get("half_zones_enabled") else 0,
                        1 if data.get("auto_draw_enabled") else 0,
                    ),
                )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/forel/judges":
                conn.execute("DELETE FROM judges_signoff")
                for i, j in enumerate(data.get("judges", [])):
                    conn.execute(
                        "INSERT INTO judges_signoff (role, category, full_name, sort_order) VALUES (?,?,?,?)",
                        (j.get("role", ""), j.get("category", ""), j.get("full_name", ""), i),
                    )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/forel/teams":
                name = (data.get("name") or "").strip()
                if not name:
                    raise ApiError("не указано название команды")
                max_reg = conn.execute("SELECT COALESCE(MAX(reg_number), 0) FROM teams").fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO teams (reg_number, name, region) VALUES (?, ?, ?)",
                    (max_reg + 1, name, data.get("region", "")),
                )
                conn.commit()
                return self._send_json({"ok": True, "id": cur.lastrowid})

            if path == "/api/forel/entries":
                full_name = (data.get("full_name") or "").strip()
                if not full_name:
                    raise ApiError("не указано ФИО спортсмена")
                a_cur = conn.execute(
                    "INSERT INTO athletes (full_name, rank_category, birth_date) VALUES (?, ?, ?)",
                    (full_name, data.get("rank_category", ""), data.get("birth_date", "")),
                )
                athlete_id = a_cur.lastrowid
                team_id = data.get("team_id")
                cur = conn.execute(
                    "INSERT INTO entries (reg_number, athlete_id, team_id, start_order) VALUES (?, ?, ?, ?)",
                    (data.get("reg_number"), athlete_id, team_id, data.get("start_order")),
                )
                entry_id = cur.lastrowid
                # v8: при РЕГИСТРАЦИИ зона не вводится вообще — жеребьёвки ещё
                # не было (регистрация идёт до начала соревнования). Зоны
                # назначаются позже, на вкладке «Жеребьёвка».
                conn.commit()
                return self._send_json({"ok": True, "id": entry_id})

            if path.startswith("/api/forel/entries/") and path.endswith("/zone"):
                entry_id = int(path.split("/")[4])
                period_number = int(data.get("period_number", 1))
                zone = (data.get("zone") or "").strip()
                half_zone = (data.get("half_zone") or "").strip() or None
                if not zone:
                    conn.execute("DELETE FROM entry_zones WHERE entry_id=? AND period_number=?",
                                 (entry_id, period_number))
                else:
                    conn.execute(
                        "INSERT INTO entry_zones (entry_id, period_number, zone, half_zone) VALUES (?,?,?,?) "
                        "ON CONFLICT(entry_id, period_number) DO UPDATE SET zone=excluded.zone, "
                        "half_zone=excluded.half_zone",
                        (entry_id, period_number, zone, half_zone),
                    )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/forel/card":
                entry_id = int(data["entry_id"])
                period_number = int(data["period_number"])
                lengths = data.get("lengths", [])
                conn.execute("DELETE FROM catches WHERE entry_id=? AND period_number=?",
                             (entry_id, period_number))
                for L in lengths:
                    L = float(L)
                    if L > 0:
                        conn.execute(
                            "INSERT INTO catches (entry_id, period_number, length_cm) VALUES (?,?,?)",
                            (entry_id, period_number, L),
                        )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/forel/draw":
                count = _run_forel_draw(conn)
                return self._send_json({"ok": True, "count": count})

            if path == "/api/forel/sanctions":
                entry_id = int(data["entry_id"])
                from_period = int(data["from_period"])
                conn.execute(
                    "INSERT INTO sanctions (entry_id, from_period, reason) VALUES (?,?,?) "
                    "ON CONFLICT(entry_id) DO UPDATE SET from_period=excluded.from_period, "
                    "reason=excluded.reason",
                    (entry_id, from_period, data.get("reason", "")),
                )
                conn.commit()
                return self._send_json({"ok": True})

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def _handle_forel_delete(self, path):
        conn = get_forel_db()
        try:
            if path.startswith("/api/forel/entries/") and "/sanction" in path:
                entry_id = int(path.split("/")[4])
                conn.execute("DELETE FROM sanctions WHERE entry_id=?", (entry_id,))
                conn.commit()
                return self._send_json({"ok": True})
            if path.startswith("/api/forel/entries/"):
                entry_id = int(path.split("/")[4])
                conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
                conn.commit()
                return self._send_json({"ok": True})
            if path.startswith("/api/forel/teams/"):
                team_id = int(path.split("/")[4])
                conn.execute("UPDATE entries SET team_id = NULL WHERE team_id = ?", (team_id,))
                conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
                conn.commit()
                return self._send_json({"ok": True})
            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    # ========= Модуль «зонный весовой» (донка/поплавок/блесна/мормышка) =========

    def _zw_entry_row(self, e):
        return {
            "id": e["id"], "reg_number": e["reg_number"],
            "full_name": e["full_name"], "rank_category": e["rank_category"], "birth_date": e["birth_date"],
            "team_id": e["team_id"], "team_name": e["team_name"], "start_order": e["start_order"],
        }

    def _zw_entries_query(self, conn):
        return conn.execute(
            "SELECT e.id, e.reg_number, e.start_order, e.team_id, t.name AS team_name, "
            "a.full_name, a.rank_category, a.birth_date "
            "FROM entries e JOIN athletes a ON a.id = e.athlete_id "
            "LEFT JOIN teams t ON t.id = e.team_id "
            "ORDER BY e.reg_number, e.id"
        ).fetchall()

    def _handle_zw_get(self, path):
        conn = get_zw_db()
        try:
            if path == "/api/zw/tournament":
                row = conn.execute("SELECT * FROM tournament WHERE id = 1").fetchone()
                return self._send_json(dict(row) if row else {})

            if path == "/api/zw/judges":
                rows = conn.execute("SELECT * FROM judges_signoff ORDER BY sort_order, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/zw/teams":
                rows = conn.execute("SELECT * FROM teams ORDER BY reg_number, id").fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/zw/athletes":
                rows = conn.execute(
                    "SELECT DISTINCT full_name, rank_category, birth_date FROM athletes ORDER BY full_name"
                ).fetchall()
                return self._send_json([dict(r) for r in rows])

            if path == "/api/zw/entries":
                entries = self._zw_entries_query(conn)
                zones = {(z["entry_id"], z["tour_number"]): z["zone"]
                         for z in conn.execute("SELECT * FROM entry_zones").fetchall()}
                halves = {(z["entry_id"], z["tour_number"]): z["half_zone"]
                          for z in conn.execute("SELECT * FROM entry_zones").fetchall()}
                sanc = {(s["entry_id"], s["tour_number"]): dict(s)
                        for s in conn.execute("SELECT * FROM sanctions").fetchall()}
                result = []
                for e in entries:
                    d = self._zw_entry_row(e)
                    d["zone_tour1"] = zones.get((e["id"], 1))
                    d["zone_tour2"] = zones.get((e["id"], 2))
                    d["half_zone_tour1"] = halves.get((e["id"], 1))
                    d["half_zone_tour2"] = halves.get((e["id"], 2))
                    d["sanction_tour1"] = sanc.get((e["id"], 1))
                    d["sanction_tour2"] = sanc.get((e["id"], 2))
                    result.append(d)
                return self._send_json(result)

            if path.startswith("/api/zw/weighin"):
                q = parse_qs(urlparse(self.path).query)
                tour_number = int(q.get("tour_number", ["1"])[0])
                entries = self._zw_entries_query(conn)
                zones = {z["entry_id"]: z["zone"] for z in
                          conn.execute("SELECT * FROM entry_zones WHERE tour_number = ?", (tour_number,)).fetchall()}
                weighs = {w["entry_id"]: dict(w) for w in
                          conn.execute("SELECT * FROM weigh_ins WHERE tour_number = ?", (tour_number,)).fetchall()}
                sanc = {s["entry_id"]: s["sanction_type"] for s in
                        conn.execute("SELECT * FROM sanctions WHERE tour_number = ?", (tour_number,)).fetchall()}
                result = []
                for e in entries:
                    d = self._zw_entry_row(e)
                    d["zone"] = zones.get(e["id"])
                    w = weighs.get(e["id"])
                    d["weight_score"] = w["weight_score"] if w else None
                    d["fish_count"] = w["fish_count"] if w else None
                    d["sanction_type"] = sanc.get(e["id"])
                    result.append(d)
                return self._send_json(result)

            if path == "/api/zw/results":
                return self._send_json(self._zw_build_results(conn))

            if path == "/api/zw/evsk":
                return self._send_json(self._zw_build_evsk(conn))

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def _zw_build_results(self, conn):
        tournament = conn.execute("SELECT * FROM tournament WHERE id = 1").fetchone()
        tours_count = tournament["tours_count"] if tournament else 2
        entries = self._zw_entries_query(conn)

        zone_rows = {(z["entry_id"], z["tour_number"]): dict(z)
                     for z in conn.execute("SELECT * FROM v_zone_places").fetchall()}
        final_rows = {f["entry_id"]: dict(f) for f in conn.execute("SELECT * FROM v_individual_final").fetchall()}
        # «Разряд до» / «Разряд после» — как в реальном протоколе
        rank_after = _evsk_rank_after(self._zw_build_evsk(conn), lambda r: r.get("entry_id"))

        individuals = []
        for e in entries:
            d = self._zw_entry_row(e)
            d["rank_before"] = e["rank_category"] or ""
            d["rank_after"] = rank_after.get(e["id"], e["rank_category"] or "")
            d["tours"] = {}
            for tn in range(1, tours_count + 1):
                z = zone_rows.get((e["id"], tn))
                d["tours"][str(tn)] = {
                    "zone": z["zone"] if z else None,
                    "displayed_weight": z["displayed_weight"] if z else None,
                    "fish_count": z["fish_count"] if z else None,
                    "is_disqualified": bool(z["is_disqualified"]) if z else False,
                    "zone_place": z["zone_place"] if z else None,
                }
            final = final_rows.get(e["id"])
            d["final_place"] = final["final_place"] if final else None
            d["sum_places"] = final["sum_places"] if final else None
            d["sum_weight"] = final["sum_weight"] if final else None
            individuals.append(d)
        individuals.sort(key=lambda d: (d["final_place"] is None, d["final_place"] if d["final_place"] is not None else 0))

        teams = [dict(t) for t in conn.execute("SELECT * FROM teams ORDER BY reg_number, id").fetchall()]
        team_tour_rows = {}
        for t in conn.execute("SELECT * FROM v_team_tour_score").fetchall():
            team_tour_rows.setdefault(t["team_id"], {})[t["tour_number"]] = dict(t)
        team_final_rows = {t["team_id"]: dict(t) for t in conn.execute("SELECT * FROM v_team_final").fetchall()}

        team_results = []
        for t in teams:
            d = dict(t)
            d["tours"] = {}
            for tn in range(1, tours_count + 1):
                tt = team_tour_rows.get(t["id"], {}).get(tn)
                d["tours"][str(tn)] = {
                    "team_sum_places": tt["team_sum_places"] if tt else None,
                    "team_sum_weight": tt["team_sum_weight"] if tt else None,
                }
            final = team_final_rows.get(t["id"])
            d["final_place"] = final["final_place"] if final else None
            team_results.append(d)
        team_results.sort(key=lambda d: (d["final_place"] is None, d["final_place"] if d["final_place"] is not None else 0))

        return {"individuals": individuals, "teams": team_results}

    def _zw_build_evsk(self, conn):
        t = conn.execute("SELECT * FROM tournament WHERE id=1").fetchone()
        tier = (t["evsk_tier"] or "") if t else ""
        discipline_code = CURRENT["discipline_code"]
        gender = _evsk_gender_from_group(t["gender_group"] if t else "")
        year = _evsk_tournament_year(t["date_tour1"] if t else "")
        entries = self._zw_entries_query(conn)
        field_size = len(entries)
        field_quality_ok = evsk.field_quality_ok_for_i_razryad([e["rank_category"] for e in entries])
        final_rows = {f["entry_id"]: dict(f) for f in conn.execute("SELECT * FROM v_individual_final").fetchall()}

        individuals = []
        for e in entries:
            final = final_rows.get(e["id"])
            if not final:
                continue
            has_catch = (final.get("sum_weight") or 0) > 0
            age = evsk.age_from_birth_date(e["birth_date"], year)
            r = evsk.compute_entry_rank(
                EVSK_STANDARDS, discipline_key=discipline_code, is_team=False, gender=gender,
                tier_text=tier, competitor_count=field_size, place=final["final_place"],
                has_catch=has_catch, current_rank_label=e["rank_category"], age=age,
                field_quality_ok_for_i=field_quality_ok,
            )
            individuals.append({
                "entry_id": e["id"], "full_name": e["full_name"], "current_rank": e["rank_category"],
                "place": final["final_place"], **r,
            })

        teams = []
        team_rows = {tm["id"]: dict(tm) for tm in conn.execute("SELECT * FROM teams").fetchall()}
        team_final = {f["team_id"]: dict(f) for f in conn.execute("SELECT * FROM v_team_final").fetchall()}
        by_team = {}
        for e in entries:
            if e["team_id"]:
                by_team.setdefault(e["team_id"], []).append(e)
        for tid, members_e in by_team.items():
            tm, tf = team_rows.get(tid), team_final.get(tid)
            if not tm or not tf:
                continue
            places = [final_rows[e["id"]]["final_place"] for e in members_e if e["id"] in final_rows]
            if not places:
                continue
            worst_place = max(places)
            has_catch = (tf.get("sum_weight") or 0) > 0
            for e in members_e:
                age = evsk.age_from_birth_date(e["birth_date"], year)
                r = evsk.compute_entry_rank(
                    EVSK_STANDARDS, discipline_key=discipline_code, is_team=True, gender=gender,
                    tier_text=tier, competitor_count=len(team_rows), place=tf["final_place"],
                    has_catch=has_catch, current_rank_label=e["rank_category"], age=age,
                    individual_place=worst_place, individual_field_size=field_size,
                    field_quality_ok_for_i=field_quality_ok,
                )
                teams.append({
                    "entry_id": e["id"], "team_id": tid, "team_name": tm["name"],
                    "full_name": e["full_name"], "current_rank": e["rank_category"],
                    "place": tf["final_place"], **r,
                })
        return {"individuals": individuals, "teams": teams, "tier": tier, "field_size": field_size}

    def _handle_zw_post(self, path, data):
        conn = get_zw_db()
        try:
            if path == "/api/zw/tournament":
                conn.execute(
                    "UPDATE tournament SET name_full=?, status=?, venue=?, date_tour1=?, date_tour2=?, "
                    "gender_group=?, tours_count=?, zones=?, evsk_tier=?, half_zones_enabled=?, "
                    "auto_draw_enabled=? WHERE id=1",
                    (
                        data.get("name_full", ""), data.get("status", ""), data.get("venue", ""),
                        data.get("date_tour1", ""), data.get("date_tour2", ""), data.get("gender_group", ""),
                        int(data.get("tours_count", 2)), data.get("zones", "А,Б,В"), data.get("evsk_tier", ""),
                        1 if data.get("half_zones_enabled") else 0,
                        1 if data.get("auto_draw_enabled") else 0,
                    ),
                )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/zw/judges":
                conn.execute("DELETE FROM judges_signoff")
                for i, j in enumerate(data.get("judges", [])):
                    conn.execute(
                        "INSERT INTO judges_signoff (role, category, full_name, sort_order) VALUES (?,?,?,?)",
                        (j.get("role", ""), j.get("category", ""), j.get("full_name", ""), i),
                    )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/zw/teams":
                name = (data.get("name") or "").strip()
                if not name:
                    raise ApiError("не указано название команды")
                max_reg = conn.execute("SELECT COALESCE(MAX(reg_number), 0) FROM teams").fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO teams (reg_number, name, region) VALUES (?, ?, ?)",
                    (max_reg + 1, name, data.get("region", "")),
                )
                conn.commit()
                return self._send_json({"ok": True, "id": cur.lastrowid})

            if path == "/api/zw/entries":
                full_name = (data.get("full_name") or "").strip()
                if not full_name:
                    raise ApiError("не указано ФИО спортсмена")
                a_cur = conn.execute(
                    "INSERT INTO athletes (full_name, rank_category, birth_date) VALUES (?, ?, ?)",
                    (full_name, data.get("rank_category", ""), data.get("birth_date", "")),
                )
                athlete_id = a_cur.lastrowid
                team_id = data.get("team_id")
                cur = conn.execute(
                    "INSERT INTO entries (reg_number, athlete_id, team_id, start_order) VALUES (?, ?, ?, ?)",
                    (data.get("reg_number"), athlete_id, team_id, data.get("start_order")),
                )
                entry_id = cur.lastrowid
                for tn, zkey, hkey in ((1, "zone_tour1", "half_zone_tour1"), (2, "zone_tour2", "half_zone_tour2")):
                    zone = (data.get(zkey) or "").strip()
                    if zone:
                        conn.execute(
                            "INSERT INTO entry_zones (entry_id, tour_number, zone, half_zone) VALUES (?, ?, ?, ?)",
                            (entry_id, tn, zone, (data.get(hkey) or "").strip() or None),
                        )
                conn.commit()
                return self._send_json({"ok": True, "id": entry_id})

            if path.startswith("/api/zw/entries/") and path.endswith("/zone"):
                entry_id = int(path.split("/")[4])
                tour_number = int(data.get("tour_number", 1))
                zone = (data.get("zone") or "").strip()
                half_zone = (data.get("half_zone") or "").strip() or None
                if not zone:
                    conn.execute("DELETE FROM entry_zones WHERE entry_id=? AND tour_number=?", (entry_id, tour_number))
                else:
                    conn.execute(
                        "INSERT INTO entry_zones (entry_id, tour_number, zone, half_zone) VALUES (?,?,?,?) "
                        "ON CONFLICT(entry_id, tour_number) DO UPDATE SET zone=excluded.zone, half_zone=excluded.half_zone",
                        (entry_id, tour_number, zone, half_zone),
                    )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/zw/weighin":
                entry_id = int(data["entry_id"])
                tour_number = int(data["tour_number"])
                weight_score = float(data.get("weight_score") or 0)
                fish_count = data.get("fish_count")
                conn.execute(
                    "INSERT INTO weigh_ins (entry_id, tour_number, weight_score, fish_count) VALUES (?,?,?,?) "
                    "ON CONFLICT(entry_id, tour_number) DO UPDATE SET weight_score=excluded.weight_score, "
                    "fish_count=excluded.fish_count",
                    (entry_id, tour_number, weight_score, fish_count),
                )
                conn.commit()
                return self._send_json({"ok": True})

            if path == "/api/zw/draw":
                count = _run_zone_draw(conn)
                return self._send_json({"ok": True, "count": count})

            if path == "/api/zw/sanctions":
                entry_id = int(data["entry_id"])
                tour_number = int(data["tour_number"])
                sanction_type = data.get("sanction_type")
                if sanction_type not in ("before_weigh", "after_weigh"):
                    raise ApiError("неизвестный тип санкции: " + str(sanction_type))
                conn.execute(
                    "INSERT INTO sanctions (entry_id, tour_number, sanction_type, reason) VALUES (?,?,?,?) "
                    "ON CONFLICT(entry_id, tour_number) DO UPDATE SET sanction_type=excluded.sanction_type, "
                    "reason=excluded.reason",
                    (entry_id, tour_number, sanction_type, data.get("reason", "")),
                )
                conn.commit()
                return self._send_json({"ok": True})

            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def _handle_zw_delete(self, path):
        conn = get_zw_db()
        try:
            if path.startswith("/api/zw/entries/") and "/sanction/" in path:
                parts = path.split("/")
                entry_id, tour_number = int(parts[4]), int(parts[6])
                conn.execute("DELETE FROM sanctions WHERE entry_id=? AND tour_number=?", (entry_id, tour_number))
                conn.commit()
                return self._send_json({"ok": True})
            if path.startswith("/api/zw/entries/"):
                entry_id = int(path.split("/")[4])
                conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
                conn.commit()
                return self._send_json({"ok": True})
            if path.startswith("/api/zw/teams/"):
                team_id = int(path.split("/")[4])
                conn.execute("UPDATE entries SET team_id = NULL WHERE team_id = ?", (team_id,))
                conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
                conn.commit()
                return self._send_json({"ok": True})
            raise ApiError("неизвестный адрес API: " + path, status=404)
        finally:
            conn.close()

    def log_message(self, format, *args):
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    init_registry()
    migrate_legacy_if_needed()
    print(f"Файл реестра турниров: {REGISTRY_DB_PATH}")
    print(f"Папка турниров: {TOURNAMENTS_DIR}")
    print(f"Папка интерфейса: {FRONTEND_DIR}")
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://127.0.0.1:{PORT}/"
        print(f"Сервер запущен: {url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        httpd.serve_forever()


if __name__ == "__main__":
    main()

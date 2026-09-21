"""
Рыбалка — расчёт спортивных разрядов/званий (ЕВСК) по официальным нормативам.

Источник: приложенный пользователем файл "Приложение № 61 к приказу
Минспорта России от «9» апреля 2026 г. № 299" (нормы по виду спорта
«рыболовный спорт»). Разобран (backend/evsk_standards.json) из 3 листов:
МСМК, МС-КМС, "массовые разряды" (I-III взрослые + I-III юношеские).
Лист "нормы" (очковая система) НЕ используется — он относится только к
дисциплине "кастинг", вне охвата этой программы (проверено: наши 6
дисциплин в этом листе не упоминаются вообще).

Модель данных (evsk_standards.json) — список записей вида:
  {rank, tier, discipline_key, is_team, gender, age_group, age_min, age_max,
   place_min, place_max, min_competitors}
где rank ∈ {МСМК, МС, КМС, I, II, III, I_ю, II_ю, III_ю}.

Правила (по тексту "Иные условия" всех трёх листов), реализованные ниже:
  1. Без улова — норматив не засчитывается НИ ПРИ КАКОМ месте (универсально).
  2. При недостатке участников (но не менее 6) по сравнению с минимумом
     заявленного статуса — норматив проверяется по требованиям предыдущего
     (более низкого) по статусу соревнования (переход вниз по TIER_ORDER).
  3. Разряды/звания выполняются последовательно: более высокое достижение
     автоматически засчитывает нижестоящее (реализовано как "первое
     совпадение сверху вниз по списку рангов").
  4. Наличие ПРЕДЫДУЩЕГО разряда — условие для присвоения следующего:
       МСМК требует уже имеющегося МС;
       МС требует уже имеющегося КМС (иначе, если у спортсмена есть хотя бы
         I разряд и норматив МС выполнен — засчитывается КМС, это отдельно
         прописанное исключение, п.2 листа МС-КМС);
       КМС требует уже имеющегося I разряда;
       I разряд требует, чтобы не менее половины участников программы имели
         разряд не ниже II (проверяется по всему полю турнира).
  5. Командные дисциплины ("...командные соревнования") — дополнительное
     условие: место спортсмена/пары в СООТВЕТСТВУЮЩЕЙ личной/парной
     дисциплине (в этом же турнире) не хуже места, занятого первой третью
     (для МСМК) или первой половиной (МС/КМС/I-III/юношеские) участников.
  6. Требование судейских категорий — только ИНФОРМАЦИОННО показывается в
     интерфейсе (программа не хранит квалификационные категории судей).
  7. "Не был заменён в ходе соревнования" — не проверяется (не отслеживается).

Возраст: если у спортсмена есть дата рождения, юношеские нормативы (и их
возрастная маска) проверяются по году турнира (date_tour1) минус год
рождения; если даты нет — юношеские нормативы просто не проверяются для
этого спортсмена (только взрослые I-III/КМС/МС/МСМК).
"""
import json
import math
import re

# Порядок статусов соревнований — от высшего к низшему. Используется для
# правила "недостаточно участников -> нормативы предыдущего по статусу".
TIER_ORDER = [
    "Чемпионат мира, Всемирные игры",
    "Чемпионат Европы",
    "Другие международные спортивные соревнования, включенные в ЕКП",
    "Чемпионат России",
    "Первенство России",
    "Кубок России (при двух и более этапах – сумма этапов)",
    "Другие всероссийские спортивные соревнования, включенные в ЕКП",
    "Чемпионат федерального округа, чемпионаты г. Москвы, г. Санкт-Петербурга",
    "Первенство федерального округа, первенства г. Москвы, г. Санкт-Петербурга",
    "Другие межрегиональные спортивные соревнования, включенные в ЕКП",
    "Чемпионат субъекта Российской Федерации, (кроме г. Москвы, г. Санкт-Петербурга)",
    "Первенство субъекта Российской Федерации (кроме г. Москвы и г. Санкт-Петербурга)",
    "Кубок субъекта Российской Федерации (при двух и более этапах – сумма этапов)",
    "Другие официальные спортивные соревнования субъекта Российской Федерации",
    "Чемпионат муниципального образования",
    "Первенство муниципального образования",
    "Другие официальные спортивные соревнования муниципального образования",
]

RANKS_DESC = ["МСМК", "МС", "КМС", "I", "II", "III", "I_ю", "II_ю", "III_ю"]

RANK_DISPLAY = {
    "МСМК": "МСМК", "МС": "МС", "КМС": "КМС",
    "I": "I разряд", "II": "II разряд", "III": "III разряд",
    "I_ю": "I юношеский разряд", "II_ю": "II юношеский разряд", "III_ю": "III юношеский разряд",
}
# наш код норматива -> уже принятая в программе метка athletes.rank_category
# (см. комментарий в schema_lodki_mvp.sql: 'б/р','3ю','2ю','1ю','3','2','1','КМС','МС')
RANK_TO_APP_LABEL = {
    "МСМК": "МСМК", "МС": "МС", "КМС": "КМС",
    "I": "1", "II": "2", "III": "3",
    "I_ю": "1ю", "II_ю": "2ю", "III_ю": "3ю",
}
# от низшего к высшему — для сравнения "уже имеющегося" разряда спортсмена
RANK_LADDER = ["", "б/р", "3ю", "2ю", "1ю", "3", "2", "1", "КМС", "МС", "МСМК"]


def _ladder_index(label):
    label = (label or "").strip()
    try:
        return RANK_LADDER.index(label)
    except ValueError:
        return 0


def ladder_index(label):
    """Публичный вариант _ladder_index: насколько высок разряд (больше —
    выше). Нужен серверу, чтобы выбрать лучший из личного и командного
    нормативов для столбца «разряд после»."""
    return _ladder_index(label)


def load_standards(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tier_index(tier_text):
    try:
        return TIER_ORDER.index(tier_text)
    except ValueError:
        return None


def _gender_matches(row_gender, athlete_gender):
    if row_gender == "МЖ" or not athlete_gender:
        return True
    return row_gender == athlete_gender


def age_from_birth_date(birth_date_iso, tournament_year):
    if not birth_date_iso or not tournament_year:
        return None
    m = re.match(r"^(\d{4})", str(birth_date_iso))
    if not m:
        return None
    return tournament_year - int(m.group(1))


def _age_matches(row, age):
    if not row.get("age_group"):
        return True
    if age is None:
        return False  # без даты рождения юношеские нормативы не проверяем
    lo, hi = row.get("age_min"), row.get("age_max")
    if lo is not None and age < lo:
        return False
    if hi is not None and age > hi:
        return False
    return True


def compute_entry_rank(standards, *, discipline_key, is_team, gender, tier_text,
                        competitor_count, place, has_catch, current_rank_label,
                        age=None, individual_place=None, individual_field_size=None,
                        field_quality_ok_for_i=True):
    """Возвращает dict {rank, rank_label, tier_used, note} для достигнутого
    норматива, либо {rank: None, note: "..."} если ничего не выполнено."""
    if not has_catch:
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "без улова — норматив не засчитывается"}
    ti = tier_index(tier_text)
    if ti is None:
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "статус соревнования не указан — расчёт невозможен"}

    def team_fraction_ok(rank_code):
        if not is_team or individual_place is None or individual_field_size is None:
            return True
        frac = 1 / 3 if rank_code == "МСМК" else 1 / 2
        threshold = math.ceil(individual_field_size * frac)
        return individual_place <= threshold

    def find_row(rank_code):
        rows = [
            s for s in standards
            if s["rank"] == rank_code and s["discipline_key"] == discipline_key
            and s["is_team"] == is_team and _gender_matches(s["gender"], gender)
            and _age_matches(s, age)
        ]
        candidates = [r for r in rows if tier_index(r["tier"]) is not None and tier_index(r["tier"]) >= ti]
        candidates.sort(key=lambda r: tier_index(r["tier"]))
        for r in candidates:
            mc = r["min_competitors"]
            if competitor_count < 6:
                continue
            if mc is None or competitor_count >= mc:
                if r["place_min"] <= place <= r["place_max"]:
                    return r
        return None

    cur_idx = _ladder_index(current_rank_label)

    for rank_code in RANKS_DESC:
        row = find_row(rank_code)
        if row is None:
            continue
        if not team_fraction_ok(rank_code):
            continue
        # -------- условия "наличие предыдущего разряда" --------
        if rank_code == "МСМК":
            if cur_idx < _ladder_index("МС"):
                continue  # нет МС — МСМК не присваивается (норматив есть, условие нет)
        elif rank_code == "МС":
            if cur_idx < _ladder_index("КМС"):
                if cur_idx >= _ladder_index("1"):
                    # исключение (лист МС-КМС, "Иные условия" п.2): есть I разряд
                    # и норматив МС выполнен -> засчитывается КМС.
                    return {
                        "rank": "КМС", "rank_label": RANK_DISPLAY["КМС"], "tier_used": row["tier"],
                        "note": "норматив МС выполнен, но нет предыдущего КМС — засчитан КМС "
                                "(есть I разряд), см. «Иные условия» п.2 листа МС-КМС",
                    }
                continue
        elif rank_code == "КМС":
            if cur_idx < _ladder_index("1"):
                continue
        elif rank_code == "I":
            if not field_quality_ok_for_i:
                continue
        # Разряд присваивается, только если он ВЫШЕ уже имеющегося у спортсмена.
        # Иначе новый разряд не присваивается, а ИМЕЮЩИЙСЯ ПОДТВЕРЖДАЕТСЯ: КМС,
        # выполнивший норматив I разряда, остаётся КМС (в v7 программа ошибочно
        # "понижала" такого спортсмена до I/II).
        if _ladder_index(RANK_TO_APP_LABEL[rank_code]) <= cur_idx:
            return {
                "rank": None, "rank_label": None, "app_label": None, "confirmed": True,
                "note": "норматив выполнен, разряд подтверждён",
            }
        return {"rank": rank_code, "rank_label": RANK_DISPLAY[rank_code],
                "app_label": RANK_TO_APP_LABEL[rank_code], "confirmed": False,
                "tier_used": row["tier"], "note": ""}

    return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
            "note": "результат не соответствует ни одному нормативу"}


def field_quality_ok_for_i_razryad(rank_labels):
    """Правило для I разряда: не менее половины участников программы имеют
    разряд не ниже II. rank_labels — список athletes.rank_category всего
    поля (может быть пустым/б-р у части — считаются как не соответствующие)."""
    if not rank_labels:
        return False
    total = len(rank_labels)
    good = sum(1 for r in rank_labels if _ladder_index(r) >= _ladder_index("2"))
    return good * 2 >= total

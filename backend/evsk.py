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

Правила (по тексту "Иные условия" всех трёх листов), реализованные ниже.
ВАЖНО: в v8 три из них уточнены по буквальному тексту документа после сверки
с реальным протоколом Чемпионата Вологодской области — в v7 они работали
приблизительно и давали разряды там, где их быть не должно.
  1. Без улова — норматив не засчитывается ни при каком месте (п.6).
  2. I разряд: требование выполняется при участии не менее половины
     спортсменов, имеющих разряд не ниже II, ОТ УКАЗАННОГО В НОРМАТИВЕ
     МИНИМАЛЬНОГО количества участников (п.1) — а НЕ от фактического числа
     заявленных, как было в v7.
  3. Если соперников МЕНЬШЕ указанного в нормативе минимума (но не менее
     шести) — требования выполняются по предыдущему по статусу соревнованию
     (п.4). В v7 этот спуск шёл безусловно, даже когда участников хватало.
  4. Разряды выполняются ПОСЛЕДОВАТЕЛЬНО от низшего к высшему: спортсмену,
     выполнившему норму более высокого разряда, чем очередной, засчитывается
     выполнение требований именно ОЧЕРЕДНОГО разряда по отношению к
     имеющемуся (п.5). Например, спортсмен без разряда, выполнивший норматив
     I разряда, получает III разряд. В v7 присваивался сразу высший
     выполненный норматив.
  5. Звания и КМС (лист МС-КМС): МСМК требует имеющегося МС; МС требует КМС
     (исключение п.2 того листа: при наличии I разряда и выполнении норматива
     МС засчитывается КМС); КМС требует I разряда.
  6. Командные дисциплины ("...командные соревнования") — дополнительное
     условие: личное место спортсмена в соответствующей некомандной
     дисциплине этого же турнира не хуже мест первой половины участников
     (для МСМК — первой трети), п.2.
  7. Требование судейских категорий (п.7) — только информационно в
     интерфейсе: программа не хранит категории судей.
  8. "Завершил соревнования без замены" (п.3) — не проверяется.

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
# Две отдельные "лестницы" для правила 5 «Иных условий» (разряды выполняются
# последовательно от низшего к высшему): взрослая и юношеская.
ADULT_LADDER = ["б/р", "3", "2", "1", "КМС", "МС", "МСМК"]
YOUTH_LADDER = ["б/р", "3ю", "2ю", "1ю"]


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


def _next_adult_rank(current_label):
    """Очередной ВЗРОСЛЫЙ разряд по отношению к имеющемуся (правило 5)."""
    cur = (current_label or "").strip() or "б/р"
    if cur in YOUTH_LADDER and cur != "б/р":
        return "3"          # после юношеских разрядов очередной взрослый — III
    if cur not in ADULT_LADDER:
        cur = "б/р"
    i = ADULT_LADDER.index(cur)
    return ADULT_LADDER[i + 1] if i + 1 < len(ADULT_LADDER) else None


def _next_youth_rank(current_label):
    """Очередной ЮНОШЕСКИЙ разряд. Если у спортсмена уже есть взрослый
    разряд, юношеский ему присваивать нечего."""
    cur = (current_label or "").strip() or "б/р"
    if cur in ADULT_LADDER and cur != "б/р":
        return None
    i = YOUTH_LADDER.index(cur) if cur in YOUTH_LADDER else 0
    return YOUTH_LADDER[i + 1] if i + 1 < len(YOUTH_LADDER) else None


def compute_entry_rank(standards, *, discipline_key, is_team, gender, tier_text,
                        competitor_count, place, has_catch, current_rank_label,
                        age=None, individual_place=None, individual_field_size=None,
                        field_ranked_count=None):
    """Возвращает dict {rank, rank_label, app_label, confirmed, tier_used, note}.

    field_ranked_count — сколько участников турнира имеют разряд не ниже II
    (нужно для правила 1 «Иных условий»: требование I разряда выполняется при
    участии не менее половины таких спортсменов ОТ УКАЗАННОГО МИНИМАЛЬНОГО
    количества участников, а не от фактического числа заявленных)."""
    if not has_catch:
        # правило 6: без улова норматив не выполнен ни при каком месте
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "без улова — норматив не засчитывается"}
    ti = tier_index(tier_text)
    if ti is None:
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "статус соревнования не указан — расчёт невозможен"}

    def rows_for(rank_code, tier_idx):
        return [
            s for s in standards
            if s["rank"] == rank_code and s["discipline_key"] == discipline_key
            and s["is_team"] == is_team and _gender_matches(s["gender"], gender)
            and _age_matches(s, age) and tier_index(s["tier"]) == tier_idx
        ]

    # ---- правило 4: если соперников МЕНЬШЕ указанного минимума (но не менее
    # шести) — требования выполняются по ПРЕДЫДУЩЕМУ по статусу соревнованию.
    # Спуск идёт по фактическому числу участников, а не просто "если норматива
    # нет на этом статусе" (в v7 спуск был безусловным — из-за этого разряды
    # присваивались там, где их быть не должно).
    def effective_tier():
        idx = ti
        while idx < len(TIER_ORDER):
            mins = [r["min_competitors"] for rc in RANKS_DESC for r in rows_for(rc, idx)
                    if r["min_competitors"] is not None]
            if not mins or competitor_count >= min(mins) or competitor_count < 6:
                return idx
            idx += 1
        return ti
    eff_ti = effective_tier()

    def team_fraction_ok(rank_code):
        # правило 2: для «командных соревнований» личное место должно быть не
        # хуже мест первой половины (для МСМК — первой трети) участников
        if not is_team or individual_place is None or individual_field_size is None:
            return True
        frac = 1 / 3 if rank_code == "МСМК" else 1 / 2
        return individual_place <= math.ceil(individual_field_size * frac)

    def field_quality_ok(row):
        # правило 1 (только для I разряда)
        if field_ranked_count is None:
            return True
        need_from = row["min_competitors"]
        if need_from is None:
            return True
        return field_ranked_count * 2 >= need_from

    def find_row(rank_code):
        for r in rows_for(rank_code, eff_ti):
            mc = r["min_competitors"]
            if competitor_count < 6:
                continue
            if mc is not None and competitor_count < mc:
                continue
            if r["place_min"] <= place <= r["place_max"]:
                return r
        return None

    cur_idx = _ladder_index(current_rank_label)

    # --- какой САМЫЙ ВЫСОКИЙ норматив выполнен ---
    met, met_row = None, None
    for rank_code in RANKS_DESC:
        row = find_row(rank_code)
        if row is None or not team_fraction_ok(rank_code):
            continue
        if rank_code == "I" and not field_quality_ok(row):
            continue
        met, met_row = rank_code, row
        break

    if met is None:
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "результат не соответствует ни одному нормативу"}

    def confirmed():
        return {"rank": None, "rank_label": None, "app_label": None, "confirmed": True,
                "note": "норматив выполнен, разряд подтверждён"}

    # --- звания и КМС: условия листа МС-КМС (наличие предыдущего разряда) ---
    if met in ("МСМК", "МС", "КМС"):
        if met == "МСМК" and cur_idx < _ladder_index("МС"):
            return confirmed() if cur_idx >= _ladder_index("МС") else {
                "rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                "note": "норматив МСМК выполнен, но не присваивается: нет звания МС"}
        if met == "МС" and cur_idx < _ladder_index("КМС"):
            if cur_idx >= _ladder_index("1"):
                # «Иные условия» п.2 листа МС-КМС: есть I разряд и выполнен
                # норматив МС -> засчитывается КМС
                return {"rank": "КМС", "rank_label": RANK_DISPLAY["КМС"],
                        "app_label": RANK_TO_APP_LABEL["КМС"], "confirmed": False,
                        "tier_used": met_row["tier"],
                        "note": "норматив МС выполнен, но нет КМС — засчитан КМС "
                                "(есть I разряд), «Иные условия» п.2 листа МС-КМС"}
            return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                    "note": "норматив МС выполнен, но не присваивается: нет КМС"}
        if met == "КМС" and cur_idx < _ladder_index("1"):
            return {"rank": None, "rank_label": None, "app_label": None, "confirmed": False,
                    "note": "норматив КМС выполнен, но не присваивается: нет I разряда"}
        if _ladder_index(RANK_TO_APP_LABEL[met]) <= cur_idx:
            return confirmed()
        return {"rank": met, "rank_label": RANK_DISPLAY[met], "app_label": RANK_TO_APP_LABEL[met],
                "confirmed": False, "tier_used": met_row["tier"], "note": ""}

    # --- массовые разряды: правило 5 «последовательно от низшего к высшему» ---
    # Спортсмену, выполнившему норму более высокого разряда, чем ОЧЕРЕДНОЙ,
    # засчитывается выполнение требований именно ОЧЕРЕДНОГО разряда.
    if _ladder_index(RANK_TO_APP_LABEL[met]) <= cur_idx:
        return confirmed()
    target = _next_youth_rank(current_rank_label) if met.endswith("_ю") \
        else _next_adult_rank(current_rank_label)
    if target is None:
        return confirmed()
    code = next((c for c, lbl in RANK_TO_APP_LABEL.items() if lbl == target), None)
    note = ""
    if code and code != met:
        note = "выполнен норматив «%s»; по правилу последовательности присвоен очередной разряд" \
               % RANK_DISPLAY[met]
    return {"rank": code, "rank_label": RANK_DISPLAY.get(code, target), "app_label": target,
            "confirmed": False, "tier_used": met_row["tier"], "note": note}


def field_quality_ok_for_i_razryad(rank_labels):
    """Правило для I разряда: не менее половины участников программы имеют
    разряд не ниже II. rank_labels — список athletes.rank_category всего
    поля (может быть пустым/б-р у части — считаются как не соответствующие)."""
    if not rank_labels:
        return False
    total = len(rank_labels)
    good = sum(1 for r in rank_labels if _ladder_index(r) >= _ladder_index("2"))
    return good * 2 >= total

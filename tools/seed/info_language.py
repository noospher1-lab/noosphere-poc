"""
Сведения к обсуждению «Заборона використання в Україні російської мови» (на проде — #1).

    python -m tools.seed.info_language                        # стенд студии
    python -m tools.seed.info_language --url postgresql://…   # другая база
    python -m tools.seed.info_language --create-topic          # стенд: завести обсуждение-двойник

Alex 29.09: «если у тебя есть официальные данные, то собери их на эту тему». Правила
те же, что у посевов 25.09 и 28.09 (vault: decisions/2026-09-28-info-sector):
  - автор — служебный «Claude (ИИ)»;
  - выдержки — дословно, на языке оригинала; сверку делает тот же код, что на
    сайте, статус пишется честно;
  - «норма» — только официальный текст (закон на zakon.rada.gov.ua); Конституция
    из Викитеки, СМИ, отчёты в пересказе — «сообщают»;
  - утверждение пересказывает источник, не спорит с постановкой обсуждения:
    спорят в дереве, а здесь — на что можно опереться.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db, info_db  # noqa: E402

DEFAULT_URL = "postgresql://noosphere@127.0.0.1:5433/noosphere_studio"
SOURCE_AUTHOR = ("Claude (ИИ)", "#9aa0b0")
TOPIC_TITLE = "Заборона використання в Україні російської мови"
# текст постановки на проде — для двойника на стенде (--create-topic)
TOPIC_TEXT = (
    "Уряд України забороняє використання російської мови в державних та громадських "
    "установах — в письмовому та усному вигляді. Далі вводяться штрафи за використання "
    "російської мови в будь-яких публічних місцях: на міській площі, у чаті ОСББ.")

LAW = "https://zakon.rada.gov.ua/laws/show/2704-19/print"
LAW_T = "Закон №2704-VIII «Про забезпечення функціонування української мови як державної»"
VENICE = "https://www.venice.coe.int/webforms/documents/default.aspx?pdffile=CDL-AD(2019)032-e"
VENICE_T = "Венецианская комиссия Совета Европы, заключение CDL-AD(2019)032"
OSBB = "https://interfax.com.ua/news/general/1197649.html"
SCHOOL = ("https://www.okremadumka.com/2026/09/01/%D0%BC%D0%BE%D0%B2%D0%BD%D0%B0-%D0%BE%D0%BC"
          "%D0%B1%D1%83%D0%B4%D1%81%D0%BC%D0%B0%D0%BD%D0%BA%D0%B0-%D0%BD%D0%B0%D0%B3%D0%B0%D0%B4"
          "%D0%B0%D0%BB%D0%B0-%D0%BF%D1%80%D0%BE-%D1%88%D1%82%D1%80%D0%B0%D1%84/")
UA = "Украина"

FACTS = [
    # --- закон
    {"section": "Закон-основание", "kind": "norm", "ord": 10,
     "title": "Единственный государственный (официальный) язык Украины — украинский",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "Єдиною державною (офіційною) мовою в Україні є українська мова."},
    {"section": "Закон-основание", "kind": "norm", "ord": 11,
     "title": "Закон о языке не распространяется на частное общение и религиозные обряды",
     "body": "Статья 2 закона. Где кончается «частное общение» — закон не определяет "
             "(см. заключение Венецианской комиссии в разделе «Права и равенство»).",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "Дія цього Закону не поширюється на сферу приватного спілкування та "
                     "здійснення релігійних обрядів."},
    {"section": "Закон-основание", "kind": "norm", "ord": 12,
     "title": "Обслуживание потребителей — на украинском; по просьбе клиента — и на другом языке, приемлемом для сторон",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "Мовою обслуговування споживачів в Україні є державна мова. … На "
                     "прохання клієнта його персональне обслуговування може здійснюватися "
                     "також іншою мовою, прийнятною для сторін."},
    {"section": "Закон-основание", "kind": "norm", "ord": 13,
     "title": "Язык публичных мероприятий — государственный, если законом не установлено иное",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "Мовою публічних заходів є державна мова, якщо інше не встановлено цим Законом."},
    {"section": "Закон-основание", "kind": "report", "ord": 14,
     "title": "Конституционный суд в 2021 году признал закон о языке конституционным",
     "body": "Решение №1-р/2021 от 14.07.2021 по представлению 51 народного депутата. "
             "Сайт суда закрыт для автоматической проверки — здесь сообщение СМИ.",
     "source_url": "https://www.pravda.com.ua/news/2021/07/14/7300512/",
     "source_title": "Украинская правда",
     "source_quote": "Конституційний суд визнав конституційним закон \"Про забезпечення "
                     "функціонування української мови як державної\"."},

    # --- права
    {"section": "Права и равенство", "kind": "report", "ord": 20,
     "title": "Конституция: государственный язык — украинский; гарантируется свободное развитие, использование и защита русского",
     "body": "Статья 10 Конституции Украины. Официальный сайт законодательства не пускает "
             "автоматическую проверку — текст по Викитеке.",
     "source_url": "https://uk.wikisource.org/wiki/%D0%9A%D0%BE%D0%BD%D1%81%D1%82%D0%B8%D1%82"
                   "%D1%83%D1%86%D1%96%D1%8F_%D0%A3%D0%BA%D1%80%D0%B0%D1%97%D0%BD%D0%B8",
     "source_title": "Конституция Украины, ст. 10 (Викитека)",
     "source_quote": "Державною мовою в Україні є українська мова . … В Україні гарантується "
                     "вільний розвиток, використання і захист російської , інших мов "
                     "національних меншин України ."},
    {"section": "Права и равенство", "kind": "report", "ord": 21,
     "title": "Венецианская комиссия: неясно, как будет определяться «частное общение»",
     "body": "Заключение 2019 года по закону о языке. Именно эта граница спорна в случаях с "
             "чатами ОСББ и школьными чатами (раздел «Практика применения»).",
     "source_url": VENICE, "source_title": VENICE_T,
     "source_quote": "It is unclear how the term “private communication” will be defined for "
                     "the purposes of the Law."},
    {"section": "Права и равенство", "kind": "report", "ord": 22,
     "title": "Венецианская комиссия: государство должно найти справедливый баланс между государственным языком и правами меньшинств",
     "source_url": VENICE, "source_title": VENICE_T,
     "source_quote": "these treaties imply that the member States have to strike a fair balance "
                     "between the preservation and promotion of the State language as a tool for "
                     "integration within society, on the one hand, and the protection of the "
                     "linguistic rights of persons belonging to national minorities, on the other hand."},

    # --- штрафы
    {"section": "Штрафы и контроль", "kind": "norm", "ord": 30,
     "title": "Органы власти, госпредприятия, суды, силовые ведомства: штраф 200–400 необлагаемых минимумов, впервые — предупреждение",
     "body": "Статья 188-52 Кодекса об административных правонарушениях (внесена законом о "
             "языке). Речь о заседаниях, рабочем общении, документах в этих органах и "
             "организациях — не о частных лицах. 200–400 минимумов — 3 400–6 800 грн.",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "тягнуть за собою накладення штрафу від двохсот до чотирьохсот "
                     "неоподатковуваних мінімумів доходів громадян або попередження, якщо "
                     "порушення вчинене вперше."},
    {"section": "Штрафы и контроль", "kind": "norm", "ord": 31,
     "title": "Бизнес в сфере обслуживания: сначала предупреждение и 30 дней, штраф 300–400 минимумов — при повторном нарушении в течение года",
     "source_url": LAW, "source_title": LAW_T,
     "source_quote": "За повторне протягом року порушення вимог, встановлених статтею 30 цього "
                     "Закону, Уповноважений накладає на суб’єктів господарювання, що провадять "
                     "господарську діяльність на території України, штраф у розмірі від трьохсот "
                     "до чотирьохсот неоподатковуваних мінімумів доходів громадян."},
    {"section": "Штрафы и контроль", "kind": "report", "ord": 32,
     "title": "2025 год: 2888 жалоб, 706 решений языкового омбудсмена — 404 предупреждения и 95 штрафов",
     "body": "По годовому отчёту Уполномоченного по защите государственного языка (сайт "
             "омбудсмена закрыт для автоматической проверки — пересказ SUD.UA). Почти половина "
             "жалоб — сфера обслуживания.",
     "source_url": "https://sud.ua/uk/news/ukraine/360058-706-del-i-tysyachi-zhalob-v-kakikh-"
                   "sferakh-i-regionakh-bolshe-vsego-narusheniy-yazykovogo-zakona",
     "source_title": "SUD.UA",
     "source_quote": "Попередження отримали 404 порушники, тоді як штрафи накладено у 95 випадках."},

    # --- практика
    {"section": "Практика применения", "kind": "report", "ord": 40,
     "title": "Председатель ОСББ получил предупреждение за рабочую переписку с жильцами в Viber-чате не на государственном языке",
     "body": "Омбудсмен рассматривает ОСББ как юрлицо, а рабочий чат — как исполнение "
             "служебных обязанностей председателя. Жильцам санкции не назначались; штраф "
             "председателю — только при повторном нарушении в течение года.",
     "source_url": OSBB, "source_title": "Интерфакс-Украина",
     "source_quote": "голові ОСББ оголошено попередження та вимогу усунути порушення протягом 30 днів."},
    {"section": "Практика применения", "kind": "report", "ord": 41,
     "title": "Омбудсмен: рабочая переписка в школьных и родительских чатах о школе — не частная",
     "body": "Сентябрь 2026. Первое нарушение в образовании — предупреждение или штраф 3 400–5 100 "
             "грн, повторное — 8 500–11 900 грн.",
     "source_url": SCHOOL, "source_title": "Окрема думка",
     "source_quote": "комунікація у робочих і батьківських групах щодо організації навчання, за "
                     "роз’ясненням уповноваженої, не є приватною та має відбуватися державною мовою."},
    {"section": "Практика применения", "kind": "report", "ord": 42,
     "title": "Разъяснение: это не означает автоматического штрафа ученику или родителю за русский язык в чате",
     "source_url": SCHOOL, "source_title": "Окрема думка",
     "source_quote": "Водночас це не означає автоматичного штрафу для кожного учня чи одного з "
                     "батьків за використання російської мови в чаті."},

    # --- законопроекты
    {"section": "Законопроекты", "kind": "report", "ord": 50,
     "title": "Законопроект №15412 (15.07.2026): штрафы за нарушение языковых требований — до 25 500 грн",
     "body": "Меняет 22 закона. Пока законопроект — не закон.",
     "source_url": "https://hvylya.net/uk/news/328305-shtraf-25-tysyach-i-zapret-rossiyskih-"
                   "pesen-v-rade-hotyat-izmenit-yazykovye-pravila",
     "source_title": "Хвиля",
     "source_quote": "Тепер пропонують від 6800 до 17 000 гривень за перший раз і від 20 400 до "
                     "25 500 гривень за повторне порушення протягом року."},
    {"section": "Законопроекты", "kind": "report", "ord": 51,
     "title": "Законопроект №15430 (20.07.2026): штрафы до 170 000 грн за публичное воспроизведение музыки исполнителей — граждан страны-агрессора",
     "body": "Сам запрет действует с 2022 года (закон №2310-IX); законопроект добавляет штрафы. "
             "Отвечает заведение или перевозчик. Пока законопроект — не закон.",
     "source_url": "https://interfax.com.ua/news/political/1187204.html",
     "source_title": "Интерфакс-Украина",
     "source_quote": "за перше порушення – штраф від 8 500 до 17 000 грн; за повторне протягом "
                     "року – від 17 000 до 85 000 грн; за третє та кожне наступне – від 85 000 до "
                     "170 000 грн."},
]
for f in FACTS:
    f["country"] = UA


async def _author():
    pool = db._pool_or_raise()
    name, color = SOURCE_AUTHOR
    async with pool.acquire() as conn:
        aid = await conn.fetchval(
            "SELECT id FROM authors WHERE name = $1 AND is_service ORDER BY id LIMIT 1", name)
    if aid is None:
        aid = await db.add_author(name, color)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE authors SET is_service = TRUE WHERE id = $1", aid)
    return aid


async def run(url, check=True, create_topic=False):
    db.DATABASE_URL = url
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        root = await conn.fetchval(
            "SELECT id FROM nodes WHERE title = $1 AND id = topic_root_id "
            "AND deleted_at IS NULL ORDER BY id LIMIT 1", TOPIC_TITLE)
    aid = await _author()
    if root is None:
        if not create_topic:
            raise SystemExit(f"нет обсуждения «{TOPIC_TITLE}» (на стенде — --create-topic)")
        root = await db.add_node(TOPIC_TEXT, author_id=aid, kind="problem", title=TOPIC_TITLE)
        print(f"заведён двойник обсуждения #{root}")
    added = skipped = 0
    stats = {}
    for f in FACTS:
        if await info_db.find_fact(root, f["title"], f.get("country")):
            skipped += 1
            continue
        status = "none"
        if f.get("source_url") and f.get("source_quote"):
            status = await asyncio.to_thread(info_db.check_quote, f["source_url"],
                                             f["source_quote"]) if check else "unchecked"
            if status == "unreachable":
                # сайт Рады иногда не отвечает на секунду — вторая попытка, иначе
                # норма зря становилась «сообщают» (стенд 29.09, #с119)
                await asyncio.sleep(3)
                status = await asyncio.to_thread(info_db.check_quote, f["source_url"],
                                                 f["source_quote"])
        data = dict(f)
        if data["kind"] == "norm" and status != "verified":
            data["kind"] = "report"
        await info_db.add_fact(root, data, aid, quote_status=status,
                               checked_at=datetime.now(timezone.utc) if status != "none" else None,
                               limit=False)
        stats[status] = stats.get(status, 0) + 1
        if status == "mismatch":
            print(f"  ✗ цитата не найдена: {f['title'][:70]}")
        added += 1
    print(f"обсуждение #{root}: добавлено {added}, уже было {skipped}; сверка: {stats}")
    await db.close_pool()


def main():
    ap = argparse.ArgumentParser(description="Сведения: закон о языке в Украине")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--create-topic", action="store_true",
                    help="стенд: завести обсуждение, если его нет")
    a = ap.parse_args()
    asyncio.run(run(a.url, check=not a.no_check, create_topic=a.create_topic))


if __name__ == "__main__":
    main()

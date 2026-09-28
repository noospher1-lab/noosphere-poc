"""
Посев информационного сектора «Украинцы в ЕС: временная защита, ВНЖ, суд»
(vault: drafts/2026-09-28-info-sector).

    python -m tools.seed.info_ua_eu                       # стенд студии (:5433)
    python -m tools.seed.info_ua_eu --url postgresql://…  # другая база

Alex 28.09: «сначала… добавим сами всю имеющуюся информацию по этому вопросу,
а далее люди уже должны сами начать добавлять». Порядок — сначала закон, на
котором основаны действия стран, и законы о правах и равенстве; потом страны;
потом города, если там своя практика.

Правила (как у посевов 25.09):
  - автор — служебный «Claude (ИИ)», не выдуманные люди;
  - каждая выдержка — ДОСЛОВНО со страницы, на языке оригинала; сверку делает
    тот же код, что на сайте (info_db.check_quote), и статус пишется честно:
    сайт не пустил робота — «сверить не удалось», а не «сверено»;
  - вид «норма» — только текст закона или официального акта; новости и разборы
    юристов — «практика» (что происходит) или «не проверено»;
  - мы не консультируем: утверждение пересказывает источник, не советует;
  - повторный запуск ничего не задваивает (то же утверждение в той же стране —
    пропуск).
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

SECTOR = {
    "slug": "ua-eu",
    "title": "Украинцы в ЕС: временная защита, ВНЖ, суд",
    "intro": (
        "Решение Совета ЕС 2026/1912 продлевает временную защиту до 04.03.2028, а новым "
        "заявителям даёт её только при выполнении воинских обязанностей перед Украиной. "
        "Страны применяют это по-своему и с разных дат. Здесь собрано, что известно — "
        "сначала общее для всех, потом по странам."),
    # связь с обсуждением «законно ли это требование» — проблема из посева 25.09
    "problem_title": "Резерв+ как условие временной защиты в ЕС",
}

EUR = "https://www.legislation.gov.uk/eudr/2001/55/pdfs/eudr_20010055_adopted_en.pdf"
EEAS = ("https://www.eeas.europa.eu/delegations/ukraine/"
        "eu-countries-agree-extend-temporary-protection-those-fleeing-ukraine-until-march-2028_en")

# ------------------------------------------------------------ суть для всех
COMMON = [
    # --- закон-основание
    {"section": "basis", "kind": "norm", "ord": 10,
     "title": "Временная защита в ЕС продлена до 04.03.2028",
     "body": "Решение Совета ЕС 2026/1912 от 30.07.2026, опубликовано 04.08.2026, вступило "
             "в силу 05.08.2026. Сам текст решения на EUR-Lex закрыт для автоматической "
             "проверки; здесь — официальное сообщение ЕС.",
     "when_text": "до 04.03.2028",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "Today, EU countries agreed to extend the temporary protection status "
                     "granted to those fleeing Ukraine until 4 March 2028"},
    {"section": "basis", "kind": "norm", "ord": 11,
     "title": "Новым заявителям защиту дают только при выполнении воинских обязанностей",
     "body": "На это решение ссылаются страны, требуя штамп о выезде или е-ВОД (документ "
             "из Резерв+). Даты: решение принято 30.07.2026, опубликовано 04.08, действует "
             "с 05.08; условие касается заявлений с 31.07. Страны называют свои даты "
             "начала (например, Эстония — 14.08, Швейцария — 20.08).",
     "applies_to": "новые заявители с воинской обязанностью по закону Украины",
     "when_text": "для заявлений с 31.07.2026",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "Taking into account Ukraine’s evolving defence needs , going forward "
                     "temporary protection will be granted only to those who satisfy their "
                     "military obligations in Ukraine."},
    {"section": "basis", "kind": "norm", "ord": 12,
     "title": "Тех, у кого защита уже есть, новое условие не касается",
     "body": "Условие — непрерывность: по тексту решения исключение распространяется на тех, "
             "кто имел защиту в данной стране на 30.07.2026 и сохраняет её без перерыва "
             "(см. разбор юристов ниже).",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "This limitation will only apply to new applicants for temporary "
                     "protection. It will not apply to those already benefiting from "
                     "temporary protection in the EU."},
    {"section": "basis", "kind": "report", "ord": 13,
     "title": "Исключение — только при непрерывной защите в той же стране",
     "body": "Пересказ текста решения 2026/1912 юридической фирмой. Если защита прерывалась "
             "(например, её сняли и человек подаёт заново), он считается новым заявителем.",
     "source_url": "https://www.weinholdlegal.com/hr-legal-update/temporary-protection-for-"
                   "persons-displaced-from-ukraine-extended-until-4-march-2028",
     "source_title": "Weinhold Legal, разбор решения 2026/1912",
     "source_quote": "this rule does not apply to persons who were already benefiting from "
                     "temporary protection in the relevant Member State before or on 30 July "
                     "2026 and who subsequently maintain such protection without interruption."},
    {"section": "basis", "kind": "norm", "ord": 14,
     "title": "Как подтверждают воинские обязанности: штамп о выезде или документ об освобождении",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "As an example, this could be done by showing a passport with the exit "
                     "stamp provided by the Ukrainian authorities proving they left Ukraine "
                     "legally and therefore satisfy military obligations. This could also be "
                     "done by showing a document, in paper or electronic format, that confirms "
                     "exemption or compliance with military obligations."},
    {"section": "basis", "kind": "report", "ord": 15,
     "title": "Под временной защитой в ЕС — 4,38 млн человек (на 31.05.2026)",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "As of 31 May 2026, 4.38 million people who fled Ukraine are under "
                     "temporary protection in the EU."},
    {"section": "basis", "kind": "norm", "ord": 16,
     "title": "Директива 2001/55/ЕС: защита даётся на год и продлевается решениями Совета",
     "body": "Директива о временной защите — рамка, внутри которой принимаются решения "
             "Совета о продлении.",
     "source_url": EUR, "source_title": "Директива 2001/55/ЕС (официальный текст, PDF)",
     "source_quote": "Without prejudice to Article 6, the duration of temporary protection "
                     "shall be one year."},
    {"section": "basis", "kind": "norm", "ord": 17,
     "title": "Совет может продлить защиту ещё не более чем на год за раз",
     "source_url": EUR, "source_title": "Директива 2001/55/ЕС, ст. 4(2)",
     "source_quote": "the Council may decide by qualified majority, on a proposal from the "
                     "Commission, which shall also examine any request by a Member State that "
                     "it submit a proposal to the Council, to extend that temporary protection "
                     "by up to one year."},
    {"section": "basis", "kind": "norm", "ord": 18,
     "title": "Совет ЕС: у тех, кто подходит, должна быть возможность перейти с защиты на другие статусы",
     "body": "Работа, учёба, семья — основания для более долгих видов на жительство.",
     "source_url": EEAS, "source_title": "EEAS: сообщение Совета ЕС от 15.07.2026",
     "source_quote": "This approach includes providing opportunities to transition to "
                     "longer-term legal resident statuses based on employment, education or "
                     "family grounds for instance for those eligible."},

    {"section": "basis", "kind": "report", "ord": 19,
     "title": "Со стороны Украины: консульства с августа 2026 не обслуживают мужчин 18–60 без военно-учётного документа",
     "body": "Постановление КМУ №981 от 29.07.2026, в пересказе УНИАН.",
     "source_url": "https://www.unian.ua/society/mobilizaciya-posolstva-ne-budut-obslugovuvati-"
                   "cholovikiv-bez-viyskovo-oblikovykh-dokumentiv-13459719.html",
     "source_title": "УНИАН",
     "source_quote": "Українські консульства за кордоном не зможуть надавати послуги "
                     "військовозобов’язаним чоловікам віком від 18 до 60 років без подання ними "
                     "військово-облікових документів."},

    # --- права и равенство
    {"section": "rights", "kind": "norm", "ord": 20,
     "title": "Исключить из защиты можно только за личное поведение из закрытого списка",
     "body": "Директива 2001/55/ЕС, ст. 28: список оснований — преступления против мира, "
             "военные и тяжкие преступления, угроза безопасности. Невыполнение воинских "
             "обязанностей в нём не названо. Соотносится ли с этим решение 2026/1912 — "
             "вопрос для юристов и суда.",
     "source_url": EUR, "source_title": "Директива 2001/55/ЕС, ст. 28(2)",
     "source_quote": "The grounds for exclusion referred to in paragraph 1 shall be based "
                     "solely on the personal conduct of the person concerned."},
    {"section": "rights", "kind": "norm", "ord": 21,
     "title": "Хартия ЕС запрещает дискриминацию, в том числе по полу и возрасту",
     "body": "Ст. 21 Хартии основных прав ЕС. Требование касается людей с воинской "
             "обязанностью — по закону Украины это в основном мужчины определённого возраста.",
     "source_url": "https://fra.europa.eu/en/eu-charter/article/21-non-discrimination",
     "source_title": "Хартия основных прав ЕС, ст. 21 (сайт FRA)",
     "source_quote": "Any discrimination based on any ground such as sex, race, colour, ethnic "
                     "or social origin, genetic features, language, religion or belief, "
                     "political or any other opinion, membership of a national minority, "
                     "property, birth, disability, age or sexual orientation shall be prohibited."},
    {"section": "rights", "kind": "norm", "ord": 22,
     "title": "Хартия ЕС: равенство мужчин и женщин во всех сферах",
     "source_url": "https://fra.europa.eu/en/eu-charter/article/23-equality-between-women-and-men",
     "source_title": "Хартия основных прав ЕС, ст. 23 (сайт FRA)",
     "source_quote": "Equality between women and men must be ensured in all areas, including "
                     "employment, work and pay."},
    {"section": "rights", "kind": "norm", "ord": 23,
     "title": "ЕКПЧ: права по Конвенции — без дискриминации, в том числе по полу",
     "body": "Ст. 14 ЕКПЧ действует вместе с другими правами Конвенции (например, "
             "на уважение частной и семейной жизни, ст. 8).",
     "source_url": "https://rm.coe.int/1680063765",
     "source_title": "ЕКПЧ, ст. 14 (Совет Европы, PDF)",
     "source_quote": "The enjoyment of the rights and freedoms set forth in this Convention "
                     "shall be secured without discrimination on any ground such as sex, race, "
                     "colour, language, religion, political or other opinion, national or social "
                     "origin, association with a national minority, property, birth or other status."},
    {"section": "rights", "kind": "norm", "ord": 24,
     "title": "Пакт ООН: каждый свободен покидать любую страну, включая свою",
     "body": "Ст. 12(2) Международного пакта о гражданских и политических правах. Украина "
             "на время военного положения заявила об отступлении от обязательств по этой статье.",
     "source_url": "https://www.ohchr.org/sites/default/files/ccpr.pdf",
     "source_title": "МПГПП, ст. 12(2) (ООН, PDF)",
     "source_quote": "Everyone shall be free to leave any country, including his own."},
    {"section": "rights", "kind": "norm", "ord": 25,
     "title": "Пакт ООН: отступление в чрезвычайной ситуации не может дискриминировать по полу",
     "source_url": "https://www.ohchr.org/sites/default/files/ccpr.pdf",
     "source_title": "МПГПП, ст. 4(1) (ООН, PDF)",
     "source_quote": "do not involve discrimination solely on the ground of race, colour, sex, "
                     "language, religion or social origin."},
    {"section": "rights", "kind": "norm", "ord": 26,
     "title": "Пакт ООН: все равны перед законом и защищены от дискриминации",
     "source_url": "https://www.ohchr.org/sites/default/files/ccpr.pdf",
     "source_title": "МПГПП, ст. 26 (ООН, PDF)",
     "source_quote": "All persons are equal before the law and are entitled without any "
                     "discrimination to the equal protection of the law."},
    {"section": "rights", "kind": "norm", "ord": 27,
     "title": "Под временной защитой можно в любой момент подать на убежище",
     "body": "Директива 2001/55/ЕС, ст. 17(1). Убежище рассматривается индивидуально, "
             "исход не гарантирован.",
     "source_url": EUR, "source_title": "Директива 2001/55/ЕС, ст. 17(1)",
     "source_quote": "Persons enjoying temporary protection must be able to lodge an "
                     "application for asylum at any time."},

    {"section": "rights", "kind": "report", "ord": 28,
     "title": "МИД Украины: правило ЕС не делит по полу — е-ВОД могут спросить и у женщин на учёте",
     "source_url": "https://news.liga.net/en/society/news/the-foreign-ministry-explained-why-women-"
                   "are-asked-for-rezerv-in-some-eu-countries-when-applying-for-protection",
     "source_title": "LIGA.net со ссылкой на МИД Украины",
     "source_quote": "In reality, however, the new European Union rule is gender-neutral and can "
                     "apply to both men and women with the relevant military status."},
    {"section": "court", "kind": "report", "ord": 37,
     "title": "Адвокат: решение 2026/1912 должно применяться с учётом пропорциональности, прав ребёнка и права на обжалование",
     "body": "Мнение адвоката Андрея Денисенко.",
     "source_url": "https://www.village-justice.com/articles/protection-temporaire-des-ukrainiens-"
                   "obligations-militaires-une-personne-fuyant,59119.html",
     "source_title": "Village de la Justice (адвокат Andrii Denysenko)",
     "source_quote": "Elle doit être appliquée en tenant compte notamment du principe de "
                     "proportionnalité, des droits de l’enfant, du droit à un recours effectif et "
                     "des autres garanties applicables."},
    {"section": "court", "kind": "report", "ord": 38,
     "title": "Адвокат: примеры оснований для спора — отказ из-за отсутствия одного документа, непринятие другого доказательства",
     "source_url": "https://www.village-justice.com/articles/protection-temporaire-des-ukrainiens-"
                   "obligations-militaires-une-personne-fuyant,59119.html",
     "source_title": "Village de la Justice (адвокат Andrii Denysenko)",
     "source_quote": "un refus fondé sur l’absence d’un document particulier ; le refus d’accepter "
                     "une preuve alternative"},

    # --- суд
    {"section": "court", "kind": "norm", "ord": 30,
     "title": "Отказ в защите можно обжаловать в той стране, где отказали",
     "source_url": EUR, "source_title": "Директива 2001/55/ЕС, ст. 29",
     "source_quote": "Persons who have been excluded from the benefit of temporary protection "
                     "or family reunification by a Member State shall be entitled to mount a "
                     "legal challenge in the Member State concerned."},
    {"section": "court", "kind": "norm", "ord": 31,
     "title": "Национальный суд может спросить Суд ЕС о толковании права ЕС",
     "body": "Ст. 267 Договора о функционировании ЕС (преюдициальный запрос). Так вопрос о "
             "решении 2026/1912 может дойти до Суда ЕС через обычное обжалование отказа.",
     "source_url": "https://www.legislation.gov.uk/eut/teec/article/267",
     "source_title": "ДФЕС, ст. 267",
     "source_quote": "Where such a question is raised before any court or tribunal of a Member "
                     "State, that court or tribunal may, if it considers that a decision on the "
                     "question is necessary to enable it to give judgment, request the Court to "
                     "give a ruling thereon."},
    {"section": "court", "kind": "norm", "ord": 32,
     "title": "Последняя инстанция обязана передать вопрос в Суд ЕС",
     "source_url": "https://www.legislation.gov.uk/eut/teec/article/267",
     "source_title": "ДФЕС, ст. 267",
     "source_quote": "Where any such question is raised in a case pending before a court or "
                     "tribunal of a Member State against whose decisions there is no judicial "
                     "remedy under national law, that court or tribunal shall bring the matter "
                     "before the Court."},
    {"section": "court", "kind": "norm", "ord": 33,
     "title": "Прямой иск частного лица об отмене акта ЕС — только при прямом и индивидуальном касании",
     "body": "Ст. 263 ДФЕС. Право частного лица на такой иск толкуется узко.",
     "source_url": "https://www.legislation.gov.uk/eut/teec/article/263",
     "source_title": "ДФЕС, ст. 263",
     "source_quote": "Any natural or legal person may, under the conditions laid down in the "
                     "first and second paragraphs, institute proceedings against an act addressed "
                     "to that person or which is of direct and individual concern to them"},
    {"section": "court", "kind": "norm", "ord": 34,
     "title": "Срок прямого иска — два месяца с публикации акта",
     "body": "Решение 2026/1912 опубликовано 04.08.2026. По правилам суда к сроку "
             "добавляются дни; точную дату считает юрист — вероятно, она приходится на "
             "конец октября 2026.",
     "when_text": "от 04.08.2026",
     "source_url": "https://www.legislation.gov.uk/eut/teec/article/263",
     "source_title": "ДФЕС, ст. 263",
     "source_quote": "The proceedings provided for in this Article shall be instituted within "
                     "two months of the publication of the measure"},
    {"section": "court", "kind": "report", "ord": 35,
     "title": "Жалоба в ЕСПЧ — в течение четырёх месяцев после окончательного решения внутри страны",
     "body": "С 01.02.2022 (Протокол № 15). Сначала нужно пройти суды страны.",
     "source_url": "https://www.lawsociety.ie/news/news/Stories/reduced-time-limit-for-ecthr-applications",
     "source_title": "Law Society of Ireland",
     "source_quote": "Effective from 1 February 2022, the time-limit for bringing applications "
                     "to the European Court of Human Rights (ECtHR) is reduced from six months "
                     "to four months following the exhausting of domestic remedies."},
    {"section": "court", "kind": "norm", "ord": 36,
     "title": "Петицию в Европарламент может подать любой житель ЕС",
     "body": "Не суд, но способ вынести вопрос на уровень ЕС (ст. 227 ДФЕС).",
     "source_url": "https://www.europarl.europa.eu/petitions/en/home",
     "source_title": "Портал петиций Европарламента",
     "source_quote": "express your right to petition, which is one of the fundamental rights of "
                     "all European citizens and residents"},
]

# ------------------------------------------------------------ страны
COUNTRIES = [
    # --- Германия
    {"country": "Германия", "section": "protection", "kind": "norm",
     "title": "Действующие разрешения по § 24 продлеваются до 04.03.2028 автоматически",
     "body": "Для граждан Украины: разрешение должно быть действительно на 01.02.2027. "
             "Идти в ведомство по делам иностранцев ради продления не нужно.",
     "when_text": "до 04.03.2028",
     "source_url": "https://www.germany4ukraine.de/EN/einreise-aufenthalt-und-rueckkehr/"
                   "ukraine-aufenthaltserlaubnis/seite_node.html",
     "source_title": "Germany4Ukraine (портал правительства Германии)",
     "source_quote": "residence permits for temporary protection that are still valid on 1 "
                     "February 2027 are automatically extended to 4 March 2028."},
    {"country": "Германия", "section": "protection", "kind": "report",
     "applies_to": "новоприбывшие мужчины призывного возраста",
     "title": "Новоприбывшим мужчинам призывного возраста защиту больше не дают автоматически — нужно подтвердить законный выезд или освобождение",
     "source_url": "https://nv.ua/ukr/world/geopolitics/timchasoviy-zahist-u-nimechchini-novi-"
                   "pravila-dlya-ukrajinskih-cholovikiv-mobilizaciynogo-viku-50632450.html",
     "source_title": "NV со ссылкой на МВД Германии (13.08.2026)",
     "source_quote": "Німеччина від 31 липня більше не надає автоматичний тимчасовий захист "
                     "новоприбулим українським чоловікам мобілізаційного віку, якщо ті не "
                     "підтвердять законність виїзду з України або звільнення від військової служби."},
    {"country": "Германия", "section": "protection", "kind": "report",
     "title": "Отказ во временной защите не означает автоматической обязанности уехать; можно просить убежище",
     "body": "МВД Германии при этом подчеркнуло: сама по себе воинская обязанность в Украине "
             "не даёт оснований для убежища — рассматривается индивидуально.",
     "source_url": "https://zn.ua/ukr/europe/nimechchina-obmezhila-timchasovij-zakhist-dlja-"
                   "ukrajinskikh-cholovikiv-komu-mozhe-zahrozhuvati-deportatsija.html",
     "source_title": "Зеркало недели",
     "source_quote": "Відмова у тимчасовому захисті не означає, що український чоловік "
                     "автоматично повинен залишити Німеччину."},
    {"country": "Германия", "section": "residence", "kind": "norm",
     "title": "В первые 90 дней после въезда можно подать на другой вид на жительство — для работы или учёбы",
     "source_url": "https://www.germany4ukraine.de/EN/einreise-aufenthalt-und-rueckkehr/"
                   "ukraine-aufenthaltserlaubnis/seite_node.html",
     "source_title": "Germany4Ukraine (портал правительства Германии)",
     "source_quote": "Within 90 days of entering Germany for the first time, you can apply for a "
                     "temporary residence permit for a different purpose, such as to study or "
                     "work in Germany."},

    # --- Польша
    {"country": "Польша", "section": "residence", "kind": "report",
     "title": "Карта побыту CUKR: на 3 года для тех, у кого статус UKR непрерывно не меньше года",
     "body": "Условия: статус PESEL UKR на 04.06.2025 и на день подачи, непрерывно не менее "
             "365 дней. Подача только онлайн через портал MOS, до 04.03.2027. Сборы: 100 и 340 злотых.",
     "when_text": "подача до 04.03.2027",
     "source_url": "https://help.unhcr.org/poland/uk/%D0%BA%D0%B0%D1%80%D1%82%D0%B0-%D0%BF%D0%BE"
                   "%D0%B1%D0%B8%D1%82%D1%83-cukr/",
     "source_title": "УВКБ ООН в Польше: карта побыту CUKR",
     "source_quote": "Подати заяву на PESEL CUKR можна до 04.03.2027 . Карта побиту CUKR дійсна "
                     "протягом 3 років з дати видачі ."},
    {"country": "Польша", "section": "residence", "kind": "report",
     "title": "После подачи на карту CUKR теряется часть прав статуса UKR",
     "body": "Бесплатное проживание в центрах, часть медицинских льгот, социальная помощь. "
             "УВКБ ООН рекомендует перед подачей обратиться к бесплатным юристам НКО.",
     "source_url": "https://help.unhcr.org/poland/uk/%D0%BA%D0%B0%D1%80%D1%82%D0%B0-%D0%BF%D0%BE"
                   "%D0%B1%D0%B8%D1%82%D1%83-cukr/",
     "source_title": "УВКБ ООН в Польше: карта побыту CUKR",
     "source_quote": "Зверніть увагу: після подання заяви ви втратите деякі права, пов’язані з "
                     "вашим поточним статусом PESEL UKR ."},
    {"country": "Польша", "section": "residence", "kind": "report",
     "title": "Выезд из Польши больше чем на 6 месяцев лишает карты CUKR",
     "source_url": "https://help.unhcr.org/poland/uk/%D0%BA%D0%B0%D1%80%D1%82%D0%B0-%D0%BF%D0%BE"
                   "%D0%B1%D0%B8%D1%82%D1%83-cukr/",
     "source_title": "УВКБ ООН в Польше: карта побыту CUKR",
     "source_quote": "Якщо ви залишите Польщу на термін понад 6 місяців , ви втратите дозвіл на "
                     "тимчасове проживання"},
    {"country": "Польша", "section": "protection", "kind": "report",
     "title": "Повторно временную защиту в Польше не дают, если она уже была в другой стране ЕС",
     "source_url": "https://visitukraine.today/departure/poland/ukraine-citizenship/temporary-protection",
     "source_title": "Visit Ukraine",
     "source_quote": "No. Poland does not grant repeated temporary protection if you have already "
                     "been granted it in another EU country."},

    # --- Чехия
    {"country": "Чехия", "section": "protection", "kind": "norm",
     "title": "Продление до 31.03.2028: онлайн-регистрация и личный визит в МВД",
     "when_text": "до 31.03.2028",
     "source_url": "https://ipc.gov.cz/uk/https-ipc-gov-cz-prodlouzeni-docasne-ochrany-do-roku-"
                   "2028-a-uprava-podminek-pro-jeji-udeleni-ua/",
     "source_title": "МВД Чехии, портал для иностранцев (04.08.2026)",
     "source_quote": "Процес продовження тимчасового захисту в Чехії відбуватиметься подібно до "
                     "попередніх років — через онлайн-реєстрацію та особистий візит до відділення "
                     "Міністерства внутрішніх справ для отримання нової візової наклейки."},
    {"country": "Чехия", "section": "protection", "kind": "norm",
     "title": "При продлении документ о воинских обязанностях не требуют",
     "source_url": "https://ipc.gov.cz/uk/https-ipc-gov-cz-prodlouzeni-docasne-ochrany-do-roku-"
                   "2028-a-uprava-podminek-pro-jeji-udeleni-ua/",
     "source_title": "МВД Чехии, портал для иностранцев (04.08.2026)",
     "source_quote": "Документ про виконання військових обов’язків не вимагатиметься під час "
                     "продовження тимчасового захисту до 31 березня 2028 ."},
    {"country": "Чехия", "section": "protection", "kind": "norm",
     "applies_to": "новые заявители 18–60 с воинской обязанностью",
     "title": "Новым заявителям 18–60 с воинской обязанностью нужен распечатанный е-ВОД",
     "body": "По порталу МВД: скриншот не принимают, профиль в Резерв+ просят показать на "
             "телефоне. 18–22: регистрация в Резерв+ и е-ВОД; 23–60: в е-ВОД должна быть "
             "отметка об освобождении. Либо штамп о выезде в паспорте и е-ВОД.",
     "source_url": "https://ipc.gov.cz/uk/https-ipc-gov-cz-prodlouzeni-docasne-ochrany-do-roku-"
                   "2028-a-uprava-podminek-pro-jeji-udeleni-ua/",
     "source_title": "МВД Чехии, портал для иностранцев (04.08.2026)",
     "source_quote": "Особи з військовим обов’язком віком 23–60 років (необхідно підтвердити, "
                     "що в їхньому е-ВОД , в роздрукованому вигляді, міститься інформація про "
                     "звільнення від виконання військового обов’язку)."},
    {"country": "Чехия", "section": "protection", "kind": "report",
     "title": "МВД Чехии ожидает, что новоприбывших станет меньше «на десятки процентов»",
     "source_url": "https://news.liga.net/ua/politics/news/chekhiia-otsinyla-novi-pravyla-yes-"
                   "shchodo-rezervu-obmezhyt-kilkist-novoprybulykh-na-desiatky-vidsotkiv",
     "source_title": "LIGA.net",
     "source_quote": "Це рішення обмежить кількість новоприбулих на десятки відсотків."},

    # --- Венгрия
    {"country": "Венгрия", "section": "protection", "kind": "report",
     "title": "Карты временной защиты действуют до 04.03.2028, даже если на карте другая дата",
     "body": "Постановление правительства 145/2026 от 04.09.2026.",
     "when_text": "до 04.03.2028",
     "source_url": "https://help.unhcr.org/hungary/temporary-protection/",
     "source_title": "УВКБ ООН в Венгрии",
     "source_quote": "Temporary Protection cards are valid until 4 March 2028, even if a "
                     "different expiry date is printed on the card"},
    {"country": "Венгрия", "section": "protection", "kind": "report",
     "applies_to": "мужчины призывного возраста",
     "title": "По данным УВКБ ООН, в Венгрии все, у кого было право на защиту, его сохраняют — включая мужчин призывного возраста",
     "body": "Как это сочетается с решением ЕС 2026/1912 — не разъяснено. Нужны подтверждения "
             "людей, подававших в Венгрии после 31.07.2026.",
     "source_url": "https://help.unhcr.org/hungary/temporary-protection/",
     "source_title": "УВКБ ООН в Венгрии",
     "source_quote": "Everyone who was previously eligible remains eligible, including "
                     "military-aged men."},

    # --- Словакия
    {"country": "Словакия", "section": "protection", "kind": "report",
     "applies_to": "мужчины, новые и повторные заявления",
     "title": "Мужчинам: штамп о выезде не старше 90 дней или распечатанный е-ВОД",
     "source_url": "https://sport.znaj.ua/559806-rezerv-uzhe-nedostatno-slovachchina-zhorstkishe-"
                   "filtruvatime-ukrajinskih-cholovikiv",
     "source_title": "ЗНАЙ ЮА",
     "source_quote": "штамп має бути поставлений не раніше ніж за 90 днів до дати подання заяви."},

    # --- Испания
    {"country": "Испания", "section": "protection", "kind": "report",
     "title": "Инструкция полиции требует выписку из Резерв+ с переводом на испанский (по сообщению СМИ)",
     "source_url": "https://cv.znaj.ua/558548-ispaniya-vidmovlyaye-cholovikam-u-timchasovomu-"
                   "zahisti-pereviryayut-rezerv-proyshli-lishe-troye",
     "source_title": "ЗНАЙ ЮА",
     "source_quote": "Документ прямо вимагає від заявників витяг із застосунку «Резерв+» у "
                     "перекладі іспанською мовою."},
    {"country": "Испания", "city": "Аликанте", "section": "protection", "kind": "report",
     "title": "Волонтёры в полиции Аликанте: одобряют только «снят с учёта» или «непригоден»",
     "body": "Пересказ слов волонтёров в СМИ; нужны подтверждения людей, подававших в Аликанте.",
     "source_url": "https://cv.znaj.ua/558548-ispaniya-vidmovlyaye-cholovikam-u-timchasovomu-"
                   "zahisti-pereviryayut-rezerv-proyshli-lishe-troye",
     "source_title": "ЗНАЙ ЮА"},

    # --- Бельгия
    {"country": "Бельгия", "city": "Брюссель", "section": "protection", "kind": "report",
     "title": "Больше 150 жалоб на отказы за первые 2,5 недели в одном центре помощи",
     "source_url": "https://tsn.ua/svit/rezerv-teper-mozut-vymahaty-navit-u-zinok-shcho-"
                   "zminylosia-dlya-ukrayintsiv-u-yes-3154013.html",
     "source_title": "ТСН",
     "source_quote": "Лише за перші 2,5 тижня нових правил до цього центру надійшло понад 150 "
                     "скарг через відмови у тимчасовому захисті."},

    # --- Болгария
    {"country": "Болгария", "city": "Варна", "section": "protection", "kind": "report",
     "applies_to": "женщины",
     "title": "По сообщению СМИ, женщинам отказывают из-за отсутствия отметки в Резерв+; агентство сослалось на «внутренние инструкции»",
     "body": "Страницу не удалось открыть автоматически; текст виден в поисковике на 25.09.2026. "
             "Нужны подтверждения людей.",
     "source_url": "https://ukranews.com/news/1172013-v-bolgarii-zhenshhinam-iz-ukrainy-"
                   "otkazyvayut-vo-vremennoj-zashhite-iz-za-otsutstviya-otmetki-o",
     "source_title": "Украинские новости"},

    # --- Финляндия
    {"country": "Финляндия", "section": "protection", "kind": "norm",
     "title": "Новым заявителям с 05.08.2026 защиту не дают без выполнения воинских обязанностей",
     "source_url": "https://migri.fi/en/-/temporary-protection-continues-until-4-march-2028-with-certain-exceptions",
     "source_title": "Migri (миграционная служба Финляндии)",
     "source_quote": "This means that, as of 5 August 2026, temporary protection is not granted to "
                     "persons who have not complied with their military obligations in Ukraine."},
    {"country": "Финляндия", "section": "protection", "kind": "norm",
     "title": "Нет штампа в паспорте — можно показать официальный документ об исполнении или освобождении",
     "source_url": "https://migri.fi/en/-/temporary-protection-continues-until-4-march-2028-with-certain-exceptions",
     "source_title": "Migri (миграционная служба Финляндии)",
     "source_quote": "If you, for some reason, did not receive a stamp on your passport, you can "
                     "prove that you are authorised to leave Ukraine by presenting an official "
                     "document which confirms that you have complied with your military "
                     "obligations or are exempt from them."},
    {"country": "Финляндия", "section": "protection", "kind": "norm",
     "title": "О продлении разрешений до 04.03.2028 Migri сообщит отдельно",
     "source_url": "https://migri.fi/en/-/temporary-protection-continues-until-4-march-2028-with-certain-exceptions",
     "source_title": "Migri (миграционная служба Финляндии)",
     "source_quote": "We will provide further instructions and information separately when we "
                     "start to extend the validity of residence permits and to issue residence "
                     "permit cards with the expiry date 4 March 2028."},
    {"country": "Финляндия", "section": "residence", "kind": "norm",
     "title": "Не получили защиту — можно подать на вид на жительство по другому основанию",
     "source_url": "https://migri.fi/en/-/temporary-protection-continues-until-4-march-2028-with-certain-exceptions",
     "source_title": "Migri (миграционная служба Финляндии)",
     "source_quote": "If you cannot get temporary protection but wish to stay in Finland, you can "
                     "apply for a residence permit on some other grounds."},

    # --- Латвия
    {"country": "Латвия", "section": "protection", "kind": "report",
     "title": "PMLP: новые заявители с 31.07.2026 подтверждают воинский статус; Резерв+ — лишь пример документа",
     "body": "Разъяснение PMLP от 12.08.2026 в пересказе LSM. Штамп о выезде — не единственный "
             "вариант; по разъяснению PMLP, обязательным для всех Резерв+ не назван.",
     "source_url": "https://ukr.lsm.lv/stattja/novini/ukraina/20.08.2026-rezerv-moze-znadobitisya-i-"
                   "zinkam-shho-zminilosya-dlya-ukrayinciv-yaki-oformlyuyut-timcasovii-zaxist-u-latviyi.a659503/",
     "source_title": "LSM (общественное вещание Латвии)",
     "source_quote": "Альтернативою є офіційний документ у паперовій або цифровій формі; PMLP "
                     "прямо наводить «Резерв+» як приклад."},
    {"country": "Латвия", "section": "protection", "kind": "report",
     "applies_to": "женщины",
     "title": "е-ВОД могут попросить и у женщин, но обязательным для всех он не назван",
     "source_url": "https://ukr.lsm.lv/stattja/novini/ukraina/20.08.2026-rezerv-moze-znadobitisya-i-"
                   "zinkam-shho-zminilosya-dlya-ukrayinciv-yaki-oformlyuyut-timcasovii-zaxist-u-latviyi.a659503/",
     "source_title": "LSM (общественное вещание Латвии)",
     "source_quote": "Латвійське PMLP у своєму роз’ясненні не пише, що всі українські жінки мають "
                     "обов’язково пред’являти «Резерв+»."},

    # --- Нидерланды
    {"country": "Нидерланды", "section": "protection", "kind": "norm",
     "title": "У кого защита была до 05.08.2026 — продлевается до 04.03.2028 автоматически",
     "source_url": "https://ind.nl/en/news/temporary-protection-directive-for-ukraine-extended-with-new-condition-for-new-applications",
     "source_title": "IND (иммиграционная служба Нидерландов)",
     "source_quote": "The extension is automatic; no action is required on their part."},
    {"country": "Нидерланды", "section": "protection", "kind": "norm",
     "title": "Новые заявления с 05.08.2026: подтвердить исполнение воинских обязанностей или освобождение",
     "source_url": "https://ind.nl/en/news/temporary-protection-directive-for-ukraine-extended-with-new-condition-for-new-applications",
     "source_title": "IND (иммиграционная служба Нидерландов)",
     "source_quote": "For new applications submitted on or after 5 August 2026, individuals "
                     "subject to military conscription in Ukraine must demonstrate that they have "
                     "fulfilled their military obligations or are exempt from them."},

    # --- Эстония
    {"country": "Эстония", "section": "protection", "kind": "norm",
     "title": "Защиту не дают тем, у кого нет законного основания выехать из Украины из-за воинской обязанности",
     "source_url": "https://www.politsei.ee/en/instructions/information-on-the-war-in-ukraine/"
                   "temporary-protection-for-ukrainian-citizens-and-their-family-members",
     "source_title": "Полиция и погранслужба Эстонии (PPA)",
     "source_quote": "Under this restriction, temporary protection is not granted to persons who do "
                     "not have a lawful basis for leaving Ukraine due to military service obligations."},
    {"country": "Эстония", "section": "protection", "kind": "norm",
     "title": "Защита, запрошенная с 14.08.2026, действует до марта 2028",
     "source_url": "https://www.politsei.ee/en/instructions/information-on-the-war-in-ukraine/"
                   "temporary-protection-for-ukrainian-citizens-and-their-family-members",
     "source_title": "Полиция и погранслужба Эстонии (PPA)",
     "source_quote": "Temporary protection applied for from August 14, 2026, is valid until March 2028."},

    # --- Швеция
    {"country": "Швеция", "section": "protection", "kind": "norm",
     "applies_to": "мужчины 23–65, первое заявление с 05.08.2026",
     "title": "Мужчины 23–65 доказывают исполнение воинских обязанностей или освобождение",
     "body": "Возрастная граница в Швеции — до 65 лет, а не до 60.",
     "source_url": "https://www.migrationsverket.se/nyheter/news-archive/2026-09-10-stricter-rules-"
                   "when-the-temporary-protection-directive-is-extended.html",
     "source_title": "Migrationsverket (10.09.2026)",
     "source_quote": "If you are a man aged 23 to 65, you must show that you have fulfilled your "
                     "military obligations or that you are exempt from the compulsory military "
                     "service requirement in Ukraine."},
    {"country": "Швеция", "section": "protection", "kind": "norm",
     "title": "Доказательства: штамп о выезде не старше 90 дней, е-ВОД (документ из Резерв+) или другой официальный документ",
     "source_url": "https://www.migrationsverket.se/nyheter/news-archive/2026-09-10-stricter-rules-"
                   "when-the-temporary-protection-directive-is-extended.html",
     "source_title": "Migrationsverket (10.09.2026)",
     "source_quote": "You can show that you meet the requirement, for example, by providing a "
                     "passport with a valid exit stamp (the stamp must not be more than 90 days "
                     "old), a certificate from the Reserv+ app (known as an e-VOD), or other "
                     "official documents showing that you are entitled to leave Ukraine."},
    {"country": "Швеция", "section": "protection", "kind": "norm",
     "title": "Разрешения, выданные до 05.08.2026, действуют до 04.03.2027; продление — в начале 2027",
     "source_url": "https://www.migrationsverket.se/nyheter/news-archive/2026-09-10-stricter-rules-"
                   "when-the-temporary-protection-directive-is-extended.html",
     "source_title": "Migrationsverket (10.09.2026)",
     "source_quote": "If you received a decision on a residence permit before 5 August 2026, your "
                     "permit is valid until 4 March 2027, and you will have the opportunity to "
                     "apply for an extension of your residence permit at the beginning of 2027."},
    {"country": "Швеция", "section": "residence", "kind": "norm",
     "title": "С 11.06.2026 можно подать на другой вид на жительство, не выезжая из Швеции",
     "source_url": "https://www.migrationsverket.se/nyheter/news-archive/2026-06-11-new-rules-for-"
                   "people-from-ukraine-from-11-june.html",
     "source_title": "Migrationsverket (11.06.2026)",
     "source_quote": "From 11 June 2026, if you have a residence permit under the Temporary "
                     "Protection Directive, you can apply for another type of residence permit "
                     "without having to leave Sweden."},

    # --- Австрия
    {"country": "Австрия", "section": "protection", "kind": "report",
     "title": "Право на пребывание — до 04.03.2028, новую «синюю карту» присылают автоматически",
     "body": "По данным ÖIF, новую карту присылают по зарегистрированному адресу — при переезде "
             "его меняют в Meldeamt. Право действует и до получения новой карты.",
     "source_url": "https://www.integrationsfonds.at/ukraine/",
     "source_title": "ÖIF (Австрийский интеграционный фонд)",
     "source_quote": "Bei Verlängerung des Aufenthaltsrechts wird allen bereits registrierten "
                     "Vertriebenen mit aufrechtem Wohnsitz in Österreich automatisch ein neuer "
                     "Ausweis mit verlängertem Gültigkeitsdatum zugesendet."},
    {"country": "Австрия", "section": "residence", "kind": "report",
     "title": "С картой переселенца нельзя сразу перейти на «Daueraufenthalt – EU»",
     "source_url": "https://www.integrationsfonds.at/ukraine/",
     "source_title": "ÖIF (Австрийский интеграционный фонд)",
     "source_quote": "Mit dem Ausweis für Vertriebene („Blaue Karte“) ist dagegen ein direkter "
                     "Umstieg auf „Daueraufenthalt – EU“ nicht möglich."},

    # --- Франция
    {"country": "Франция", "section": "protection", "kind": "report",
     "title": "Адвокат: разрешение APS действует 6 месяцев и продлевается в префектуре; с 01.05.2026 — пошлина 100 €",
     "body": "Разбор адвоката со ссылками на Кодекс о въезде и страницу префектуры полиции Парижа.",
     "source_url": "https://kohenavocats.com/protection-temporaire-ukrainiens-prolongation-2028-restrictions-renouvellement-recours/",
     "source_title": "Адвокат Hassan Kohen, Париж (27.09.2026)",
     "source_quote": "Depuis le 1er mai 2026, un timbre fiscal de 100 euros est perçu à partir du "
                     "deuxième renouvellement"},
    {"country": "Франция", "section": "protection", "kind": "report",
     "title": "Адвокат: при перерыве в защите человека могут считать новым заявителем",
     "source_url": "https://kohenavocats.com/protection-temporaire-ukrainiens-prolongation-2028-restrictions-renouvellement-recours/",
     "source_title": "Адвокат Hassan Kohen, Париж (27.09.2026)",
     "source_quote": "La continuité du statut, sans interruption, devient ainsi une condition à "
                     "protéger jalousement"},
    {"country": "Франция", "section": "protection", "kind": "report",
     "title": "Адвокат: пока действует защита в другой стране ЕС, префектура может отказать в продлении",
     "source_url": "https://kohenavocats.com/protection-temporaire-ukrainiens-prolongation-2028-restrictions-renouvellement-recours/",
     "source_title": "Адвокат Hassan Kohen, Париж (27.09.2026)",
     "source_quote": "tant qu’un titre actif subsiste dans un autre État membre, la préfecture "
                     "française peut refuser"},
    {"country": "Франция", "section": "residence", "kind": "report",
     "title": "Адвокат: на обычный вид на жительство можно подать без долгосрочной визы",
     "source_url": "https://kohenavocats.com/protection-temporaire-ukrainiens-prolongation-2028-restrictions-renouvellement-recours/",
     "source_title": "Адвокат Hassan Kohen, Париж (27.09.2026)",
     "source_quote": "Il est possible de demander dès à présent un titre de séjour sans visa long "
                     "séjour préalable"},
    {"country": "Франция", "section": "court", "kind": "report",
     "title": "Адвокат: отказ в продлении оспаривают в административном суде; он советует не тянуть",
     "source_url": "https://kohenavocats.com/protection-temporaire-ukrainiens-prolongation-2028-restrictions-renouvellement-recours/",
     "source_title": "Адвокат Hassan Kohen, Париж (27.09.2026)",
     "source_quote": "chaque refus doit être contesté vite, devant le tribunal administratif."},

    # --- Ирландия
    {"country": "Ирландия", "section": "protection", "kind": "norm",
     "title": "С 05.08.2026 защиту дают, только если выезд из Украины был разрешён по закону Украины",
     "source_url": "https://www.irishimmigration.ie/information-on-temporary-protection-for-people-fleeing-the-conflict-in-ukraine/",
     "source_title": "Immigration Service Delivery (Минюст Ирландии)",
     "source_quote": "Temporary Protection will only be granted to applicants who can demonstrate "
                     "that they were authorised under Ukrainian law to leave the territory of Ukraine."},
    {"country": "Ирландия", "section": "protection", "kind": "norm",
     "title": "Получившим защиту до 05.08.2026 дополнительные доказательства не нужны",
     "source_url": "https://www.irishimmigration.ie/information-on-temporary-protection-for-people-fleeing-the-conflict-in-ukraine/",
     "source_title": "Immigration Service Delivery (Минюст Ирландии)",
     "source_quote": "You are not required to provide additional evidence that you were authorised "
                     "to leave Ukraine in accordance with Ukrainian law."},
    {"country": "Ирландия", "section": "residence", "kind": "norm",
     "title": "Новый статус Stamp 4 «Temporary Protection Transition Scheme» — на 2 года с продлением",
     "body": "Приём заявлений планировался с сентября 2026. Держать одновременно защиту и "
             "этот статус нельзя.",
     "source_url": "https://www.gov.ie/en/department-of-justice-home-affairs-and-migration/collections/a-new-permission-en/",
     "source_title": "Минюст Ирландии (обновлено 05.08.2026)",
     "source_quote": "It will be granted for a period of up to two years and renewable for periods "
                     "of two years thereafter."},

    # --- Италия
    {"country": "Италия", "section": "protection", "kind": "report",
     "title": "Разрешения по временной защите продлены декретом 201/2025 до 04.03.2027; акт на 2028 пока не найден",
     "source_url": "https://help.unhcr.org/italy/forms-of-protection-in-italy/temporary-protection/",
     "source_title": "УВКБ ООН в Италии",
     "source_quote": "201/2025 until 4 March 2027 , in line with decisions taken by the European Union."},
    {"country": "Италия", "section": "residence", "kind": "report",
     "title": "Разрешение по временной защите можно перевести в рабочее, если выполнены требования закона",
     "source_url": "https://help.unhcr.org/italy/forms-of-protection-in-italy/temporary-protection/",
     "source_title": "УВКБ ООН в Италии",
     "source_quote": "Yes, a temporary protection residence permit can be converted into a work "
                     "permit, provided the legal requirements are met"},

    # --- Польша (официально)
    {"country": "Польша", "section": "protection", "kind": "norm",
     "title": "Условие о воинских обязанностях не касается тех, у кого защита в Польше была на 04.08.2026 (или раньше) и не прерывалась",
     "source_url": "https://www.gov.pl/web/udsc/przedluzenie-ochrony-czasowej-do-4-marca-2028-r",
     "source_title": "Управление по делам иностранцев Польши (UDSC)",
     "source_quote": "Ograniczenia dotyczącego wypełnienia obowiązków wojskowych nie stosuje się do "
                     "osób korzystających z tymczasowej ochrony w danym państwie członkowskim przed "
                     "4 sierpnia 2026 r. oraz w tym dniu, i nieprzerwanie zachowujących status "
                     "ochronny w tym państwie członkowskim."},

    # --- Словакия (официально через пресс-службу)
    {"country": "Словакия", "section": "protection", "kind": "report",
     "applies_to": "мужчины 18–60, новые и повторные заявления с 06.08.2026",
     "title": "18–22: достаточно регистрации в Резерв+; 23–60: нужна запись об освобождении или снятии с учёта",
     "body": "Документ из Резерв+ — с официальным переводом на словацкий или английский; "
             "полиция может попросить показать профиль в приложении вживую.",
     "source_url": "https://www.topky.sk/cl/10/9467657/Docasne-utocisko-sa-meni--Muzi-z-Ukrajiny-musia-po-novom-splnit-novu-podmienku",
     "source_title": "Topky.sk со слов пресс-службы полиции",
     "source_quote": "Muži vo veku 23 až 60 rokov musia preukázať záznam o vyradení z plnenia "
                     "vojenských záväzkov, teda skutočné oslobodenie alebo vyradenie z evidencie."},
    {"country": "Словакия", "section": "protection", "kind": "report",
     "title": "е-ВОД нужен в официальном переводе на словацкий (или английский)",
     "source_url": "https://www.topky.sk/cl/10/9467657/Docasne-utocisko-sa-meni--Muzi-z-Ukrajiny-musia-po-novom-splnit-novu-podmienku",
     "source_title": "Topky.sk со слов пресс-службы полиции",
     "source_quote": "doklad z aplikácie „Reserv+“ musí byť predložený v úradnom preklade do "
                     "slovenského jazyka, pričom akceptovaný je aj úradný preklad do anglického jazyka."},

    # --- Румыния
    {"country": "Румыния", "section": "protection", "kind": "report",
     "title": "Сообщения об изъятии паспортов и обязательном Резерв+ официально не подтверждены",
     "body": "Люди в соцсетях пишут, что 05.08.2026 у мужчин, перешедших границу вне пунктов "
             "пропуска, изымали паспорта. По проверке СтопКор: на официальных ресурсах Румынии и у УВКБ "
             "ООН такого правила нет. Нужны свидетельства людей.",
     "source_url": "https://www.stopcor.org/ukr/section-uanews/news-pasporti-ta-rezerv-scho-vidomo-"
                   "pro-novi-perevirki-ukraintsiv-u-rumunii-06-08-2026.html",
     "source_title": "СтопКор",
     "source_quote": "Повідомлення про масове скасування вже оформленого захисту, обов’язковий "
                     "\"Резерв+\" та загальне вилучення паспортів у Румунії наразі не мають достатнього "
                     "офіційного підтвердження."},

    # --- Швейцария (официально)
    {"country": "Швейцария", "section": "protection", "kind": "norm",
     "title": "Статус S продлён до марта 2028; для новых — только при соблюдении воинских обязанностей",
     "body": "Решение Федерального совета 19.08.2026. Касается прежде всего призывного возраста, "
             "резервистов и добровольцев ВСУ; тех, у кого статус S уже есть, не касается.",
     "source_url": "https://www.admin.ch/de/newnsb/hFEfxUQEMQFg",
     "source_title": "Федеральный совет Швейцарии",
     "source_quote": "Die Einschränkung betrifft insbesondere ukrainische Personen im "
                     "wehrdienstpflichtigen Alter, Personen auf der Reservistenliste und Personen, "
                     "die sich freiwillig den Streitkräften angeschlossen haben."},

    # --- Великобритания
    {"country": "Великобритания", "section": "residence", "kind": "norm",
     "title": "Ukraine Permission Extension: ещё 24 месяца; подавать можно в последние 90 дней перед окончанием",
     "body": "Касается тех, кто получил первые 18 месяцев по UPE. Великобритания не в ЕС: "
             "временной защиты ЕС там нет, есть свои схемы для украинцев.",
     "source_url": "https://assets.publishing.service.gov.uk/media/6a22a27456e988a798b386d5/"
                   "Ukraine_Permission_Extension_Scheme.pdf",
     "source_title": "Home Office, руководство по UPE (08.06.2026)",
     "source_quote": "From 8 April 2026, applicants who were granted an initial 18‑month period of "
                     "permission under the UPE scheme and are within 90 days of that permission "
                     "expiring may apply for an additional 24‑month period of permission under the scheme."},

    # --- Молдова
    {"country": "Молдова", "section": "protection", "kind": "report",
     "title": "Временная защита в Молдове — до 01.03.2027",
     "body": "Молдова не в ЕС; решение 2026/1912 на неё не распространяется.",
     "source_url": "https://help.unhcr.org/moldova/temporary-protection/",
     "source_title": "УВКБ ООН в Молдове",
     "source_quote": "Remain on the territory of the Republic of Moldova for a limited period of "
                     "time (until 01.03.2027)"},

    # --- вне ЕС (из посева 25.09)
    {"country": "Норвегия", "section": "protection", "kind": "report",
     "applies_to": "мужчины 18–60, заявления с 05.05.2026",
     "title": "Мужчин 18–60, как правило, больше не берут под коллективную защиту — их заявление рассматривают как просьбу об убежище",
     "body": "Норвегия ввела это раньше решения ЕС.",
     "source_url": "https://www.utrop.no/nyheter/nytt/386263/", "source_title": "Utrop",
     "source_quote": "Menn mellom 18 og 60 år som søker om beskyttelse i Norge fra og med "
                     "5. mai 2026 , er som hovedregel ikke lenger omfattet av ordningen med "
                     "midlertidig kollektiv beskyttelse. De får i stedet asylsøknaden "
                     "vurdert individuelt."},
    {"country": "Швейцария", "section": "protection", "kind": "report",
     "title": "Статус S для новых заявлений с 20.08.2026 — с условием о воинских обязанностях",
     "body": "Уже получивших статус S не касается.",
     "source_url": "https://www.pravda.com.ua/eng/news/2026/08/19/8049319/",
     "source_title": "Украинская правда",
     "source_quote": "The restrictions will not apply to Ukrainian citizens who have already "
                     "been granted S status. The new provision applies to all new "
                     "applications submitted from 20 August 2026."},
    {"country": "Исландия", "section": "protection", "kind": "report",
     "title": "Воинские обязанности могут быть «независимо от возраста и пола» — так пишут юристы о новых правилах Исландии",
     "source_url": "https://eiglaw.com/iceland-issues-new-rules-for-collective-protection-applications/",
     "source_title": "EIG Law",
     "source_quote": "Ukrainian citizens who are not subject to conscription may nevertheless "
                     "have obligations relating to military service, regardless of age or gender."},
    {"country": "Дания", "section": "protection", "kind": "report",
     "applies_to": "мужчины 23–60 без освобождения",
     "title": "С 25.06.2026 мужчины 23–60 без освобождения не получают вид на жительство по спецзакону",
     "body": "Дания не участвует в решении ЕС о временной защите и ввела своё правило.",
     "source_url": "https://www.eurointegration.com.ua/news/2026/06/25/7240442/",
     "source_title": "Европейская правда",
     "source_quote": "чоловіки віком 23–60 років, які не звільнені від військової служби, "
                     "більше не зможуть отримати дозвіл на проживання в Данії."},
]


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


async def run(url, check=True, replace=False):
    db.DATABASE_URL = url
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        problem = await conn.fetchval(
            "SELECT id FROM nodes WHERE kind = 'problem' AND title = $1 ORDER BY id LIMIT 1",
            SECTOR["problem_title"])
    if problem is None:
        raise SystemExit(f"нет обсуждения «{SECTOR['problem_title']}» — сначала посев rezerv_plus")
    # сведения — слой обсуждения; строка info_sectors осталась только как
    # адрес старых ссылок /info.html?s=ua-eu
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO info_sectors (slug, title, problem_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (slug) DO UPDATE SET problem_id = EXCLUDED.problem_id",
            SECTOR["slug"], SECTOR["title"], problem)
    aid = await _author()
    if replace:
        # Пересев после правки текстов: снимаем прежний посев (его автор —
        # служебный Claude) и тестовые «ТЕСТ:» записи; сведения живых людей
        # не трогаем. Снятие — пометкой, событие пишется как обычно.
        async with pool.acquire() as conn:
            # «Масштаб» — строки, перенесённые из карточки проблемы (посев 25.09):
            # этот посев их не заводил и не снимает
            gone = await conn.fetch(
                "UPDATE info_facts SET deleted_at = now() WHERE topic_root_id = $1 "
                "AND deleted_at IS NULL AND ((author_id = $2 AND section <> $3) "
                "OR title LIKE 'ТЕСТ%') RETURNING id", problem, aid, info_db.SCALE_SECTION)
            for r in gone:
                await db._log(conn, "info_fact_removed", {"id": r["id"], "reason": "reseed"})
        print(f"снято перед пересевом: {len(gone)}")
    added = skipped = 0
    stats = {}
    for f in COMMON + COUNTRIES:
        if await info_db.find_fact(problem, f["title"], f.get("country")):
            skipped += 1
            continue
        status = "none"
        if f.get("source_url") and f.get("source_quote"):
            status = await asyncio.to_thread(info_db.check_quote, f["source_url"],
                                             f["source_quote"]) if check else "unchecked"
        data = dict(f)
        if data["kind"] == "norm" and status != "verified":
            data["kind"] = "report"
        await info_db.add_fact(problem, data, aid, quote_status=status,
                               checked_at=datetime.now(timezone.utc) if status != "none" else None,
                               limit=False)
        stats[status] = stats.get(status, 0) + 1
        if status == "mismatch":
            print(f"  ✗ цитата не найдена: {f.get('country') or 'общее'} — {f['title'][:70]}")
        added += 1
    print(f"обсуждение #{problem}: добавлено {added}, "
          f"уже было {skipped}; сверка: {stats}")
    await db.close_pool()


def main():
    ap = argparse.ArgumentParser(description="Посев: украинцы в ЕС — сведения")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--no-check", action="store_true", help="не сверять цитаты (без сети)")
    ap.add_argument("--replace", action="store_true",
                    help="снять прежний посев и тестовые записи, залить заново")
    a = ap.parse_args()
    if "opmap" in a.url:
        raise SystemExit("отказ: в синтетическую базу реальную тему не сеем")
    asyncio.run(run(a.url, check=not a.no_check, replace=a.replace))


if __name__ == "__main__":
    main()

"""
Посев: причины войны России и Украины — только по первичным документам.

    python -m tools.seed.war_causes          # в стенд студии (noosphere_studio, :5433)

Alex 25.09.2026: «полномасштабное исследование о причинах конфликта Украины и
России. Опираться только на сухие факты, законы, уголовные расследования,
передача оружия и т.д. Не учитывать посты в соцсетях и новостные ленты».

Правила этого посева:
  - только первичные документы: тексты договоров и законов (базы законодательства
    Украины и России, сайты НАТО и ООН), решения судов (Международный суд ООН, МУС,
    ЕСПЧ, окружной суд Гааги по MH17, Конституционный суд Украины), резолюции ООН,
    официальные доклады ООН и Совета Европы, официальные отчёты о поставках оружия
    (Госдеп США, MSMT) и исследовательская база данных (Кильский институт);
  - заявленные причины каждой стороны — как документ «кто что официально заявил»,
    с формулировкой «заявлено», а не как установленный факт;
  - каждая ссылка открыта 25.09.2026, выдержка — дословно, на языке оригинала;
  - что не удалось проверить по первоисточнику (решение ВС РФ по «Азову»,
    проект договора РФ–НАТО 17.12.2021, передача иранских БПЛА) — вопросами;
  - автор — «Claude (ИИ)» (feedback_seed_author_is_ai), людей в позициях нет;
  - вопросы — «да/нет»: ответ «за» читается как «да», «против» — как «нет».
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db  # noqa: E402
from tools.seed import rezerv_plus as base  # noqa: E402

RADA = "https://zakon.rada.gov.ua/laws/show/"

WAR = {
    "title": "Война России и Украины: причины по документам",
    "statement":
        "С 2014 года между Россией и Украиной идёт вооружённый конфликт, с 24 февраля "
        "2022 года — полномасштабная война. Стороны называют разные причины: "
        "расширение НАТО, смена власти в Киеве в 2014 году, права русскоязычных, "
        "«геноцид» на Донбассе, нарушение территориальной целостности Украины. Здесь "
        "собраны только первичные документы — договоры, законы, решения судов, "
        "резолюции ООН, официальные доклады, отчёты о поставках оружия, — чтобы "
        "каждый мог проверить, какие из заявленных причин они подтверждают, а какие нет.",
    "causes":
        "Заявленные стороной России (обращение 24.02.2022): расширение НАТО на восток, "
        "«геноцид» на Донбассе, «демилитаризация и денацификация». Заявленные стороной "
        "Украины и большинством членов ООН: нарушение Россией территориальной "
        "целостности Украины (резолюции ГА ООН 68/262, ES-11/1, ES-11/4). Документы "
        "по каждой — в вопросах ниже.",
    "gap":
        "Не собрано по первоисточникам: текст решения Верховного суда РФ о «Азове» "
        "(02.08.2022), официальный текст проекта договора РФ–НАТО от 17.12.2021 (сайт "
        "МИД РФ недоступен для проверки), документы переговоров в Стамбуле (2022), "
        "распределение жертв среди мирных жителей 2014–2021 по сторонам линии "
        "соприкосновения, решение Международного суда ООН по существу дела о "
        "геноциде. Ждут и решения по встречным требованиям России.",
    "author_idx": 0,
    "domain": "politics", "sub": "Международные отношения",
    "geo": ["Украина", "Россия"],
    "tags": ["война", "международное право", "НАТО", "Донбасс", "Крым", "MH17"],
    "scale": [
        {"region": "Украина, 14.04.2014–31.12.2021 (ООН)",
         "figure": "14 200–14 400 погибших, из них не менее 3 404 мирных жителей "
                   "(включая 298 человек на борту MH17)",
         "source_url": "https://ukraine.un.org/sites/default/files/2022-02/Conflict-related%20"
                       "civilian%20casualties%20as%20of%2031%20December%202021%20%28rev%2027%20"
                       "January%202022%29%20corr%20EN_0.pdf",
         "source_excerpt":
             "OHCHR estimates the total number of conflict-related casualties in Ukraine from "
             "14 April 2014 to 31 December 2021 to be 51,000–54,000: 14,200-14,400 killed (at "
             "least 3,404 civilians, estimated 4,400 Ukrainian forces, and estimated 6,500 "
             "members of armed groups)"},
        {"region": "Украина, 24.02.2022–февраль 2026 (ООН)",
         "figure": "Более 15 000 мирных жителей погибли, более 41 000 ранены",
         "source_url": "https://ukraine.ohchr.org/sites/default/files/2026-02/2026-02-16%20"
                       "HRMMU_Four%20Years%20On_fact%20sheet_2.pdf",
         "source_excerpt":
             "Since the Russian Federation launched its full-scale invasion in February 2022, "
             "four years of hostilities have killed more than 15,000 civilians and injured "
             "over 41,000"},
        {"region": "США → Украина (Госдепартамент США)",
         "figure": "$66,9 млрд военной помощи с 24.02.2022; около $69,7 млрд с 2014 года",
         "source_url": "https://www.state.gov/bureau-of-political-military-affairs/releases/"
                       "2025/01/u-s-security-cooperation-with-ukraine",
         "source_excerpt":
             "we have provided $66.9 billion in military assistance since Russia launched its "
             "full-scale invasion of Ukraine on February 24, 2022, and approximately $69.7 "
             "billion in military assistance since Russia’s initial invasion of Ukraine in 2014."},
        {"region": "Европа → Украина (Кильский институт, база данных)",
         "figure": "В 2025 году военная помощь Европы на 67% выше среднего за 2022–2024",
         "source_url": "https://www.kielinstitut.de/publications/news/ukraine-support-after-4-"
                       "years-of-war-europe-steps-up/",
         "source_excerpt":
             "European military aid rose by 67 percent above the 2022–2024 average, while "
             "non-military aid increased by 59 percent."},
        {"region": "КНДР → Россия (MSMT, 11 государств)",
         "figure": "До 9 млн снарядов артиллерии и РСЗО в 2024 году; баллистические "
                   "ракеты, боевые машины; обучение войск КНДР в России",
         "source_url": "https://msmt.info/view/save/2025/05/29/1085cade-a4b1-4405-94c0-"
                       "7c980c24fd21-Unlawful_Military_Cooperation_including_Arms_Transfers_"
                       "between_North_Korea_and_Russia_(MSMT_2025_1).pdf",
         "source_excerpt":
             "Russian-flagged cargo vessels delivered as many as 9 million rounds of mixed "
             "artillery and multiple rocket launcher ammunition from the DPRK to Russia in 2024."},
    ],
    "interventions": [
        {"what": "Будапештский меморандум: Россия, Великобритания и США обязуются "
                 "воздерживаться от угрозы силой против территориальной целостности "
                 "Украины (в обмен на отказ Украины от ядерного оружия)",
         "actor": "Россия, Великобритания, США, Украина", "geo": "Украина",
         "when_text": "05.12.1994",
         "outcome": "16.03.2014 в Крыму проведён референдум; ГА ООН постановила, что он не "
                    "имеет законной силы (68/262).",
         "outcome_kind": "failure",
         "source_url": RADA + "998_158",
         "source_excerpt":
             "Російська Федерація, Сполучене Королівство Великої Британії та Північної "
             "Ірландії і Сполучені Штати Америки підтверджують їх зобов'язання утримуватися "
             "від загрози силою чи її використання проти територіальної цілісності чи "
             "політичної незалежності України"},
        {"what": "Договор о дружбе, сотрудничестве и партнёрстве Украины и России: "
                 "взаимное уважение территориальной целостности и нерушимости границ (ст. 2)",
         "actor": "Украина и Россия", "geo": "Украина", "when_text": "31.05.1997",
         "outcome": "Нарушение территориальной целостности Украины констатировано ГА ООН "
                    "(68/262, ES-11/4).",
         "outcome_kind": "failure",
         "source_url": RADA + "643_006/print",
         "source_excerpt":
             "поважають територіальну цілісність одна одної і підтверджують непорушність "
             "існуючих між ними кордонів"},
        {"what": "«Комплекс мер» по выполнению Минских соглашений, одобрен резолюцией "
                 "СБ ООН 2202 единогласно",
         "actor": "Трёхсторонняя контактная группа; СБ ООН", "geo": "Украина",
         "when_text": "12.02.2015 / 17.02.2015",
         "outcome": "21.02.2022 Россия объявила о признании ДНР и ЛНР, 24.02.2022 — о "
                    "начале военной операции.",
         "outcome_kind": "failure",
         "source_url": "https://www.securitycouncilreport.org/atf/cf/%7B65BFCF9B-6D27-4E9C-"
                       "8CD3-CF6E4FF96FF9%7D/s_res_2202.pdf",
         "source_excerpt":
             "Endorses the “Package of measures for the Implementation of the Minsk "
             "Agreements”, adopted and signed in Minsk on 12 February 2015 (Annex I)"},
        {"what": "Приказ Международного суда ООН о временных мерах: Россия должна "
                 "немедленно приостановить военные операции (13 голосов против 2)",
         "actor": "Международный суд ООН", "geo": "Украина", "when_text": "16.03.2022",
         "outcome": "Военные действия продолжаются (данные ООН о жертвах — в масштабе).",
         "outcome_kind": "failure",
         "source_url": "https://www.icj-cij.org/node/106135",
         "source_excerpt":
             "The Russian Federation shall immediately suspend the military operations that "
             "it commenced on 24 February 2022 in the territory of Ukraine"},
        {"what": "Решение Международного суда ООН (Украина против России, конвенции о "
                 "финансировании терроризма и о расовой дискриминации)",
         "actor": "Международный суд ООН", "geo": "Украина, Крым", "when_text": "31.01.2024",
         "outcome": "Россия нарушила обязанность расследовать (ст. 9 МКБФТ) и нарушила "
                    "КЛРД в школьном образовании на украинском языке в Крыму; остальные "
                    "требования Украины отклонены.",
         "outcome_kind": "partial",
         "source_url": "https://www.icj-cij.org/node/203486",
         "source_excerpt":
             "Finds that the Russian Federation, by the way in which it has implemented its "
             "educational system in Crimea after 2014 with regard to school education in the "
             "Ukrainian language, has violated its obligations under Articles 2, paragraph 1 "
             "(a), and 5 (e) (v) of the International Convention on the Elimination of Racial "
             "Discrimination"},
        {"what": "Дело о геноциде (Украина против России): суд вправе решить, совершала "
                 "ли Украина геноцид на Донбассе; Россия подала встречные требования",
         "actor": "Международный суд ООН", "geo": "Украина", "when_text": "02.02.2024; 18.11.2024",
         "outcome": "Решения по существу нет. Требование Украины признать применение силы "
                    "Россией нарушением конвенции суд к рассмотрению не принял.",
         "outcome_kind": "unclear",
         "source_url": "https://www.icj-cij.org/node/203516",
         "source_excerpt":
             "The Court finds that it has jurisdiction to entertain Ukraine’s request for a "
             "declaration that it did not breach its obligations under the Convention on the "
             "Prevention and Punishment of the Crime of Genocide, and that this request is admissible"},
        {"what": "ЕСПЧ, Большая палата: «Украина и Нидерланды против России» (восток "
                 "Украины с 2014 года и MH17)",
         "actor": "Европейский суд по правам человека", "geo": "Украина", "when_text": "09.07.2025",
         "outcome": "Россия ответственна за многочисленные нарушения на протяжении более "
                    "восьми лет и за нарушение права на жизнь при уничтожении MH17.",
         "outcome_kind": "success",
         "source_url": "https://www.echr.coe.int/w/grand-chamber-judgment-in-an-inter-state-case-1",
         "source_excerpt":
             "The Court also found that Russia was responsible for violating the right to life "
             "by shooting down flight MH17"},
        {"what": "ЕСПЧ: «Вячеславова и другие против Украины» (Одесса, 2 мая 2014 года)",
         "actor": "Европейский суд по правам человека", "geo": "Украина, Одесса",
         "when_text": "13.03.2025",
         "outcome": "Украина нарушила право на жизнь: власти не предотвратили и не "
                    "остановили насилие, не обеспечили спасение, не расследовали эффективно.",
         "outcome_kind": "success",
         "source_url": "https://hudoc.echr.coe.int/app/conversion/pdf/?library=ECHR&id=003-"
                       "8180839-11477923&filename=Judgment+Vyacheslavova+and+Others+v.+Ukraine"
                       "+-+State+negligence+in+clashes+between+Maidan+supporters+and+opponents"
                       "+in+Odesa+in+May+2014.pdf",
         "source_excerpt":
             "the relevant authorities’ failure to do everything that could reasonably be "
             "expected of them to prevent the violence in Odesa on 2 May 2014, to stop that "
             "violence after its outbreak, to ensure timely rescue measures for people trapped "
             "in the fire, and to institute and conduct an effective investigation into the events"},
        {"what": "Суд по делу MH17 (Нидерланды): приговор троим обвиняемым",
         "actor": "Окружной суд Гааги", "geo": "Украина, Донецкая область",
         "when_text": "17.11.2022",
         "outcome": "Гиркин, Дубинский, Харченко — пожизненное заключение; Пулатов оправдан.",
         "outcome_kind": "partial",
         "source_url": "https://www.courtmh17.com/en/summaries-and-news/news/summary-of-the-"
                       "day-in-court-17-november-2022-judgment.htm",
         "source_excerpt":
             "Today the Court sentenced the accused Kharchenko, Dubinskiy and Girkin to life "
             "imprisonment for causing Flight MH17 to crash and for the murder of the 298 "
             "persons on board. Defendant Pulatov has been acquitted."},
        {"what": "Ордера Международного уголовного суда на арест В. Путина и М. "
                 "Львовой-Беловой (депортация детей)",
         "actor": "Международный уголовный суд", "geo": "Украина", "when_text": "17.03.2023",
         "outcome": "Ордера выданы.",
         "outcome_kind": "unclear",
         "source_url": "https://www.icc-cpi.int/news/situation-ukraine-icc-judges-issue-arrest-"
                       "warrants-against-vladimir-vladimirovich-putin-and",
         "source_excerpt":
             "is allegedly responsible for the war crime of unlawful deportation of population "
             "(children) and that of unlawful transfer of population (children) from occupied "
             "areas of Ukraine to the Russian Federation"},
    ],
    "arguments": [
        # --- 1. территориальная целостность
        {"k": "q-territory", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Нарушила ли Россия свои обязательства уважать территориальную "
                 "целостность Украины?"},
        {"k": "t-68262", "rel": "support", "to": "q-territory", "author_idx": 0,
         "text": "Да, по оценке ГА ООН: резолюция 68/262 (27.03.2014) постановила, что "
                 "референдум в Крыму 16.03.2014 «не имея законной силы, не может служить "
                 "основанием для какого-либо изменения статуса» Крыма и Севастополя: "
                 "https://www.securitycouncilreport.org/atf/cf/%7B65BFCF9B-6D27-4E9C-8CD3-"
                 "CF6E4FF96FF9%7D/a_res_68_262.pdf"},
        {"k": "t-es114", "rel": "support", "to": "q-territory", "author_idx": 0,
         "text": "Да, по оценке ГА ООН: резолюция ES-11/4 (12.10.2022, 143 «за», 5 «против», "
                 "35 воздержались) назвала присоединение Донецкой, Луганской, Херсонской и "
                 "Запорожской областей нарушением территориальной целостности Украины. "
                 "Резолюции ГА ООН не имеют обязательной силы."},
        {"k": "t-sovfed", "rel": "qualify", "to": "q-territory", "author_idx": 0,
         "text": "Правовая основа, заявленная Россией в 2014 году: Совет Федерации 01.03.2014 "
                 "дал согласие «на использование Вооружённых Сил Российской Федерации на "
                 "территории Украины до нормализации общественно-политической обстановки в "
                 "этой стране»: http://council.gov.ru/activity/documents/39979/"},
        {"k": "t-art51", "rel": "qualify", "to": "q-territory", "author_idx": 0,
         "text": "Правовая основа, заявленная Россией 24.02.2022: ст. 51 Устава ООН "
                 "(самооборона), согласие Совета Федерации и договоры с ДНР и ЛНР, "
                 "ратифицированные 22.02.2022: http://en.kremlin.ru/events/president/news/67843 . "
                 "Международный суд ООН 16.03.2022 обязал Россию приостановить операцию "
                 "независимо от заявленного основания."},
        # --- 2. НАТО
        {"k": "q-nato", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Было ли обещание не расширять НАТО на восток — и было ли расширение "
                 "причиной войны?"},
        {"k": "n-putin", "rel": "support", "to": "q-nato", "author_idx": 0,
         "text": "Заявлено Россией как причина: в обращении 24.02.2022 президент РФ назвал "
                 "«расширение НАТО на восток, которое придвигает военную инфраструктуру к "
                 "российской границе»: http://en.kremlin.ru/events/president/news/67843"},
        {"k": "n-archive", "rel": "support", "to": "q-nato", "author_idx": 0,
         "text": "Рассекреченные документы (Архив национальной безопасности США) показывают "
                 "устные заверения западных лидеров Горбачёву в 1990–1991 годах, включая "
                 "«ни на дюйм на восток» госсекретаря Бейкера 09.02.1990: "
                 "https://nsarchive.gwu.edu/briefing-book/russia-programs/2017-12-12/nato-"
                 "expansion-what-gorbachev-heard-western-leaders-early"},
        {"k": "n-art10", "rel": "refute", "to": "q-nato", "author_idx": 0,
         "text": "Письменного обязательства нет: ст. 10 Североатлантического договора "
                 "позволяет приглашать «любое другое европейское государство» по единогласному "
                 "решению, и эта статья не менялась: "
                 "https://www.nato.int/cps/en/natohq/official_texts_17120.htm"},
        {"k": "n-founding", "rel": "qualify", "to": "q-nato", "author_idx": 0,
         "text": "Что было подписано: Основополагающий акт Россия–НАТО (1997) — у НАТО «нет "
                 "намерения, плана и причины» размещать ядерное оружие на территории новых "
                 "членов, оборона — без «дополнительного постоянного размещения существенных "
                 "боевых сил»: https://www.nato.int/cps/en/natohq/official_texts_25468.htm"},
        {"k": "n-bucharest", "rel": "qualify", "to": "q-nato", "author_idx": 0,
         "text": "Бухарестская декларация НАТО (2008): «мы договорились сегодня, что эти "
                 "страны [Украина и Грузия] станут членами НАТО» — без плана и сроков: "
                 "https://www.nato.int/cps/en/natohq/official_texts_8443.htm"},
        {"k": "n-nonbloc", "rel": "qualify", "to": "q-nato", "author_idx": 0,
         "text": "Статус Украины: закон 23.12.2014 №35-VIII отменил внеблоковую политику, "
                 "поправка к Конституции 2019 года (№2680-VIII) закрепила курс на членство в "
                 "ЕС и НАТО: " + RADA + "35-19 , " + RADA + "2680-19"},
        {"k": "q-draft2021", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Кто может дать официальный текст проекта соглашения России с НАТО от "
                 "17.12.2021 (требование исключить расширение, в том числе на Украину)? Сайт "
                 "МИД РФ для проверки недоступен, а пересказы СМИ здесь не принимаются."},
        # --- 3. смена власти 2014
        {"k": "q-2014", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Была ли смена власти в Киеве в феврале 2014 года проведена по Конституции "
                 "Украины?"},
        {"k": "p-assoc", "rel": "qualify", "to": "q-2014", "author_idx": 0,
         "text": "С чего началось: распоряжение Кабмина от 21.11.2013 №905-р «приостановить "
                 "процесс подготовки к заключению Соглашения об ассоциации» с ЕС (отменено "
                 "02.03.2014): " + RADA + "905-2013-р/print"},
        {"k": "p-rada", "rel": "qualify", "to": "q-2014", "author_idx": 0,
         "text": "Постановление Рады от 22.02.2014 №757-VII: «Президент Украины В. Янукович "
                 "самоустранился от выполнения конституционных полномочий», назначены "
                 "досрочные выборы: " + RADA + "757-18/print"},
        {"k": "p-const", "rel": "refute", "to": "q-2014", "author_idx": 0,
         "text": "Ст. 108 Конституции Украины перечисляет основания досрочного прекращения "
                 "полномочий президента: отставка, неспособность по состоянию здоровья, "
                 "импичмент (ст. 111), смерть. Постановление Рады ссылается на «самоустранение», "
                 "которого в этом перечне нет: " + RADA + "254к/96-вр/print"},
        # --- 4. права русскоязычных
        {"k": "q-lang", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Нарушало ли законодательство Украины о языке права русскоязычных и других "
                 "меньшинств?"},
        {"k": "l-vc2019", "rel": "support", "to": "q-lang", "author_idx": 0,
         "text": "Частично да, по оценке Венецианской комиссии (2019): закон о языке «не "
                 "обеспечивает справедливого баланса» между укреплением украинского языка и "
                 "защитой языковых прав меньшинств: https://www.venice.coe.int/webforms/"
                 "documents/default.aspx?pdffile=CDL-AD(2019)032-e"},
        {"k": "l-vc2017", "rel": "support", "to": "q-lang", "author_idx": 0,
         "text": "Венецианская комиссия о законе об образовании (2017): менее благоприятный "
                 "режим для языков, не являющихся языками ЕС, прежде всего русского, «трудно "
                 "оправдать, и потому он порождает вопросы дискриминации»: "
                 "https://www.venice.coe.int/webforms/documents/default.aspx?pdffile=CDL-AD(2017)030-e"},
        {"k": "l-ccu", "rel": "refute", "to": "q-lang", "author_idx": 0,
         "text": "Конституционный суд Украины 14.07.2021 (№1-р/2021) признал закон о языке "
                 "соответствующим Конституции: " + RADA + "v001p710-21/print"},
        {"k": "l-crimea", "rel": "qualify", "to": "q-lang", "author_idx": 0,
         "text": "Обратный случай: Международный суд ООН (31.01.2024) признал, что Россия "
                 "нарушила Конвенцию о расовой дискриминации в школьном образовании на "
                 "украинском языке в Крыму после 2014 года: https://www.icj-cij.org/node/203486"},
        # --- 5. «геноцид»
        {"k": "q-genocide", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Подтверждается ли документами заявление о «геноциде» на Донбассе, "
                 "названное причиной операции 24.02.2022?"},
        {"k": "g-claim", "rel": "support", "to": "q-genocide", "author_idx": 0,
         "text": "Заявлено Россией: цель операции — «защитить людей, которые восемь лет "
                 "подвергаются издевательствам, геноциду со стороны киевского режима»: "
                 "http://en.kremlin.ru/events/president/news/67843"},
        {"k": "g-icj", "rel": "refute", "to": "q-genocide", "author_idx": 0,
         "text": "Международный суд ООН 16.03.2022: суд «не располагает доказательствами, "
                 "подтверждающими утверждение Российской Федерации о том, что на территории "
                 "Украины был совершён геноцид» (предварительная стадия): "
                 "https://www.icj-cij.org/node/106135"},
        {"k": "g-ohchr", "rel": "qualify", "to": "q-genocide", "author_idx": 0,
         "text": "Данные ООН за 14.04.2014–31.12.2021: не менее 3 404 погибших мирных "
                 "жителей за 8 лет по обе стороны линии соприкосновения, включая 298 человек "
                 "на борту MH17. Разбивку по сторонам этот документ не даёт."},
        {"k": "g-pending", "rel": "qualify", "to": "q-genocide", "author_idx": 0,
         "text": "Вопрос по существу ещё не решён: суд принял к рассмотрению требование "
                 "Украины признать, что она не совершала геноцида (02.02.2024), Россия подала "
                 "встречные требования 18.11.2024: https://www.icj-cij.org/node/205126"},
        # --- 6. кто воевал на Донбассе
        {"k": "q-control", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Контролировала ли Россия вооружённые формирования ДНР и ЛНР в 2014–2022 годах?"},
        {"k": "c-court", "rel": "support", "to": "q-control", "author_idx": 0,
         "text": "Суд по делу MH17 (Гаага, 17.11.2022): «многочисленные признаки указывают, "
                 "что с середины мая 2014 года ДНР фактически получала указания от "
                 "Российской Федерации», поэтому конфликт стал международным: "
                 "https://www.courtmh17.com/en/summaries-and-news/news/summary-of-the-day-in-"
                 "court-17-november-2022-judgment.htm"},
        {"k": "c-jit", "rel": "support", "to": "q-control", "author_idx": 0,
         "text": "Совместная следственная группа (08.02.2023): есть «серьёзные признаки», что "
                 "решение о поставке «Бука» сепаратистам принимал президент России; для новых "
                 "обвинений доказательств недостаточно: https://www.prosecutionservice.nl/"
                 "latest/news/2023/02/08/jit-mh17-strong-indications-that-russian-president-"
                 "decided-on-supplying-buk"},
        {"k": "c-denial", "rel": "qualify", "to": "q-control", "author_idx": 0,
         "text": "Позиция России по суду: тот же приговор отмечает, что «Российская Федерация "
                 "и обвиняемые по сей день отрицают такое участие России»."},
        # --- 7. Одесса
        {"k": "q-odesa", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Кто несёт ответственность за гибель людей в Одессе 2 мая 2014 года?"},
        {"k": "o-echr", "rel": "qualify", "to": "q-odesa", "author_idx": 0,
         "text": "ЕСПЧ (13.03.2025) признал ответственность Украины: власти не предотвратили "
                 "и не остановили насилие, не обеспечили спасение людей из горящего здания и "
                 "не провели эффективного расследования. Суд также отметил, что искажение этих "
                 "событий стало инструментом российской пропаганды."},
        # --- 8. военные преступления
        {"k": "q-crimes", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Какие военные преступления установлены официальными органами — и кем "
                 "совершены?"},
        {"k": "w-coi", "rel": "qualify", "to": "q-crimes", "author_idx": 0,
         "text": "Комиссия ООН по Украине (A/HRC/52/62, 2023): российские власти совершили "
                 "многочисленные нарушения, многие из них — военные преступления (убийства, "
                 "удары по мирным жителям, пытки, изнасилования, депортация детей); "
                 "задокументировано и «небольшое число нарушений украинских вооружённых сил», "
                 "включая два эпизода, квалифицируемых как военные преступления: "
                 "https://www.ohchr.org/sites/default/files/documents/hrbodies/hrcouncil/"
                 "coiukraine/A_HRC_52_62_AUV_EN.pdf"},
        {"k": "w-icc", "rel": "qualify", "to": "q-crimes", "author_idx": 0,
         "text": "МУС 17.03.2023 выдал ордера на арест президента РФ и уполномоченной по "
                 "правам ребёнка М. Львовой-Беловой по подозрению в незаконной депортации "
                 "детей (см. реестр)."},
        {"k": "q-azov", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Кто может дать официальный текст решения Верховного суда РФ от "
                 "02.08.2022 о признании «Азова» террористической организацией? Это главный "
                 "документ, на который опирается заявленная цель «денацификации»; пересказы "
                 "СМИ здесь не принимаются."},
        # --- 9. оружие
        {"k": "q-arms", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Кто и в каком объёме поставляет оружие сторонам?"},
        {"k": "a-msmt", "rel": "qualify", "to": "q-arms", "author_idx": 0,
         "text": "Доклад MSMT (11 государств, 29.05.2025): КНДР передавала России "
                 "артиллерию, баллистические ракеты и боевые машины «для использования в "
                 "войне России против Украины», Россия КНДР — системы ПВО; российские силы "
                 "обучали войска КНДР (цифры — в масштабе)."},
        {"k": "a-west", "rel": "qualify", "to": "q-arms", "author_idx": 0,
         "text": "Поставки Украине: США — около $69,7 млрд военной помощи с 2014 года "
                 "(Госдеп), Европа в 2025 году нарастила военную помощь на 67% к среднему "
                 "за 2022–2024 (Кильский институт; цифры — в масштабе)."},
        {"k": "q-iran", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Кто может дать официальный документ ООН с выводами о передаче Ираном "
                 "России БПЛА Shahed? Доклады генсека по резолюции 2231 для проверки пока не "
                 "найдены, заявления сторон здесь не принимаются."},
        # --- 10. Минск
        {"k": "q-minsk", "kind": "question", "rel": "question", "author_idx": 0,
         "text": "Почему не были выполнены Минские соглашения?"},
        {"k": "m-order", "rel": "qualify", "to": "q-minsk", "author_idx": 0,
         "text": "Спорный порядок был заложен в самом тексте: восстановление контроля "
                 "Украины над границей — «начиная с первого дня после местных выборов» и "
                 "после конституционной реформы (п. 9); вывод иностранных вооружённых "
                 "формирований — под наблюдением ОБСЕ (п. 10); конституционная реформа — до "
                 "конца 2015 года (п. 11). Приложение I к резолюции СБ ООН 2202."},
        {"k": "m-end", "rel": "qualify", "to": "q-minsk", "author_idx": 0,
         "text": "21.02.2022 президент РФ объявил о решении «немедленно признать "
                 "независимость и суверенитет» ДНР и ЛНР: "
                 "http://en.kremlin.ru/events/president/news/67828"},
    ],
}


async def run(url):
    db.DATABASE_URL = url
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    pool = db._pool_or_raise()
    name, color = base.SOURCE_AUTHOR
    async with pool.acquire() as conn:
        aid = await conn.fetchval(
            "SELECT id FROM authors WHERE name = $1 AND is_service ORDER BY id LIMIT 1", name)
    if aid is None:
        aid = await db.add_author(name, color)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE authors SET is_service = TRUE WHERE id = $1", aid)
    await base.seed_problem(WAR, [aid])
    await db.close_pool()


def main():
    ap = argparse.ArgumentParser(description="Посев: причины войны России и Украины по документам")
    ap.add_argument("--url", default=base.DEFAULT_URL)
    a = ap.parse_args()
    if "opmap" in a.url:
        raise SystemExit("отказ: в синтетическую базу реальную тему не сеем")
    asyncio.run(run(a.url))


if __name__ == "__main__":
    main()

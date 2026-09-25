"""
Посев: права украинцев за рубежом и воинский учёт (Резерв+ и временная защита).

    python -m tools.seed.rezerv_plus                     # в стенд студии (noosphere_studio, :5433)
    python -m tools.seed.rezerv_plus --url postgresql://…  # в другую базу

Источник — сводка Alex `rezerv-plus-eu.md` (25.09.2026), расширенная до мира по его
слову («делай весь мир»). Каждая ссылка открыта 25.09.2026, выдержка — дословно
со страницы (язык оригинала). Что подтвердить не удалось (пересказы соцсетей),
вынесено вопросами «кто может подтвердить», а не фактами.

Правила — как у первого посева (vault: decisions/seed-problems-first-echelon):
ни одного числа PoI, посевные персоны без входа. Отличия, согласованные 25.09:
  - ДОБАВЛЯЕТ, а не стирает базу; повторный запуск ничего не задваивает
    (проблема с тем же названием уже есть — пропуск);
  - людей в позициях нет: позиции «складываются», пока не придут живые (никакой
    синтетики — посев для настоящих людей);
  - философский слой (Гоббс, Руссо, Радбрух, ЕСПЧ, УВКБ ООН) — вопросами «да/нет»:
    ответ «за» читается как «да», «против» — как «нет»;
  - позиция Alex («закон, гонящий на смерть против воли, не обязывает») НЕ
    сеется — он напишет её сам, позже.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db, opinion_db, taxonomy  # noqa: E402

RETRIEVED = datetime(2026, 9, 25, tzinfo=timezone.utc)
DEFAULT_URL = "postgresql://noosphere@127.0.0.1:5433/noosphere_studio"

PEOPLE = [("Оксана", "#e0b878"), ("Тарас", "#66b0ea"), ("Ирина", "#bf98ff"),
          ("Андрей", "#5fd18f"), ("Мирослава", "#ec6b66"), ("Павел", "#9aa0b0")]

# ---------------------------------------------------------------- мир
WORLD = {
    "key": "world",
    "title": "Права украинцев за рубежом привязаны к воинскому учёту",
    "statement":
        "Украинец за границей всё чаще получает документы, услуги и защиту только "
        "при подтверждении воинского учёта. Консульства Украины по всему миру с "
        "августа 2026 года не обслуживают мужчин 18–60 лет без военно-учётного "
        "документа. Страны, принимающие беженцев, — ЕС, Норвегия, Швейцария, "
        "Исландия, Дания — ставят защиту в зависимость от того, выполнил ли "
        "человек воинские обязанности перед Украиной, в том числе по просьбе "
        "самого Киева. Ошибка в реестре, отсутствие штампа о выезде или "
        "расширительное толкование оставляют человека без паспорта, без статуса "
        "или и без того, и без другого.",
    "causes":
        "Нехватка личного состава армии и мобилизация; запрет выезда мужчин "
        "призывного возраста; просьба Украины к странам-хозяевам проверять "
        "воинский статус; нагрузка на социальные системы стран-хозяев (довод "
        "МВД Чехии).",
    "gap":
        "Не собрано: практика консульств по странам (кому и в чём отказывают, "
        "есть ли обходные пути), данные вне Европы (США, Канада, Великобритания, "
        "Израиль и др.), число отказов вообще. Не решён вопрос, кто отвечает за "
        "ошибку в данных реестра, если на её основании человеку отказали. И "
        "главный — философский: где граница, за которой закон перестаёт обязывать.",
    "author_idx": 0,
    "domain": "politics", "sub": "Миграционная политика",
    "geo": ["Весь мир", "Украина", "Евросоюз", "Норвегия", "Швейцария", "Исландия", "Дания"],
    "tags": ["мобилизация", "консульства", "Резерв+", "права человека", "беженцы"],
    "scale": [
        {"region": "Весь мир (консульства Украины)",
         "figure": "С августа 2026: мужчинам 18–60 без военно-учётного документа "
                   "консульские услуги не оказываются (постановление КМУ №981 от 29.07.2026)",
         "source_url": "https://www.unian.ua/society/mobilizaciya-posolstva-ne-budut-"
                       "obslugovuvati-cholovikiv-bez-viyskovo-oblikovykh-dokumentiv-13459719.html",
         "source_excerpt":
             "Українські консульства за кордоном не зможуть надавати послуги "
             "військовозобов’язаним чоловікам віком від 18 до 60 років без подання "
             "ними військово-облікових документів."},
        {"region": "Норвегия",
         "figure": "С 05.05.2026 мужчины 18–60 выведены из коллективной защиты: "
                   "только индивидуальное рассмотрение убежища",
         "source_url": "https://www.utrop.no/nyheter/nytt/386263/",
         "source_excerpt":
             "Menn mellom 18 og 60 år som søker om beskyttelse i Norge fra og med "
             "5. mai 2026 , er som hovedregel ikke lenger omfattet av ordningen med "
             "midlertidig kollektiv beskyttelse. De får i stedet asylsøknaden "
             "vurdert individuelt."},
        {"region": "Швейцария",
         "figure": "С 20.08.2026 статус S только при соблюдении воинских "
                   "обязанностей перед Украиной; уже получивших не касается",
         "source_url": "https://www.pravda.com.ua/eng/news/2026/08/19/8049319/",
         "source_excerpt":
             "The restrictions will not apply to Ukrainian citizens who have already "
             "been granted S status. The new provision applies to all new "
             "applications submitted from 20 August 2026."},
        {"region": "Исландия",
         "figure": "С 23.08.2026 нужно доказать право на выезд; обязанности могут "
                   "быть «независимо от возраста и пола»",
         "source_url": "https://eiglaw.com/iceland-issues-new-rules-for-collective-"
                       "protection-applications/",
         "source_excerpt":
             "Ukrainian citizens who are not subject to conscription may nevertheless "
             "have obligations relating to military service, regardless of age or gender."},
        {"region": "Дания (вне решения ЕС)",
         "figure": "С 25.06.2026 мужчины 23–60, не освобождённые от службы, не "
                   "получают вид на жительство по спецзакону",
         "source_url": "https://www.eurointegration.com.ua/news/2026/06/25/7240442/",
         "source_excerpt":
             "чоловіки віком 23–60 років, які не звільнені від військової служби, "
             "більше не зможуть отримати дозвіл на проживання в Данії."},
    ],
    "interventions": [
        {"what": "Суд признал незаконной автоматическую верификацию данных в "
                 "реестре военнообязанных (пп. 17 и 17-1 постановления КМУ №932), "
                 "дело №320/31215/25",
         "actor": "Шестой апелляционный административный суд", "geo": "Украина",
         "when_text": "в силе с 16.09.2026",
         "outcome": "Решение вступило в силу; правительство может обжаловать в "
                    "кассации. Для людей за рубежом это первый официальный довод, "
                    "что данные Резерв+ бывают ошибочными не по их вине.",
         "outcome_kind": "partial",
         "conditions": "Касается автоматической постановки на учёт. Процедуры "
                       "исправления ошибочной записи для находящихся за рубежом "
                       "пока нет.",
         "source_url": "https://tck.znaj.ua/564621-sud-skasuvav-avtomatichniy-viyskoviy-"
                       "oblik-perevirte-sviy-status-u-rezerv",
         "source_excerpt":
             "Рішення у справі №320/31215/25 набрало законної сили 16 вересня 2026 року."},
    ],
    # вопросы «да/нет»: «за» = да, «против» = нет
    "arguments": [
        {"k": "q-death", "author_idx": 1, "kind": "question", "rel": "question",
         "text": "Может ли государство требовать от человека идти на смерть против "
                 "его воли?"},
        {"k": "rousseau", "author_idx": 3, "rel": "support", "to": "q-death",
         "text": "Да — так считал Руссо: безопасность, которой человек жил, "
                 "получена от государства на условии. «Его жизнь уже не просто дар "
                 "природы, а дар, данный государством на условии» («Об общественном "
                 "договоре», кн. II, гл. 5): https://www.gutenberg.org/cache/epub/46333/pg46333.txt"},
        {"k": "hobbes", "author_idx": 2, "rel": "refute", "to": "q-death",
         "text": "Нет — даже Гоббс, защитник сильного государства, признавал "
                 "границу: солдат, которому приказано сражаться, «во многих случаях "
                 "может отказаться, не совершая несправедливости», хотя суверен и "
                 "вправе его наказать («Левиафан», гл. 21): "
                 "https://www.gutenberg.org/cache/epub/3207/pg3207.txt"},
        {"k": "hobbes-q", "author_idx": 5, "rel": "qualify", "to": "hobbes",
         "text": "У Гоббса отказ оправдан, в частности, когда человек выставляет "
                 "вместо себя достаточного солдата, — то есть речь о праве не "
                 "умирать самому, а не о праве оставить общину без защиты. Это "
                 "важное различие для спора."},
        {"k": "q-radbruch", "author_idx": 2, "kind": "question", "rel": "question",
         "text": "Остаётся ли законом закон, несправедливый до невыносимой степени?"},
        {"k": "radbruch", "author_idx": 2, "rel": "refute", "to": "q-radbruch",
         "text": "Нет — это формула Радбруха (1946), по которой после войны "
                 "проверяли законы на античеловечность: позитивное право имеет "
                 "приоритет, «разве что противоречие закона справедливости достигает "
                 "столь невыносимой меры, что закон как „неправильное право“ должен "
                 "уступить справедливости»: https://de.wikipedia.org/wiki/Radbruchsche_Formel"},
        {"k": "positivism", "author_idx": 3, "rel": "support", "to": "q-radbruch",
         "text": "Да, пока нет общего суда над ним: та же формула начинается с того, "
                 "что закон имеет приоритет, «даже если он по содержанию несправедлив». "
                 "Если каждый сам решает, где «невыносимо», закон перестаёт быть общим."},
        {"k": "q-objector", "author_idx": 4, "kind": "question", "rel": "question",
         "text": "Должен ли человек, отказавшийся служить по убеждениям, иметь право "
                 "на защиту?"},
        {"k": "bayatyan", "author_idx": 4, "rel": "support", "to": "q-objector",
         "text": "Да: ЕСПЧ в деле «Баятян против Армении» (Большая палата, 7 июля "
                 "2011) признал, что осуждение отказника по убеждениям нарушает "
                 "ст. 9 Конвенции (свобода мысли, совести и религии): "
                 "https://ebco-beoc.org/node/197"},
        {"k": "unhcr", "author_idx": 5, "rel": "qualify", "to": "q-objector",
         "text": "Уточнение из Руководства УВКБ ООН (п. 171): несогласия с "
                 "политическим обоснованием войны недостаточно. Но если военные "
                 "действия осуждены международным сообществом как противоречащие "
                 "основным нормам человеческого поведения, наказание за уклонение "
                 "само может считаться преследованием: https://en.connection-ev.org/article-1415"},
        {"k": "q-men", "author_idx": 4, "kind": "question", "rel": "question",
         "text": "Справедливо ли, что смертельную обязанность несут только мужчины "
                 "определённого возраста?"},
        {"k": "gender", "author_idx": 2, "rel": "refute", "to": "q-men",
         "text": "Нет, и в праве ЕС это спорно: разбор в European Law Blog проверяет "
                 "исключение мужчин из временной защиты на соответствие принципам "
                 "равенства и недискриминации (ст. 20, 21, 23 Хартии ЕС): "
                 "https://europeanlawblog.eu/has-war-remained-mans-matter-mens-exclusion-from-eu/"},
        {"k": "q-registry", "author_idx": 0, "kind": "question", "rel": "question",
         "text": "Кто отвечает за ошибку в данных Резерв+, если на её основании "
                 "человеку отказали в документах или защите за рубежом?"},
        {"k": "q-consulate", "author_idx": 1, "kind": "question", "rel": "question",
         "text": "Что делать, если консульство отказало из-за военно-учётного "
                 "документа? Кто уже прошёл через это — в какой стране и что помогло?"},
    ],
}

# ---------------------------------------------------------------- ЕС
EU = {
    "key": "eu",
    "title": "Резерв+ как условие временной защиты в ЕС",
    "statement":
        "С 31.07.2026 временная защита в ЕС предоставляется новым заявителям только "
        "при условии, что они выполняют воинские обязанности по украинскому "
        "законодательству. На практике ведомства требуют штамп о выезде или "
        "документ из Резерв+, применяют требование шире текста решения (к "
        "женщинам, к молодёжи 18–22, к уже защищённым) и по-разному толкуют "
        "статусы в приложении.",
    "causes":
        "Исполнительное решение Совета (ЕС) 2026/1912; по словам Брюсселя — по "
        "просьбе Киева. Пограничная служба Украины ставит штамп о выезде только "
        "по просьбе человека — у многих законно выехавших его нет. Единых "
        "операционных рекомендаций Еврокомиссии на 25.09.2026 нет.",
    "gap":
        "Нет публичных данных о практике: Франция, Италия, Австрия, Нидерланды, "
        "Ирландия, Португалия, Швеция, Финляндия, Литва, Латвия, Греция, Хорватия, "
        "Словения, Кипр, Мальта, Люксембург. Не опубликованы обещанные "
        "рекомендации Комиссии. Нет ни одного известного решения суда по отказу.",
    "author_idx": 1,
    "domain": "politics", "sub": "Миграционная политика",
    "geo": ["Евросоюз", "Германия", "Чехия", "Испания", "Бельгия", "Словакия",
            "Болгария", "Венгрия", "Польша", "Румыния"],
    "tags": ["временная защита", "Резерв+", "беженцы", "право ЕС"],
    "scale": [
        {"region": "Евросоюз",
         "figure": "Решение 2026/1912: защита продлена до 04.03.2028; с 31.07.2026 "
                   "новым заявителям — только при соблюдении воинских обязанностей; "
                   "у кого защита была на 30.07.2026 и не прерывалась — не касается",
         "source_url": "https://www.weinholdlegal.com/hr-legal-update/temporary-"
                       "protection-for-persons-displaced-from-ukraine-extended-until-4-march-2028",
         "source_excerpt":
             "this rule does not apply to persons who were already benefiting from "
             "temporary protection in the relevant Member State before or on 30 July "
             "2026 and who subsequently maintain such protection without interruption."},
        {"region": "Евросоюз",
         "figure": "1,07 млн украинских мужчин под временной защитой (июнь 2026, "
                   "Eurostat): Германия 324,3 тыс., Польша 155,2, Чехия 130,6, "
                   "Румыния 75,4, Испания 67,1",
         "source_url": "https://www.slovoidilo.ua/2026/08/21/infografika/svit/yaki-krayiny-"
                       "pershymy-obmezhyly-nadannya-tymchasovoho-zaxystu-vijskovozobovyazanym-ukrayincyam",
         "source_excerpt":
             "у країнах ЄС перебуває 1,07 млн українських чоловіків зі статусом "
             "тимчасового захисту"},
        {"region": "Германия",
         "figure": "Мужчины 23–60, прибывшие с 31.07.2026, должны доказать законный "
                   "выезд или освобождение; без оснований пребывания возможна депортация",
         "source_url": "https://zn.ua/ukr/europe/nimechchina-obmezhila-timchasovij-zakhist-"
                       "dlja-ukrajinskikh-cholovikiv-komu-mozhe-zahrozhuvati-deportatsija.html",
         "source_excerpt":
             "Нові правила поширюються на українських чоловіків віком від 23 до 60 "
             "років, які прибули до Німеччини починаючи з 31 липня 2026 року."},
        {"region": "Чехия",
         "figure": "Первичные и повторные заявления, воссоединение семьи — только с "
                   "подтверждением через Резерв+; МВД: новоприбывших станет меньше "
                   "«на десятки процентов»",
         "source_url": "https://news.liga.net/ua/politics/news/chekhiia-otsinyla-novi-"
                       "pravyla-yes-shchodo-rezervu-obmezhyt-kilkist-novoprybulykh-na-desiatky-vidsotkiv",
         "source_excerpt":
             "Це рішення обмежить кількість новоприбулих на десятки відсотків. Завдяки "
             "цьому також зменшиться навантаження на наші ресурси та полегшиться "
             "ситуація для чеської системи охорони здоров’я та соціального забезпечення"},
        {"region": "Испания",
         "figure": "Инструкция полиции требует выписку из Резерв+ с переводом; по "
                   "данным волонтёров в Аликанте статус получили трое из многих",
         "source_url": "https://cv.znaj.ua/558548-ispaniya-vidmovlyaye-cholovikam-u-"
                       "timchasovomu-zahisti-pereviryayut-rezerv-proyshli-lishe-troye",
         "source_excerpt":
             "Документ прямо вимагає від заявників витяг із застосунку «Резерв+» у "
             "перекладі іспанською мовою."},
        {"region": "Бельгия",
         "figure": "Свыше 150 жалоб на отказы за первые 2,5 недели в одном центре "
                   "помощи (Брюссель); офицеры по-разному читают статусы",
         "source_url": "https://tsn.ua/svit/rezerv-teper-mozut-vymahaty-navit-u-zinok-"
                       "shcho-zminylosia-dlia-ukrayintsiv-u-yes-3154013.html",
         "source_excerpt":
             "Лише за перші 2,5 тижня нових правил до цього центру надійшло понад 150 "
             "скарг через відмови у тимчасовому захисті."},
        {"region": "Словакия",
         "figure": "Паспорт со штампом о выезде не старше 90 дней до подачи или "
                   "документ Резерв+ на бумаге",
         "source_url": "https://sport.znaj.ua/559806-rezerv-uzhe-nedostatno-slovachchina-"
                       "zhorstkishe-filtruvatime-ukrajinskih-cholovikiv",
         "source_excerpt":
             "штамп має бути поставлений не раніше ніж за 90 днів до дати подання заяви."},
        {"region": "Болгария",
         "figure": "Варна: женщинам отказывают, т.к. в Резерв+ они «не состоят на "
                   "учёте»; агентство подтвердило «внутренние инструкции»",
         "source_url": "https://ukranews.com/news/1172013-v-bolgarii-zhenshhinam-iz-ukrainy-"
                       "otkazyvayut-vo-vremennoj-zashhite-iz-za-otsutstviya-otmetki-o",
         "source_excerpt":
             "[страница закрыта для автоматической проверки; содержание подтверждено "
             "поисковой выдачей 25.09.2026] В Болгарии женщинам из Украины отказывают "
             "во временной защите из-за отсутствия отметки о регистрации в «Резерв+»"},
    ],
    "interventions": [
        {"what": "Запрос о доступе к информации в Государственное агентство по "
                 "беженцам о причинах отказов женщинам",
         "actor": "R.I.W.E. (Иванна Ходос)", "geo": "Болгария", "when_text": "август 2026",
         "outcome": "Агентство подтвердило, что «внутренние инструкции» существуют; "
                    "их содержание не раскрыто.",
         "outcome_kind": "partial",
         "conditions": "Работает как способ доказать, что практика — не ошибка "
                       "одного сотрудника. Сам текст инструкций остаётся закрытым.",
         "source_url": "https://ukranews.com/news/1172013-v-bolgarii-zhenshhinam-iz-ukrainy-"
                       "otkazyvayut-vo-vremennoj-zashhite-iz-za-otsutstviya-otmetki-o",
         "source_excerpt":
             "[страница закрыта для автоматической проверки; содержание подтверждено "
             "поисковой выдачей 25.09.2026]"},
        {"what": "Разъяснение Еврокомиссии: решение не различает мужчин и женщин, "
                 "проверка — дело национальных органов, обещаны операционные рекомендации",
         "actor": "Еврокомиссия (Маркус Ламмерт)", "geo": "Евросоюз", "when_text": "20.08.2026",
         "outcome": "Рекомендации на 25.09.2026 не опубликованы; практика стран "
                    "остаётся разной.",
         "outcome_kind": "unclear",
         "source_url": "https://www.bagnet.org/news/politics/1402461/evrokomisiya-poyasnila-"
                       "pravila-dlya-viyskovozobovyazanih-ukrayintsiv-riznitsi-mizh-cholovikami-ta-zhinkami-nemae",
         "source_excerpt":
             "будуть додані оновлені оперативні настанови, які також будуть "
             "опубліковані найближчими тижнями"},
        {"what": "Разъяснение МИД Украины: при подтверждённом законном выезде данные "
                 "Резерв+ не нужны; случаи с женщинами не массовые",
         "actor": "МИД Украины", "geo": "Евросоюз", "when_text": "15.08.2026",
         "outcome": "Официально признано, что случаи есть; единого подхода в ЕС нет.",
         "outcome_kind": "unclear",
         "source_url": "https://lb.ua/pravo/2026/08/15/759218_mzs_rozyasnilo_situatsiyu_z.html",
         "source_excerpt":
             "У МЗС наголошують, що відомі дипломатам випадки не є масовими. "
             "Переважно йдеться про ситуації, коли заявники не могли підтвердити "
             "законність свого виїзду з України."},
        {"what": "Отказ поддержать исключение мужчин призывного возраста из защиты",
         "actor": "Правительство Венгрии (премьер Петер Мадяр)", "geo": "Венгрия",
         "when_text": "30.06.2026",
         "outcome": "Решение ЕС принято; Венгрия заявляла, что продолжит давать "
                    "убежище. Как исполняется на практике — данных нет.",
         "outcome_kind": "unclear",
         "source_url": "https://www.slovoidilo.ua/2026/06/30/novyna/polityka/premyer-"
                       "uhorshhyny-vystupyv-proty-pozbavlennya-ukrayinskyx-cholovikiv-zaxystu-yes",
         "source_excerpt":
             "Мадяр заявив, що Угорщина і надалі надаватиме притулок українським чоловікам"},
        {"what": "Вместо временной защиты — индивидуальное заявление на убежище",
         "actor": "Государство", "geo": "Германия", "when_text": "с 31.07.2026",
         "outcome": "Путь открыт, но результата не гарантирует: рассматривается "
                    "индивидуально.",
         "outcome_kind": "unclear",
         "conditions": "Такой же путь прямо назван в Норвегии и Исландии.",
         "source_url": "https://zn.ua/ukr/europe/nimechchina-obmezhila-timchasovij-zakhist-"
                       "dlja-ukrajinskikh-cholovikiv-komu-mozhe-zahrozhuvati-deportatsija.html",
         "source_excerpt":
             "Замість тимчасового захисту можна просити притулок. Відмова у "
             "тимчасовому захисті не означає, що український чоловік автоматично "
             "повинен залишити Німеччину."},
    ],
    "arguments": [
        # позиции: три, обе стороны; «слабые места» — возражения
        {"k": "for-defense", "author_idx": 3, "rel": "refute",
         "text": "Требование оправдано: Украина обороняется, и защита за рубежом не "
                 "должна становиться способом обойти мобилизацию. Брюссель сам "
                 "говорит, что изменение сделано по просьбе Киева."},
        {"k": "for-justice", "author_idx": 3, "rel": "refute",
         "text": "Это вопрос справедливости к тем, кто служит, и к их семьям: одни "
                 "несут обязанность, другие получают защиту именно за то, что её не "
                 "несут."},
        {"k": "w-readers", "author_idx": 1, "rel": "refute", "to": "for-defense",
         "text": "Проверку переложили на ведомства, которые не умеют читать украинские "
                 "документы: в Бельгии один офицер принимает «не обліковується», "
                 "другой — нет. Цель может быть законной, а исполнитель — случайным."},
        {"k": "w-stamp", "author_idx": 0, "rel": "undercut", "to": "for-justice",
         "quote": "другие получают защиту именно за то, что её не несут",
         "text": "Под удар попадают и те, кто выехал законно: пограничная служба "
                 "ставит штамп о выезде только по просьбе, и у многих его просто нет. "
                 "МИД Украины сам говорит, что проблемы — именно у тех, кто не может "
                 "подтвердить законность выезда."},
        {"k": "against-law", "author_idx": 2, "rel": "support",
         "text": "Требование недопустимо в принципе: защита беженца не должна "
                 "зависеть от исполнения обязанностей перед государством, от "
                 "которого он бежит. Юристы указывают и на спорную правовую основу "
                 "в директиве, и на единство семьи, и на дискриминацию по полу."},
        {"k": "w-special", "author_idx": 5, "rel": "refute", "to": "against-law",
         "text": "Временная защита — особый режим, а не статус беженца по Конвенции. "
                 "Суд ЕС в деле Kaduna (C-244/24, C-290/24, 19.12.2024) подтвердил "
                 "широкое усмотрение государств в отношении необязательной защиты — "
                 "правда, речь шла о гражданах третьих стран, не об украинцах."},
        {"k": "overreach", "author_idx": 4, "rel": "support",
         "text": "Правило, может быть, и допустимо, но исполнение уже вышло за его "
                 "текст: Резерв+ спрашивают у женщин (Болгария, Испания, Бельгия), у "
                 "молодёжи 18–22, которая выезжает законно, и, по сообщениям, у уже "
                 "защищённых. Исландия прямо пишет «независимо от возраста и пола»."},
        {"k": "w-overreach", "author_idx": 3, "rel": "refute", "to": "overreach",
         "text": "Если за два месяца ни одна страна не исполняет правило одинаково, "
                 "может быть, «правильного исполнения» не существует: иностранное "
                 "ведомство не может надёжно читать чужой реестр. Тогда это довод "
                 "против самого правила, а не только против исполнения."},
        {"k": "registry-court", "author_idx": 0, "rel": "qualify",
         "text": "Данные Резерв+ — не бесспорное доказательство: 16.09.2026 вступило "
                 "в силу решение суда, признавшее незаконной автоматическую "
                 "верификацию данных в реестре (дело №320/31215/25). На этих данных "
                 "сейчас строят отказы в другой стране."},
        # вопросы
        {"k": "q-duty", "author_idx": 4, "kind": "question", "rel": "question",
         "text": "Должна ли защита беженца зависеть от исполнения им обязанностей "
                 "перед государством, от которого он бежит?"},
        {"k": "q-uniform", "author_idx": 1, "kind": "question", "rel": "question",
         "text": "Почему решение, принятое, по словам Брюсселя, по просьбе Киева, "
                 "исполняется по-разному в каждой стране?"},
        {"k": "q-2028", "author_idx": 5, "kind": "question", "rel": "question",
         "text": "Что будет с 1,07 млн мужчин под временной защитой после 04.03.2028?"},
        {"k": "q-refused", "author_idx": 0, "kind": "question", "rel": "question",
         "text": "Что делать, если отказали во временной защите? Какой аргумент уже "
                 "сработал — в суде или в ведомстве — и где?"},
        {"k": "asylum", "author_idx": 5, "rel": "support", "to": "q-refused",
         "text": "Один путь назван официально в нескольких странах: подать на "
                 "убежище индивидуально. Так прямо пишут Германия, Норвегия и "
                 "Исландия. Результат не гарантирован — рассматривают каждое дело "
                 "отдельно."},
        # непроверенное — вопросами
        {"k": "q-romania", "author_idx": 1, "kind": "question", "rel": "question",
         "text": "Кто может подтвердить: в Румынии на границе требуют документы о "
                 "воинском статусе, включая Резерв+? Официальных решений власти не "
                 "объявляли."},
        {"k": "q-sofia", "author_idx": 4, "kind": "question", "rel": "question",
         "text": "Кто может подтвердить: в Софии требуют Резерв+ у тех, у кого защита "
                 "уже есть, включая женщин? Если да — это прямо противоречит ст. 2 "
                 "решения."},
        {"k": "q-poland", "author_idx": 2, "kind": "question", "rel": "question",
         "text": "Кто может подтвердить случаи в Польше, когда Резерв+ требовали у "
                 "женщин при первом оформлении PESEL UKR?"},
        {"k": "q-gaps", "author_idx": 0, "kind": "question", "rel": "question",
         "text": "Кто оформлял защиту после 31.07.2026 во Франции, Италии, Австрии, "
                 "Нидерландах, Ирландии, Португалии, Швеции, Финляндии, странах "
                 "Балтии, Греции, Хорватии, Словении, на Кипре, Мальте или в "
                 "Люксембурге? Что спрашивали?"},
    ],
    # позиции: люди в них не записываются — только состав доводов
    "positions": [
        {"headline": "Требование оправдано: защита не должна обходить мобилизацию",
         "members": ["for-defense", "for-justice"]},
        {"headline": "Требование недопустимо в принципе",
         "members": ["against-law"]},
        {"headline": "Правило допустимо, но исполнение вышло за его текст",
         "members": ["overreach"]},
    ],
}


async def seed_problem(p, author_ids):
    async with db._pool_or_raise().acquire() as conn:
        found = await conn.fetchval(
            "SELECT id FROM nodes WHERE kind = 'problem' AND title = $1 "
            "AND deleted_at IS NULL", p["title"])
    if found:
        print(f"пропуск: «{p['title']}» уже есть (#{found})")
        return found, False
    author = author_ids[p["author_idx"]]
    root = await db.add_node(p["statement"], author_id=author, kind="problem",
                             topic_root_id=None, title=p["title"])
    await db.set_topic_facets(root, p["domain"], p.get("sub"),
                              sorted(taxonomy.geo_closure(p["geo"])), p["tags"])
    await db.set_problem(root, causes=p["causes"], gap=p["gap"], author_id=author)
    for row in p["scale"]:
        await db.add_scale_row(root, region=row["region"], figure=row["figure"],
                               source_url=row["source_url"],
                               source_excerpt=row["source_excerpt"],
                               retrieved_at=RETRIEVED, author_id=author)
    for iv in p["interventions"]:
        await db.add_intervention(
            root, what=iv["what"], actor=iv.get("actor"), geo=iv.get("geo"),
            when_text=iv.get("when_text"), outcome=iv.get("outcome"),
            outcome_kind=iv["outcome_kind"], conditions=iv.get("conditions"),
            source_url=iv["source_url"], source_excerpt=iv["source_excerpt"],
            source_retrieved_at=RETRIEVED, author_id=author)
    by_key = {}
    for a in p["arguments"]:
        nid = await db.add_node(a["text"], author_id=author_ids[a["author_idx"]],
                                kind=a.get("kind", "argument"), topic_root_id=root)
        by_key[a["k"]] = nid
        target = by_key[a["to"]] if a.get("to") else root
        q = a.get("quote")
        anchor = {}
        if q:
            ttext = next(x["text"] for x in p["arguments"] if x["k"] == a["to"])
            s = ttext.index(q)
            anchor = {"anchor_hash": db.text_hash(ttext), "anchor_start": s,
                      "anchor_end": s + len(q), "anchor_quote": q}
        await db.add_edge(nid, target, a["rel"], **anchor)
    for pos in p.get("positions", []):
        pid = await db.add_position(root, pos["headline"], pos["headline"], "mixed")
        for k in pos["members"]:
            await db.set_node_position(by_key[k], pid)
    print(f"засеяно «{p['title']}» (#{root}): строк масштаба {len(p['scale'])}, "
          f"реестр {len(p['interventions'])}, узлов {len(by_key)}, "
          f"позиций {len(p.get('positions', []))} (людей в них — 0)")
    return root, True


async def run(url):
    db.DATABASE_URL = url
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    pool = db._pool_or_raise()
    ids = []
    async with pool.acquire() as conn:
        for name, color in PEOPLE:
            aid = await conn.fetchval(
                "SELECT id FROM authors WHERE name = $1 AND username IS NULL "
                "AND NOT is_service ORDER BY id LIMIT 1", name)
            ids.append(aid)
    ids = [aid or await db.add_author(name, color)
           for aid, (name, color) in zip(ids, PEOPLE)]
    world, _ = await seed_problem(WORLD, ids)
    eu, new_eu = await seed_problem(EU, ids)
    if new_eu:
        # «Б порождает А»: привязка прав к учёту (в том числе просьба Киева к
        # ЕС) — причина; требование в ЕС — следствие. Обоснование — сама
        # постановка причины (одна мысль — один узел).
        await db.add_problem_link(world, eu, node_id=world, author_id=ids[0])
        print(f"связь: #{world} порождает #{eu}")
    await db.close_pool()


def main():
    ap = argparse.ArgumentParser(description="Посев: Резерв+ и права украинцев за рубежом")
    ap.add_argument("--url", default=DEFAULT_URL)
    a = ap.parse_args()
    if "opmap" in a.url:
        raise SystemExit("отказ: в синтетическую базу реальную тему не сеем")
    asyncio.run(run(a.url))


if __name__ == "__main__":
    main()

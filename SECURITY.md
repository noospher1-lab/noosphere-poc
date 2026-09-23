# Security Policy / Сообщить об уязвимости

**Contact / Контакт: noosphere@noosphere.live**

English below / Русский текст выше английского.

---

## Куда писать

Нашли уязвимость — напишите на **noosphere@noosphere.live**. Не открывайте
issue: он публичен с первой секунды, а живой стенд обновляется вручную.

Полезно приложить: что именно происходит, шаги для воспроизведения, версию
(коммит) и что вы получили — доступ, данные, отказ в обслуживании. Если для
проверки пришлось затронуть чужие данные, скажите об этом прямо: это не
претензия, это нужно, чтобы понять, кого предупредить.

Проект ведёт один человек. Подтверждение получения — в течение **5 рабочих
дней**; сроки починки зависят от тяжести и честно сообщаются в переписке.
Вознаграждения за находки нет — денег у проекта пока нет, и обещать их было бы
нечестно. Благодарность в истории изменений — по вашему желанию.

## Что в рамках

- этот репозиторий;
- боевой стенд прототипа **graph.noosphere.live**.

Пожалуйста, проверяйте на своей копии (`./run.sh`, см. README). На боевом
стенде работают живые люди: не удаляйте и не портите чужие тексты, не читайте
чужие аккаунты дальше, чем нужно для доказательства, и не устраивайте нагрузку.
Действуя так, вы не рискуете претензиями с нашей стороны.

## Что уязвимостью НЕ считается

Это прототип, и часть ограничений — осознанные решения, а не недосмотр:

- **Оценку PoI можно получить через внешний ИИ.** Известно и принято: PoI —
  линза, а не власть; балл не даёт веса голоса именно поэтому.
- **Сибил-устойчивость не решена** семантическим слоем и не заявляется
  решённой — см. README.
- **Баллы участия** ничего не разблокируют и ничем не управляют.
- Отсутствие защиты от нагрузки, `DEV_TOOLS` на локальной копии, слабые пароли
  в примерах конфигурации.

## Раскрытие

Просим дать время на починку — ориентир **90 дней** с момента подтверждения
или до выхода исправления, смотря что раньше. Если считаете, что люди в
опасности прямо сейчас, скажите — договоримся о более коротком сроке.

---

## Where to report

Found a vulnerability? Write to **noosphere@noosphere.live**. Please do not
open an issue: issues are public from the first second, and the live instance
is updated by hand.

Useful to include: what happens, steps to reproduce, the commit you tested, and
what you obtained — access, data, or a denial of service. If checking it
required touching someone else's data, say so plainly: that is not held against
you, it is how we know who to warn.

This project is run by one person. Acknowledgement within **5 working days**;
fix timelines depend on severity and are stated honestly in the thread. There
is no bug bounty — the project has no money yet, and promising some would be
dishonest. Credit in the changelog if you want it.

## Scope

- this repository;
- the live prototype at **graph.noosphere.live**.

Please test against your own copy (`./run.sh`, see README). Real people use the
live instance: do not delete or damage anyone's texts, do not read other
accounts beyond what proves the point, and do not run load tests. Stay within
this and you have nothing to fear from us.

## Not a vulnerability

This is a proof of concept, and some limits are decisions rather than oversights:

- **A PoI score can be obtained through an external LLM.** Known and accepted:
  PoI is a lens, not power — which is exactly why a score carries no vote weight.
- **Sybil resistance is not solved** by the semantic layer, and is not claimed
  to be — see README.
- **Participation points** unlock nothing and control nothing.
- No load protection, `DEV_TOOLS` on a local copy, weak passwords in example
  configuration.

## Disclosure

Please allow time for a fix — **90 days** from acknowledgement, or until a fix
ships, whichever comes first. If you believe people are at risk right now, say
so and we will agree on something shorter.

#!/usr/bin/env python3
"""Прогон: пять ИИ-агентов пользуются Noosphere PoC как живые участники.

Каждый агент — обычный аккаунт с подтверждённой почтой и ОДНИМ лимитом на ИИ
(по умолчанию $3): в него входят и его собственные размышления, и всё, что он
тратит внутри платформы — компаньон черновика, оценка PoI, собеседник
голосования. Кончились деньги — агент замолкает.

Ходит агент через тот же HTTP API, что и браузер: никаких прямых записей в
базу мимо правил (единственное исключение — отметка «почта подтверждена», она
иначе требует письма).

    ./serve.sh                     # стенд на :8000 с грантом $3 и без писем
    python3 runner.py --problems problems.json --rounds 2
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain import Brain, BudgetOut, Wallet          # noqa: E402
from personas import PERSONAS                        # noqa: E402
from poc import Poc, PocError                        # noqa: E402
import export                                        # noqa: E402
import view                                          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COLORS = ["\033[38;5;74m", "\033[38;5;173m", "\033[38;5;108m",
          "\033[38;5;140m", "\033[38;5;179m"]
DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def say(prefix, text, color="", dim=False):
    body = f"{DIM}{text}{OFF}" if dim else text
    print(f"{color}{prefix}{OFF} {body}", flush=True)


def head(text):
    print(f"\n{BOLD}{'─' * 78}\n{text}\n{'─' * 78}{OFF}", flush=True)


# --------------------------------------------------------------- инструменты
ACT_TOOL = {
    "name": "act",
    "description": "Твой ход в графе: ответить, отреагировать или пропустить.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string", "enum": ["reply", "react", "pass"],
                "description": ("reply — написать свой довод в ответ на узел; "
                                "react — молча согласиться/не согласиться; "
                                "pass — сказать нечего, пропустить ход"),
            },
            "target_id": {
                "type": "integer",
                "description": "id узла, на который отвечаешь или реагируешь",
            },
            "edge_type": {
                "type": "string",
                "enum": ["support", "refute", "qualify", "question"],
                "description": ("чем твой текст приходится цели: support — "
                                "усиливает, refute — опровергает, qualify — "
                                "уточняет границы, question — спрашивает"),
            },
            "draft": {
                "type": "string",
                "description": "черновик довода, 3–6 предложений, от себя",
            },
            "stance": {"type": "string", "enum": ["agree", "disagree"]},
            "why": {"type": "string",
                    "description": "одной фразой: почему именно этот ход"},
        },
        "required": ["action", "why"],
    },
}

PROBLEM_TOOL = {
    "name": "frame_problem",
    "description": "Поставить проблему в графе так, чтобы с ней можно было работать.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string",
                      "description": "заголовок, 4–9 слов, без лозунга"},
            "text": {"type": "string",
                     "description": ("постановка: что именно не работает, для "
                                     "кого и почему держится. 5–9 предложений")},
            "domain": {"type": "string", "description": "код направления из справочника"},
            "sub": {"type": "string", "description": "подветвь из справочника"},
            "geo": {"type": "array", "items": {"type": "string"},
                    "description": "коды стран/регионов, если проблема привязана"},
        },
        "required": ["title", "text"],
    },
}

BALLOT_TOOL = {
    "name": "cast",
    "description": "Выбрать вариант(ы) в голосовании.",
    "input_schema": {
        "type": "object",
        "properties": {
            "option_ids": {"type": "array", "items": {"type": "integer"}},
            "why": {"type": "string"},
        },
        "required": ["option_ids", "why"],
    },
}


# --------------------------------------------------------------------- агент
class Agent:
    def __init__(self, persona, idx, args):
        self.p = persona
        self.name = persona["name"]
        self.color = COLORS[idx % len(COLORS)]
        self.username = persona["username"]
        self.password = args.password
        self.wallet = Wallet(args.budget)
        self.brain = Brain(args.model, persona["system"], self.wallet,
                           effort=args.effort)
        self.poc = Poc(args.base_url)
        self.alive = True
        self.published = []          # id узлов, которые он написал
        self.weight = None           # вес голоса после диалога
        self.runlog = None           # копилка разговоров с компаньоном

    # ------------------------------------------------------------ служебное
    def log(self, text, dim=False):
        say(f"{self.name}:", text, self.color, dim)

    def sync_spend(self):
        acc = (self.poc.profile() or {}).get("account") or {}
        self.wallet.platform = float(acc.get("spent_usd") or 0.0)

    def afford(self, reserve=0.0):
        if not self.alive:
            return False
        self.sync_spend()
        if self.wallet.left <= reserve:
            if self.alive:
                self.log(f"лимит ${self.wallet.limit:.2f} исчерпан — умолкаю "
                         f"(своё ${self.wallet.own:.3f} + платформа "
                         f"${self.wallet.platform:.3f})", dim=True)
            self.alive = False
            return False
        return True

    def signin(self):
        try:
            self.poc.register(self.username, self.password, self.name,
                              f"{self.username}@agents.local")
        except PocError as e:
            if e.status not in (409, 400):
                raise
        return self

    def login(self):
        me = self.poc.login(self.username, self.password)
        self.author_id = me["id"]
        return me

    # ------------------------------------------------------------------ ход
    def turn(self, root_id, rounds_left):
        if not self.afford(reserve=0.05):
            return None
        ctx = (f"{view.problem_brief(self.poc, root_id)}\n\n"
               f"РАЗБОР СЕЙЧАС (отступ = ответ на узел выше):\n"
               f"{view.branch_view(self.poc, root_id)}\n\n"
               f"Как здесь читают разбор. Ценность графа — в ширине, а не в "
               f"длине: проблема разобрана, когда у неё несколько независимых "
               f"веток, каждая про своё. Цепочка «все отвечают последнему» — "
               f"это чат, а не разбор, и она оставляет доводы выше по ветке "
               f"неразобранными.\n"
               f"И главное правило очерёдности: если в дереве есть узел "
               f"ЖИВОГО участника (не агента) без ответа — отвечают сначала "
               f"ему. Человек, которому не ответили, из графа уходит, и "
               f"никакой разбор этого не окупает. Слабый довод — не повод "
               f"молчать, а повод сказать прямо, в чём он слаб, и вытащить из "
               f"него то, что стоит разбора.\n"
               f"Поэтому: если ветка, к которой тянет ответить, уже глубже "
               f"трёх уровней — сначала посмотри узлы с пометкой БЕЗ ОТВЕТА и "
               f"саму постановку. Отвечать на свежий узел стоит только когда "
               f"у тебя есть возражение именно к нему, которого больше никто "
               f"не выскажет. Открыть новую ветку прямо от проблемы — "
               f"нормальный и часто лучший ход.\n\n"
               f"У тебя осталось ${self.wallet.left:.2f} на ИИ и примерно "
               f"{rounds_left} ход(ов). Молчание — тоже ход, если добавить "
               f"нечего.\n"
               f"Что ты делаешь сейчас?")
        try:
            d = self.brain.choose(ctx, ACT_TOOL)
        except BudgetOut:
            self.alive = False
            return None
        if not d:
            return None
        action = d.get("action")

        if action == "pass":
            self.log(f"пропускает ход — {d.get('why', '')}", dim=True)
            return None

        if action == "react":
            target, stance = d.get("target_id"), d.get("stance") or "agree"
            if target is None:
                return None
            try:
                self.poc.react(target, stance)
            except PocError as e:
                self.log(f"реакция не прошла: {e}", dim=True)
                return None
            mark = "＋" if stance == "agree" else "−"
            self.log(f"{mark} реакция на #{target} — {d.get('why', '')}", dim=True)
            return None

        draft, target = (d.get("draft") or "").strip(), d.get("target_id")
        if not draft or target is None:
            return None
        edge = d.get("edge_type") or "support"
        self.log(f"пишет ответ на #{target} ({edge}) — {d.get('why', '')}", dim=True)
        return self._publish(root_id, target, edge, draft, ctx)

    def _publish(self, root_id, target, edge, draft, ctx):
        """Черновик → один ход с ИИ-компаньоном → публикация."""
        text, companion_turn = draft, None
        if self.afford(reserve=0.03):
            try:
                comp = self.poc.companion(draft, connect_to=target)
                reply = (comp.get("reply") or "").strip()
                sugg = (comp.get("suggestion") or "").strip()
                companion_turn = (draft, reply, sugg)
                if reply:
                    say("   компаньон:", reply, DIM, dim=True)
                if sugg:
                    say("   компаньон предлагает:", sugg[:300], DIM, dim=True)
                text = self.brain.say(
                    f"{ctx}\n\nТвой черновик:\n{draft}\n\n"
                    f"ИИ-компаньон платформы ответил тебе:\n{reply}\n"
                    + (f"\nОн предложил формулировку:\n{sugg}\n" if sugg else "")
                    + "\nРешай сам: согласиться, взять частично или отбить.\n\n"
                      "ВАЖНО: компаньон — не участник дискуссии. Этот разговор "
                      "никто, кроме тебя, не увидит: в граф уходит только твой "
                      "текст. Поэтому в нём не должно быть НИ ОДНОЙ отсылки к "
                      "сказанному компаньоном — ни «возражение принимаю», ни "
                      "«согласен, что», ни «как ты заметил». Читатель видит "
                      "твой довод рядом с узлом, на который ты отвечаешь, и "
                      "больше ничего: текст обязан держаться сам.\n"
                      "Верни ТОЛЬКО окончательный текст довода — без пояснений, "
                      "без кавычек, без заголовка.")
            except (PocError, BudgetOut) as e:
                say("   компаньон:", f"недоступен ({e}) — публикую как есть",
                    DIM, dim=True)
                text = draft
        text = (text or draft).strip()
        # разговор платформа не хранит — значит хранит прогон, иначе после
        # публикации не восстановить, ЧТО именно правил компаньон
        if self.runlog is not None and companion_turn:
            self.runlog.turn(self.name, companion_turn[0], companion_turn[1],
                             companion_turn[2], final=text)
        try:
            node = self.poc.argument(text, connect_to=target, edge_type=edge)
        except PocError as e:
            self.log(f"публикация не прошла: {e}", dim=True)
            return None
        self.published.append(node["id"])
        if self.runlog is not None:
            self.runlog.attach(node["id"])
        self.log(f"#{node['id']} опубликован:")
        print(f"   {text}\n", flush=True)
        return node["id"]

    # ------------------------------------------------------------- проблема
    def frame_problem(self, prompt, taxonomy_hint):
        if not self.afford(reserve=0.05):
            return None
        d = self.brain.choose(
            f"Ты приносишь в Ноосферу новую проблему. Вот как её сформулировал "
            f"тот, кто её принёс:\n\n«{prompt}»\n\n"
            f"Преврати это в ПОСТАНОВКУ проблемы: что именно не работает, для "
            f"кого, почему держится, и что осталось бы нерешённым, даже если "
            f"сделать очевидное. Не решай её здесь — ставь. Не приписывай "
            f"сторонам намерений, которых не знаешь.\n\n"
            f"Справочник рубрик (выбери подходящую пару):\n{taxonomy_hint}",
            PROBLEM_TOOL)
        if not d:
            return None
        body = dict(text=d["text"], title=d["title"], kind="problem")
        for k in ("domain", "sub", "geo"):
            if d.get(k):
                body[k] = d[k]
        try:
            node = self.poc.argument(**body)
        except PocError as e:
            body.pop("domain", None), body.pop("sub", None), body.pop("geo", None)
            self.log(f"рубрика не принята ({e}) — ставлю без рубрики", dim=True)
            node = self.poc.argument(**body)
        self.log(f"поставил проблему #{node['id']}: {d['title']}")
        print(f"   {d['text']}\n", flush=True)
        return node["id"]


# ----------------------------------------------------------------- служебное
def verify_accounts(usernames, balance):
    """Отметить почту подтверждённой и выставить грант.

    Единственное место, где раннер идёт в базу мимо API: подтверждение адреса
    требует письма, а писем на выдуманные адреса быть не должно.
    """
    import asyncpg

    async def go():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        await conn.execute(
            "UPDATE authors SET email_verified = TRUE, balance_usd = $2 "
            "WHERE username = ANY($1::text[])", usernames, Decimal(str(balance)))
        await conn.close()

    asyncio.run(go())


def poll_poi(poc, node_ids, timeout=120):
    """Оценка приходит фоном — ждём и показываем разбор по критериям."""
    if not node_ids:
        return
    pending, deadline = list(node_ids), time.time() + timeout
    head("PoI — как платформа оценила эти тексты")
    while pending and time.time() < deadline:
        for nid in list(pending):
            n = poc.node(nid)
            if n.get("poi_score") is not None:
                b = n.get("poi_breakdown") or {}
                crit = b.get("criteria") or b
                parts = ", ".join(
                    f"{k}: {v.get('score') if isinstance(v, dict) else v}"
                    for k, v in list(crit.items())[:6]
                    if k not in ("total", "summary"))
                print(f"  #{nid} · {n.get('author')} · "
                      f"{BOLD}PoI {n['poi_score']:.1f}{OFF}", flush=True)
                if parts:
                    print(f"     {DIM}{parts}{OFF}", flush=True)
                if isinstance(b, dict) and b.get("summary"):
                    print(f"     {DIM}{b['summary'][:300]}{OFF}", flush=True)
                pending.remove(nid)
        if pending:
            time.sleep(4)
    for nid in pending:
        print(f"  #{nid} — оценка не пришла за {timeout}s", flush=True)


def taxonomy_hint(poc):
    try:
        t = poc.get("/api/taxonomy")
    except PocError:
        return "(справочник недоступен)"
    lines = []
    for dom in (t.get("domains") or [])[:20]:
        subs = ", ".join((dom.get("subs") or [])[:8])
        lines.append(f'{dom.get("code")} — {dom.get("title")}: {subs}')
    return "\n".join(lines) or json.dumps(t, ensure_ascii=False)[:1500]


OPTIONS_TOOL = {
    "name": "ballot",
    "description": "Варианты для голосования, если позиции ещё не сведены.",
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {"type": "array", "items": {"type": "string"},
                        "description": "2–4 взаимоисключающих варианта, коротко"},
        },
        "required": ["options"],
    },
}


# ------------------------------------------------------------------- фазы
def phase_debate(agents, reader, root_id, rounds):
    for r in range(rounds):
        head(f"Раунд {r + 1} из {rounds} — проблема #{root_id}")
        order = [a for a in agents if a.alive]
        random.shuffle(order)
        if not order:
            print("все агенты исчерпали лимит", flush=True)
            return
        fresh = []
        for a in order:
            try:
                nid = a.turn(root_id, rounds - r)
            except BudgetOut:
                a.alive = False
                continue
            if nid:
                fresh.append(nid)
        poll_poi(reader, fresh)


def phase_positions(reader, root_id):
    head(f"Позиции — во что свелись доводы (проблема #{root_id})")
    try:
        pos = reader.positions(root_id, recompute=True)
    except PocError as e:
        print(f"свести позиции не удалось: {e}", flush=True)
        return {"positions": []}
    print(view.positions_view(pos), flush=True)
    return pos


def _render_turns(turns, limit=6, chars=400):
    out = []
    for t in turns[-limit:]:
        who = "СОБЕСЕДНИК" if t.get("role") == "assistant" else "ТЫ"
        out.append(f"{who}: {view._clip(t.get('content'), chars)}")
    return "\n".join(out)


def vote_dialogue(agent, decision_id, question, min_turns):
    """Диалог перед голосованием: вес голоса рождается здесь, а не в кошельке."""
    try:
        meta = agent.poc.dlg_start(decision_id)
    except PocError as e:
        agent.log(f"диалог не начался: {e}", dim=True)
        return None
    agent.log("сел разбираться с вопросом голосования", dim=True)
    guard = 0
    while guard < 40:
        guard += 1
        turns = meta.get("transcript") or []
        last = turns[-1] if turns else {}
        if last.get("meta") == "inform_offer_pending":
            if not agent.afford(reserve=0.10):
                return None
            meta = agent.poc.dlg_inform(decision_id, accept=True)
            continue
        need = max(min_turns, meta.get("min_turns", 8))
        if meta.get("user_turns", 0) >= need:
            break
        if not agent.afford(reserve=0.15):
            return None
        say("   собеседник:", view._clip(last.get("content"), 320), DIM, dim=True)
        try:
            reply = agent.brain.say(
                f"Ты готовишься голосовать по вопросу:\n«{question}»\n\n"
                f"Разговор с ИИ-собеседником платформы (он не советчик — он "
                f"проверяет, понял ли ты вопрос):\n{_render_turns(turns)}\n\n"
                f"Ответь ему одной репликой — по существу, своим голосом, "
                f"3–5 предложений. Не льсти и не соглашайся из вежливости.",
                max_tokens=1200)
        except BudgetOut:
            agent.alive = False
            return None
        try:
            meta = agent.poc.dlg_message(decision_id, reply)
        except PocError as e:
            agent.log(f"реплика не прошла: {e}", dim=True)
            break
        say(f"   {agent.name}:", view._clip(reply, 320), agent.color, dim=True)
    try:
        fin = agent.poc.dlg_finalize(decision_id)
    except PocError as e:
        agent.log(f"завершить диалог не вышло: {e}", dim=True)
        return None
    agent.weight = fin.get("weight")
    agent.log(f"вес голоса {agent.weight} · {view._clip(fin.get('summary'), 220)}")
    return fin


def phase_vote(agents, reader, root_id, positions, args):
    live = [a for a in agents if a.alive]
    if not live:
        return
    host = live[0]
    head(f"Голосование по проблеме #{root_id}")
    if not host.afford(reserve=0.20):
        return
    pos_list = (positions or {}).get("positions") or []
    question = host.brain.say(
        f"{view.problem_brief(host.poc, root_id)}\n\n"
        f"Позиции, в которые свёлся разбор:\n{view.positions_view(positions or {})}\n\n"
        f"Сформулируй ОДИН вопрос для совещательного голосования по этой "
        f"проблеме — такой, где расхождение реально есть. Верни только вопрос, "
        f"одной фразой, без пояснений.", max_tokens=400).strip().strip('"')
    print(f"вопрос: {BOLD}{question}{OFF}", flush=True)
    try:
        dec = host.poc.create_decision(root_id, question)
    except PocError as e:
        print(f"голосование не создалось: {e}", flush=True)
        return
    did = dec["id"]

    added = 0
    for p in pos_list[:4]:
        try:
            host.poc.add_option(did, position_id=p["id"])
            added += 1
        except PocError:
            pass
    if added < 2:
        d = host.brain.choose(
            f"Вопрос голосования: «{question}»\n\nПозиции ещё не сведены. "
            f"Предложи 2–4 взаимоисключающих варианта ответа.", OPTIONS_TOOL)
        for label in (d or {}).get("options", [])[:4]:
            try:
                host.poc.add_option(did, label=label)
                added += 1
            except PocError:
                pass
    if added < 2:
        print("вариантов меньше двух — голосовать не по чему", flush=True)
        return
    host.poc.open_decision(did)
    opts = (host.poc.decision(did) or {}).get("options") or []
    print("варианты:", flush=True)
    for o in opts:
        print(f"  [{o['id']}] {view._clip(o.get('label') or o.get('headline'), 160)}",
              flush=True)

    for a in live:
        if not a.alive:
            continue
        vote_dialogue(a, did, question, args.vote_turns)
        if not a.afford(reserve=0.05):
            continue
        listing = "\n".join(
            f"[{o['id']}] {o.get('label') or o.get('headline')}" for o in opts)
        try:
            d = a.brain.choose(
                f"Вопрос: «{question}»\n\nВарианты:\n{listing}\n\n"
                f"Голосуй. Обычно один вариант; несколько — только если они "
                f"действительно совместимы.", BALLOT_TOOL)
        except BudgetOut:
            a.alive = False
            continue
        ids = [i for i in (d or {}).get("option_ids", [])
               if i in {o["id"] for o in opts}]
        if not ids:
            continue
        res = a.poc.cast_vote(did, ids)
        a.log(f"голос за {ids} (вес {res.get('weight')}) — {d.get('why', '')}")

    final = host.poc.decision(did) or {}
    head("Итог голосования")
    print(f"вопрос: {question}", flush=True)
    tally = final.get("tally") or {}
    print(json.dumps(tally, ensure_ascii=False, indent=2), flush=True)


def summary(agents, reader, root_ids):
    head("Итоги прогона")
    print(f"{'агент':<16}{'узлов':>7}{'вес':>7}{'своё $':>10}"
          f"{'платформа $':>13}{'всего $':>10}", flush=True)
    for a in agents:
        a.sync_spend()
        print(f"{a.name:<16}{len(a.published):>7}"
              f"{(a.weight if a.weight is not None else '—'):>7}"
              f"{a.wallet.own:>10.3f}{a.wallet.platform:>13.3f}"
              f"{a.wallet.total:>10.3f}", flush=True)
    total = sum(a.wallet.total for a in agents)
    print(f"{DIM}суммарно потрачено на ИИ: ${total:.2f} "
          f"(лимит на агента ${agents[0].wallet.limit:.2f}){OFF}", flush=True)

    for root_id in root_ids:
        head(f"Дерево проблемы #{root_id} после прогона")
        print(view.branch_view(reader, root_id, text_chars=200), flush=True)


# -------------------------------------------------------------------- запуск
def main():
    ap = argparse.ArgumentParser(description="Прогон ИИ-агентов по PoC")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="medium",
                    choices=["low", "medium", "high", "xhigh"])
    ap.add_argument("--budget", type=float, default=3.0,
                    help="лимит на ИИ на агента, $ (своё + платформа)")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--agents", type=int, default=len(PERSONAS))
    ap.add_argument("--problems", help="json со списком постановок проблем")
    ap.add_argument("--topic", type=int, action="append",
                    help="работать в существующей проблеме (можно несколько)")
    ap.add_argument("--vote-on", type=int,
                    help="id проблемы, по которой голосуем (по умолчанию первая)")
    ap.add_argument("--vote-turns", type=int, default=8)
    # Голосованием агенты пока не пользуются (решение Alex, 2026-08-27):
    # механика бюллетеня меняется — участник получил право предложить свой
    # вариант, — и обкатывать её должны живые люди, а не пять машин, которые
    # проголосуют раньше, чем человек дочитает вопрос.
    ap.add_argument("--vote", action="store_true",
                    help="включить фазу голосования (по умолчанию выключена)")
    ap.add_argument("--no-vote", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--password", default="agents-local-2026")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--tag", default=None,
                    help="метка в имени папки с текстами прогона")
    args = ap.parse_args()

    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("нет ANTHROPIC_API_KEY (.env)")
    if args.seed is not None:
        random.seed(args.seed)

    probe = Poc(args.base_url)
    try:
        probe.get("/healthz")
    except Exception as e:
        sys.exit(f"стенд не отвечает на {args.base_url} ({e}) — запусти ./serve.sh")

    head(f"Пять участников заходят в Ноосферу · модель {args.model} · "
         f"лимит ${args.budget:.2f} на агента")
    agents = [Agent(p, i, args) for i, p in enumerate(PERSONAS[:args.agents])]
    runlog = export.RunLog()
    for a in agents:
        a.runlog = runlog
        a.signin()
    verify_accounts([a.username for a in agents], args.budget)
    for a in agents:
        me = a.login()
        a.log(f"вошёл (id {me['id']}, грант ${args.budget:.2f})", dim=True)

    reader = agents[0].poc
    roots = list(args.topic or [])
    if args.problems:
        head("Постановка проблем")
        hint = taxonomy_hint(reader)
        spec = json.loads(Path(args.problems).read_text(encoding="utf-8"))
        for i, item in enumerate(spec):
            author = agents[i % len(agents)]
            if item.get("text"):
                node = author.poc.argument(
                    item["text"], title=item["title"], kind="problem",
                    domain=item.get("domain"), sub=item.get("sub"),
                    geo=item.get("geo"), tags=item.get("tags"))
                # рамка проблемы — причины и пробел — кладётся отдельным
                # вызовом: в графе это состояние проблемы, а не текст корня
                if item.get("causes") or item.get("gap"):
                    author.poc.call("PUT", f'/api/problems/{node["id"]}',
                                    json={"causes": item.get("causes"),
                                          "gap": item.get("gap")})
                for row in item.get("scale") or []:
                    author.poc.post(f'/api/problems/{node["id"]}/scale', row)
                author.log(f"поставил проблему #{node['id']}: {item['title']}")
                roots.append(node["id"])
            elif item.get("prompt"):
                nid = author.frame_problem(item["prompt"], hint)
                if nid:
                    roots.append(nid)
    if not roots:
        roots = [t["id"] for t in reader.topics()][:1]
    print(f"работаем в проблемах: {roots}", flush=True)

    for root_id in roots:
        phase_debate(agents, reader, root_id, args.rounds)
    positions = {r: phase_positions(reader, r) for r in roots}

    if args.vote and not args.no_vote:
        target = args.vote_on or roots[0]
        phase_vote(agents, reader, target, positions.get(target), args)

    summary(agents, reader, roots)

    out = export.new_run_dir(args.tag)
    for root_id in roots:
        export.export_problem(reader, root_id, out / f"problem-{root_id}", runlog)
    (out / "README.md").write_text(
        f"# Прогон {out.name}\n\n"
        f"Модель {args.model} (effort {args.effort}), лимит ${args.budget:.2f} "
        f"на агента, раундов: {args.rounds}.\n\n"
        f"Участники: {', '.join(a.name for a in agents)}.\n\n"
        f"Проблемы: {', '.join('#' + str(r) for r in roots)}.\n",
        encoding="utf-8")
    (out / "summary.md").write_text(
        "# Итоги\n\n| участник | узлов | вес голоса | своё $ | платформа $ |\n"
        "|---|---|---|---|---|\n" + "\n".join(
            f"| {a.name} | {len(a.published)} | "
            f"{a.weight if a.weight is not None else '—'} | "
            f"{a.wallet.own:.3f} | {a.wallet.platform:.3f} |" for a in agents)
        + "\n", encoding="utf-8")
    print(f"\nтексты прогона: {out}", flush=True)


if __name__ == "__main__":
    main()

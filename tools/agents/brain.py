"""Мышление агента и его кошелёк.

Лимит на ИИ у агента ОДИН — по умолчанию $3, как грант живого тестера. В него
входит и то, что агент тратит на собственные размышления здесь, и то, что он
тратит внутри платформы (компаньон, оценка PoI, собеседник голосования). Второе
считает сам PoC в usage_events; раннер читает его через /api/me/profile и
складывает с первым. Кончились деньги — агент замолкает, как замолчал бы
человек с исчерпанным грантом.
"""

import json
import anthropic

# $ за 1M токенов: (вход, выход). Кэш считаем по правилам API: чтение ~0.1x,
# запись ~1.25x от входной цены.
PRICES = {
    "claude-opus-5":     (5.0, 25.0),
    "claude-fable-5":    (10.0, 50.0),
    "claude-sonnet-5":   (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}


class BudgetOut(RuntimeError):
    """Деньги агента кончились — не ошибка, а конец его участия."""


class Wallet:
    def __init__(self, limit_usd):
        self.limit = float(limit_usd)
        self.own = 0.0          # размышления агента (этот раннер)
        self.platform = 0.0     # траты внутри PoC, из usage_events

    def charge(self, usage, model):
        pin, pout = PRICES.get(model, PRICES["claude-opus-5"])
        cost = (getattr(usage, "input_tokens", 0) * pin
                + getattr(usage, "output_tokens", 0) * pout
                + (getattr(usage, "cache_read_input_tokens", 0) or 0) * pin * 0.1
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * pin * 1.25
                ) / 1_000_000
        self.own += cost
        return cost

    @property
    def total(self):
        return self.own + self.platform

    @property
    def left(self):
        return self.limit - self.total

    def check(self, reserve=0.0):
        """reserve — сколько нужно оставить на уже начатое дело."""
        if self.left <= reserve:
            raise BudgetOut(f"лимит ${self.limit:.2f} исчерпан "
                            f"(своё ${self.own:.3f} + платформа ${self.platform:.3f})")


class Brain:
    """Голова одного агента. Разговоров не помнит: каждый ход — свежий взгляд
    на текущее состояние графа, как у человека, который зашёл и прочитал ветку."""

    def __init__(self, model, system, wallet, effort="medium", client=None):
        self.model = model
        self.system = system
        self.wallet = wallet
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def _call(self, prompt, tools=None, max_tokens=4000):
        self.wallet.check()
        kw = dict(model=self.model, max_tokens=max_tokens, system=self.system,
                  output_config={"effort": self.effort},
                  messages=[{"role": "user", "content": prompt}])
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = {"type": "any"}
        r = self.client.messages.create(**kw)
        self.wallet.charge(r.usage, self.model)
        return r

    def choose(self, prompt, tool):
        """Решение в структуре — через вызов инструмента, а не парсинг текста."""
        r = self._call(prompt, tools=[tool])
        for b in r.content:
            if b.type == "tool_use":
                # input приходит уже разобранным; строковые поля не трогаем
                return dict(b.input)
        return None

    def say(self, prompt, max_tokens=2000):
        r = self._call(prompt, max_tokens=max_tokens)
        return "\n".join(b.text for b in r.content if b.type == "text").strip()

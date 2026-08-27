"""Тонкий клиент к PoC: ровно те вызовы, которые делает браузер участника."""

import httpx


class PocError(RuntimeError):
    def __init__(self, method, path, status, body):
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> {status}: {body}")


class Poc:
    """Одна сессия одного участника: свои cookies, свой аккаунт."""

    def __init__(self, base_url, timeout=300):
        self.http = httpx.Client(base_url=base_url, timeout=timeout)

    # ---------------------------------------------------------------- сырьё
    def call(self, method, path, json=None, params=None):
        r = self.http.request(method, path, json=json, params=params)
        if r.status_code >= 400:
            raise PocError(method, path, r.status_code, r.text[:400])
        return r.json() if r.content else None

    def get(self, path, **params):
        return self.call("GET", path, params=params or None)

    def post(self, path, body=None):
        return self.call("POST", path, json=body if body is not None else {})

    # ---------------------------------------------------------------- вход
    def register(self, username, password, name, email):
        return self.post("/api/auth/register", {
            "username": username, "password": password, "name": name,
            "email": email, "accept_terms": True})

    def login(self, username, password):
        return self.post("/api/auth/login", {
            "username": username, "password": password})

    def profile(self):
        return self.get("/api/me/profile")

    # ---------------------------------------------------------------- чтение
    def topics(self):
        return self.get("/api/topics")

    def graph(self):
        return self.get("/api/graph")

    def problem(self, root_id):
        return self.get(f"/api/problems/{root_id}")

    def topic_nodes(self, root_id):
        return self.get(f"/api/topics/{root_id}/nodes")

    def node(self, node_id):
        return self.get(f"/api/nodes/{node_id}")

    def positions(self, root_id, recompute=False):
        return self.get(f"/api/positions/{root_id}",
                        **({"recompute": "true"} if recompute else {}))

    # ---------------------------------------------------------------- письмо
    def companion(self, text, connect_to=None, history=(), scope="near"):
        return self.post("/api/draft/companion", {
            "text": text, "connect_to": connect_to,
            "history": list(history), "scope": scope})

    def argument(self, text, connect_to=None, edge_type=None, kind=None,
                 title=None, domain=None, sub=None, geo=None, tags=None):
        body = {"text": text}
        for k, v in (("connect_to", connect_to), ("edge_type", edge_type),
                     ("kind", kind), ("title", title), ("domain", domain),
                     ("sub", sub), ("geo", geo), ("tags", tags)):
            if v is not None:
                body[k] = v
        return self.post("/api/argument", body)

    def react(self, node_id, stance):
        return self.post("/api/reactions", {"node_id": node_id, "stance": stance})

    # ---------------------------------------------------------------- голосование
    def create_decision(self, root_id, question):
        return self.post("/api/decisions",
                         {"topic_root_id": root_id, "question": question})

    def add_option(self, decision_id, position_id=None, label=None):
        body = {"origin": "initial"}
        if position_id is not None:
            body["position_id"] = position_id
        if label is not None:
            body["label"] = label
        return self.post(f"/api/decisions/{decision_id}/options", body)

    def open_decision(self, decision_id):
        return self.post(f"/api/decisions/{decision_id}/open")

    def decision(self, decision_id):
        return self.get(f"/api/decisions/{decision_id}")

    def dlg_start(self, decision_id):
        return self.post(f"/api/decisions/{decision_id}/dialogue/start")

    def dlg_message(self, decision_id, text):
        return self.post(f"/api/decisions/{decision_id}/dialogue/message",
                         {"text": text})

    def dlg_inform(self, decision_id, accept=True):
        return self.post(f"/api/decisions/{decision_id}/dialogue/inform",
                         {"accept": accept})

    def dlg_finalize(self, decision_id):
        return self.post(f"/api/decisions/{decision_id}/dialogue/finalize")

    def cast_vote(self, decision_id, option_ids):
        return self.post(f"/api/decisions/{decision_id}/vote",
                         {"option_ids": option_ids})

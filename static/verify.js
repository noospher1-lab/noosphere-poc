// Вынесено из verify.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);
const token = new URLSearchParams(location.search).get("token");

function show(kind, text) {
  const m = $("#msg");
  m.className = "msg " + kind;
  m.textContent = text;
  m.classList.remove("hidden");
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch (_) { /* пустой ответ */ }
  if (!r.ok) throw new Error((data && data.detail) || ("ошибка " + r.status));
  return data;
}

// Кнопка «прислать заново» бьёт по ручке, требующей сессии: если человек
// открыл письмо в браузере, где не залогинен, честнее отправить его на вход,
// чем показать голое 401.
$("#againBtn").onclick = async () => {
  $("#againBtn").disabled = true;
  try {
    const d = await post("/api/auth/verify/resend");
    show("ok", d && d.already
      ? "Адрес уже подтверждён — письмо не нужно."
      : "Отправили новое письмо. Ссылка живёт 48 часов.");
  } catch (e) {
    show("err", /401|403/.test(e.message)
      ? "Сначала войди в аккаунт, потом нажми ещё раз."
      : e.message);
    $("#againBtn").disabled = false;
  }
};

(async () => {
  if (!token) {
    $("#lead").textContent = "Ссылка неполная — в ней нет кода подтверждения. "
      + "Открой её из письма целиком или запроси новое письмо.";
    $("#again").classList.remove("hidden");
    return;
  }
  try {
    await post("/api/auth/verify", { token });
    $("#head").textContent = "Почта подтверждена";
    // Регистрация завершается здесь, но вход — отдельное действие: ссылка из
    // письма не должна быть входом, её могли переслать или открыть на чужом
    // устройстве.
    $("#lead").textContent = "Аккаунт готов. Войдите логином и паролем — и "
      + "можно писать: заводить проблемы, возражать и разветвлять позиции.";
    show("ok", "Адрес подтверждён.");
  } catch (e) {
    $("#lead").textContent = "";
    show("err", e.message);
    $("#again").classList.remove("hidden");
  }
})();

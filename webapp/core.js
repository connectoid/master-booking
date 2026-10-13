// Общее для клиентской части и кабинета мастера: платформа, вызовы API, утилиты.
(function () {
  "use strict";

  const MAX = window.WebApp && window.WebApp.initData ? window.WebApp : null;
  const TG = !MAX && window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData
    ? window.Telegram.WebApp : null;
  const bridge = MAX || TG;
  const channel = MAX ? "max" : TG ? "telegram" : "web";
  const initData = bridge ? bridge.initData : "";
  const unsafe = (bridge && bridge.initDataUnsafe) || {};
  const query = new URLSearchParams(location.search);
  const startParam = String(unsafe.start_param || "").toLowerCase();
  // короткие адреса веб-версии: okoshko.website/<slug> и okoshko.website/cabinet
  const path = location.pathname.replace(/\/+$/, "");
  const pathSlug = /^\/[a-z0-9_]{3,40}$/.test(path) && path !== "/app" && path !== "/cabinet" ? path.slice(1) : "";
  // кабинет мастера: max.ru/<бот>?startapp=cabinet, /cabinet в браузере (или ?cabinet для отладки)
  const mode = startParam === "cabinet" || query.has("cabinet") || path === "/cabinet" ? "cabinet" : "client";

  // localStorage может быть недоступен (приватный режим) — тогда просто не помним
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) { /* нет */ } },
  };
  let authHeader = null; // вход из браузера: «web <токен>» (клиент) или «master <токен>» (кабинет)
  function setAuth(value) { authHeader = value; }

  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря"];

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "value") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function haptic(kind) {
    try {
      const hf = bridge && bridge.HapticFeedback;
      if (!hf) return;
      if (kind === "select") hf.selectionChanged();
      else if (kind === "success" || kind === "error") hf.notificationOccurred(kind);
      else hf.impactOccurred("light");
    } catch (_) { /* не критично */ }
  }

  async function api(path, opts) {
    const headers = { "Content-Type": "application/json" };
    if (initData) {
      headers["Authorization"] = "tma " + initData;
      headers["X-Channel"] = channel;
    } else if (authHeader) {
      headers["Authorization"] = authHeader;
    }
    const r = await fetch("/api" + path, Object.assign({ headers }, opts || {}));
    let data = null;
    try { data = await r.json(); } catch (_) { /* пустой ответ */ }
    if (!r.ok) {
      let msg = "Что-то пошло не так. Попробуйте ещё раз.";
      if (data && typeof data.detail === "string") msg = data.detail;
      else if (r.status === 422) msg = "Проверьте введённые данные.";
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  // Кнопка «назад» платформы: один обработчик, экран сам решает, что делать
  let backHandler = null;
  const hasNativeBack = !!(bridge && bridge.BackButton);
  if (bridge && bridge.BackButton) {
    try { bridge.BackButton.onClick(() => backHandler && backHandler()); } catch (_) { /* нет */ }
  }
  function setBack(fn) {
    backHandler = fn;
    try {
      if (bridge && bridge.BackButton) fn ? bridge.BackButton.show() : bridge.BackButton.hide();
    } catch (_) { /* нет кнопки назад */ }
  }

  const price = (p) => p === null || p === undefined
    ? "по договорённости" : p.toLocaleString("ru-RU") + " ₽";
  const duration = (m) => m < 60 ? m + " мин"
    : Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
  const timeOf = (iso) => iso.slice(11, 16); // ISO уже в поясе мастера
  function dateParts(isoDate) {
    const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
    const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return { wd: WD[wd], day: d, month: MONTHS[m - 1] };
  }
  const dayLabel = (isoDate) => {
    const p = dateParts(isoDate);
    return `${p.wd}, ${p.day} ${p.month}`;
  };

  // Тема: Telegram сообщает свою (может не совпадать с темой телефона)
  function applyTheme() {
    if (!TG || !TG.colorScheme) return;
    document.documentElement.dataset.theme = TG.colorScheme;
    try {
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
      if (bg && TG.setHeaderColor) TG.setHeaderColor(bg);
      if (bg && TG.setBackgroundColor) TG.setBackgroundColor(bg);
    } catch (_) { /* старый клиент */ }
  }
  if (TG) {
    applyTheme();
    try { TG.onEvent("themeChanged", applyTheme); } catch (_) { /* нет событий */ }
  }

  function ready() {
    try { if (bridge && bridge.ready) bridge.ready(); } catch (_) { /* нет метода */ }
    try { if (TG && TG.expand) TG.expand(); } catch (_) { /* нет метода */ }
  }

  // Телефон из профиля мессенджера. MAX отдаёт номер приложению; Telegram — боту,
  // но новые клиенты Telegram возвращают его и в ответе. null — не получилось.
  function requestPhone() {
    return new Promise((resolve) => {
      try {
        if (MAX && MAX.requestContact) {
          Promise.resolve(MAX.requestContact())
            .then((res) => resolve(res && res.phone ? String(res.phone) : null), () => resolve(null));
        } else if (TG && TG.requestContact) {
          TG.requestContact((ok, res) => {
            const c = ok && res && res.responseUnsafe && res.responseUnsafe.contact;
            resolve(c && c.phone_number ? String(c.phone_number) : null);
          });
        } else resolve(null);
      } catch (_) { resolve(null); }
    });
  }
  const canRequestPhone = !!((MAX && MAX.requestContact) || (TG && TG.requestContact && TG.isVersionAtLeast && TG.isVersionAtLeast("6.9")));

  // Telegram: бот может писать человеку, только если тот разрешил. Спрашиваем перед записью,
  // иначе подтверждение и напоминания не дойдут. true — писать можно (или это не Telegram).
  function ensureCanMessage() {
    return new Promise((resolve) => {
      if (!TG || (unsafe.user && unsafe.user.allows_write_to_pm)) return resolve(true);
      if (!TG.requestWriteAccess || !(TG.isVersionAtLeast && TG.isVersionAtLeast("6.9"))) return resolve(true);
      try { TG.requestWriteAccess((allowed) => resolve(!!allowed)); } catch (_) { resolve(true); }
    });
  }

  window.OK = {
    MAX, TG, bridge, channel, initData, unsafe, query, startParam, mode, WD, MONTHS,
    h, haptic, api, setBack, price, duration, timeOf, dateParts, dayLabel, ready,
    requestPhone, canRequestPhone, ensureCanMessage,
    pathSlug, store, setAuth, hasNativeBack, currentBack: () => backHandler,
  };
})();

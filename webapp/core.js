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
  // кабинет мастера: ссылка max.ru/<бот>?startapp=cabinet (или ?cabinet для отладки)
  const mode = startParam === "cabinet" || query.has("cabinet") ? "cabinet" : "client";

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

  function ready() {
    try { if (bridge && bridge.ready) bridge.ready(); } catch (_) { /* нет метода */ }
  }

  window.OK = {
    MAX, TG, bridge, channel, initData, unsafe, query, startParam, mode, WD, MONTHS,
    h, haptic, api, setBack, price, duration, timeOf, dateParts, dayLabel, ready,
  };
})();

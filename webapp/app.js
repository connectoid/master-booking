// Окошко — Mini App записи к мастеру. Работает в MAX (window.WebApp), в Telegram
// (window.Telegram.WebApp) и в обычном браузере (okoshko.website/<slug>: запись по имени и телефону).
(function () {
  "use strict";

  const {
    MAX, bridge, channel, unsafe, query, h, haptic, api, setBack,
    price, duration, timeOf, dateParts, dayLabel, ready,
    requestPhone, canRequestPhone, ensureCanMessage, pathSlug, store, setAuth, hasNativeBack, currentBack,
  } = window.OK;
  if (window.OK.mode !== "client") return; // кабинет мастера — в cabinet.js
  const slug = String(unsafe.start_param || query.get("m") || pathSlug || "").toLowerCase();
  const tgUser = unsafe.user || {};
  const web = channel === "web";

  // Браузер: анонимный токен посетителя. Ссылка «…/slug#k=<токен>» переносит записи на другое устройство.
  let webToken = null;
  if (web) {
    const fromHash = /(?:^#|&)k=([0-9a-f]{16}\.[0-9a-f]{32})/.exec(location.hash);
    if (fromHash) {
      store.set("okWeb", fromHash[1]);
      try { history.replaceState(null, "", location.pathname + location.search); } catch (_) { /* нет */ }
    }
    webToken = store.get("okWeb");
    if (webToken) setAuth("web " + webToken);
  }
  async function ensureWebToken() {
    if (!web || webToken) return;
    webToken = (await api("/web/session", { method: "POST" })).token;
    store.set("okWeb", webToken);
    setAuth("web " + webToken);
  }
  const canSeeMy = () => !web || !!webToken;

  const state = {
    master: null, service: null, slots: null, day: null, slot: null,
    view: "services", appointment: null, my: [], sending: false, error: "", confirmCancel: null,
    bookedDays: new Set(), // дни, на которые клиент уже записан (если мастер ограничил «1 в день»)
    form: { name: [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" "), phone: "", comment: "" },
  };
  const app = document.getElementById("app");

  function tzNote(sampleIso) {
    // сдвиг пояса мастера vs устройства клиента
    const m = /([+-]\d{2}):(\d{2})$/.exec(sampleIso || "");
    if (!m) return null;
    const masterOffset = (Number(m[1]) * 60) + Math.sign(Number(m[1]) || 1) * Number(m[2]);
    const localOffset = -new Date().getTimezoneOffset();
    if (masterOffset === localOffset) return null;
    return `Время указано по часовому поясу мастера (UTC${m[1]}:${m[2]})`;
  }

  // ---------- навигация ----------
  const back = {
    slots: () => go("services"),
    confirm: () => go("slots"),
    my: () => go("services"),
    done: () => go("services"),
  };
  function go(view) {
    state.view = view;
    state.error = "";
    setBack(back[view] || null);
    render();
    window.scrollTo(0, 0);
  }

  // ---------- экраны ----------
  function header() {
    const m = state.master;
    return h("div", { class: "header" },
      h("div", { class: "avatar" }, (m.name || "?").trim().charAt(0).toUpperCase()),
      h("div", { class: "grow" },
        h("h1", null, m.name),
        h("div", { class: "sub" }, "Онлайн-запись")),
      canSeeMy() && state.view !== "my"
        ? h("button", { class: "link-btn", onclick: openMy }, "Мои записи") : null,
    );
  }

  // в браузере нет кнопки «назад» мессенджера — рисуем свою
  function backLink() {
    const fn = !hasNativeBack && currentBack();
    return fn ? h("button", { class: "back-link", onclick: () => fn() }, "← Назад") : null;
  }

  function viewServices() {
    const services = state.master.services;
    return [
      header(),
      h("h2", null, "Выберите услугу"),
      services.length === 0
        ? h("p", { class: "muted" }, "Мастер ещё не добавил услуги. Загляните позже.")
        : h("div", { class: "list" }, services.map((s) =>
          h("button", { class: "item", onclick: () => pickService(s) },
            h("div", { class: "grow" },
              h("div", { class: "title" }, s.name),
              h("div", { class: "meta" }, duration(s.duration_min))),
            h("div", { class: "price" }, price(s.price))))),
      footer(),
    ];
  }

  function serviceSummary() {
    const s = state.service;
    return h("div", { class: "summary" },
      h("div", { class: "title" }, s.name),
      h("div", { class: "small muted" }, `${duration(s.duration_min)} · ${price(s.price)}`));
  }

  function viewSlots() {
    const out = [header(), serviceSummary()];
    if (state.slots === null) {
      out.push(h("div", { class: "center muted" }, "Ищем свободное время…"));
      return out;
    }
    const days = state.slots.days;
    if (days.length === 0) {
      out.push(h("p", { class: "center muted" },
        `В ближайшие ${state.master.horizon_days || 30} дней свободного времени нет. `
        + "Напишите мастеру напрямую."));
      return out;
    }
    out.push(h("h2", null, "Выберите день"));
    out.push(h("div", { class: "days" }, days.map((d, i) => {
      const p = dateParts(d.date);
      const showMonth = i === 0 || dateParts(days[i - 1].date).month !== p.month;
      return h("button", {
        class: "day" + (state.day === d.date ? " active" : ""),
        disabled: state.bookedDays.has(d.date),
        onclick: () => { state.day = d.date; state.slot = null; haptic("select"); render(); },
      }, h("div", { class: "mon" }, showMonth ? p.month.slice(0, 3) : "\u00a0"),
        h("div", { class: "wd" }, p.wd), h("div", { class: "dn" }, p.day));
    })));
    if (state.bookedDays.size) {
      out.push(h("p", { class: "small muted" },
        "Неактивные дни — у вас уже есть запись: мастер принимает одну запись в день."));
    }
    const current = days.find((d) => d.date === state.day) || days[0];
    if (state.bookedDays.has(current.date)) {
      out.push(h("p", { class: "muted" }, "Выберите другой день."));
      return out;
    }
    out.push(h("h2", null, dayLabel(current.date)));
    out.push(h("div", { class: "slots" }, current.slots.map((iso) =>
      h("button", {
        class: "slot" + (state.slot === iso ? " active" : ""),
        onclick: () => { state.slot = iso; haptic("select"); go("confirm"); },
      }, timeOf(iso)))));
    const note = tzNote(current.slots[0]);
    if (note) out.push(h("p", { class: "small muted" }, note));
    if (state.error) out.splice(2, 0, h("div", { class: "error" }, state.error));
    return out;
  }

  function viewConfirm() {
    const f = state.form;
    const input = (key, attrs) => h("input", Object.assign({
      value: f[key], oninput: (e) => { f[key] = e.target.value; },
    }, attrs));
    const out = [
      header(),
      serviceSummary(),
      h("h2", null, `${dayLabel(state.slot)}, ${timeOf(state.slot)}`),
    ];
    out.push(
      h("label", { for: "name" }, "Ваше имя"),
      input("name", { id: "name", autocomplete: "name", maxlength: "120" }),
      h("label", { for: "phone" }, web ? "Телефон" : "Телефон (необязательно)"),
      h("div", { class: "row" },
        input("phone", { id: "phone", type: "tel", autocomplete: "tel", placeholder: "+7…" }),
        canRequestPhone
          ? h("button", { class: "btn secondary small-btn", onclick: fillPhone },
            channel === "telegram" ? "Из Telegram" : "Из MAX") : null),
      h("label", { for: "comment" }, "Комментарий мастеру (необязательно)"),
      h("textarea", { id: "comment", maxlength: "500", oninput: (e) => { f.comment = e.target.value; } },
        f.comment),
      state.error ? h("div", { class: "error" }, state.error) : null,
      h("button", { class: "btn", onclick: submit, disabled: state.sending },
        state.sending ? "Записываем…" : "Записаться"),
      h("p", { class: "small muted consent" },
        "Нажимая «Записаться», вы соглашаетесь на обработку ваших данных для записи и напоминаний — ",
        h("a", { href: "/privacy", target: "_blank", rel: "noopener" }, "политика конфиденциальности"), "."),
    );
    return out;
  }

  function viewDone() {
    const a = state.appointment;
    if (web) return viewDoneWeb(a);
    return [
      h("div", { class: "done-icon" }, "✓"),
      h("div", { class: "center", style: "padding:0" },
        h("h2", null, "Вы записаны"),
        h("p", null, `${a.service_name}`),
        h("p", null, h("b", null, `${dayLabel(a.starts_at)}, ${timeOf(a.starts_at)}`)),
        h("p", { class: "muted small" }, `Мастер: ${state.master.name}. ` + (state.canMessage === false
          ? "Вы не разрешили боту писать вам — напоминания не придут. Запись видна в «Мои записи»."
          : "Подтверждение и напоминания придут в чат с ботом."))),
      h("button", { class: "btn secondary", onclick: openMy }, "Мои записи"),
      bridge && bridge.close ? h("button", { class: "btn", onclick: () => bridge.close() }, "Готово") : null,
    ];
  }

  function myLink() {
    return `${location.origin}/${slug}#k=${webToken}`;
  }

  async function copyMyLink() {
    try {
      await navigator.clipboard.writeText(myLink());
      state.copied = true;
    } catch (_) {
      state.copied = "manual";
    }
    render();
  }

  async function openRemindLink() {
    // окно открываем сразу (иначе браузер посчитает его всплывающей рекламой), адрес — после ответа
    const win = window.open("about:blank", "_blank");
    try {
      const { url } = await api("/web/remind-link");
      if (!url) throw new Error("Бот MAX сейчас недоступен — попробуйте позже.");
      if (win) win.location.href = url; else location.href = url;
    } catch (e) {
      if (win) win.close();
      state.error = e.message;
      render();
    }
  }

  function viewDoneWeb(a) {
    return [
      h("div", { class: "done-icon" }, "✓"),
      h("div", { class: "center", style: "padding:0" },
        h("h2", null, "Вы записаны"),
        h("p", null, `${a.service_name}`),
        h("p", null, h("b", null, `${dayLabel(a.starts_at)}, ${timeOf(a.starts_at)}`)),
        h("p", { class: "muted small" }, `Мастер: ${state.master.name}. Если что-то изменится, мастер позвонит.`)),
      h("div", { class: "card" },
        h("div", { class: "set-title" }, "Напоминание о записи"),
        h("div", { class: "set-hint" },
          "Пришлём напоминание за день и за 2 часа в MAX — там же можно отменить запись."),
        h("button", { class: "btn", onclick: openRemindLink }, "Получать напоминания в MAX")),
      h("div", { class: "card" },
        h("div", { class: "set-title" }, "Ваши записи"),
        h("div", { class: "set-hint" },
          "Записи видны в этом браузере в «Мои записи». Чтобы открыть их на другом устройстве, сохраните ссылку:"),
        state.copied === "manual" ? h("div", { class: "link-box" }, myLink()) : null,
        h("button", { class: "btn secondary", onclick: copyMyLink },
          state.copied === true ? "Ссылка скопирована" : "Скопировать ссылку на мои записи")),
      state.error ? h("div", { class: "error" }, state.error) : null,
      h("button", { class: "btn secondary", onclick: openMy }, "Мои записи"),
    ];
  }

  function viewMy() {
    const out = [header(), h("h2", null, "Мои записи")];
    if (state.my === null) {
      out.push(h("div", { class: "center muted" }, "Загрузка…"));
    } else if (state.my.length === 0) {
      out.push(h("p", { class: "muted" }, "Предстоящих записей нет."));
    } else {
      out.push(h("div", { class: "list" }, state.my.map((a) =>
        h("div", { class: "item", style: "cursor:default" },
          h("div", { class: "grow" },
            h("div", { class: "title" }, `${dayLabel(a.starts_at)}, ${timeOf(a.starts_at)}`),
            h("div", { class: "meta" }, a.service_name)),
          h("button", { class: "btn secondary small-btn danger", onclick: () => cancel(a) },
            state.confirmCancel === a.id ? "Точно отменить?" : "Отменить")))));
    }
    if (state.error) out.push(h("div", { class: "error" }, state.error));
    out.push(h("button", { class: "btn", onclick: () => go("services") }, "Записаться ещё"));
    return out;
  }

  function footer() {
    return h("div", { class: "footer" }, "Онлайн-запись для мастеров — ",
      h("a", { href: "https://okoshko.website", target: "_blank", rel: "noopener" }, "Окошко"));
  }

  function render() {
    const views = { services: viewServices, slots: viewSlots, confirm: viewConfirm, done: viewDone, my: viewMy };
    app.replaceChildren(...[backLink()].concat(views[state.view]()).filter(Boolean));
  }

  function fatal(text) {
    app.replaceChildren(h("div", { class: "center" },
      h("p", null, text),
      h("button", { class: "btn secondary", onclick: () => location.reload() }, "Обновить")));
  }

  // ---------- действия ----------
  async function pickService(s) {
    haptic();
    state.service = s;
    state.slots = null;
    state.day = null;
    state.slot = null;
    go("slots");
    await loadSlots();
  }

  async function loadSlots() {
    try {
      const days = Math.min((state.master.horizon_days || 30) + 1, 92);
      state.slots = await api(
        `/masters/${encodeURIComponent(slug)}/slots?service_id=${state.service.id}&days=${days}`);
      const first = state.slots.days.find((d) => !state.bookedDays.has(d.date))
        || state.slots.days[0];
      if (!state.day || !state.slots.days.some((d) => d.date === state.day)
          || state.bookedDays.has(state.day)) {
        state.day = first ? first.date : null;
      }
    } catch (e) {
      state.slots = { days: [] };
      state.error = e.message;
    }
    if (state.view === "slots") render();
  }

  async function fillPhone() {
    const phone = await requestPhone();
    if (phone) {
      state.form.phone = phone;
      render();
    }
  }

  async function submit() {
    if (state.sending) return;
    if (web && (state.form.name.trim().length < 2 || state.form.phone.replace(/\D/g, "").length < 10)) {
      state.error = "Укажите имя и телефон — мастер позвонит, если что-то изменится.";
      haptic("error");
      render();
      return;
    }
    state.sending = true;
    state.error = "";
    state.copied = false;
    render();
    state.canMessage = await ensureCanMessage();
    try {
      await ensureWebToken();
      state.appointment = await api(`/masters/${encodeURIComponent(slug)}/appointments`, {
        method: "POST",
        body: JSON.stringify({
          service_id: state.service.id,
          starts_at: state.slot,
          client_name: state.form.name || null,
          phone: state.form.phone || null,
          comment: state.form.comment || null,
        }),
      });
      haptic("success");
      state.sending = false;
      go("done");
      loadBookedDays();
    } catch (e) {
      haptic("error");
      state.sending = false;
      if (e.status === 409 || (e.status === 422 && /время/i.test(e.message))) {
        // время заняли — обновляем слоты и возвращаем к выбору
        state.slot = null;
        state.slots = null;
        go("slots");
        state.error = e.message;
        await loadSlots();
        return;
      }
      state.error = e.message;
      render();
    }
  }

  async function openMy() {
    haptic();
    state.my = null;
    go("my");
    try {
      state.my = await api(`/masters/${encodeURIComponent(slug)}/appointments/my`);
    } catch (e) {
      state.my = [];
      state.error = e.message;
    }
    if (state.view === "my") render();
  }

  async function cancel(a) {
    if (state.confirmCancel !== a.id) {  // первое нажатие — просим подтвердить
      state.confirmCancel = a.id;
      haptic();
      render();
      return;
    }
    state.confirmCancel = null;
    try {
      await api(`/appointments/${a.id}/cancel`, { method: "POST" });
      haptic("success");
      state.my = state.my.filter((x) => x.id !== a.id);
      state.error = "";
      loadBookedDays();
    } catch (e) {
      state.error = e.message;
    }
    render();
  }

  async function loadBookedDays() {
    if (!state.master.one_booking_per_day || !canSeeMy()) return;
    try {
      const my = await api(`/masters/${encodeURIComponent(slug)}/appointments/my`);
      state.bookedDays = new Set(my.map((a) => a.starts_at.slice(0, 10)));
      if (state.view === "slots") render();
    } catch (_) { /* не критично: сервер всё равно проверит */ }
  }

  // ---------- старт ----------
  async function start() {
    ready();
    if (!slug) {
      fatal(web ? "Откройте страницу по ссылке мастера, например okoshko.website/имя_мастера."
        : "Откройте приложение по ссылке мастера.");
      return;
    }
    try {
      state.master = await api(`/masters/${encodeURIComponent(slug)}`);
    } catch (e) {
      fatal(e.status === 404 ? "Мастер по этой ссылке не найден." : e.message);
      return;
    }
    document.title = `${state.master.name} — запись`;
    go("services");
    loadBookedDays();
  }

  start();
})();

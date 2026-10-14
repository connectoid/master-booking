// Окошко — админка владельца сервиса: сводка, мастера, продление и блокировка.
// Вход — по ссылке из бота MAX (команда /admin), сессия хранится в браузере.
(function () {
  "use strict";

  const { h, api, query, store, setAuth, mode } = window.OK;
  if (mode !== "admin") return;

  const app = document.getElementById("app");
  document.body.classList.add("admin");

  const STATUS = {
    active: ["активен", "ok"], idle: ["нет записей 30 дн", "muted"],
    setup: ["не настроен", "warn"], blocked: ["заблокирован", "bad"],
  };
  const SOURCE = { max: "MAX", web: "сайт", manual: "вручную", telegram: "Telegram" };
  const FILTERS = [["all", "Все"], ["active", "Активные"], ["idle", "Без записей"], ["setup", "Не настроены"], ["blocked", "Заблокированы"]];

  const state = {
    stats: null, masters: null, filter: "all", q: "", open: null, detail: null,
    noteDraft: null, msgDraft: "", dateDraft: "", confirmBlock: false, notice: "", error: "",
  };

  const num = (n) => Number(n || 0).toLocaleString("ru-RU");
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" }) : "—";
  const fmtShort = (iso) => { const [, m, d] = iso.split("-"); return `${Number(d)}.${m}`; };
  const daysLeft = (iso) => iso ? Math.ceil((new Date(iso) - Date.now()) / 86400000) : null;

  // ---------- вход ----------
  async function login() {
    const code = query.get("login");
    if (code) {
      try { history.replaceState(null, "", "/admin"); } catch (_) { /* нет */ }
      try {
        const { token } = await api("/web/admin-login", { method: "POST", body: JSON.stringify({ code }) });
        store.set("okAdmin", token);
      } catch (e) {
        return e.message;
      }
    }
    const token = store.get("okAdmin");
    if (token) setAuth("admin " + token);
    return token ? null : "";
  }

  function viewLogin(error) {
    document.title = "Админка · Окошко";
    app.replaceChildren(h("div", { class: "login" },
      h("div", { class: "avatar" }, "О"),
      h("h1", null, "Админка «Окошко»"),
      error ? h("div", { class: "error" }, error) : null,
      h("p", null, "Отправьте боту «Окошко» в MAX команду ", h("b", null, "/admin"),
        ". Придёт ссылка для входа, она действует 10 минут."),
      h("p", { class: "small muted" }, "Доступ есть только у ID из ADMIN_MAX_IDS в .env на сервере. Свой ID бот покажет по команде /myid.")));
  }

  function logout() {
    store.set("okAdmin", null);
    setAuth(null);
    viewLogin("");
  }

  // ---------- данные ----------
  async function load() {
    try {
      const [stats, masters] = await Promise.all([
        api("/admin/stats"),
        api(`/admin/masters?status=${state.filter}&q=${encodeURIComponent(state.q)}`),
      ]);
      state.stats = stats;
      state.masters = masters;
      state.error = "";
    } catch (e) {
      if (e.status === 401) { store.set("okAdmin", null); viewLogin(e.message); return; }
      state.error = e.message;
    }
    render();
  }

  async function reloadMasters() {
    try {
      state.masters = await api(`/admin/masters?status=${state.filter}&q=${encodeURIComponent(state.q)}`);
    } catch (e) { state.error = e.message; }
    render();
  }

  async function openMaster(id) {
    if (state.open === id) { state.open = null; render(); return; }
    state.open = id;
    state.detail = null;
    state.noteDraft = null;
    state.msgDraft = "";
    state.dateDraft = "";
    state.confirmBlock = false;
    render();
    try { state.detail = await api(`/admin/masters/${id}`); } catch (e) { state.error = e.message; }
    render();
  }

  async function act(path, opts, okText) {
    state.error = "";
    try {
      const res = await api(path, opts);
      if (res && res.id) {
        state.detail = res;
        const i = state.masters.findIndex((m) => m.id === res.id);
        if (i >= 0) state.masters[i] = Object.assign({}, state.masters[i], res);
      }
      state.notice = okText;
      setTimeout(() => { if (state.notice === okText) { state.notice = ""; render(); } }, 2500);
    } catch (e) {
      state.error = e.message;
    }
    state.confirmBlock = false;
    render();
    return !state.error;
  }

  const patch = (body, ok) => act(`/admin/masters/${state.open}`, { method: "PATCH", body: JSON.stringify(body) }, ok);

  // ---------- экраны ----------
  function tile(label, value, sub) {
    return h("div", { class: "tile" },
      h("div", { class: "tile-label" }, label),
      h("div", { class: "tile-value" }, value),
      sub ? h("div", { class: "tile-sub" }, sub) : null);
  }

  function chart(daily) {
    const max = Math.max(1, ...daily.map((d) => d.count));
    const total = daily.reduce((s, d) => s + d.count, 0);
    return h("div", { class: "card chart-card" },
      h("div", { class: "row-between" },
        h("div", { class: "set-title", style: "margin:0" }, "Новые записи по дням"),
        h("div", { class: "small muted" }, `${num(total)} за ${daily.length} дн.`)),
      h("div", { class: "bars", role: "img", "aria-label": "Новые записи по дням за последние 30 дней" },
        daily.map((d) => h("div", { class: "bar-col", tabindex: "0", "data-tip": `${fmtShort(d.date)}: ${d.count}` },
          h("div", { class: "bar" + (d.count ? "" : " zero"), style: `height:${Math.max(d.count ? 6 : 2, (d.count / max) * 100)}%` })))),
      h("div", { class: "bars-axis small muted" },
        h("span", null, fmtShort(daily[0].date)), h("span", null, "сегодня")),
      // таблица — для тех, кому график неудобен
      h("details", { class: "small" }, h("summary", null, "Таблицей"),
        h("table", { class: "mini-table" },
          daily.filter((d) => d.count).map((d) => h("tr", null, h("td", null, fmtShort(d.date)), h("td", null, d.count))))));
  }

  function statsView() {
    const s = state.stats;
    if (!s) return null;
    const src = Object.entries(s.by_source_30d || {}).map(([k, v]) => `${SOURCE[k] || k} ${v}`).join(" · ") || "—";
    return [
      h("div", { class: "tiles" },
        tile("Мастеров", num(s.masters_total), `+${s.masters_new_7d} за неделю · +${s.masters_new_30d} за месяц`),
        tile("Активных за 30 дн", num(s.masters_active_30d), "получали записи"),
        tile("Записей за 30 дн", num(s.appointments_30d), src),
        tile("Предстоящих", num(s.upcoming), `отмен за 30 дн: ${s.cancelled_30d}`),
        tile("Клиентов", num(s.clients_total), `всего записей: ${num(s.appointments_total)}`)),
      chart(s.daily),
    ];
  }

  function badge(status) {
    const [text, kind] = STATUS[status] || [status, "muted"];
    return h("span", { class: "st st-" + kind }, text);
  }

  function paidText(m) {
    if (!m.paid_until) return "—";
    const left = daysLeft(m.paid_until);
    return `${fmtDate(m.paid_until)}` + (left < 0 ? " (истёк)" : ` (${left} дн)`);
  }

  function detailView() {
    const d = state.detail;
    if (!d) return h("div", { class: "detail muted" }, "Загрузка…");
    const note = state.noteDraft === null ? (d.admin_note || "") : state.noteDraft;
    return h("div", { class: "detail" },
      h("div", { class: "detail-grid" },
        h("div", null,
          h("div", { class: "set-title" }, "Профиль"),
          h("div", { class: "kv" }, "Адрес: ", d.site_url ? h("a", { href: d.site_url, target: "_blank", rel: "noopener" }, d.site_url) : d.slug),
          h("div", { class: "kv" }, `Мессенджеры: ${d.channels.map((c) => SOURCE[c] || c).join(", ") || "—"}`),
          h("div", { class: "kv" }, `Часовой пояс: ${d.timezone}`),
          h("div", { class: "kv" }, `Зарегистрирован: ${fmtDate(d.created_at)}`),
          h("div", { class: "kv" }, `Последняя запись создана: ${fmtDate(d.last_booking_at)}`),
          h("div", { class: "set-title" }, `Услуги (${d.services_list.length})`),
          d.services_list.length ? h("ul", { class: "plain" }, d.services_list.map((s) =>
            h("li", { class: s.is_active ? "" : "muted" },
              `${s.name}, ${s.duration_min} мин, ${s.price === null ? "по договорённости" : num(s.price) + " ₽"}`
              + (s.is_active ? "" : " (скрыта)")))) : h("div", { class: "muted" }, "нет"),
          h("div", { class: "set-title" }, "График"),
          h("pre", { class: "sched" }, d.schedule)),
        h("div", null,
          h("div", { class: "set-title" }, "Доступ"),
          h("div", { class: "kv" }, `Оплачено до: ${paidText(d)}`),
          h("div", { class: "btn-row" },
            [30, 90, 365].map((n) => h("button", { class: "btn secondary small-btn",
              onclick: () => act(`/admin/masters/${d.id}/extend`, { method: "POST", body: JSON.stringify({ days: n }) }, `Продлено на ${n} дн`) },
            `+${n} дн`))),
          h("div", { class: "row inline-label" },
            h("input", { type: "date", value: state.dateDraft, class: "time", oninput: (e) => { state.dateDraft = e.target.value; } }),
            h("button", { class: "btn secondary small-btn", onclick: () => state.dateDraft && patch({ paid_until: state.dateDraft }, "Дата сохранена") }, "Поставить дату"),
            d.paid_until ? h("button", { class: "btn secondary small-btn danger", onclick: () => patch({ paid_until: null }, "Дата снята") }, "Снять") : null),
          h("div", { class: "set-hint" }, "На время запуска ни на что не влияет, только для учёта."),

          h("div", { class: "set-title" }, "Блокировка"),
          d.blocked_at
            ? [h("div", { class: "kv" }, `Заблокирован ${fmtDate(d.blocked_at)}, страница записи закрыта.`),
              h("button", { class: "btn secondary small-btn", onclick: () => patch({ blocked: false }, "Разблокирован") }, "Разблокировать")]
            : [h("button", { class: "btn secondary small-btn danger", onclick: () => {
              if (!state.confirmBlock) { state.confirmBlock = true; render(); return; }
              patch({ blocked: true }, "Заблокирован");
            } }, state.confirmBlock ? "Точно заблокировать?" : "Заблокировать"),
            h("div", { class: "set-hint" }, "Клиенты не смогут записаться. Кабинет и записи мастера сохранятся.")],

          h("div", { class: "set-title" }, "Заметка (видна только вам)"),
          h("textarea", { maxlength: "2000", oninput: (e) => { state.noteDraft = e.target.value; } }, note),
          state.noteDraft !== null && state.noteDraft !== (d.admin_note || "")
            ? h("button", { class: "btn secondary small-btn", onclick: async () => {
              if (await patch({ admin_note: state.noteDraft }, "Заметка сохранена")) state.noteDraft = null;
            } }, "Сохранить заметку") : null,

          h("div", { class: "set-title" }, "Написать мастеру в бот"),
          h("textarea", { maxlength: "2000", placeholder: "Например: «Здравствуйте! Как вам сервис? Нужна помощь с настройкой?»",
            oninput: (e) => { state.msgDraft = e.target.value; } }, state.msgDraft),
          h("button", { class: "btn secondary small-btn", onclick: async () => {
            if (!state.msgDraft.trim()) return;
            if (await act(`/admin/masters/${d.id}/message`, { method: "POST", body: JSON.stringify({ text: state.msgDraft }) }, "Сообщение отправлено")) {
              state.msgDraft = ""; render();
            }
          } }, "Отправить"))));
  }

  function mastersView() {
    if (state.masters === null) return h("div", { class: "muted" }, "Загрузка…");
    return [
      h("div", { class: "toolbar" },
        h("div", { class: "seg" }, FILTERS.map(([v, t]) => h("button", {
          class: "seg-btn" + (state.filter === v ? " active" : ""),
          onclick: () => { state.filter = v; state.open = null; reloadMasters(); },
        }, t))),
        h("input", { id: "aq", class: "search", placeholder: "Поиск по имени или адресу", value: state.q,
          oninput: (e) => { state.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(reloadMasters, 300); } })),
      state.masters.length ? h("div", { class: "mtable" },
        h("div", { class: "mrow mhead" },
          h("div", null, "Мастер"), h("div", null, "Статус"), h("div", { class: "num" }, "Записей 30 дн"),
          h("div", { class: "num" }, "Впереди"), h("div", { class: "num" }, "Клиентов"), h("div", null, "Оплачено до")),
        state.masters.flatMap((m) => [
          h("button", { class: "mrow" + (state.open === m.id ? " open" : ""), onclick: () => openMaster(m.id) },
            h("div", null, h("div", { class: "mname" }, m.name, m.admin_note ? h("span", { class: "note-dot", title: m.admin_note }, " •") : null),
              h("div", { class: "small muted" }, `${m.slug} · с ${fmtDate(m.created_at)}`)),
            h("div", null, badge(m.status)),
            h("div", { class: "num" }, num(m.appointments_30d)),
            h("div", { class: "num" }, num(m.upcoming)),
            h("div", { class: "num" }, num(m.clients)),
            h("div", { class: "small" }, paidText(m))),
          state.open === m.id ? detailView() : null,
        ])) : h("p", { class: "muted" }, "Никого не нашли."),
    ];
  }
  let searchTimer = null;

  function render() {
    const active = document.activeElement && document.activeElement.id;
    const pos = active ? document.activeElement.selectionStart : null;
    app.replaceChildren(...[
      h("div", { class: "header" },
        h("div", { class: "avatar" }, "О"),
        h("div", { class: "grow" }, h("h1", null, "Админка"), h("div", { class: "sub" }, "Окошко")),
        h("button", { class: "link-btn", onclick: load }, "Обновить"),
        h("button", { class: "link-btn", onclick: logout }, "Выйти")),
      state.error ? h("div", { class: "error" }, state.error) : null,
      statsView(),
      h("h2", null, "Мастера"),
      mastersView(),
      state.notice ? h("div", { class: "toast" }, state.notice) : null,
    ].flat(Infinity).filter(Boolean));
    if (active) {
      const el = document.getElementById(active);
      if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch (_) { /* нет */ } }
    }
  }

  async function start() {
    const err = await login();
    if (err !== null) { viewLogin(err); return; }
    document.title = "Админка · Окошко";
    app.replaceChildren(h("div", { class: "center muted" }, "Загрузка…"));
    await load();
  }

  start();
})();

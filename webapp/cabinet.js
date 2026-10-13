// Окошко — кабинет мастера: записи (календарь + ручная запись) и недельный график.
(function () {
  "use strict";

  const { h, haptic, api, setBack, ready, channel, dateParts, dayLabel, timeOf, duration, price } = window.OK;
  if (window.OK.mode !== "cabinet") return;

  const DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
  const app = document.getElementById("app");

  const state = {
    tab: "appointments", // appointments | schedule | days
    cal: null,           // календарь особых дней: {month, overrides, counts, selected, range, mode, ...}
    view: "list",        // list | new (внутри вкладки «Записи»)
    appts: null,         // записи на 14 дней
    day: null,           // выбранный день (YYYY-MM-DD)
    services: null,
    form: null,          // форма ручной записи
    freeSlots: null,
    hints: [],           // подсказки клиентов
    confirmCancel: null,
    me: null,
    week: null,      // [{weekday, intervals: [{start: "10:00", end: "19:00"}]}] × 7
    saved: "",       // снимок последнего сохранённого графика — чтобы понимать, есть ли изменения
    saving: false,
    error: "",
    notice: "",
  };

  // ---------- время ----------
  const toMin = (t) => { const [hh, mm] = t.split(":").map(Number); return hh * 60 + mm; };
  const toTime = (m) => {
    m = Math.max(0, Math.min(m, 23 * 60 + 59));
    return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
  };
  const snapshot = () => JSON.stringify(state.week);
  const dirty = () => state.week && snapshot() !== state.saved;

  function validateDay(day) {
    const list = day.intervals.map((i) => [toMin(i.start), toMin(i.end)]).sort((a, b) => a[0] - b[0]);
    for (const [s, e] of list) if (s >= e) return "начало должно быть раньше конца";
    for (let i = 1; i < list.length; i++) if (list[i][0] < list[i - 1][1]) return "интервалы пересекаются";
    return "";
  }

  // ---------- действия с графиком ----------
  function toggleDay(day) {
    haptic("select");
    if (day.intervals.length) {
      day._last = day.intervals;   // запомним, чтобы вернуть при повторном включении
      day.intervals = [];
    } else {
      const prev = [...state.week].reverse().find((d) => d.weekday < day.weekday && d.intervals.length)
        || state.week.find((d) => d.intervals.length);
      day.intervals = day._last || (prev ? prev.intervals.map((i) => ({ ...i })) : [{ start: "10:00", end: "19:00" }]);
    }
    render();
  }

  function addInterval(day) {
    haptic();
    const last = day.intervals[day.intervals.length - 1];
    const start = last ? Math.min(toMin(last.end) + 60, 22 * 60) : 10 * 60;
    day.intervals.push({ start: toTime(start), end: toTime(Math.min(start + 180, 23 * 60 + 59)) });
    render();
  }

  function removeInterval(day, idx) {
    haptic();
    day.intervals.splice(idx, 1);
    render();
  }

  function copyToWeekdays(source) {
    haptic("select");
    for (const d of state.week) {
      if (d.weekday !== source.weekday && d.weekday < 5) {
        d.intervals = source.intervals.map((i) => ({ ...i }));
      }
    }
    state.notice = `Часы «${DAYS[source.weekday].toLowerCase()}» применены ко всем будням`;
    render();
  }

  async function save() {
    if (state.saving) return;
    const bad = state.week.map((d) => [d, validateDay(d)]).find(([, err]) => err);
    if (bad) {
      state.error = `${DAYS[bad[0].weekday]}: ${bad[1]}`;
      haptic("error");
      render();
      return;
    }
    state.saving = true;
    state.error = "";
    render();
    try {
      const res = await api("/cabinet/schedule", {
        method: "PUT",
        body: JSON.stringify({
          days: state.week.map((d) => ({ weekday: d.weekday, intervals: d.intervals })),
        }),
      });
      loadWeek(res);
      state.notice = "График сохранён";
      haptic("success");
    } catch (e) {
      state.error = e.message;
      haptic("error");
    }
    state.saving = false;
    render();
  }

  function loadWeek(schedule) {
    state.week = schedule.days.map((d) => ({
      weekday: d.weekday,
      intervals: d.intervals.map((i) => ({ start: i.start.slice(0, 5), end: i.end.slice(0, 5) })),
    }));
    state.saved = snapshot();
  }

  // ---------- экран ----------
  function timeInput(value, onchange) {
    return h("input", {
      type: "time", step: "900", value, class: "time",
      onchange: (e) => { if (e.target.value) { onchange(e.target.value); render(); } },
    });
  }

  function dayCard(day) {
    const on = day.intervals.length > 0;
    const err = on ? validateDay(day) : "";
    return h("div", { class: "card day-card" + (on ? "" : " off") },
      h("div", { class: "day-head" },
        h("div", { class: "day-name" }, DAYS[day.weekday]),
        h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: on, onchange: () => toggleDay(day) }),
          h("span", { class: "slider" }),
          h("span", { class: "switch-text" }, on ? "работаю" : "выходной"))),
      on ? h("div", { class: "intervals" },
        day.intervals.map((iv, idx) => h("div", { class: "interval" },
          timeInput(iv.start, (v) => { iv.start = v; }),
          h("span", { class: "dash" }, "—"),
          timeInput(iv.end, (v) => { iv.end = v; }),
          day.intervals.length > 1
            ? h("button", { class: "icon-btn", title: "Убрать", onclick: () => removeInterval(day, idx) }, "×")
            : h("span", { class: "icon-btn placeholder" }))),
        err ? h("div", { class: "field-error" }, err) : null,
        h("div", { class: "day-actions" },
          day.intervals.length < 4
            ? h("button", { class: "link-btn", onclick: () => addInterval(day) }, "+ перерыв / ещё интервал")
            : null,
          day.weekday === 0
            ? h("button", { class: "link-btn", onclick: () => copyToWeekdays(day) }, "Как в пн → все будни")
            : null)) : null);
  }

  function viewSchedule() {
    return [
      h("p", { class: "small muted" },
        "Обычная неделя. Перерыв — это второй интервал в тот же день. "
        + "Выходные и отпуск на конкретные даты пока настраиваются в боте."),
      state.notice ? h("div", { class: "notice" }, state.notice) : null,
      state.week.map(dayCard),
      h("div", { class: "save-bar" },
        state.error ? h("div", { class: "error" }, state.error) : null,
        h("button", { class: "btn", onclick: save, disabled: state.saving || !dirty() },
          state.saving ? "Сохраняем…" : dirty() ? "Сохранить график" : "Сохранено")),
    ];
  }

  // ---------- даты ----------
  function addDays(iso, n) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d + n));
    return dt.toISOString().slice(0, 10);
  }
  const days14 = () => Array.from({ length: 14 }, (_, i) => addDays(state.me.today, i));
  const apptsOn = (iso) => (state.appts || []).filter((a) => a.starts_at.slice(0, 10) === iso);

  // ---------- записи ----------
  async function loadAppointments() {
    state.confirmCancel = null;
    try {
      state.appts = await api(`/cabinet/appointments?date_from=${state.me.today}&days=14`);
    } catch (e) {
      state.appts = [];
      state.error = e.message;
    }
    render();
  }

  async function cancelAppt(a) {
    if (state.confirmCancel !== a.id) {
      state.confirmCancel = a.id;
      haptic();
      render();
      return;
    }
    state.confirmCancel = null;
    try {
      await api(`/cabinet/appointments/${a.id}/cancel`, { method: "POST" });
      haptic("success");
      state.notice = `Запись ${timeOf(a.starts_at)} отменена` + (a.source !== "manual"
        ? ". Клиенту отправлено уведомление." : ".");
      await loadAppointments();
    } catch (e) {
      state.error = e.message;
      render();
    }
  }

  function dayStrip(onPick) {
    return h("div", { class: "days" }, days14().map((iso, i, all) => {
      const p = dateParts(iso);
      const n = apptsOn(iso).length;
      const showMonth = i === 0 || dateParts(all[i - 1]).month !== p.month;
      return h("button", {
        class: "day" + (state.day === iso ? " active" : ""),
        onclick: () => { state.day = iso; state.confirmCancel = null; haptic("select"); onPick(); },
      },
      h("div", { class: "mon" }, showMonth ? p.month.slice(0, 3) : "\u00a0"),
      h("div", { class: "wd" }, p.wd),
      h("div", { class: "dn" }, p.day),
      h("div", { class: "cnt" + (n ? "" : " zero") }, n || "·"));
    }));
  }

  function apptCard(a) {
    const c = a.client;
    return h("div", { class: "card appt" },
      h("div", { class: "appt-time" }, `${timeOf(a.starts_at)}–${timeOf(a.ends_at)}`),
      h("div", { class: "grow" },
        h("div", { class: "title" }, a.service_name,
          a.source === "manual" ? h("span", { class: "badge" }, "вручную") : null),
        h("div", { class: "meta" }, c.name,
          c.phone ? [" · ", h("a", { href: "tel:" + c.phone }, c.phone)] : null),
        a.comment ? h("div", { class: "meta" }, "«" + a.comment + "»") : null,
        h("button", { class: "link-btn danger-link", onclick: () => cancelAppt(a) },
          state.confirmCancel === a.id ? "Точно отменить?" : "Отменить")));
  }

  function viewAppointments() {
    if (state.appts === null) return [h("div", { class: "center muted" }, "Загрузка…")];
    const list = apptsOn(state.day);
    return [
      dayStrip(render),
      h("div", { class: "row-between" },
        h("h2", null, dayLabel(state.day)),
        h("button", { class: "btn small-btn", onclick: openNew }, "+ Записать")),
      state.notice ? h("div", { class: "notice" }, state.notice) : null,
      state.error ? h("div", { class: "error" }, state.error) : null,
      list.length ? list.map(apptCard)
        : h("p", { class: "muted" }, "Записей нет. Клиент позвонил? Нажмите «Записать»."),
    ];
  }

  // ---------- ручная запись ----------
  async function openNew() {
    haptic();
    state.view = "new";
    state.error = "";
    state.notice = "";
    state.confirmCancel = null;
    state.form = { serviceId: null, slot: null, customTime: "", clientId: null, name: "", phone: "", comment: "" };
    state.freeSlots = null;
    state.hints = [];
    setBack(() => { state.view = "list"; setBack(null); render(); });
    if (!state.services) {
      try { state.services = await api("/cabinet/services"); } catch (e) { state.error = e.message; }
    }
    if (state.services && state.services.length === 1) await pickService(state.services[0].id);
    render();
  }

  async function pickService(id) {
    state.form.serviceId = id;
    state.form.slot = null;
    state.freeSlots = null;
    render();
    try {
      state.freeSlots = await api(`/cabinet/free-slots?service_id=${id}&date=${state.day}`);
    } catch (e) {
      state.freeSlots = [];
      state.error = e.message;
    }
    render();
  }

  let hintTimer = null;
  function onNameInput(value) {
    state.form.name = value;
    state.form.clientId = null; // поменяли имя — это уже не выбранный клиент
    clearTimeout(hintTimer);
    if (value.trim().length < 2) { state.hints = []; return; }
    hintTimer = setTimeout(async () => {
      try {
        state.hints = await api(`/cabinet/clients?q=${encodeURIComponent(value.trim())}`);
        renderKeepFocus();
      } catch (_) { /* подсказки не критичны */ }
    }, 300);
  }

  function pickHint(c) {
    haptic("select");
    Object.assign(state.form, { clientId: c.id, name: c.name, phone: c.phone || "" });
    state.hints = [];
    render();
  }

  function startsAt() {
    const f = state.form;
    if (f.customTime) return `${state.day}T${f.customTime}:00${state.me.utc_offset}`;
    return f.slot;
  }

  async function submitNew() {
    const f = state.form;
    const when = startsAt();
    state.error = !f.serviceId ? "Выберите услугу"
      : !when ? "Выберите время"
        : !f.name.trim() ? "Укажите имя клиента" : "";
    if (state.error) { haptic("error"); render(); return; }
    state.saving = true;
    render();
    try {
      const a = await api("/cabinet/appointments", {
        method: "POST",
        body: JSON.stringify({
          service_id: f.serviceId, starts_at: when, client_id: f.clientId,
          client_name: f.name.trim(), phone: f.phone || null, comment: f.comment || null,
        }),
      });
      haptic("success");
      state.saving = false;
      state.view = "list";
      setBack(null);
      state.notice = `Записали: ${a.client.name}, ${dayLabel(a.starts_at)} в ${timeOf(a.starts_at)}`;
      state.day = a.starts_at.slice(0, 10); // показать день, на который записали
      await loadAppointments();
    } catch (e) {
      haptic("error");
      state.saving = false;
      state.error = e.message;
      if (e.status === 409) await pickService(f.serviceId); // время заняли — обновим подсказки
      render();
    }
  }

  function viewNew() {
    const f = state.form;
    const out = [
      h("h2", null, "Новая запись"),
      dayStrip(() => { if (f.serviceId) pickService(f.serviceId); else render(); }),
      h("label", null, "Услуга"),
    ];
    if (!state.services) out.push(h("div", { class: "muted" }, "Загрузка…"));
    else if (!state.services.length) out.push(h("p", { class: "muted" }, "Сначала добавьте услуги в боте."));
    else {
      out.push(h("div", { class: "chips" }, state.services.map((s) =>
        h("button", { class: "chip" + (f.serviceId === s.id ? " active" : ""), onclick: () => pickService(s.id) },
          s.name, h("span", { class: "chip-sub" }, `${duration(s.duration_min)} · ${price(s.price)}`)))));
    }
    if (f.serviceId) {
      out.push(h("label", null, `Время, ${dayLabel(state.day)}`));
      if (state.freeSlots === null) out.push(h("div", { class: "muted small" }, "Ищем свободное время…"));
      else if (!state.freeSlots.length) out.push(h("p", { class: "muted small" }, "По графику свободного времени нет — укажите время вручную."));
      else {
        out.push(h("div", { class: "slots" }, state.freeSlots.map((iso) =>
          h("button", {
            class: "slot" + (f.slot === iso && !f.customTime ? " active" : ""),
            onclick: () => { f.slot = iso; f.customTime = ""; haptic("select"); render(); },
          }, timeOf(iso)))));
      }
      out.push(h("div", { class: "row inline-label" },
        h("span", { class: "muted small" }, "Другое время:"),
        h("input", {
          type: "time", step: "300", value: f.customTime, class: "time",
          onchange: (e) => { f.customTime = e.target.value; f.slot = null; render(); },
        })));
    }
    out.push(
      h("label", { for: "cname" }, "Клиент"),
      h("input", { id: "cname", value: f.name, placeholder: "Имя", autocomplete: "off",
        oninput: (e) => onNameInput(e.target.value) }),
      state.hints.length ? h("div", { class: "hints" }, state.hints.map((c) =>
        h("button", { class: "hint", onclick: () => pickHint(c) },
          c.name, c.phone ? h("span", { class: "chip-sub" }, c.phone) : null))) : null,
      f.clientId ? h("div", { class: "small muted" }, "Постоянный клиент — запись попадёт в его историю") : null,
      h("label", { for: "cphone" }, "Телефон (необязательно)"),
      h("input", { id: "cphone", type: "tel", value: f.phone, placeholder: "+7…",
        oninput: (e) => { f.phone = e.target.value; } }),
      h("label", { for: "ccomment" }, "Комментарий (необязательно)"),
      h("textarea", { id: "ccomment", maxlength: "500", oninput: (e) => { f.comment = e.target.value; } }, f.comment),
      state.error ? h("div", { class: "error" }, state.error) : null,
      h("button", { class: "btn", onclick: submitNew, disabled: state.saving },
        state.saving ? "Записываем…" : "Записать клиента"),
    );
    return out;
  }

  // ---------- особые дни: календарь ----------
  const WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  const MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
    "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  const MAX_MONTHS_AHEAD = 3;

  const weekdayOf = (iso) => { // 0 — понедельник
    const [y, m, d] = iso.split("-").map(Number);
    return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  };
  const monthKey = (iso) => iso.slice(0, 7);
  function shiftMonth(key, n) {
    const [y, m] = key.split("-").map(Number);
    const d = new Date(Date.UTC(y, m - 1 + n, 1));
    return d.toISOString().slice(0, 7);
  }
  const fmtIv = (iv) => `${iv.start.slice(0, 5)}–${iv.end.slice(0, 5)}`;

  async function loadCalendar() {
    const c = state.cal || (state.cal = {
      month: monthKey(state.me.today), overrides: {}, counts: {}, selected: new Set(),
      rangeMode: false, rangeStart: null, hoursMode: false, hours: [{ start: "10:00", end: "16:00" }],
      saving: false, loaded: false,
    });
    try {
      const [ov, appts] = await Promise.all([
        api("/cabinet/overrides"),
        api(`/cabinet/appointments?date_from=${state.me.today}&days=92`),
      ]);
      c.overrides = Object.fromEntries(ov.map((o) => [o.date, o.intervals]));
      c.counts = {};
      for (const a of appts) {
        const d = a.starts_at.slice(0, 10);
        c.counts[d] = (c.counts[d] || 0) + 1;
      }
      c.loaded = true;
    } catch (e) {
      state.error = e.message;
    }
    render();
  }

  function dayKind(iso) {
    const c = state.cal;
    if (iso in c.overrides) return c.overrides[iso].length ? "hours" : "off";
    const weekly = state.week[weekdayOf(iso)];
    return weekly && weekly.intervals.length ? "work" : "weekly-off";
  }

  function tapDay(iso) {
    const c = state.cal;
    haptic("select");
    if (c.rangeMode) {
      if (!c.rangeStart) {
        c.rangeStart = iso;
        c.selected = new Set([iso]);
      } else {
        let [a, b] = [c.rangeStart, iso].sort();
        c.selected = new Set();
        for (let d = a; d <= b; d = addDays(d, 1)) c.selected.add(d);
        c.rangeStart = null;
      }
    } else if (c.selected.has(iso)) c.selected.delete(iso);
    else c.selected.add(iso);
    c.hoursMode = false;
    state.notice = "";
    state.error = "";
    render();
  }

  function clearSelection() {
    const c = state.cal;
    c.selected = new Set();
    c.rangeStart = null;
    c.hoursMode = false;
    render();
  }

  // подряд идущие даты → [[начало, конец], …]
  function contiguous(dates) {
    const sorted = [...dates].sort();
    const out = [];
    for (const d of sorted) {
      const last = out[out.length - 1];
      if (last && addDays(last[1], 1) === d) last[1] = d;
      else out.push([d, d]);
    }
    return out;
  }

  function conflictText(list) {
    if (!list.length) return "";
    const lines = list.slice(0, 8).map((a) =>
      `${dayLabel(a.starts_at)} ${timeOf(a.starts_at)} — ${a.client_name}${a.phone ? ", " + a.phone : ""}`);
    return " На эти дни уже есть записи — они сохранены, договоритесь с клиентами: " + lines.join("; ")
      + (list.length > 8 ? ` и ещё ${list.length - 8}` : "") + ".";
  }

  async function applyOverride(intervals) {
    const c = state.cal;
    if (c.saving || !c.selected.size) return;
    for (const iv of intervals) {
      if (toMin(iv.start) >= toMin(iv.end)) {
        state.error = "Начало должно быть раньше конца";
        haptic("error"); render(); return;
      }
    }
    c.saving = true;
    render();
    try {
      const conflicts = await api("/cabinet/overrides", {
        method: "PUT",
        body: JSON.stringify({ dates: [...c.selected], intervals }),
      });
      const n = c.selected.size;
      state.notice = (intervals.length ? `Другие часы на ${n} дн. сохранены.` : `Выходные на ${n} дн. сохранены.`)
        + conflictText(conflicts);
      haptic("success");
      c.selected = new Set();
      c.hoursMode = false;
      await loadCalendar();
    } catch (e) {
      state.error = e.message;
      haptic("error");
    }
    c.saving = false;
    render();
  }

  async function resetToNormal(dates) {
    const c = state.cal;
    if (c.saving) return;
    c.saving = true;
    render();
    try {
      for (const [a, b] of contiguous(dates)) {
        await api(`/cabinet/overrides?start=${a}&end=${b}`, { method: "DELETE" });
      }
      state.notice = "Эти дни снова работают по обычному графику.";
      haptic("success");
      c.selected = new Set();
      await loadCalendar();
    } catch (e) {
      state.error = e.message;
    }
    c.saving = false;
    render();
  }

  function monthGrid() {
    const c = state.cal;
    const first = c.month + "-01";
    const cells = [];
    for (let i = 0; i < weekdayOf(first); i++) cells.push(h("div", { class: "cal-cell empty" }));
    for (let d = first; monthKey(d) === c.month; d = addDays(d, 1)) {
      const past = d < state.me.today;
      const kind = dayKind(d);
      const cls = ["cal-cell", "k-" + kind];
      if (past) cls.push("past");
      if (d === state.me.today) cls.push("today");
      if (c.selected.has(d)) cls.push("sel");
      if (c.rangeStart === d) cls.push("range-start");
      const n = c.counts[d] || 0;
      cells.push(h("button", {
        class: cls.join(" "), disabled: past, onclick: () => tapDay(d),
        title: kind === "hours" ? c.overrides[d].map(fmtIv).join(", ") : null,
      },
      h("span", { class: "num" }, Number(d.slice(8))),
      kind === "hours" ? h("span", { class: "tag" }, c.overrides[d][0].start.slice(0, 5)) : null,
      n ? h("span", { class: "dot" }, n) : null));
    }
    return h("div", { class: "cal-grid" },
      WD_SHORT.map((w) => h("div", { class: "cal-wd" }, w)), cells);
  }

  function specialList() {
    const c = state.cal;
    const dates = Object.keys(c.overrides).sort();
    if (!dates.length) return h("p", { class: "muted small" }, "Особых дней пока нет.");
    // склеиваем подряд идущие дни с одинаковыми часами
    const groups = [];
    for (const d of dates) {
      const sig = JSON.stringify(c.overrides[d]);
      const last = groups[groups.length - 1];
      if (last && last.sig === sig && addDays(last.end, 1) === d) last.end = d;
      else groups.push({ start: d, end: d, sig, iv: c.overrides[d] });
    }
    return h("div", { class: "list" }, groups.map((g) => {
      const p1 = dateParts(g.start); const p2 = dateParts(g.end);
      const period = g.start === g.end ? `${p1.day} ${p1.month}`
        : `${p1.day} ${p1.month} – ${p2.day} ${p2.month}`;
      const dates = []; for (let d = g.start; d <= g.end; d = addDays(d, 1)) dates.push(d);
      return h("div", { class: "card special" },
        h("div", { class: "grow" },
          h("div", { class: "title" }, period),
          h("div", { class: "meta" }, g.iv.length ? g.iv.map(fmtIv).join(", ") : "выходной")),
        h("button", { class: "icon-btn", title: "Вернуть обычный график",
          onclick: () => resetToNormal(dates) }, "×"));
    }));
  }

  function selectionBar() {
    const c = state.cal;
    if (!c.selected.size) return null;
    const n = c.selected.size;
    const hasSpecial = [...c.selected].some((d) => d in c.overrides);
    const head = h("div", { class: "row-between" },
      h("b", null, `Выбрано: ${n} ${n === 1 ? "день" : n < 5 ? "дня" : "дней"}`),
      h("button", { class: "link-btn", onclick: clearSelection }, "Сбросить"));
    if (c.hoursMode) {
      return h("div", { class: "save-bar sel-bar" }, head,
        c.hours.map((iv, idx) => h("div", { class: "interval" },
          timeInput(iv.start, (v) => { iv.start = v; }), h("span", { class: "dash" }, "—"),
          timeInput(iv.end, (v) => { iv.end = v; }),
          c.hours.length > 1
            ? h("button", { class: "icon-btn", onclick: () => { c.hours.splice(idx, 1); render(); } }, "×")
            : h("span", { class: "icon-btn placeholder" }))),
        c.hours.length < 3 ? h("button", { class: "link-btn", onclick: () => {
          const last = c.hours[c.hours.length - 1];
          const st = Math.min(toMin(last.end) + 60, 22 * 60);
          c.hours.push({ start: toTime(st), end: toTime(Math.min(st + 120, 23 * 60 + 59)) }); render();
        } }, "+ перерыв") : null,
        h("div", { class: "row" },
          h("button", { class: "btn secondary", onclick: () => { c.hoursMode = false; render(); } }, "Назад"),
          h("button", { class: "btn", disabled: c.saving, onclick: () => applyOverride(c.hours) },
            c.saving ? "Сохраняем…" : "Сохранить часы")));
    }
    return h("div", { class: "save-bar sel-bar" }, head,
      h("div", { class: "row" },
        h("button", { class: "btn", disabled: c.saving, onclick: () => applyOverride([]) }, "Выходной"),
        h("button", { class: "btn secondary", onclick: () => { c.hoursMode = true; render(); } }, "Другие часы")),
      hasSpecial ? h("button", { class: "btn secondary", disabled: c.saving,
        onclick: () => resetToNormal(c.selected) }, "Как обычно (по графику)") : null);
  }

  function viewCalendar() {
    const c = state.cal;
    if (!c || !c.loaded) return [h("div", { class: "center muted" }, "Загрузка…")];
    const minMonth = monthKey(state.me.today);
    const maxMonth = shiftMonth(minMonth, MAX_MONTHS_AHEAD);
    const [y, m] = c.month.split("-").map(Number);
    return [
      h("p", { class: "small muted" },
        "Нажмите на дни, чтобы сделать их выходными или задать другие часы. "
        + "Для отпуска включите «Период» и отметьте первый и последний день."),
      state.notice ? h("div", { class: "notice" }, state.notice) : null,
      state.error ? h("div", { class: "error" }, state.error) : null,
      h("div", { class: "cal-head" },
        h("button", { class: "icon-btn", disabled: c.month <= minMonth,
          onclick: () => { c.month = shiftMonth(c.month, -1); render(); } }, "‹"),
        h("div", { class: "cal-title" }, `${MONTH_NAMES[m - 1]} ${y}`),
        h("button", { class: "icon-btn", disabled: c.month >= maxMonth,
          onclick: () => { c.month = shiftMonth(c.month, 1); render(); } }, "›")),
      h("div", { class: "row-between" },
        h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: c.rangeMode,
            onchange: () => { c.rangeMode = !c.rangeMode; c.rangeStart = null; render(); } }),
          h("span", { class: "slider" }),
          h("span", { class: "switch-text" },
            c.rangeMode ? (c.rangeStart ? "теперь последний день" : "период: первый день") : "Период")),
        c.selected.size ? null : h("span", { class: "small muted" }, "")),
      monthGrid(),
      h("div", { class: "legend" },
        h("span", null, h("i", { class: "lg k-off" }), "выходной"),
        h("span", null, h("i", { class: "lg k-hours" }), "другие часы"),
        h("span", null, h("i", { class: "lg k-weekly-off" }), "выходной по графику"),
        h("span", null, h("i", { class: "lg dotlg" }), "записи")),
      h("h2", null, "Особые дни"),
      specialList(),
      selectionBar(),
    ];
  }

  // ---------- каркас ----------
  function header() {
    const me = state.me;
    const tab = (id, title) => h("button", {
      class: "tab" + (state.tab === id ? " active" : ""),
      onclick: () => switchTab(id),
    }, title);
    return [
      h("div", { class: "header" },
        h("div", { class: "avatar" }, (me.name || "?").trim().charAt(0).toUpperCase()),
        h("div", { class: "grow" },
          h("h1", null, "Кабинет"),
          h("div", { class: "sub" }, me.name))),
      h("div", { class: "tabs" },
        tab("appointments", "Записи"), tab("schedule", "График"), tab("days", "Выходные")),
    ];
  }

  function switchTab(id) {
    if (state.tab === id) return;
    if (state.tab === "schedule" && dirty()) {
      state.error = "Сохраните или отмените изменения графика";
      haptic("error");
      render();
      return;
    }
    haptic("select");
    state.tab = id;
    state.view = "list";
    state.error = "";
    state.notice = "";
    setBack(null);
    if (id === "appointments") loadAppointments();
    if (id === "days") loadCalendar();
    render();
    window.scrollTo(0, 0);
  }

  function render() {
    const body = state.tab === "schedule" ? viewSchedule()
      : state.tab === "days" ? viewCalendar()
      : state.view === "new" ? viewNew() : viewAppointments();
    app.replaceChildren(...[header(), body].flat(Infinity).filter(Boolean));
    if (state.tab === "schedule") state.notice = state.notice && !dirty() ? state.notice : "";
  }

  // перерисовка без потери фокуса в поле имени (подсказки приходят, пока мастер печатает)
  function renderKeepFocus() {
    const active = document.activeElement && document.activeElement.id;
    const pos = active ? document.activeElement.selectionStart : null;
    render();
    if (active) {
      const el = document.getElementById(active);
      if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch (_) { /* не текст */ } }
    }
  }

  function fatal(text) {
    app.replaceChildren(h("div", { class: "center" }, h("p", null, text)));
  }

  async function start() {
    ready();
    setBack(null);
    if (channel === "web") {
      fatal("Кабинет открывается из бота в MAX: кнопка «Кабинет» в меню.");
      return;
    }
    try {
      state.me = await api("/cabinet/me");
    } catch (e) {
      fatal(e.message);
      return;
    }
    loadWeek(state.me.schedule);
    state.day = state.me.today;
    document.title = "Кабинет — " + state.me.name;
    render();
    loadAppointments();
    // не потерять несохранённые изменения при закрытии
    try {
      const b = window.OK.bridge;
      if (b && b.enableClosingConfirmation) {
        window.addEventListener("input", () => (dirty() ? b.enableClosingConfirmation() : b.disableClosingConfirmation()));
      }
    } catch (_) { /* нет метода */ }
  }

  start();
})();

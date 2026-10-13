// Окошко — кабинет мастера: недельный график (дальше — особые дни, услуги, записи).
(function () {
  "use strict";

  const { h, haptic, api, setBack, ready, channel } = window.OK;
  if (window.OK.mode !== "cabinet") return;

  const DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
  const app = document.getElementById("app");

  const state = {
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
    const me = state.me;
    return [
      h("div", { class: "header" },
        h("div", { class: "avatar" }, (me.name || "?").trim().charAt(0).toUpperCase()),
        h("div", { class: "grow" },
          h("h1", null, "График работы"),
          h("div", { class: "sub" }, me.name))),
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

  function render() {
    const views = viewSchedule().flat(Infinity).filter(Boolean);
    app.replaceChildren(...views);
    state.notice = state.notice && !dirty() ? state.notice : "";
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
    document.title = "Кабинет — " + state.me.name;
    render();
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

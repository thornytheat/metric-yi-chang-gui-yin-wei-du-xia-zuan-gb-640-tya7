/* timeline.js —— 横向水位时间轴：日期刻度、运行刻度与水位线。
   只读页面里的 JSON 并绘制，不做任何判定。 */
(function () {
  "use strict";

  var LABEL_BOTTOM = "34px";

  function place(el, percent) {
    el.style.left = Math.max(0, Math.min(100, percent)) + "%";
  }

  function percentAt(days, value) {
    var index = days.indexOf(value);
    if (index < 0 || days.length < 2) {
      return -1;
    }
    return (index / (days.length - 1)) * 100;
  }

  function drawDayScale(root, days) {
    var bar = document.createElement("div");
    bar.className = "scale";
    root.appendChild(bar);
    if (!days.length) {
      return;
    }
    var step = Math.max(1, Math.ceil(days.length / 12));
    days.forEach(function (day, index) {
      var percent = (index / (days.length - 1)) * 100;
      var label = document.createElement("span");
      label.className = "caption tick-label";
      label.style.position = "absolute";
      label.style.bottom = LABEL_BOTTOM;
      label.style.fontSize = "10.5px";
      label.style.color = "var(--ink-faint)";
      if (index % step === 0 || index === days.length - 1) {
        label.textContent = day.slice(5);
        place(label, percent);
        if (index === 0) {
          label.classList.add("edge-start");
        } else if (index === days.length - 1) {
          label.classList.add("edge-end");
        }
        root.appendChild(label);
      }
      if (index % step === 0) {
        var pin = document.createElement("div");
        pin.className = "tick";
        pin.style.height = "11px";
        place(pin, percent);
        root.appendChild(pin);
      }
    });
  }

  function drawWatermark(root, percent) {
    var line = document.createElement("div");
    line.className = "watermark";
    place(line, percent);
    if (percent > 80) {
      line.classList.add("end");
    }
    root.appendChild(line);
  }

  function caption(root, text) {
    var node = document.createElement("div");
    node.className = "caption";
    node.textContent = text;
    root.appendChild(node);
  }

  function renderRunAxis(root, payload) {
    var runs = payload.runs || [];
    var days = payload.days || [];
    drawDayScale(root, days);
    runs.forEach(function (run, index) {
      var percent = days.length && run.date ? percentAt(days, run.date) : -1;
      if (percent < 0) {
        percent = runs.length === 1 ? 100 : (index / (runs.length - 1)) * 100;
      }
      var tick = document.createElement("button");
      tick.type = "button";
      tick.className = "tick " + (run.status || "ok");
      tick.title = run.label + " · " + (run.sub || "");
      tick.style.height = (8 + Math.min(26, Math.abs(run.magnitude || 0))) + "px";
      place(tick, percent);
      if (percent > 88) {
        tick.classList.add("end");
      }
      var mark = document.createElement("span");
      mark.textContent = run.label;
      tick.appendChild(mark);
      root.appendChild(tick);
    });
    if (payload.watermark) {
      var percent = days.length ? percentAt(days, payload.watermark) : 100;
      drawWatermark(root, percent < 0 ? 100 : percent);
    }
    caption(root, "运行 " + runs.length + " 次 · 水位 " + (payload.watermark || "—")
      + (days.length ? " · " + days[0] + " 起" : ""));
  }

  function renderDateAxis(root, payload) {
    var days = payload.days || [];
    drawDayScale(root, days);
    if (!days.length) {
      caption(root, "没有日期刻度");
      return;
    }
    (payload.marks || []).forEach(function (mark) {
      var percent = percentAt(days, mark.biz_date);
      if (percent < 0) {
        return;
      }
      var pin = document.createElement("div");
      pin.className = "daymark " + (mark.status || "");
      pin.title = mark.biz_date + " · " + mark.count + " 条";
      place(pin, percent);
      root.appendChild(pin);
    });
    if (payload.watermark) {
      var percent = percentAt(days, payload.watermark);
      if (percent >= 0) {
        drawWatermark(root, percent);
      }
    }
    caption(root, days[0] + " → " + days[days.length - 1]
      + " · 水位 " + (payload.watermark || "—"));
  }

  function init() {
    var root = document.querySelector(".axis");
    var payload = document.querySelector(".axis-data");
    if (!root || !payload) {
      return;
    }
    var data = {};
    try {
      data = JSON.parse(payload.textContent || "{}");
    } catch (err) {
      data = {};
    }
    if (root.getAttribute("data-kind") === "run") {
      renderRunAxis(root, data);
    } else {
      renderDateAxis(root, data);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();

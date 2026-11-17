/* table.js —— 明细表排序与分解条宽度；只做展示，不改数据。 */
(function () {
  "use strict";

  function cellValue(row, index) {
    var cell = row.children[index];
    if (!cell) {
      return "";
    }
    var text = (cell.textContent || "").replace(/,/g, "").replace(/\s|ms|元|%/g, "");
    var number = Number(text);
    return isNaN(number) ? cell.textContent.trim() : number;
  }

  function sortTable(table, index, ascending) {
    var body = table.tBodies[0];
    if (!body) {
      return;
    }
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (left, right) {
      var a = cellValue(left, index);
      var b = cellValue(right, index);
      if (typeof a === "number" && typeof b === "number") {
        return ascending ? a - b : b - a;
      }
      return ascending
        ? String(a).localeCompare(String(b), "zh-CN")
        : String(b).localeCompare(String(a), "zh-CN");
    });
    rows.forEach(function (row) { body.appendChild(row); });
  }

  function bindSort(table) {
    var heads = table.tHead ? Array.prototype.slice.call(table.tHead.rows[0].cells) : [];
    heads.forEach(function (th, index) {
      if (th.classList.contains("static")) {
        return;
      }
      th.addEventListener("click", function () {
        var ascending = th.getAttribute("data-order") !== "asc";
        heads.forEach(function (other) { other.removeAttribute("data-order"); });
        th.setAttribute("data-order", ascending ? "asc" : "desc");
        sortTable(table, index, ascending);
      });
    });
  }

  function renderBars() {
    var wrap = document.getElementById("factor-bars");
    if (!wrap) {
      return;
    }
    var rows = Array.prototype.slice.call(wrap.querySelectorAll(".bar-row"));
    var peak = 0;
    rows.forEach(function (row) {
      var value = Math.abs(Number(row.getAttribute("data-value") || 0));
      if (value > peak) {
        peak = value;
      }
    });
    rows.forEach(function (row) {
      var value = Number(row.getAttribute("data-value") || 0);
      var fill = row.querySelector(".bar-fill");
      if (!fill || !peak) {
        return;
      }
      var width = Math.min(50, (Math.abs(value) / peak) * 50);
      fill.style.width = width + "%";
      fill.style.left = value >= 0 ? "50%" : (50 - width) + "%";
      fill.className = "bar-fill " + (value >= 0 ? "pos" : "neg");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.slice.call(document.querySelectorAll("table")).forEach(bindSort);
    renderBars();
  });
})();

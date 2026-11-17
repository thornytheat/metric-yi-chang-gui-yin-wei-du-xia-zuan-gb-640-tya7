/* app.js —— 表单请求与错误提示；状态集中在一个对象里。 */
(function () {
  "use strict";

  var state = { busy: false };

  function message(text, isError) {
    var node = document.getElementById("import-msg");
    if (!node) {
      return;
    }
    node.textContent = text;
    node.className = isError ? "msg err" : "msg";
  }

  function pickInput() {
    var file = document.getElementById("file-input");
    var sample = document.getElementById("sample-select");
    var form = new FormData();
    if (file && file.files && file.files.length) {
      form.append("file", file.files[0]);
    } else if (sample) {
      form.append("sample", sample.value);
    }
    return form;
  }

  function send(url, form, done) {
    if (state.busy) {
      return;
    }
    state.busy = true;
    message("处理中…", false);
    fetch(url, { method: "POST", body: form })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      })
      .then(function (result) {
        state.busy = false;
        if (!result.ok) {
          var code = result.body.error || "失败";
          message(result.status + " " + code + " " + (result.body.message || ""), true);
          return;
        }
        done(result.body);
      })
      .catch(function (err) {
        state.busy = false;
        message("请求失败：" + err, true);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("import-form");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        send("/api/import", pickInput(), function (body) {
          message("批次 " + body.run_id + (body.reused ? " 已复用" : " 已写入"), false);
          window.location.href = "/anomalies";
        });
      });
    }
    var preview = document.getElementById("preview-btn");
    if (preview) {
      preview.addEventListener("click", function () {
        send("/api/import/preview", pickInput(), function (body) {
          message("采信 " + body.accepted_rows + " 行，隔离 " + body.rejected_rows
            + " 行，编码 " + body.encoding, false);
        });
      });
    }
    var rerun = document.getElementById("rerun-btn");
    if (rerun) {
      rerun.addEventListener("click", function () {
        var node = document.getElementById("rerun-msg");
        fetch("/api/attribution/rerun", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        })
          .then(function (response) { return response.json(); })
          .then(function (body) {
            if (node) {
              node.textContent = body.reused ? "复用 " + body.run_id : "新建 " + body.run_id;
            }
          })
          .catch(function () {
            if (node) {
              node.textContent = "重跑失败";
            }
          });
      });
    }
  });
})();

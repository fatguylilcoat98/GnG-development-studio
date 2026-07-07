/* Decision Deck — vanilla JS, local-first, no dependencies.
 *
 * It guides one decision through a card stepper and produces a one-page
 * summary. It never scores options or tells the user what to choose.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "decision_deck_current";

  // ---- Card definitions (order defines the flow) ----
  // "welcome" and "summary" are book-ends; the middle cards get progress + nav.
  var CARDS = [
    { id: "welcome", tpl: "tpl-welcome", step: false },
    { id: "situation", tpl: "tpl-situation", step: true },
    { id: "options", tpl: "tpl-options", step: true },
    { id: "proscons", tpl: "tpl-proscons", step: true },
    { id: "risks", tpl: "tpl-risks", step: true },
    { id: "future", tpl: "tpl-future", step: true },
    { id: "summary", tpl: "tpl-summary", step: false }
  ];
  var STEP_CARDS = CARDS.filter(function (c) { return c.step; });

  // ---- DOM handles ----
  var viewport = document.getElementById("cardViewport");
  var topbar = document.getElementById("topbar");
  var navbar = document.getElementById("navbar");
  var progressFill = document.getElementById("progressFill");
  var progressLabel = document.getElementById("progressLabel");
  var saveState = document.getElementById("saveState");
  var btnBack = document.getElementById("btnBack");
  var btnNext = document.getElementById("btnNext");
  var toastEl = document.getElementById("toast");

  var state = loadState();
  var index = 0; // current card index into CARDS

  // ---------------------------------------------------------------------------
  // State + persistence
  // ---------------------------------------------------------------------------
  function blankState() {
    var now = new Date().toISOString();
    return {
      title: "",
      situation: "",
      options: [],
      pros: [],
      cons: [],
      risks: [],
      futureYou: "",
      knowNow: "",
      needToLearn: "",
      createdAt: now,
      updatedAt: now
    };
  }

  function loadState() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return blankState();
      var parsed = JSON.parse(raw);
      // Merge onto a blank shape so older/partial records stay valid.
      var base = blankState();
      Object.keys(base).forEach(function (k) {
        if (parsed[k] !== undefined && parsed[k] !== null) base[k] = parsed[k];
      });
      return base;
    } catch (e) {
      return blankState();
    }
  }

  var saveTimer = null;
  function scheduleSave() {
    setSaveState("saving");
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(commitSave, 400);
  }

  function commitSave() {
    state.updatedAt = new Date().toISOString();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      setSaveState("saved");
    } catch (e) {
      setSaveState("error");
    }
  }

  function setSaveState(mode) {
    if (!saveState) return;
    if (mode === "saving") {
      saveState.textContent = "Saving…";
      saveState.classList.add("saving");
    } else if (mode === "error") {
      saveState.textContent = "Not saved";
      saveState.classList.remove("saving");
    } else {
      saveState.textContent = "Saved";
      saveState.classList.remove("saving");
    }
  }

  // ---------------------------------------------------------------------------
  // Navigation
  // ---------------------------------------------------------------------------
  function goTo(i) {
    index = Math.max(0, Math.min(CARDS.length - 1, i));
    render();
    window.scrollTo(0, 0);
  }
  function next() { if (index < CARDS.length - 1) goTo(index + 1); }
  function back() { if (index > 0) goTo(index - 1); }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  function render() {
    var card = CARDS[index];
    var tpl = document.getElementById(card.tpl);
    viewport.innerHTML = "";
    var node = tpl.content.cloneNode(true);
    viewport.appendChild(node);

    // Chrome: progress bar + nav only on stepper cards.
    var onStep = card.step;
    topbar.hidden = card.id === "welcome";
    navbar.hidden = !onStep;

    if (onStep) {
      var stepIndex = STEP_CARDS.indexOf(card); // 0-based
      var total = STEP_CARDS.length;
      var pct = ((stepIndex + 1) / total) * 100;
      progressFill.style.width = pct + "%";
      progressLabel.textContent = "Step " + (stepIndex + 1) + " of " + total;
    } else if (card.id === "summary") {
      progressFill.style.width = "100%";
      progressLabel.textContent = "Summary";
    }

    // Wire up card-specific behavior.
    if (card.id === "welcome") wireWelcome();
    else if (card.id === "summary") wireSummary();
    else wireStepInputs(card.id);
  }

  // ---- Welcome ----
  function wireWelcome() {
    var titleInput = document.getElementById("input-title");
    if (titleInput) {
      titleInput.value = state.title;
      titleInput.addEventListener("input", function () {
        state.title = titleInput.value;
        scheduleSave();
      });
    }
    var startBtn = viewport.querySelector('[data-action="start"]');
    if (startBtn) startBtn.addEventListener("click", function () { next(); });
  }

  // ---- Text + list inputs on the stepper cards ----
  function wireStepInputs(cardId) {
    // Plain textarea / input fields, matched by input-<key> id.
    var textKeys = {
      situation: ["situation"],
      future: ["futureYou", "knowNow", "needToLearn"]
    };
    (textKeys[cardId] || []).forEach(function (key) {
      var el = document.getElementById("input-" + key);
      if (!el) return;
      el.value = state[key];
      el.addEventListener("input", function () {
        state[key] = el.value;
        scheduleSave();
      });
    });

    // List editors (options / pros / cons / risks).
    viewport.querySelectorAll(".list-editor").forEach(function (host) {
      buildListEditor(host);
    });
  }

  // ---- Reusable list editor ----
  function buildListEditor(host) {
    var key = host.getAttribute("data-list");
    var placeholder = host.getAttribute("data-placeholder") || "";
    var addLabel = host.getAttribute("data-add-label") || "Add";
    var items = state[key];

    function redraw() {
      host.innerHTML = "";
      items.forEach(function (value, i) {
        var row = document.createElement("div");
        row.className = "list-row";

        var input = document.createElement("input");
        input.type = "text";
        input.className = "text-input";
        input.placeholder = placeholder;
        input.value = value;
        input.addEventListener("input", function () {
          items[i] = input.value;
          scheduleSave();
        });
        // Enter adds another row for fast entry.
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            addRow(true);
          }
        });

        var rm = document.createElement("button");
        rm.type = "button";
        rm.className = "list-remove";
        rm.setAttribute("aria-label", "Remove");
        rm.innerHTML = "&times;";
        rm.addEventListener("click", function () {
          items.splice(i, 1);
          scheduleSave();
          redraw();
        });

        row.appendChild(input);
        row.appendChild(rm);
        host.appendChild(row);
      });

      var add = document.createElement("button");
      add.type = "button";
      add.className = "list-add";
      add.textContent = "+ " + addLabel;
      add.addEventListener("click", function () { addRow(true); });
      host.appendChild(add);
    }

    function addRow(focusIt) {
      items.push("");
      scheduleSave();
      redraw();
      if (focusIt) {
        var inputs = host.querySelectorAll(".list-row .text-input");
        if (inputs.length) inputs[inputs.length - 1].focus();
      }
    }

    // Always show at least one empty row to invite input.
    if (items.length === 0) items.push("");
    redraw();
  }

  // ---------------------------------------------------------------------------
  // Summary
  // ---------------------------------------------------------------------------
  // Pure summary/formatting logic lives in summary.js (shared with tests).
  var cleanList = DDSummary.cleanList;
  var textOrDash = DDSummary.textOrDash;
  var listOrDash = DDSummary.listOrDash;
  function buildSummaryText() { return DDSummary.buildSummaryText(state); }

  function wireSummary() {
    var sheet = document.getElementById("summarySheet");
    sheet.innerHTML = buildSummaryHTML();

    viewport.querySelector('[data-action="copy"]').addEventListener("click", function () {
      copyText(buildSummaryText()).then(function (ok) {
        toast(ok ? "Summary copied" : "Copy failed — select and copy manually");
      });
    });
    viewport.querySelector('[data-action="download"]').addEventListener("click", function () {
      downloadMarkdown();
    });
    viewport.querySelector('[data-action="new"]').addEventListener("click", function () {
      if (window.confirm("Start a new decision? Your current one will be cleared from this device.")) {
        state = blankState();
        commitSave();
        goTo(0);
      }
    });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function sectionText(title, value) {
    var v = (value || "").trim();
    var body = v ? "<p>" + esc(v) + "</p>" : '<p class="empty">Not filled in.</p>';
    return "<h3>" + esc(title) + "</h3>" + body;
  }

  function sectionList(title, arr) {
    var items = cleanList(arr);
    var body;
    if (items.length === 0) {
      body = '<p class="empty">None listed.</p>';
    } else {
      body = "<ul>" + items.map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>";
    }
    return "<h3>" + esc(title) + "</h3>" + body;
  }

  function buildSummaryHTML() {
    var title = (state.title || "").trim() || "Untitled decision";
    var pros = cleanList(state.pros), cons = cleanList(state.cons);

    var html = "";
    html += '<h3>Decision</h3><p class="summary-decision">' + esc(title) + "</p>";
    html += sectionText("Situation", state.situation);
    html += sectionList("Options considered", state.options);

    html += '<div class="summary-proscons"><div>' +
              sectionList("Pros", pros) +
            "</div><div>" +
              sectionList("Cons", cons) +
            "</div></div>";

    html += sectionList("Risks", state.risks);
    html += sectionText("Future-self reflection", state.futureYou);
    html += sectionText("What I know now", state.knowNow);
    html += sectionText("What I still need to learn", state.needToLearn);

    html += '<div class="summary-footer">This summary reflects your own thinking. ' +
            "Decision Deck doesn't recommend a choice &mdash; that part is yours.</div>";
    return html;
  }

  // ---------------------------------------------------------------------------
  // Copy + download helpers
  // ---------------------------------------------------------------------------
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; },
                                                      function () { return fallbackCopy(text); });
    }
    return Promise.resolve(fallbackCopy(text));
  }
  function fallbackCopy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  function downloadMarkdown() {
    var text = buildSummaryText();
    var title = (state.title || "decision").trim() || "decision";
    var slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "decision";
    var blob = new Blob([text], { type: "text/markdown" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "decision-deck-" + slug + ".md";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    toast("Markdown downloaded");
  }

  // ---------------------------------------------------------------------------
  // Toast
  // ---------------------------------------------------------------------------
  var toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    // reflow so the transition fires
    void toastEl.offsetWidth;
    toastEl.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove("show");
      setTimeout(function () { toastEl.hidden = true; }, 250);
    }, 2200);
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------
  btnNext.addEventListener("click", next);
  btnBack.addEventListener("click", back);
  setSaveState("saved");
  render();

  // Expose a tiny surface for the automated test harness (non-essential).
  window.DecisionDeck = {
    _get: function () { return state; },
    _summaryText: buildSummaryText,
    _reset: function () { state = blankState(); commitSave(); goTo(0); }
  };
})();

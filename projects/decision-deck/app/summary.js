/* Decision Deck — pure summary logic.
 *
 * No DOM, no side effects. Shared by app.js (in the browser) and the Node
 * test harness (tests/logic.test.js) so tests exercise the shipped code.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api; // Node
  root.DDSummary = api; // browser
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function cleanList(arr) {
    return (arr || [])
      .map(function (s) { return (s == null ? "" : String(s)).trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function textOrDash(v) {
    v = (v == null ? "" : String(v)).trim();
    return v ? v : "_(not filled in)_";
  }

  function listOrDash(arr) {
    var items = cleanList(arr);
    if (items.length === 0) return "_(none listed)_";
    return items.map(function (i) { return "- " + i; }).join("\n");
  }

  function decisionTitle(state) {
    return (state.title || "").trim() || "Untitled decision";
  }

  // Produces the plain-text / markdown summary used for Copy and Download.
  function buildSummaryText(state) {
    state = state || {};
    var L = [];
    L.push("# Decision Deck — " + decisionTitle(state));
    L.push("");
    L.push("## Situation");
    L.push(textOrDash(state.situation));
    L.push("");
    L.push("## Options considered");
    L.push(listOrDash(state.options));
    L.push("");
    L.push("## Pros");
    L.push(listOrDash(state.pros));
    L.push("");
    L.push("## Cons");
    L.push(listOrDash(state.cons));
    L.push("");
    L.push("## Risks");
    L.push(listOrDash(state.risks));
    L.push("");
    L.push("## Future-self reflection");
    L.push(textOrDash(state.futureYou));
    L.push("");
    L.push("## What I know now");
    L.push(textOrDash(state.knowNow));
    L.push("");
    L.push("## What I still need to learn");
    L.push(textOrDash(state.needToLearn));
    L.push("");
    L.push("---");
    L.push("_This summary reflects the author's own thinking. Decision Deck does not recommend a choice._");
    return L.join("\n");
  }

  return {
    cleanList: cleanList,
    textOrDash: textOrDash,
    listOrDash: listOrDash,
    decisionTitle: decisionTitle,
    buildSummaryText: buildSummaryText
  };
});

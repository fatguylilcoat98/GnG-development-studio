/* Decision Deck — automated logic tests (no browser, no dependencies).
 * Run: node tests/logic.test.js
 * Exercises the shipped pure logic in ../summary.js.
 */
"use strict";
var S = require("../summary.js");

var pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}
function eq(name, a, b) {
  var ok = a === b;
  if (!ok) console.log("   expected: " + JSON.stringify(b) + "\n   got:      " + JSON.stringify(a));
  check(name, ok);
}

// cleanList trims and drops empties
var cl = S.cleanList(["  a ", "", "  ", "b", null, undefined]);
eq("cleanList length", cl.length, 2);
eq("cleanList trims", cl[0], "a");

// textOrDash
eq("textOrDash empty", S.textOrDash(""), "_(not filled in)_");
eq("textOrDash whitespace", S.textOrDash("   "), "_(not filled in)_");
eq("textOrDash trims", S.textOrDash("  hi  "), "hi");

// listOrDash
eq("listOrDash empty", S.listOrDash([]), "_(none listed)_");
eq("listOrDash bullets", S.listOrDash(["x", "y"]), "- x\n- y");

// decisionTitle
eq("decisionTitle fallback", S.decisionTitle({}), "Untitled decision");
eq("decisionTitle uses title", S.decisionTitle({ title: " Move? " }), "Move?");

// Full summary — populated
var state = {
  title: "Take the new job?",
  situation: "Offered a role in another city.",
  options: ["Accept", "Decline", "Negotiate"],
  pros: ["More pay", "Growth"],
  cons: ["Relocation"],
  risks: ["Team might not fit"],
  futureYou: "I'd regret not trying.",
  knowNow: "The pay is real.",
  needToLearn: "The team culture."
};
var text = S.buildSummaryText(state);
check("summary has title header", text.indexOf("# Decision Deck — Take the new job?") === 0);
check("summary has Situation", /## Situation\nOffered a role/.test(text));
check("summary lists options", text.indexOf("- Accept\n- Decline\n- Negotiate") !== -1);
check("summary has all 8 sections", ["## Situation","## Options considered","## Pros","## Cons","## Risks","## Future-self reflection","## What I know now","## What I still need to learn"].every(function(h){ return text.indexOf(h) !== -1; }));
check("summary never prescribes a choice", !/you should choose|we recommend|the best option is/i.test(text));
check("summary states it does not recommend", /does not recommend a choice/.test(text));

// Full summary — empty state uses placeholders, still safe
var empty = S.buildSummaryText({});
check("empty summary untitled", empty.indexOf("Untitled decision") !== -1);
check("empty summary not-filled placeholder", empty.indexOf("_(not filled in)_") !== -1);
check("empty summary none-listed placeholder", empty.indexOf("_(none listed)_") !== -1);

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail === 0 ? 0 : 1);

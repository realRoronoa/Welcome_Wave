/* The intro's three words, and its controls.
   Words: a different set on every load, picked at random from the list below, never one of the last five shown in this tab
   (remembered in sessionStorage). Each set is three single words in the product's voice, set in Geist Mono capitals
   under the name; the longest must fit one line at 320 px (10 px, .12em tracking): keep new sets no longer than
   "Listen · Understand · Contribute".
   Controls: Esc, Enter, Space, Tab, a click, a tap or a scroll skips the intro; src/boot.ts listens for "ww-boot-skip". Tab
   skips without being swallowed, so focus moves on into the app as the intro leaves instead of into controls hidden behind
   it. Records the intro's first frame (window.__wwBootT0), so boot.ts times the intro by what was actually on screen.
   Failsafe: if the app never takes over (a script failed to load, an error stopped it, the server is very slow, or a skip
   got no answer), the hint line turns into a message with a Reload button instead of a dead "esc to skip".
   Loaded as a file, right after #ww-boot, because the CSP forbids inline scripts and inline handlers. The light, the field
   and the exit are /boot-field.js. */
(function () {
  // the pre-boarding page has no intro (boot-theme.js marked the root): take it out of the page and stop
  if (document.documentElement.classList.contains("ww-no-boot")) {
    var gone = document.getElementById("ww-boot");
    if (gone) gone.parentNode.removeChild(gone);
    return;
  }

  var SETS = [
    ["Explain", "Link", "Reuse"],
    ["Record", "Connect", "Remember"],
    ["Ask", "Learn", "Ship"],
    ["Captured", "Cited", "Shared"],
    ["Listen", "Understand", "Contribute"],
    ["Context", "Clarity", "Confidence"],
    ["Welcome", "Learn", "Belong"],
    ["Explained", "Once", "Everywhere"],
    ["Recorded", "Linked", "Reused"],
    ["Watch", "Trace", "Merge"],
    ["Heard", "Linked", "Understood"],
    ["Read", "Ask", "Ship"],
    ["Spoken", "Cited", "Kept"],
    ["Learn", "Build", "Merge"],
  ];
  // the last five sets shown in this tab are left out of the draw, so reloads never repeat soon
  var KEY = "ww-boot-words", seen = [];
  try { seen = (sessionStorage.getItem(KEY) || "").split(",").filter(Boolean).map(Number); } catch (e) {}
  var fresh = [];
  for (var s = 0; s < SETS.length; s++) if (seen.indexOf(s) < 0) fresh.push(s);
  if (!fresh.length) fresh = [0];
  var pick = fresh[Math.floor(Math.random() * fresh.length)];
  seen.push(pick);
  try { sessionStorage.setItem(KEY, seen.slice(-5).join(",")); } catch (e) {}
  var words = document.querySelectorAll("#ww-boot .ww-w");
  for (var w = 0; w < words.length && w < 3; w++) words[w].textContent = SETS[pick][w];

  var boot = document.getElementById("ww-boot");
  if (!boot) return;
  var hint = boot.querySelector(".ww-boot-skip");
  var STALL_MS = 12000;     // after the first frame: the intro is 5 s, so the app is badly late
  var SKIP_STALL_MS = 3000; // after a skip that the app could not answer yet

  // the CSS clock starts with the first frame; boot.ts reads this when the animations themselves cannot tell it
  window.__wwBootT0 = null;
  requestAnimationFrame(function (ts) {
    window.__wwBootT0 = ts;
    setTimeout(function () { show("stalled"); }, STALL_MS);
  });

  /* still covering a page that has nothing to show: once boot.ts's hideBoot() has run (__wwBootReady), the intro leaves
     on its own and nothing here interferes */
  function waiting() {
    return boot.isConnected && !boot.classList.contains("ww-boot--done") && !window.__wwBootReady;
  }

  var RANK = { loading: 1, stalled: 2, failed: 3 };
  var state = "";
  function show(next) {
    if (!waiting() || !hint || (state && RANK[state] >= RANK[next])) return;
    state = next;
    hint.textContent = "";
    if (next === "loading") {
      boot.classList.add("ww-boot--waiting"); // the slow-load sweep starts now rather than at 5.6 s
      hint.textContent = "loading…";
      return;
    }
    var text = next === "failed" ? "Couldn’t load Welcome Wave." : window.__wwBootApp ? "Still connecting…" : "Still loading…";
    hint.appendChild(document.createTextNode(text));
    var b = document.createElement("button");
    b.type = "button";
    b.className = "ww-boot-reload";
    b.textContent = "Reload";
    b.addEventListener("click", function (e) { e.stopPropagation(); location.reload(); });
    hint.appendChild(b);
    hint.removeAttribute("aria-hidden");
    hint.setAttribute("role", "alert");
    boot.classList.add("ww-boot--stuck");
    if (next === "failed") {
      boot.classList.add("ww-boot--failed");
      boot.setAttribute("aria-label", "Welcome Wave couldn’t load");
    }
  }

  // a script that cannot load (the bundle, a chunk, a network drop) fails at once; an uncaught error before the app took
  // over gets a moment to recover first
  function onError(e) {
    if (!boot.isConnected) return removeEventListener("error", onError, true);
    var t = e.target;
    if (t && t.tagName === "SCRIPT") show("failed");
    else if (t === window) setTimeout(function () { show("failed"); }, 2000);
  }
  addEventListener("error", onError, true);

  function stop() {
    removeEventListener("keydown", skip, true);
    boot.removeEventListener("click", skip);
    removeEventListener("wheel", skip);
    boot.removeEventListener("touchmove", skip);
  }
  function skip(e) {
    // the intro is already leaving or gone: stop listening so later key presses belong to the app alone
    if (!boot.isConnected || boot.classList.contains("ww-boot--done")) return stop();
    var t = e.target;
    if (t && t.closest && t.closest(".ww-boot-reload")) return; // the Reload button behaves as a button
    if (e.type === "keydown") {
      if (e.key === "Escape" || e.key === "Enter" || e.key === " ") e.preventDefault(); // Space would scroll, and Enter submit, the page behind
      else if (e.key !== "Tab") return;
    }
    window.__wwBootSkip = true;
    window.dispatchEvent(new Event("ww-boot-skip"));
    stop();
    // boot.ts answers a skip at once when the app is ready; otherwise say so, and offer a reload if it stays stuck
    if (waiting()) {
      show("loading");
      setTimeout(function () { show("stalled"); }, SKIP_STALL_MS);
    }
  }
  addEventListener("keydown", skip, true);
  boot.addEventListener("click", skip); // a tap arrives as a click too (boot-field.js draws its ripple on pointerdown)
  addEventListener("wheel", skip, { passive: true });
  boot.addEventListener("touchmove", skip, { passive: true });
})();

/* The intro's light, its field ("the crowd") and its exit (docs/brand/BRAND.md). Timings mirror index.html; keep them in step.
   - The pen: a point of teal light with a short trail, riding the tip of each stroke as the W draws (0.28-1.65 s). It is
     placed with the strokes' own easing on the intro's CSS clock (the .ww-art clock animation), so it sits on the ink.
     (0.22-1.51 s)
   - The crowd: one canvas of short strokes, the mark's capsule at small size, standing still and faint. A wave of light
     crosses it left to right in step with the pen's x (0.16-1.62 s); the strokes it passes stand up and lean into the mark's
     angle, alternately, so for a moment the crowd is woven of W's, and only the crest catches a little teal.
   - The pointer: strokes near it stand and lean toward it, and the lockup drifts a few pixels after it. A tap or click
     sends a ripple from that point (boot-words.js turns the same click into a skip, and the exit then opens from there).
   - The exit (.ww-boot--done from src/boot.ts): the page appears through a W-shaped window, the mark's own four strokes as
     a clip-path, growing from the mark (or the click point) and zooming through its third stroke until it fills the screen,
     while the field disperses outward. REVEAL_MS must stay within boot.ts FADE_MS.
   One canvas, every stroke batched into a few alpha buckets per frame, a DPR cap; it sleeps when nothing moves and stops
   for good once the intro has left. Reduced motion: one still frame, no listeners, and the plain CSS fade. Loaded as a
   file (the CSP forbids inline scripts), right after #ww-boot and /boot-words.js. */
(function () {
  var root = document.documentElement;
  if (root.classList.contains("ww-no-boot")) return;
  var boot = document.getElementById("ww-boot");
  var cv = boot && boot.querySelector(".ww-field");
  var ctx = cv && cv.getContext && cv.getContext("2d");
  if (!ctx) return;
  var art = boot.querySelector(".ww-art"), mark = boot.querySelector(".ww-mark"), lockup = boot.querySelector(".ww-lockup");
  var tagEl = boot.querySelector(".ww-tag");
  var pen = boot.querySelector(".ww-pen"), glow = boot.querySelector(".ww-pen-glow"), core = boot.querySelector(".ww-pen-core");
  var trail = boot.querySelector(".ww-trail"), trailGrad = document.getElementById("ww-trail-g");
  var still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (!still) boot.classList.add("ww-boot--reveal");

  // ---- the timeline (seconds from first paint) and the mark's geometry (its 64-unit grid) ----
  var PEN0 = 0.22, STEP = 0.31, DUR = 0.36, PEN_END = PEN0 + 3 * STEP + DUR;
  var WAVE0 = 0.16, WAVE1 = 1.62, RES0 = 1.62, RES_DUR = 0.9, K = 2.75, REVEAL_MS = 820;
  var V = [[8, 11], [20, 53], [32, 11], [44, 53], [56, 11]], SEG = Math.hypot(12, 42), LEAN = Math.atan2(12, 42);
  var FOCUS = [35, 21.5]; // on the third stroke's centre-line: the window zooms through it
  var PIECES = [
    "M4.346 12.044L15.372 50.634L19.324 36.802L11.654 9.956A3.8 3.8 0 0 0 4.346 12.044Z",
    "M23.654 54.044L31.324 27.198L27.372 13.366L16.346 51.956A3.8 3.8 0 0 0 23.654 54.044Z",
    "M28.346 12.044L39.372 50.634L43.324 36.802L35.654 9.956A3.8 3.8 0 0 0 28.346 12.044Z",
    "M47.654 54.044L59.654 12.044A3.8 3.8 0 0 0 52.346 9.956L40.346 51.956A3.8 3.8 0 0 0 47.654 54.044Z",
  ].map(function (d) {
    var out = [], re = /([MLAZ])([^MLAZ]*)/g, m;
    while ((m = re.exec(d))) { var n = m[2].trim() ? m[2].trim().split(/[\s,]+/).map(Number) : []; out.push({ c: m[1], n: n }); }
    return out;
  });

  /** CSS cubic-bezier(x1, y1, x2, y2) as a function of x */
  function bezier(x1, y1, x2, y2) {
    return function (x) {
      if (x <= 0) return 0; if (x >= 1) return 1;
      var lo = 0, hi = 1, t = x;
      for (var i = 0; i < 22; i++) { t = (lo + hi) / 2; var bx = 3 * x1 * t * (1 - t) * (1 - t) + 3 * x2 * t * t * (1 - t) + t * t * t; if (bx < x) lo = t; else hi = t; }
      return 3 * y1 * t * (1 - t) * (1 - t) + 3 * y2 * t * t * (1 - t) + t * t * t;
    };
  }
  var drawEase = bezier(0.5, 0, 0.2, 1), resEase = bezier(0.65, 0, 0.15, 1);
  var clamp01 = function (v) { return v < 0 ? 0 : v > 1 ? 1 : v; };
  var smooth = function (v) { v = clamp01(v); return v * v * (3 - 2 * v); };

  // the intro's own clock: the CSS clock animation on .ww-art (started at first paint), else the first frame
  var clock = null, wall0 = null;
  function introTime(ts) {
    if (!clock && art && art.getAnimations) { var a = art.getAnimations(); if (a.length) clock = a[0]; }
    if (clock && typeof clock.currentTime === "number") return clock.currentTime / 1000;
    if (wall0 === null) wall0 = ts;
    return (ts - wall0) / 1000;
  }

  var W = 0, H = 0, dpr = 1, gap = 24, dashes = [], ink = "#fafafa", teal = "#2dd4bf", base = 0.085, dark = true, geo = null;
  var ripples = [], raf = 0, last = 0;
  var ptr = { x: 0, y: 0, tx: 0, ty: 0, on: 0, want: 0, seen: false }, pull = { x: 0, y: 0 }, lastDown = null;
  var exit = null; // { at, ox, oy, unit, smax }

  function colours() {
    var cs = getComputedStyle(boot);
    ink = cs.getPropertyValue("--b-ink").trim() || "#fafafa";
    teal = cs.getPropertyValue("--b-teal").trim() || "#2dd4bf";
    dark = (cs.getPropertyValue("--b-bg").trim().toLowerCase() || "#0a0a0a") === "#0a0a0a";
    base = dark ? 0.085 : 0.075;
  }

  function layout() {
    W = boot.clientWidth; H = boot.clientHeight;
    dpr = Math.min(window.devicePixelRatio || 1, 1.5); // 1.25 px strokes need no more; keeps a full-screen canvas cheap
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    gap = W < 640 ? 21 : 24;
    var cols = Math.ceil(W / gap) + 2, rows = Math.ceil(H / (gap * 0.9)) + 2;
    var ox = (W - (cols - 1) * gap) / 2, oy = (H - (rows - 1) * gap * 0.9) / 2;
    dashes = [];
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        // every other row half a step over: a crowd, not a ruled grid; clearest around the centre, falling away to the edges
        var dx = ox + c * gap + (r % 2 ? gap / 2 : 0), dy = oy + r * gap * 0.9;
        var fx = (dx - W / 2) / (W / 2), fy = (dy - H / 2) / (H / 2);
        dashes.push({ x: dx, y: dy, s: (c + r) % 2 ? 1 : -1, v: 1 - 0.6 * Math.min(1, (fx * fx + fy * fy) / 2) });
      }
    }
    // the lockup's own box (without the pointer's drift) and the mark's place in it; the big W starts at the lockup's centre
    var saved = lockup.style.transform; lockup.style.transform = "none";
    var b = boot.getBoundingClientRect(), L = lockup.getBoundingClientRect(), T = tagEl ? tagEl.getBoundingClientRect() : null;
    lockup.style.transform = saved;
    var m = mark.offsetWidth;
    geo = {
      m: m,
      fx: L.left - b.left + mark.offsetLeft + m / 2, fy: L.top - b.top + mark.offsetTop + m / 2,
      bx: L.left - b.left + L.width / 2, by: L.top - b.top + L.height / 2,
      lock: { x0: L.left - b.left, y0: L.top - b.top, x1: L.right - b.left, y1: L.bottom - b.top },
      tag: T ? { x0: T.left - b.left, y0: T.top - b.top, x1: T.right - b.left, y1: T.bottom - b.top + 10 } : null,
    };
  }

  /** the mark on screen at intro time t: centre and size in px (the FLIP from the big centred W to its lockup place) */
  function markAt(t) {
    var e = resEase((t - RES0) / RES_DUR);
    return { x: geo.bx + (geo.fx - geo.bx) * e + pull.x, y: geo.by + (geo.fy - geo.by) * e + pull.y, size: geo.m * (K + (1 - K) * e) };
  }

  /** the pen on the W path at time t: grid point, arc position, visibility */
  function penAt(t) {
    if (t < PEN0) return null;
    var i = Math.min(3, Math.floor((t - PEN0) / STEP)), u = drawEase((t - PEN0 - i * STEP) / DUR);
    var a = V[i], b = V[i + 1];
    var vis = smooth((t - PEN0) / 0.06) * (1 - smooth((t - PEN_END) / 0.2));
    return { x: a[0] + (b[0] - a[0]) * u, y: a[1] + (b[1] - a[1]) * u, arc: (i + u) * SEG, vis: vis };
  }
  function pointAtArc(s) {
    s = Math.max(0, Math.min(4 * SEG, s));
    var i = Math.min(3, Math.floor(s / SEG)), u = (s - i * SEG) / SEG;
    return [V[i][0] + (V[i + 1][0] - V[i][0]) * u, V[i][1] + (V[i + 1][1] - V[i][1]) * u];
  }

  function drawPen(p) {
    if (!pen) return;
    if (!p || p.vis <= 0.001) { pen.style.opacity = "0"; return; }
    pen.style.opacity = p.vis.toFixed(3);
    glow.setAttribute("cx", p.x.toFixed(2)); glow.setAttribute("cy", p.y.toFixed(2));
    core.setAttribute("cx", p.x.toFixed(2)); core.setAttribute("cy", p.y.toFixed(2));
    // the trail: the last 11 units of the path behind the tip, turning corners with it
    var from = Math.max(0, p.arc - 11), pts = [pointAtArc(from)];
    for (var k = 1; k < 4; k++) if (k * SEG > from && k * SEG < p.arc) pts.push(V[k]);
    pts.push([p.x, p.y]);
    var d = "M" + pts.map(function (q) { return q[0].toFixed(2) + " " + q[1].toFixed(2); }).join("L");
    trail.setAttribute("d", d);
    trailGrad.setAttribute("x1", pts[0][0].toFixed(2)); trailGrad.setAttribute("y1", pts[0][1].toFixed(2));
    trailGrad.setAttribute("x2", p.x.toFixed(2)); trailGrad.setAttribute("y2", p.y.toFixed(2));
  }

  /** the exit window: the four pieces of the mark, focus at (ox, oy), `unit` px per grid unit, cut out of the screen */
  function windowPath(ox, oy, unit) {
    var d = "M0 0H" + W + "V" + H + "H0Z";
    for (var p = 0; p < 4; p++) {
      var P = PIECES[p];
      for (var j = 0; j < P.length; j++) {
        var c = P[j], n = c.n;
        if (c.c === "Z") { d += "Z"; continue; }
        var x = n[n.length - 2], y = n[n.length - 1];
        var X = (ox + (x - FOCUS[0]) * unit).toFixed(1), Y = (oy + (y - FOCUS[1]) * unit).toFixed(1);
        if (c.c === "A") { var rr = (n[0] * unit).toFixed(2); d += "A" + rr + " " + rr + " 0 0 0 " + X + " " + Y; }
        else d += c.c + X + " " + Y;
      }
    }
    return d;
  }

  function startExit() {
    if (exit || still) return;
    var t = performance.now(), mk = markAt(clock && typeof clock.currentTime === "number" ? clock.currentTime / 1000 : 9);
    var unit, ox, oy;
    if (lastDown && t - lastDown.at < 2500) { unit = geo.m * 0.55 / 64; ox = lastDown.x; oy = lastDown.y; }
    else { unit = mk.size / 64; ox = mk.x + (FOCUS[0] - 32) * unit; oy = mk.y + (FOCUS[1] - 32) * unit; }
    // the third stroke's half-width (3.8 units) must outgrow half the screen's diagonal
    var smax = 1.15 * (Math.hypot(W, H) / 2) / (3.8 * unit);
    exit = { at: t, ox: ox, oy: oy, unit: unit, smax: Math.max(2, smax) };
    ripples.push({ x: ox, y: oy, at: null, amp: 1, v: 1700 });
    wake();
  }

  function frame(ts) {
    raf = 0;
    if (!boot.isConnected) return stop();
    var t = still ? 9 : introTime(ts), dt = Math.min(0.05, Math.max(0, (ts - last) / 1000)); last = ts;
    var now = ts / 1000;

    // the exit window
    var ex = 0;
    if (exit) {
      ex = clamp01((performance.now() - exit.at) / REVEAL_MS);
      if (ex >= 1) { boot.style.clipPath = "inset(50%)"; boot.style.visibility = "hidden"; return stop(); }
      var s = Math.exp(Math.log(exit.smax) * Math.pow(ex, 1.7)), c = smooth(ex);
      var wx = exit.ox + (W / 2 - exit.ox) * c, wy = exit.oy + (H / 2 - exit.oy) * c;
      boot.style.clipPath = 'path(evenodd, "' + windowPath(wx, wy, exit.unit * s) + '")';
    }

    // pointer: ease toward the target, fade its presence in and out; the lockup drifts after it
    var k = 1 - Math.exp(-dt * 12), pulling = false;
    ptr.x += (ptr.tx - ptr.x) * k; ptr.y += (ptr.ty - ptr.y) * k;
    ptr.on += (ptr.want - ptr.on) * (1 - Math.exp(-dt * 6));
    if (fine && ptr.seen && !exit) {
      var gx = Math.max(-8, Math.min(8, (ptr.x - W / 2) * 0.012)) * ptr.on, gy = Math.max(-6, Math.min(6, (ptr.y - H / 2) * 0.012)) * ptr.on;
      pulling = Math.abs(gx - pull.x) > 0.05 || Math.abs(gy - pull.y) > 0.05;
      pull.x += (gx - pull.x) * k; pull.y += (gy - pull.y) * k;
      lockup.style.transform = "translate3d(" + pull.x.toFixed(2) + "px," + pull.y.toFixed(2) + "px,0)";
    }

    // the pen (in the mark's own svg, so it moves and scales with it)
    var p = still ? null : penAt(t);
    drawPen(p);
    var mk = markAt(t), penX = null, penY = null;
    if (p && p.vis > 0.01) { penX = mk.x + (p.x - 32) / 64 * mk.size; penY = mk.y + (p.y - 32) / 64 * mk.size; }

    // the wave of light: a front crossing the whole field left to right in step with the pen, a crescent leaning back
    var wv = (t - WAVE0) / (WAVE1 - WAVE0), front = null, wAmp = 0, band = W < 640 ? 70 : 120;
    if (!still && wv > -0.05 && wv < 1.1) { front = -0.14 * W + 1.28 * W * clamp01(wv); wAmp = smooth(wv / 0.12) * (1 - smooth((wv - 0.9) / 0.2)); }

    for (var q = ripples.length - 1; q >= 0; q--) {
      var rp = ripples[q];
      if (rp.at === null) rp.at = now;
      if ((now - rp.at) * rp.v > Math.hypot(W, H) + 200) ripples.splice(q, 1);
    }

    // where the lockup is, the crowd steps aside (soft edges; the zones follow the big W as it settles)
    var zones = [];
    var hw = mk.size * 0.46 + 14, hh = mk.size * 0.42 + 14;
    zones.push({ x0: mk.x - hw, y0: mk.y - hh, x1: mk.x + hw, y1: mk.y + hh, s: 1 });
    var nameOn = smooth((t - 1.95) / 0.4);
    if (nameOn > 0) zones.push({ x0: geo.lock.x0 - 16 + pull.x, y0: geo.lock.y0 - 12 + pull.y, x1: geo.lock.x1 + 16 + pull.x, y1: geo.lock.y1 + 12 + pull.y, s: nameOn });
    var tagOn = smooth((t - 2.55) / 0.4);
    if (tagOn > 0 && geo.tag) zones.push({ x0: geo.tag.x0 - 16 + pull.x, y0: geo.tag.y0 - 8 + pull.y, x1: geo.tag.x1 + 16 + pull.x, y1: geo.tag.y1 + 8 + pull.y, s: tagOn });

    var fade = still ? 1 : smooth(t / 0.45);
    var exitFade = 1 - smooth(ex * 1.15), spread = 0.5 * ex * ex;
    var cxo = exit ? exit.ox : 0, cyo = exit ? exit.oy : 0;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // the light itself: a soft band travelling with the front
    if (front !== null && wAmp > 0.01) {
      var lg = ctx.createLinearGradient(front - 220, 0, front + 220, 0);
      var tint = dark ? "255,255,255" : "12,124,114";
      lg.addColorStop(0, "rgba(" + tint + ",0)"); lg.addColorStop(0.5, "rgba(" + tint + "," + (0.05 * wAmp).toFixed(3) + ")"); lg.addColorStop(1, "rgba(" + tint + ",0)");
      ctx.fillStyle = lg; ctx.fillRect(Math.max(0, front - 220), 0, 440, H);
    }
    ctx.lineCap = "round"; ctx.lineWidth = 1.25;
    var inkB = [], tealB = [];
    for (var z = 0; z <= 12; z++) { inkB.push([]); tealB.push([]); }
    var PS2 = 2 * 110 * 110, QS2 = 2 * 85 * 85, RW = 46;
    for (var n = 0; n < dashes.length; n++) {
      var d = dashes[n], x = d.x, y = d.y;
      if (exit) { x += (x - cxo) * spread; y += (y - cyo) * spread; }
      var room = 1;
      for (var zi = 0; zi < zones.length; zi++) {
        var Z = zones[zi];
        var exz = Math.max(Z.x0 - x, 0, x - Z.x1), eyz = Math.max(Z.y0 - y, 0, y - Z.y1);
        var o = Math.sqrt(exz * exz + eyz * eyz);
        room = Math.min(room, 1 - Z.s * (1 - Math.min(1, o / 28)));
      }
      if (room <= 0.01) continue;
      var e = 0, crest = 0, lean = 0;
      if (front !== null && wAmp > 0.01) {
        var fxw = front - Math.abs(y - H / 2) * 0.22, dw = (x - fxw) / band;
        if (dw > -3 && dw < 3) { crest = wAmp * Math.exp(-dw * dw); e += crest; }
      }
      if (penX !== null) { var px = x - penX, py = y - penY, pd = px * px + py * py; if (pd < 4 * PS2) e += 0.32 * p.vis * Math.exp(-pd / PS2); }
      for (var j = 0; j < ripples.length; j++) {
        var R = ripples[j], age = now - R.at;
        if (age < 0) continue;
        var dist = Math.hypot(d.x - R.x, d.y - R.y) - age * R.v;
        if (dist > -3 * RW && dist < 3 * RW) e += R.amp * Math.exp(-age * 0.6) * Math.exp(-(dist * dist) / (RW * RW));
      }
      if (ptr.on > 0.01 && !exit) {
        var mx = x - ptr.x, my = y - ptr.y, md = mx * mx + my * my;
        if (md < 4 * QS2) { var pe = 0.8 * ptr.on * Math.exp(-md / QS2); e += pe; lean += Math.max(-1, Math.min(1, -mx / 85)) * pe * 0.9; }
      }
      if (e > 1) e = 1;
      // standing up: taller, raised, brighter, leaning alternately into the mark's angle (and toward the pointer)
      var len = 6 + e * 6, rise = e * 5, ang = d.s * LEAN * Math.min(1, e * 1.4) + lean;
      var sx = Math.sin(ang) * len / 2, sy = Math.cos(ang) * len / 2;
      var alpha = (base * d.v + e * 0.5) * fade * room * exitFade;
      var seg = [x + sx, y - rise - sy, x - sx, y - rise + sy];
      // the crest of the wave catches a little teal; everything else stays monochrome
      var ta = crest > 0.5 ? (crest - 0.5) * 1.1 * fade * room : 0;
      if (ta > 0.04) { var tb = Math.min(12, Math.round(ta * 20)); tealB[tb].push(seg); alpha *= 0.55; }
      var bi = Math.min(12, Math.round(alpha * 20));
      if (bi > 0) inkB[bi].push(seg);
    }
    function strokeBuckets(B, colour) {
      ctx.strokeStyle = colour;
      for (var b = 1; b <= 12; b++) {
        var L = B[b];
        if (!L.length) continue;
        ctx.globalAlpha = b / 20;
        ctx.beginPath();
        for (var m = 0; m < L.length; m++) { var S = L[m]; ctx.moveTo(S[0], S[1]); ctx.lineTo(S[2], S[3]); }
        ctx.stroke();
      }
    }
    strokeBuckets(inkB, ink);
    strokeBuckets(tealB, teal);
    ctx.globalAlpha = 1;

    var busy = !still && (exit || t < RES0 + RES_DUR + 0.3 || ripples.length > 0 || Math.abs(ptr.want - ptr.on) > 0.01 || pulling ||
      Math.abs(ptr.tx - ptr.x) > 0.3 || Math.abs(ptr.ty - ptr.y) > 0.3);
    if (busy) raf = requestAnimationFrame(frame);
  }

  function wake() { if (!raf && boot.isConnected && !still) { last = performance.now(); raf = requestAnimationFrame(frame); } }

  function onMove(e) {
    if (e.pointerType === "touch") return;
    var b = boot.getBoundingClientRect();
    ptr.tx = e.clientX - b.left; ptr.ty = e.clientY - b.top;
    if (!ptr.seen) { ptr.x = ptr.tx; ptr.y = ptr.ty; ptr.seen = true; }
    ptr.want = 1; wake();
  }
  function onLeave() { ptr.want = 0; wake(); }
  function onDown(e) {
    var b = boot.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
    lastDown = { x: x, y: y, at: performance.now() };
    ripples.push({ x: x, y: y, at: null, amp: 0.85, v: 900 });
    wake();
  }
  function onResize() { if (exit) return; layout(); wake(); if (still) requestAnimationFrame(frame); }
  var watch = typeof MutationObserver !== "undefined" ? new MutationObserver(function () {
    if (boot.classList.contains("ww-boot--done")) startExit();
    colours(); wake();
  }) : null;

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    boot.removeEventListener("pointermove", onMove);
    boot.removeEventListener("pointerleave", onLeave);
    boot.removeEventListener("pointerdown", onDown);
    removeEventListener("resize", onResize);
    if (watch) watch.disconnect();
  }

  colours();
  layout();
  addEventListener("resize", onResize);
  if (still) { requestAnimationFrame(frame); return; }
  boot.addEventListener("pointermove", onMove);
  boot.addEventListener("pointerleave", onLeave);
  boot.addEventListener("pointerdown", onDown);
  if (watch) {
    watch.observe(root, { attributes: true, attributeFilter: ["data-theme", "class"] });
    watch.observe(boot, { attributes: true, attributeFilter: ["class"] });
  }
  raf = requestAnimationFrame(frame);
})();

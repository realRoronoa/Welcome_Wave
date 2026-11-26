/* One shot's <video>. It plays at its own speed while its shot is on screen and pauses once it is off; the scroll never sets
   its time. A clip starts from its shot's entry time (c0, as a media fragment, so no seek is needed) and loops, so a shot
   held on screen while the reader reads never freezes on its last frame: from its own loop point (shot.loopAt, or its
   start), a jump in the clip's own time when it ends, never one the scroll sets.
   The loop is never seen as a jump: as the clip ends, its last frame is held on a canvas in the same box (so it breathes
   and moves with the footage) and dissolves into the clip running again from its loop point (SEAM ms, opacity only, on
   the compositor), the way a film lap-dissolves a held shot into itself. The canvas exists only while its shot is on.
   The poster (an <img> under the video) shows until the video has a frame, and stays if anything fails, so a decode
   error, a blocked autoplay (an iPhone in Low Power Mode) or a slow network never leaves a hole.
   Loading: nothing until the shot is near, then the whole clip. Giving a video its source sets up a decoder on the main
   thread, so that waits for the page to be still (SceneVideo.quietAt): a few seconds at most for a clip ahead, a moment
   when the shot is already on screen. A clip that has played and is well off screen goes back to
   its entry frame while the page is still (rewind), so it starts again from there next time. */
import { Shot, sources, variant } from "./film";

/** ms: a loop's dissolve (the held last frame into the clip running again) */
const SEAM = 1100;
type FrameVideo = HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };

export class SceneVideo {
  /** until when the page counts as moving (the film's controller pushes it on every scroll) */
  static quietAt = 0;
  readonly shot: Shot;
  readonly el: HTMLVideoElement;
  private layer: HTMLElement;
  private assigned = false;
  private waiting = 0;
  private waitUntil = 0;
  private want = false;
  private failed = false;
  private gone = false;
  /** the held last frame a loop dissolves from (made at the first loop, let go when the shot goes off) */
  private seam: HTMLCanvasElement | null = null;
  private seamFade: Animation | null = null;
  /** a frame is held and waiting to dissolve (one dissolve per loop, whichever signal comes first) */
  private armed = false;
  /** the clip time a shot starts from */
  private readonly entry: number;

  constructor(shot: Shot, el: HTMLVideoElement, layer: HTMLElement) {
    this.shot = shot;
    this.el = el;
    this.layer = layer;
    this.entry = shot.c0;
    el.muted = true;
    el.defaultMuted = true;
    el.setAttribute("muted", "");
    el.playsInline = true;
    el.disablePictureInPicture = true;
    el.loop = false; // looped here, through a dissolve (onEnded)
    el.preload = "none";
    el.addEventListener("loadeddata", this.onReady);
    el.addEventListener("error", this.onError);
    el.addEventListener("ended", this.onEnded);
  }

  /** Load the clip (as it approaches). `urgent`: the shot is on screen now, so the source goes in at the first still moment
   *  or shortly, whichever comes first. */
  prepare(urgent = false) {
    if (this.assigned || this.failed || this.gone) return;
    const limit = performance.now() + (urgent ? 250 : 2500);
    if (!this.waiting || limit < this.waitUntil) this.waitUntil = limit;
    if (!this.waiting) this.waitForQuiet();
  }

  private waitForQuiet = () => {
    this.waiting = 0;
    if (this.assigned || this.gone || this.failed) return;
    const now = performance.now();
    if (now < SceneVideo.quietAt && now < this.waitUntil) {
      this.waiting = window.setTimeout(this.waitForQuiet, Math.max(16, Math.min(SceneVideo.quietAt, this.waitUntil) - now));
      return;
    }
    this.assign();
  };

  private assign() {
    const list = sources(this.shot, variant(this.shot));
    const pick = list.find((s) => this.el.canPlayType(s.type) !== "") ?? list[0];
    this.assigned = true;
    this.el.preload = "auto";
    this.el.src = this.entry > 0 ? `${pick.src}#t=${this.entry.toFixed(2)}` : pick.src;
    if (this.want) this.start();
  }

  /** Whether the clip has been asked to play (its shot is on screen and it was let start). */
  get playing() { return this.want; }

  /** Play while the shot is on screen, pause once it is not. */
  play(on: boolean) {
    if (on === this.want) return;
    this.want = on;
    if (!on) { if (this.assigned) this.el.pause(); this.dropSeam(); return; }
    if (!this.assigned) { this.prepare(true); return; }
    this.start();
  }

  private start() {
    if (this.failed || this.gone || !this.el.paused) return;
    this.el.play()?.catch(() => { /* autoplay refused: the frame on screen (or the poster) stays */ });
  }

  /** Off screen and the page still: back to the entry frame if it has played on from there, ready for its next showing. */
  rewind() {
    if (this.shot.u0 === 0 || this.want || !this.assigned || this.failed || this.el.readyState < 1) return;
    if (this.el.ended || Math.abs(this.el.currentTime - this.entry) > 0.25) this.el.currentTime = this.entry;
  }

  /** The page was hidden or shown again. */
  suspend(hidden: boolean) {
    if (!this.assigned) return;
    if (hidden) this.el.pause();
    else if (this.want) this.start();
  }

  private onReady = () => {
    this.layer.classList.add("is-ready");
    if (this.want) this.start();
  };

  /** the clip ended: back to its loop point and on (only while its shot is on screen), through a dissolve from the frame it
   *  ended on, so the loop reads as the shot carrying on rather than as a jump */
  private onEnded = () => {
    if (this.gone || this.failed) return;
    const to = this.shot.loopAt ?? 0;
    if (!this.want || document.hidden) { this.el.currentTime = to; return; }
    const held = this.hold();
    const el = this.el as FrameVideo;
    const on = () => {
      if (this.gone || !this.want) return;
      this.start();
      if (!held) return;
      // the dissolve starts once the clip shows its first frame from the loop point (not while the seek is still decoding),
      // or shortly anyway: the held frame never stays over the playing clip
      const go = () => this.fadeSeam();
      if (el.requestVideoFrameCallback) el.requestVideoFrameCallback(go);
      else el.addEventListener("playing", go, { once: true });
      window.setTimeout(go, 400);
    };
    const seek = () => {
      if (this.gone) return;
      if (!this.want) { el.currentTime = to; return; }
      el.addEventListener("seeked", on, { once: true });
      el.currentTime = to;
    };
    // the held frame must be on screen before the clip moves: video frames reach the compositor by their own path, and a
    // quick seek could show the loop point for a frame before the canvas over it is drawn (a one-frame flash). The clip
    // rests on its last frame meanwhile, the same picture the canvas holds.
    if (held) requestAnimationFrame(() => requestAnimationFrame(seek));
    else seek();
  };

  /** the frame on screen now, held on the seam canvas at full opacity (false if it cannot be drawn: then the loop is a
   *  plain jump, as before) */
  private hold(): boolean {
    const el = this.el, w = el.videoWidth, h = el.videoHeight;
    if (!w || !h || el.readyState < 2) return false;
    try {
      let c = this.seam;
      if (!c) {
        c = document.createElement("canvas");
        c.className = "s-seam";
        c.setAttribute("aria-hidden", "true");
        el.after(c);
        this.seam = c;
      }
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      const g = c.getContext("2d", { alpha: false });
      if (!g) return false;
      g.drawImage(el, 0, 0, w, h);
      this.seamFade?.cancel(); // the last dissolve's held end (opacity 0) goes, in the same task as the frame comes up
      this.seamFade = null;
      c.style.opacity = "1";
      this.armed = true;
      return true;
    } catch {
      return false;
    }
  }

  private fadeSeam() {
    const c = this.seam;
    if (!c || this.gone || !this.armed) return;
    this.armed = false;
    this.seamFade?.cancel();
    // gentle at both ends, and it keeps its end (fill): nothing else is written as it starts, so no frame can show the
    // canvas gone before the animation has begun (the next loop's hold cancels it)
    const a = c.animate?.([{ opacity: 1 }, { opacity: 0 }], { duration: SEAM, easing: "cubic-bezier(.45, 0, .55, 1)", fill: "forwards" }) ?? null;
    if (!a) c.style.opacity = "0";
    this.seamFade = a;
  }

  /** the shot went off: the held frame is let go (its memory too) */
  private dropSeam() {
    const c = this.seam;
    this.armed = false;
    if (!c) return;
    this.seamFade?.cancel();
    this.seamFade = null;
    c.remove();
    c.width = c.height = 0;
    this.seam = null;
  }

  private onError = () => {
    if (this.failed) return;
    this.failed = true;
    this.layer.classList.remove("is-ready");
    this.layer.classList.add("is-failed");
  };

  dispose() {
    this.gone = true;
    clearTimeout(this.waiting);
    this.el.pause();
    this.el.removeEventListener("loadeddata", this.onReady);
    this.el.removeEventListener("error", this.onError);
    this.el.removeEventListener("ended", this.onEnded);
    this.dropSeam();
    this.el.removeAttribute("src");
    this.el.load();
  }
}

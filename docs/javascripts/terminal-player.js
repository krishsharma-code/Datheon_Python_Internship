/* Terminal player for the live demo pages.
 *
 * The pages ship the whole transcript as static HTML inside a <pre>, so they are
 * readable without this file, in a feed reader and by a screen reader. This
 * script only *enhances* that: it hides the static copy, fetches the session
 * JSON the page points at (data-session), and replays it line by line with a
 * prompt, a caret and controls.
 *
 * Three rules it keeps:
 *
 *   1. No network beyond the page's own origin. The session JSON and this file
 *      are the only things it loads, so the demos work on a locked-down network
 *      and on a static host with no build step.
 *   2. The static transcript stays in the DOM (visually hidden) for screen
 *      readers, and comes back if the fetch fails — a broken fetch must not cost
 *      the reader the content.
 *   3. prefers-reduced-motion is honoured: the session is rendered complete,
 *      instantly, with the controls still available for a manual replay.
 */
(function () {
  "use strict";

  var LINE_DELAY_MS = 90; // per output line at 1x
  var CHAR_DELAY_MS = 28; // per typed character at 1x
  var START_DELAY_MS = 250;

  function prefersReducedMotion() {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  /* Flatten the session into a list of renderable actions. */
  function toActions(session) {
    var actions = [];
    (session.steps || []).forEach(function (step) {
      if (step.kind === "command") {
        actions.push({ type: "command", text: step.text });
      } else if (step.kind === "input") {
        actions.push({ type: "input", text: step.text, prompt: step.prompt || "" });
      } else {
        String(step.text || "")
          .split("\n")
          .forEach(function (line) {
            actions.push({ type: "output", text: line });
          });
      }
    });
    return actions;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function Terminal(root) {
    this.root = root;
    this.url = root.getAttribute("data-session");
    this.fallback = root.querySelector(".demo-terminal__fallback");
    this.session = null;
    this.actions = [];
    this.index = 0;
    this.timer = null;
    this.playing = false;
    this.speed = 1;
    this.finished = false;
    this.build();
  }

  Terminal.prototype.build = function () {
    var self = this;

    var toolbar = el("div", "demo-terminal__toolbar");

    this.title = el("span", "demo-terminal__title", this.root.getAttribute("data-command") || "");
    toolbar.appendChild(this.title);

    var controls = el("span", "demo-terminal__controls");

    this.playButton = el("button", "demo-terminal__button", "Pause");
    this.playButton.type = "button";
    this.playButton.setAttribute("aria-label", "Pause the replay");
    this.playButton.addEventListener("click", function () {
      if (self.finished) {
        self.replay();
      } else if (self.playing) {
        self.pause();
      } else {
        self.play();
      }
    });
    controls.appendChild(this.playButton);

    this.replayButton = el("button", "demo-terminal__button", "Replay");
    this.replayButton.type = "button";
    this.replayButton.addEventListener("click", function () {
      self.replay();
    });
    controls.appendChild(this.replayButton);

    this.speedButton = el("button", "demo-terminal__button", "1×");
    this.speedButton.type = "button";
    this.speedButton.setAttribute("aria-label", "Replay speed");
    this.speedButton.addEventListener("click", function () {
      self.speed = self.speed === 1 ? 2 : self.speed === 2 ? 4 : 1;
      self.speedButton.textContent = self.speed + "×";
    });
    controls.appendChild(this.speedButton);

    toolbar.appendChild(controls);
    this.root.appendChild(toolbar);

    this.screen = el("div", "demo-terminal__screen");
    this.screen.setAttribute("aria-hidden", "true");
    this.root.appendChild(this.screen);

    if (this.fallback) {
      this.fallback.classList.add("demo-terminal__fallback--hidden");
    }

    this.status = el("p", "demo-terminal__status", "");
    this.root.appendChild(this.status);

    if (!this.url) {
      this.fail("no session to play");
      return;
    }

    fetch(this.url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (session) {
        self.session = session;
        self.actions = toActions(session);
        self.status.textContent =
          session.title + " · " + self.actions.length + " lines · captured from a real run";
        if (prefersReducedMotion()) {
          self.renderAll({ scroll: false });
          self.finished = true;
          /* With the whole session already on screen a play/pause button has
           * nothing to do, and two controls labelled "Replay" is worse than
           * one. The replay button stays. */
          self.playButton.classList.add("demo-terminal__button--hidden");
        } else {
          window.setTimeout(function () {
            self.play();
          }, START_DELAY_MS);
        }
      })
      .catch(function (error) {
        self.fail("the session could not be loaded (" + error.message + ")");
      });
  };

  Terminal.prototype.fail = function (reason) {
    /* Leave the static transcript in place: it is the content. */
    if (this.fallback) {
      this.fallback.classList.remove("demo-terminal__fallback--hidden");
    }
    this.status.textContent = reason + " — showing the captured transcript.";
    if (this.playButton) this.playButton.disabled = true;
    if (this.replayButton) this.replayButton.disabled = true;
    if (this.speedButton) this.speedButton.disabled = true;
  };

  Terminal.prototype.append = function (node) {
    this.screen.appendChild(node);
    this.screen.scrollTop = this.screen.scrollHeight;
  };

  Terminal.prototype.renderAction = function (action, partial) {
    var line = el("div", "demo-terminal__line demo-terminal__line--" + action.type);
    if (action.type === "command") {
      line.appendChild(el("span", "demo-terminal__prompt", "$ "));
      line.appendChild(el("span", "demo-terminal__text", action.text));
    } else if (action.type === "input") {
      if (action.prompt) line.appendChild(el("span", "demo-terminal__prompt", action.prompt));
      line.appendChild(el("span", "demo-terminal__text", partial === undefined ? action.text : partial));
    } else {
      line.appendChild(el("span", "demo-terminal__text", action.text));
    }
    return line;
  };

  Terminal.prototype.play = function () {
    if (!this.actions.length) return;
    this.playing = true;
    this.playButton.textContent = "Pause";
    this.playButton.setAttribute("aria-label", "Pause the replay");
    this.tick();
  };

  Terminal.prototype.pause = function () {
    this.playing = false;
    this.playButton.textContent = "Resume";
    this.playButton.setAttribute("aria-label", "Resume the replay");
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
  };

  Terminal.prototype.replay = function () {
    if (this.timer) window.clearTimeout(this.timer);
    this.screen.textContent = "";
    this.index = 0;
    this.finished = false;
    this.playing = false;
    if (prefersReducedMotion()) {
      this.renderAll({ scroll: false });
      this.finished = true;
      return;
    }
    this.play();
  };

  Terminal.prototype.tick = function () {
    var self = this;
    if (!this.playing) return;
    if (this.index >= this.actions.length) {
      this.finished = true;
      this.playing = false;
      this.playButton.textContent = "Replay";
      this.playButton.setAttribute("aria-label", "Replay the session");
      return;
    }
    var action = this.actions[this.index];
    var line = this.renderAction(action);
    this.append(line);
    this.index += 1;

    var delay = LINE_DELAY_MS;
    if (action.type === "input") {
      /* Typed input is the part that makes it feel live: type it, then move on. */
      var textNode = line.querySelector(".demo-terminal__text");
      var typed = "";
      var chars = action.text.split("");
      var charDelay = CHAR_DELAY_MS / this.speed;
      var step = function () {
        if (!self.playing) return;
        if (!chars.length) {
          self.timer = window.setTimeout(function () {
            self.tick();
          }, LINE_DELAY_MS / self.speed);
          return;
        }
        typed += chars.shift();
        textNode.textContent = typed;
        self.timer = window.setTimeout(step, charDelay);
      };
      this.timer = window.setTimeout(step, charDelay);
      return;
    }
    this.timer = window.setTimeout(function () {
      self.tick();
    }, delay / this.speed);
  };

  Terminal.prototype.renderAll = function (options) {
    var self = this;
    var scroll = options && options.scroll === false ? false : true;
    this.screen.textContent = "";
    this.actions.forEach(function (action) {
      self.screen.appendChild(self.renderAction(action));
    });
    /* The animated replay follows the caret, so it scrolls to the bottom; a
     * session rendered whole should be read from the top instead. */
    this.screen.scrollTop = scroll ? this.screen.scrollHeight : 0;
  };

  function init(root) {
    if (!root || root.getAttribute("data-demo-ready") === "true") return;
    root.setAttribute("data-demo-ready", "true");
    try {
      new Terminal(root);
    } catch (error) {
      /* A player that cannot start must not hide the transcript. */
      var fallback = root.querySelector(".demo-terminal__fallback");
      if (fallback) fallback.classList.remove("demo-terminal__fallback--hidden");
    }
  }

  function initAll() {
    var nodes = document.querySelectorAll(".demo-terminal[data-session]");
    Array.prototype.forEach.call(nodes, init);
  }

  /* Two entry points, because the theme gives us two:
   *
   *   - Material's instant navigation swaps the page body without a reload and
   *     announces it on `document$`, so the script has to subscribe to that or
   *     the second page you visit has a dead terminal;
   *   - the first load may or may not be announced on `document$` depending on
   *     how the bundle initialised, so the document's own lifecycle is wired up
   *     as well.
   *
   * `init` is idempotent per terminal (it sets `data-demo-ready`), so being
   * called twice costs nothing. A player that only listened to `document$` and
   * missed its emission would leave the static transcript showing — correct,
   * but not the demo. */
  function start() {
    initAll();
  }

  if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
    window.document$.subscribe(function () {
      initAll();
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

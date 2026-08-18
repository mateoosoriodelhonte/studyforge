/*
 * StudyForge — the small amount of JavaScript the server cannot do.
 *
 * Deliberately minimal. Every interaction that changes data goes through HTMX
 * to a server route; nothing here holds application state. What is here is
 * purely about the keyboard, because a review session driven by mouse is a
 * review session people stop doing.
 */
(function () {
  "use strict";

  /** Keys 1–4 map to the four FSRS ratings, matching the button labels. */
  var RATING_KEYS = { 1: "1", 2: "2", 3: "3", 4: "4" };

  function isTyping(target) {
    if (!target) return false;
    var tag = target.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      target.isContentEditable
    );
  }

  /**
   * Announce a change to assistive technology.
   *
   * HTMX swaps content without a page load, so a screen reader has no reason to
   * re-read anything. The live region in base.html is how we tell it to.
   */
  function announce(message) {
    var region = document.getElementById("live-region");
    if (!region) return;
    region.textContent = "";
    // Re-setting the same text does not re-announce; the tick forces it.
    window.setTimeout(function () {
      region.textContent = message;
    }, 60);
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTyping(event.target)) return;

    var study = document.querySelector("[data-study-session]");
    if (study) {
      // Space or Enter reveals the answer.
      if ((event.key === " " || event.key === "Enter") && !study.dataset.revealed) {
        var reveal = study.querySelector("[data-reveal]");
        if (reveal) {
          event.preventDefault();
          reveal.click();
          return;
        }
      }

      if (RATING_KEYS[event.key] && study.dataset.revealed) {
        var button = study.querySelector('[data-rating="' + event.key + '"]');
        if (button) {
          event.preventDefault();
          button.click();
          return;
        }
      }

      if (event.key === "Escape") {
        var leave = study.querySelector("[data-end-session]");
        if (leave) {
          event.preventDefault();
          leave.click();
        }
      }
      return;
    }

    // "/" focuses search, the one shortcut worth having everywhere.
    if (event.key === "/") {
      var search = document.querySelector('[data-search-input]');
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    var announcement = event.detail.target.getAttribute("data-announce");
    if (announcement) announce(announcement);

    // Move focus to a newly swapped region that asks for it, so keyboard users
    // are not stranded at the top of the document after an update.
    var focusTarget = event.detail.target.querySelector("[data-autofocus]");
    if (focusTarget) focusTarget.focus();
  });

  /** Surface server failures instead of leaving the page silently inert. */
  document.body.addEventListener("htmx:responseError", function (event) {
    announce("Something went wrong. Please try again.");
    console.error("StudyForge request failed", event.detail.xhr.status);
  });

  document.body.addEventListener("htmx:sendError", function () {
    announce("Could not reach the server.");
  });
})();

"use strict";

if ("scrollRestoration" in history) {
  history.scrollRestoration = "manual";
}

window.addEventListener("load", () => {
  window.requestAnimationFrame(() => window.scrollTo(0, 0));
}, { once: true });

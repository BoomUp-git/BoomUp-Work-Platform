"use strict";

const mascot = document.querySelector(".mascot-helper");

if (mascot) {
  const trigger = mascot.querySelector(".floating-koala");
  const message = mascot.querySelector(".mascot-message");
  const messageText = mascot.querySelector(".mascot-message-text");
  const close = mascot.querySelector(".mascot-message-close");
  const alertDot = mascot.querySelector(".mascot-alert-dot");

  const setOpen = (open, unread = false) => {
    message.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
    alertDot.hidden = open || !unread;
  };

  const update = ({ state = "idle", message: text, open = false, notify = false }) => {
    mascot.dataset.mascotState = state;
    if (text) messageText.textContent = text;
    setOpen(open, Boolean(text));
    if (notify && text && "Notification" in window && Notification.permission === "granted") {
      new Notification("BoomUp 发票处理提醒", { body: text, icon: "/static/koala-mascot-v2.png" });
    }
  };

  trigger.addEventListener("click", () => setOpen(message.hidden));
  close.addEventListener("click", () => setOpen(false));
  window.addEventListener("boomup:mascot", (event) => update(event.detail || {}));
}

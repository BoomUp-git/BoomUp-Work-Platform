"use strict";

if ("scrollRestoration" in history) {
  history.scrollRestoration = "manual";
}

window.addEventListener("load", () => {
  window.requestAnimationFrame(() => {
    window.scrollTo(0, 0);
    const failed = document.querySelector(".job-status.failed");
    const review = document.querySelector(".job-status.manual_review");
    const reviewCount = document.querySelector(".result-status-block strong")?.textContent?.trim();
    let detail = { state: "complete", message: "单子已经改完啦，请检查处理后的发票。", open: true, notify: true };
    if (review) {
      detail = { state: "review", message: `单子已经改完，${reviewCount || "还有项目需要人工审核"}，请检查。`, open: true, notify: true };
    } else if (failed) {
      detail = { state: "failed", message: "这张单子没有处理成功，请检查文件和价格数据。", open: true, notify: true };
    }
    window.dispatchEvent(new CustomEvent("boomup:mascot", { detail }));
  });
}, { once: true });

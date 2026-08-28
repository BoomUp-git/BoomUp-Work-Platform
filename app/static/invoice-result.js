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

document.querySelectorAll(".review-item").forEach((item) => {
  if (item.dataset.defaultPriceId) {
    const defaultPrice = item.querySelector(`input[name^="price_choice_"][value="candidate:${CSS.escape(item.dataset.defaultPriceId)}"]`);
    if (defaultPrice) defaultPrice.checked = true;
  }
  const update = () => {
    const customPrice = item.querySelector('input[name^="custom_price_"]');
    const customPriceChoice = item.querySelector('input[name^="price_choice_"][value="custom"]');
    const customDiscount = item.querySelector('input[name^="custom_discount_"]');
    const customDiscountChoice = item.querySelector('input[name^="discount_choice_"][value="custom"]');
    if (customPrice) customPrice.required = Boolean(customPriceChoice?.checked);
    if (customDiscount) customDiscount.required = Boolean(customDiscountChoice?.checked);
    const radioGroups = [...new Set([...item.querySelectorAll('input[type="radio"]')].map((input) => input.name))];
    const radiosDone = radioGroups.every((name) => item.querySelector(`input[name="${name}"]:checked`));
    const checksDone = [...item.querySelectorAll('input[type="checkbox"][required]')].every((input) => input.checked);
    const valuesDone = [...item.querySelectorAll('input[type="number"][required]')].every((input) => input.value !== "" && input.checkValidity());
    const state = item.querySelector(".review-state");
    const done = radiosDone && checksDone && valuesDone;
    item.classList.toggle("review-complete", done);
    if (state) state.textContent = done ? "已审核" : "待审核";
  };
  item.addEventListener("change", update);
  item.addEventListener("input", update);
  update();
});

const reviewForm = document.querySelector(".review-form");
if (reviewForm) {
  reviewForm.addEventListener("submit", () => {
    const button = reviewForm.querySelector('.finalize-bar button[type="submit"]');
    const message = reviewForm.querySelector(".finalize-bar span");
    if (button) {
      button.disabled = true;
      button.textContent = "正在生成最终 Invoice…";
    }
    if (message) message.textContent = "正在写入确认价格和折扣，请不要刷新或重复点击。";
    window.dispatchEvent(new CustomEvent("boomup:mascot", {
      detail: { state: "working", message: "正在生成最终版，请稍等一下。", open: true }
    }));
  });
}

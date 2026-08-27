const form = document.getElementById("invoice-process-form");
const fileInput = document.getElementById("invoice-pdf");
const fileName = document.getElementById("selected-file-name");

if (fileInput && fileName) {
  fileInput.addEventListener("change", () => {
    fileName.textContent = fileInput.files.length
      ? fileInput.files[0].name
      : "尚未选择文件";
  });
}

if (form) {
  form.addEventListener("submit", () => {
    const button = document.getElementById("process-button");
    const status = document.getElementById("processing-status");
    button.disabled = true;
    button.textContent = "正在处理……";
    status.hidden = false;
    window.dispatchEvent(new CustomEvent("boomup:mascot", {
      detail: { state: "processing", message: "正在认真核对价格，请稍候……", open: true },
    }));
  });
}

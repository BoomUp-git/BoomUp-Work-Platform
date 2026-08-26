const form = document.getElementById("invoice-process-form");
if (form) {
  form.addEventListener("submit", () => {
    const button = document.getElementById("process-button");
    const status = document.getElementById("processing-status");
    button.disabled = true;
    button.textContent = "Processing...";
    status.hidden = false;
  });
}

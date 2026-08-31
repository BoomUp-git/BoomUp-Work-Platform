const form = document.getElementById("invoice-process-form");
const fileInput = document.getElementById("invoice-pdf");
const fileName = document.getElementById("selected-file-name");
const dropzone = document.getElementById("invoice-dropzone");

// Browsers may restore this page from the back/forward cache without making a
// request. Reload only for that restoration path so invoice history reflects
// the job the user has just viewed or processed.
window.addEventListener("pageshow", (event) => {
  const navigation = performance.getEntriesByType("navigation")[0];
  if (event.persisted || navigation?.type === "back_forward") {
    window.location.reload();
  }
});

function updateSelectedFile(files) {
  if (!fileName) return;
  fileName.textContent = files?.length ? files[0].name : "尚未选择文件";
  fileName.classList.toggle("has-file", Boolean(files?.length));
}

if (fileInput && fileName) {
  fileInput.addEventListener("change", () => updateSelectedFile(fileInput.files));
}

if (dropzone && fileInput) {
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    if (!files?.length) return;
    const file = files[0];
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      updateSelectedFile([]);
      return;
    }
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
    updateSelectedFile(fileInput.files);
  });
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
}

const selectAllJobs = document.getElementById("select-all-jobs");
const jobSelections = [...document.querySelectorAll(".job-select")];
const deleteButton = document.getElementById("delete-selected-jobs");
const selectionStatus = document.getElementById("history-selection-status");

function updateHistorySelection() {
  const selected = jobSelections.filter((checkbox) => checkbox.checked).length;
  if (deleteButton) deleteButton.disabled = selected === 0;
  if (selectionStatus) {
    selectionStatus.textContent = selected ? `已选择 ${selected} 条记录` : "请选择要删除的记录";
  }
  if (selectAllJobs) {
    selectAllJobs.checked = selected === jobSelections.length && selected > 0;
    selectAllJobs.indeterminate = selected > 0 && selected < jobSelections.length;
  }
}

if (selectAllJobs) {
  selectAllJobs.addEventListener("change", () => {
    jobSelections.forEach((checkbox) => { checkbox.checked = selectAllJobs.checked; });
    updateHistorySelection();
  });
}
jobSelections.forEach((checkbox) => checkbox.addEventListener("change", updateHistorySelection));

document.getElementById("history-delete-form")?.addEventListener("submit", (event) => {
  const selected = jobSelections.filter((checkbox) => checkbox.checked).length;
  if (!selected || !window.confirm(`确定删除选中的 ${selected} 条发票历史吗？PDF 文件也会一并删除。`)) {
    event.preventDefault();
  }
});

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

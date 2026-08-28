const form = document.getElementById("invoice-process-form");
const fileInput = document.getElementById("invoice-pdf");
const fileName = document.getElementById("selected-file-name");
const dropzone = document.getElementById("invoice-dropzone");
const fileError = document.getElementById("file-picker-error");

const localDateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZoneName: "short",
});

const localTimes = [...document.querySelectorAll("time.local-time")];
localTimes.forEach((element) => {
  const date = new Date(element.dateTime);
  if (Number.isNaN(date.getTime())) return;

  const parts = Object.fromEntries(
    localDateTimeFormatter.formatToParts(date).map(({ type, value }) => [type, value]),
  );
  element.textContent = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} ${parts.timeZoneName}`;
  element.title = `浏览器当地时间（${Intl.DateTimeFormat().resolvedOptions().timeZone}）`;
});

const todayJobCount = document.getElementById("today-job-count");
if (todayJobCount) {
  const today = new Date();
  const todayParts = Object.fromEntries(
    localDateTimeFormatter.formatToParts(today).map(({ type, value }) => [type, value]),
  );
  const todayKey = `${todayParts.year}-${todayParts.month}-${todayParts.day}`;
  todayJobCount.textContent = String(
    localTimes.filter((element) => element.textContent.startsWith(todayKey)).length,
  );
}

// Browsers may restore this page from the back/forward cache without making a
// request. Reload only for that restoration path so invoice history reflects
// the job the user has just viewed or processed.
window.addEventListener("pageshow", (event) => {
  const navigation = performance.getEntriesByType("navigation")[0];
  if (event.persisted || navigation?.type === "back_forward") {
    window.location.reload();
  }
});

const showSelectedFile = (file) => {
  if (!fileName || !dropzone) return;
  fileName.textContent = file ? file.name : "尚未选择文件";
  dropzone.classList.toggle("has-file", Boolean(file));
  if (fileError) {
    fileError.hidden = true;
    fileError.textContent = "";
  }
};

const validateFile = (file) => {
  if (!file) return "请选择一个 PDF 文件。";
  const pdfName = file.name.toLowerCase().endsWith(".pdf");
  const pdfType = !file.type || file.type === "application/pdf";
  if (!pdfName || !pdfType) return "这里只能上传 PDF 文件。";
  const maxBytes = Number(dropzone?.dataset.maxBytes || 0);
  if (maxBytes && file.size > maxBytes) {
    return `PDF 不能超过 ${Math.round(maxBytes / 1024 / 1024)} MB。`;
  }
  return "";
};

const acceptFile = (file) => {
  const error = validateFile(file);
  if (error) {
    if (fileError) {
      fileError.textContent = error;
      fileError.hidden = false;
    }
    dropzone?.classList.add("has-error");
    return false;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  dropzone?.classList.remove("has-error");
  showSelectedFile(file);
  return true;
};

if (fileInput && fileName && dropzone) {
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file && !validateFile(file)) showSelectedFile(file);
    else if (file) acceptFile(file);
    else showSelectedFile(null);
  });

  dropzone.addEventListener("click", (event) => {
    if (event.target !== fileInput) fileInput.click();
  });
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropzone.classList.remove("is-dragover");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const files = [...(event.dataTransfer?.files || [])];
    if (files.length !== 1) {
      if (fileError) {
        fileError.textContent = "每次请只拖入一个 PDF 文件。";
        fileError.hidden = false;
      }
      return;
    }
    acceptFile(files[0]);
  });
}

if (form) {
  form.addEventListener("submit", (event) => {
    const error = validateFile(fileInput?.files[0]);
    if (error) {
      event.preventDefault();
      if (fileError) {
        fileError.textContent = error;
        fileError.hidden = false;
      }
      dropzone?.classList.add("has-error");
      return;
    }
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

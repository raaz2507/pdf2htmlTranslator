const reader = document.querySelector("#reader");
const readerNav = document.querySelector("#readerNav");
const navToggle = document.querySelector("#navToggle");
const languageSelect = document.querySelector("#languageSelect");
const sourceLanguageSelect = document.querySelector("#sourceLanguageSelect");
const targetLanguageSelect = document.querySelector("#targetLanguageSelect");
const providerSelect = document.querySelector("#providerSelect");
const translateDocumentButton = document.querySelector("#translateDocumentButton");
const abortTranslationButton = document.querySelector("#abortTranslationButton");
const themeSelect = document.querySelector("#themeSelect");
const pageColorSelect = document.querySelector("#pageColorSelect");
const pageOpacityRange = document.querySelector("#pageOpacityRange");
const missingImageToggle = document.querySelector("#missingImageToggle");
const readerPackageFile = document.querySelector("#readerPackageFile");
const printButton = document.querySelector("#printButton");
const zoomIn = document.querySelector("#zoomIn");
const zoomOut = document.querySelector("#zoomOut");
const documentTitle = document.querySelector("#documentTitle");
const pageCount = document.querySelector("#pageCount");
const readerMessages = document.querySelector("#readerMessages");
const readerMessage = document.querySelector("#readerMessage");
const translationProgress = document.querySelector("#translationProgress");
const translationProgressLabel = document.querySelector("#translationProgressLabel");
const translationLogPanel = document.querySelector("#translationLogPanel");
const translationLogList = document.querySelector("#translationLogList");
const clearTranslationLogView = document.querySelector("#clearTranslationLogView");
const translationEditor = document.querySelector("#translationEditor");
const closeEditor = document.querySelector("#closeEditor");
const editorSource = document.querySelector("#editorSource");
const editorValue = document.querySelector("#editorValue");
const retranslateText = document.querySelector("#retranslateText");
const saveTextTranslation = document.querySelector("#saveTextTranslation");
const editorMessage = document.querySelector("#editorMessage");

let documentData = null;
let translationData = null;
let currentLanguage = "en";
let selectedTextId = null;
const jobId = currentJobId();
let zoom = 1;
let observer = null;
let activeTranslationPoll = null;
let lastTranslationReload = 0;
let translationPollFailures = 0;
const renderedPages = new Map();
const supportedLanguages = [
  { code: "en", label: "English" },
  { code: "hi_modern", label: "Hindi - Modern (Recommended)" },
  { code: "hi_pure", label: "Hindi - Pure" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "ar", label: "Arabic" },
];

init();
initFloatingNav();

function showReaderMessage(kind, text) {
  const message = document.createElement("div");
  message.className = `reader-message-item ${kind}`;
  message.textContent = text;
  readerMessages.prepend(message);
}

async function init() {
  if (!jobId) {
    documentTitle.textContent = "Universal Reader";
    pageCount.textContent = "";
    readerMessage.textContent = "Choose a .phjz package to open it in the reader.";
    reader.innerHTML = "";
    return;
  }

  const dataBase = dataBaseUrl();
  const [documentJson, translationsJson] = await Promise.all([
    fetch(`${dataBase}/document.json`).then((response) => response.json()),
    fetch(`${dataBase}/translations.json`).then((response) => response.json()),
  ]);

  documentData = documentJson;
  translationData = translationsJson;
  currentLanguage = translationData.defaultLanguage || "en";

  documentTitle.textContent = documentData.source || "PDF Reader";
  pageCount.textContent = `${documentData.pages.length} page${documentData.pages.length === 1 ? "" : "s"}`;
  readerMessage.textContent = "";
  buildLanguageOptions();
  buildTranslateOptions();
  renderPlaceholders();
  fitPageToViewport();
  applyZoom();
  reloadTranslationLog();
}

function buildLanguageOptions() {
  languageSelect.innerHTML = "";

  for (const language of availableLanguages()) {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.label;
    languageSelect.appendChild(option);
  }

  languageSelect.value = currentLanguage;
}

function buildTranslateOptions() {
  sourceLanguageSelect.innerHTML = "";
  targetLanguageSelect.innerHTML = "";

  for (const language of availableLanguages()) {
    sourceLanguageSelect.appendChild(languageOption(language));
    targetLanguageSelect.appendChild(languageOption(language));
  }

  sourceLanguageSelect.value = translationData.defaultLanguage || "en";
  targetLanguageSelect.value = currentLanguage === sourceLanguageSelect.value ? "hi_modern" : currentLanguage;
}

function availableLanguages() {
  const languages = new Map();
  for (const language of supportedLanguages) {
    languages.set(language.code, language);
  }
  for (const language of translationData.availableLanguages || []) {
    languages.set(language.code, language);
  }
  return Array.from(languages.values());
}

function languageOption(language) {
  const option = document.createElement("option");
  option.value = language.code;
  option.textContent = language.label;
  return option;
}

function renderPlaceholders() {
  reader.innerHTML = "";
  renderedPages.clear();

  observer = new IntersectionObserver(onPageIntersection, {
    root: null,
    rootMargin: "900px 0px",
    threshold: 0,
  });

  const fragment = document.createDocumentFragment();
  documentData.pages.forEach((page, index) => {
    const pageEl = document.createElement("section");
    pageEl.className = "page";
    pageEl.dataset.pageIndex = String(index);
    pageEl.style.setProperty("--page-width", page.width);
    pageEl.style.setProperty("--page-height", page.height);
    pageEl.innerHTML = `<div class="page-loading">Page ${page.page}</div>`;
    fragment.appendChild(pageEl);
    observer.observe(pageEl);
  });

  reader.appendChild(fragment);
}

function onPageIntersection(entries) {
  for (const entry of entries) {
    const pageEl = entry.target;
    const index = Number(pageEl.dataset.pageIndex);

    if (entry.isIntersecting) {
      if (!renderedPages.has(index)) {
        renderPageAsync(pageEl, documentData.pages[index], index);
      }
    } else if (renderedPages.has(index)) {
      unloadPage(pageEl, index);
    }
  }
}

function renderPageAsync(pageEl, page, index) {
  renderedPages.set(index, true);
  requestAnimationFrame(() => {
    const fragment = document.createDocumentFragment();
    fragment.appendChild(renderVectorLayer(page));

    for (const imageItem of page.images || []) {
      fragment.appendChild(renderImage(imageItem));
    }

    for (const item of page.texts) {
      fragment.appendChild(renderText(item));
    }

    pageEl.replaceChildren(fragment);
  });
}

function unloadPage(pageEl, index) {
  renderedPages.delete(index);
  const page = documentData.pages[index];
  pageEl.replaceChildren(pagePlaceholder(page));
}

function pagePlaceholder(page) {
  const placeholder = document.createElement("div");
  placeholder.className = "page-loading";
  placeholder.textContent = `Page ${page.page}`;
  return placeholder;
}

function renderImage(imageItem) {
  const image = document.createElement("img");
  image.className = "image-item";
  image.loading = "lazy";
  image.decoding = "async";
  image.src = resolveImageSrc(imageItem.src);
  image.alt = "";
  image.style.left = `${imageItem.x}px`;
  image.style.top = `${imageItem.y}px`;
  image.style.width = `${Math.max(imageItem.w, 1)}px`;
  image.style.height = `${Math.max(imageItem.h, 1)}px`;
  image.addEventListener("error", () => {
    image.classList.add("image-missing");
    image.removeAttribute("src");
  });
  return image;
}

function renderText(item) {
  const textEl = document.createElement("span");
  textEl.className = "text-item";
  textEl.dataset.id = item.id;
  textEl.dataset.originalSize = String(item.fontSize);
  textEl.dataset.boxWidth = String(Math.max(item.w, 8));
  textEl.title = item.text;
  textEl.style.left = `${item.x}px`;
  textEl.style.top = `${item.y}px`;
  textEl.style.width = `${Math.max(item.w, 8)}px`;
  textEl.style.height = `${Math.max(item.h * 1.35, item.fontSize * 1.35)}px`;
  textEl.style.fontSize = `${item.fontSize}px`;
  textEl.style.fontWeight = item.fontWeight || "400";
  textEl.style.fontStyle = item.fontStyle || "normal";
  textEl.style.color = item.color || "#15171c";
  textEl.style.fontFamily = fontStack(item.font);
  applyTranslatedText(textEl);
  textEl.addEventListener("click", () => openTranslationEditor(item.id));
  applyTextScale(textEl);
  return textEl;
}

function renderVectorLayer(page) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("vector-layer");
  svg.setAttribute("viewBox", `0 0 ${page.width} ${page.height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");

  const fragment = document.createDocumentFragment();
  for (const drawing of page.drawings || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", drawingPath(drawing));
    path.setAttribute("stroke", drawing.stroke || "none");
    path.setAttribute("fill", drawing.fill || "none");
    path.setAttribute("stroke-width", drawing.strokeWidth || 1);
    path.setAttribute("stroke-opacity", drawing.strokeOpacity ?? 1);
    path.setAttribute("fill-opacity", drawing.fillOpacity ?? 1);
    if (drawing.evenOdd) {
      path.setAttribute("fill-rule", "evenodd");
    }
    fragment.appendChild(path);
  }
  svg.appendChild(fragment);

  return svg;
}

function drawingPath(drawing) {
  const commands = [];

  for (const item of drawing.items) {
    if (item.type === "line") {
      commands.push(`M ${item.x1} ${item.y1} L ${item.x2} ${item.y2}`);
    } else if (item.type === "rect") {
      commands.push(`M ${item.x} ${item.y} H ${item.x + item.w} V ${item.y + item.h} H ${item.x} Z`);
    } else if (item.type === "curve") {
      commands.push(`M ${item.x1} ${item.y1} C ${item.cx1} ${item.cy1} ${item.cx2} ${item.cy2} ${item.x2} ${item.y2}`);
    } else if (item.type === "quad") {
      const points = item.points;
      commands.push(`M ${points[0][0]} ${points[0][1]} L ${points[1][0]} ${points[1][1]} L ${points[2][0]} ${points[2][1]} L ${points[3][0]} ${points[3][1]} Z`);
    }
  }

  return commands.join(" ");
}

function fontStack(fontName) {
  if (!fontName) {
    return "Arial, Helvetica, sans-serif";
  }
  const family = fontName.replace(/^[A-Z]{6}\+/, "").replace(/[-_](Bold|Italic|Regular|Roman).*$/i, "");
  return `"${family}", Arial, Helvetica, sans-serif`;
}

function translatedText(id) {
  const item = translationData.items[id];
  return item?.[currentLanguage] || "";
}

function sourceText(id) {
  const item = translationData.items[id];
  return item?.[translationData.defaultLanguage] || item?.en || "";
}

function applyTranslatedText(el) {
  const value = translatedText(el.dataset.id);
  el.classList.toggle("translation-missing", !value);
  el.textContent = value || sourceText(el.dataset.id) || " ";
  el.title = value || `No ${currentLanguage} translation yet`;
}

function applyLanguage() {
  for (const el of document.querySelectorAll(".text-item")) {
    applyTranslatedText(el);
    applyTextScale(el);
  }
  updateLanguageStatus();
}

function updateLanguageStatus(isTranslating = false) {
  if (!translationData?.items) {
    return;
  }
  const items = Object.values(translationData.items);
  const missing = items.filter((item) => !item?.[currentLanguage]).length;
  const completed = items.length - missing;
  readerMessage.classList.toggle("warning", isTranslating || missing > 0);
  if (isTranslating) {
    readerMessage.textContent = `Translation is running. Showing ${completed} translated strings; ${missing} pending.`;
  } else if (missing === items.length && currentLanguage !== translationData.defaultLanguage) {
    readerMessage.textContent = `No ${currentLanguage} translation is available yet. Start translation first.`;
  } else if (missing > 0) {
    readerMessage.textContent = `${completed} translated strings are visible. ${missing} strings are still pending.`;
  } else {
    readerMessage.classList.remove("warning");
    readerMessage.textContent = "";
  }
}

function applyTextScale(el) {
  el.style.transform = "";

  if (currentLanguage === translationData.defaultLanguage) {
    return;
  }

  const width = Number(el.dataset.boxWidth || 0);
  if (!width || el.scrollWidth <= width) {
    return;
  }

  const scale = Math.max(0.72, width / el.scrollWidth);
  el.style.transform = `scaleX(${scale})`;
}

function applyZoom() {
  reader.style.setProperty("--zoom", zoom.toFixed(2));
}

function fitPageToViewport() {
  const firstPage = documentData?.pages?.[0];
  if (!firstPage?.width) {
    return;
  }
  const availableWidth = Math.max(260, window.innerWidth - 20);
  zoom = Math.min(zoom, Math.max(0.25, Math.min(1, availableWidth / firstPage.width)));
}

function currentJobId() {
  const match = window.location.pathname.match(/\/reader\/([^/]+)/)
    || window.location.pathname.match(/\/outputs\/([^/]+)\/reader\//);
  return match ? match[1] : "";
}

function dataBaseUrl() {
  return jobId ? `/outputs/${jobId}/data` : "../data";
}

function resolveImageSrc(src) {
  if (!jobId || /^(https?:|data:|\/)/.test(src)) {
    return src;
  }
  if (src.startsWith("../images/")) {
    return `/outputs/${jobId}/images/${src.replace("../images/", "")}`;
  }
  return src;
}

function openTranslationEditor(textId) {
  selectedTextId = textId;
  for (const el of document.querySelectorAll(".text-item.selected")) {
    el.classList.remove("selected");
  }
  const selected = Array.from(document.querySelectorAll(".text-item"))
    .find((el) => el.dataset.id === textId);
  if (selected) {
    selected.classList.add("selected");
  }
  const item = translationData.items[textId] || {};
  editorSource.textContent = item[sourceLanguageSelect.value] || item.en || "";
  editorValue.value = item[currentLanguage] || "";
  editorMessage.textContent = jobId
    ? `Editing ${currentLanguage}. Change text, then Save.`
    : "Open this reader from the dashboard to save translations.";
  translationEditor.hidden = false;
}

async function postEditorForm(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Request failed.");
  }
  return response.json();
}

retranslateText.addEventListener("click", async () => {
  if (!selectedTextId || !jobId) {
    editorMessage.textContent = "Select text from a dashboard reader first.";
    return;
  }

  const formData = new FormData();
  formData.append("text_id", selectedTextId);
  formData.append("source_language", sourceLanguageSelect.value);
  formData.append("target_language", currentLanguage);
  formData.append("provider", providerSelect.value);

  try {
    editorMessage.textContent = `Retranslating with ${providerLabel()}...`;
    const data = await postEditorForm(`/api/reader/${jobId}/retranslate`, formData);
    editorValue.value = data.translation;
    editorMessage.textContent = "Review the new translation, then save if you like it.";
  } catch (error) {
    editorMessage.textContent = error.message;
  }
});

function providerLabel() {
  return providerSelect.options[providerSelect.selectedIndex]?.textContent || "selected provider";
}

saveTextTranslation.addEventListener("click", async () => {
  if (!selectedTextId || !jobId) {
    editorMessage.textContent = "Select text from a dashboard reader first.";
    return;
  }

  const formData = new FormData();
  formData.append("text_id", selectedTextId);
  formData.append("target_language", currentLanguage);
  formData.append("value", editorValue.value);

  try {
    editorMessage.textContent = "Saving...";
    await postEditorForm(`/api/reader/${jobId}/save-translation`, formData);
    translationData.items[selectedTextId][currentLanguage] = editorValue.value;
    applyLanguage();
    editorMessage.textContent = "Saved permanently.";
  } catch (error) {
    editorMessage.textContent = error.message;
  }
});

closeEditor.addEventListener("click", () => {
  translationEditor.hidden = true;
});

translateDocumentButton.addEventListener("click", async () => {
  if (!jobId) {
    readerMessage.textContent = "Open a .phjz package before translating.";
    return;
  }

  const formData = new FormData();
  formData.append("source_language", sourceLanguageSelect.value);
  formData.append("target_language", targetLanguageSelect.value);
  formData.append("provider", providerSelect.value);

  try {
    translateDocumentButton.disabled = true;
    abortTranslationButton.disabled = false;
    currentLanguage = targetLanguageSelect.value;
    if (!Array.from(languageSelect.options).some((option) => option.value === currentLanguage)) {
      languageSelect.appendChild(languageOption({ code: currentLanguage, label: currentLanguage }));
    }
    languageSelect.value = currentLanguage;
    applyLanguage();
    setTranslationProgress(0, `Translating ${sourceLanguageSelect.value} to ${targetLanguageSelect.value} with ${providerLabel()}...`);
    updateLanguageStatus(true);
    showReaderMessage("warning", "Translation started. Partial results will appear as strings complete.");
    const response = await fetch(`/api/jobs/${jobId}/translate`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Translation failed.");
    }
    pollTranslationJob();
  } catch (error) {
    translateDocumentButton.disabled = false;
    abortTranslationButton.disabled = true;
    showReaderMessage("error", error.message);
    readerMessage.textContent = error.message;
  }
});

abortTranslationButton.addEventListener("click", async () => {
  if (!jobId) {
    return;
  }
  try {
    abortTranslationButton.disabled = true;
    readerMessage.classList.add("warning");
    readerMessage.textContent = "Abort requested. Current string may finish before the process stops.";
    showReaderMessage("warning", "Abort requested. Waiting for the current string to finish.");
    const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Abort failed.");
    }
  } catch (error) {
    showReaderMessage("error", error.message);
    readerMessage.textContent = error.message;
  }
});

readerPackageFile.addEventListener("change", async () => {
  const file = readerPackageFile.files[0];
  if (!file) {
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    readerMessage.textContent = "Opening .phjz package...";
    const response = await fetch("/api/package/open", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Package open failed.");
    }
    const data = await response.json();
    window.location.href = `/reader/${data.jobId}`;
  } catch (error) {
    readerMessage.textContent = error.message;
  }
});

languageSelect.addEventListener("change", () => {
  currentLanguage = languageSelect.value;
  applyLanguage();
  if (translationData.items[selectedTextId]) {
    openTranslationEditor(selectedTextId);
  }
});

sourceLanguageSelect.addEventListener("change", () => {
  if (translationData.items[selectedTextId]) {
    openTranslationEditor(selectedTextId);
  }
});

targetLanguageSelect.addEventListener("change", () => {
  currentLanguage = targetLanguageSelect.value;
  if (!Array.from(languageSelect.options).some((option) => option.value === currentLanguage)) {
    languageSelect.appendChild(languageOption({ code: currentLanguage, label: currentLanguage }));
  }
  languageSelect.value = currentLanguage;
  applyLanguage();
  if (translationData.items[selectedTextId]) {
    openTranslationEditor(selectedTextId);
  }
});

themeSelect.addEventListener("change", () => {
  document.documentElement.dataset.theme = themeSelect.value;
});

pageColorSelect.addEventListener("change", () => {
  document.documentElement.style.setProperty("--page-bg", pageColorSelect.value);
});

pageOpacityRange.addEventListener("input", () => {
  document.documentElement.style.setProperty("--page-opacity", pageOpacityRange.value);
});

missingImageToggle.addEventListener("change", () => {
  document.documentElement.dataset.showMissingImages = missingImageToggle.checked ? "true" : "false";
});

printButton.addEventListener("click", () => {
  window.print();
});

zoomIn.addEventListener("click", () => {
  zoom = Math.min(2, zoom + 0.1);
  applyZoom();
});

zoomOut.addEventListener("click", () => {
  zoom = Math.max(0.25, zoom - 0.1);
  applyZoom();
});

function setTranslationProgress(percent, text) {
  translationProgress.hidden = false;
  translationProgress.querySelector(".progress span").style.width = `${percent}%`;
  translationProgressLabel.textContent = text;
}

async function reloadTranslationLog() {
  if (!jobId) {
    return;
  }
  const response = await fetch(`${dataBaseUrl()}/translation-log.json?ts=${Date.now()}`);
  if (!response.ok) {
    return;
  }
  const data = await response.json();
  renderTranslationLog(data.entries || []);
}

function renderTranslationLog(entries) {
  translationLogPanel.hidden = entries.length === 0;
  translationLogList.innerHTML = "";
  for (const entry of entries.slice(-80).reverse()) {
    const item = document.createElement("div");
    item.className = `translation-log-item ${entry.status || ""}`;

    const meta = document.createElement("div");
    meta.className = "translation-log-meta";
    meta.textContent = `${entry.time || ""} | ${entry.status || ""} | page ${entry.page || "-"} | paragraph ${entry.paragraph || "-"} | line ${entry.line || "-"}`;

    const text = document.createElement("div");
    text.className = "translation-log-text";
    text.textContent = entry.text || "";

    item.append(meta, text);
    if (entry.raw) {
      const raw = document.createElement("div");
      raw.className = "translation-log-meta";
      raw.textContent = `raw: ${entry.raw}`;
      item.appendChild(raw);
    }
    if (entry.translated) {
      const translated = document.createElement("div");
      translated.className = "translation-log-text";
      translated.textContent = `final: ${entry.translated}`;
      item.appendChild(translated);
    }
    if (entry.error) {
      const error = document.createElement("div");
      error.className = "translation-log-meta";
      error.textContent = entry.error;
      item.appendChild(error);
    }
    translationLogList.appendChild(item);
  }
}

clearTranslationLogView.addEventListener("click", () => {
  translationLogList.innerHTML = "";
  translationLogPanel.hidden = true;
});

function initFloatingNav() {
  let dragging = false;
  let moved = false;
  let offsetX = 0;
  let offsetY = 0;
  let startX = 0;
  let startY = 0;

  navToggle.addEventListener("pointerdown", (event) => {
    dragging = true;
    moved = false;
    startX = event.clientX;
    startY = event.clientY;
    const rect = navToggle.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;
    navToggle.setPointerCapture(event.pointerId);
  });

  navToggle.addEventListener("pointermove", (event) => {
    if (!dragging) {
      return;
    }
    moved = moved || Math.abs(event.clientX - startX) > 4 || Math.abs(event.clientY - startY) > 4;
    const x = Math.min(window.innerWidth - navToggle.offsetWidth - 8, Math.max(8, event.clientX - offsetX));
    const y = Math.min(window.innerHeight - navToggle.offsetHeight - 8, Math.max(8, event.clientY - offsetY));
    navToggle.style.left = `${x}px`;
    navToggle.style.top = `${y}px`;
    navToggle.style.right = "auto";
    navToggle.style.bottom = "auto";
  });

  navToggle.addEventListener("pointerup", (event) => {
    dragging = false;
    navToggle.releasePointerCapture(event.pointerId);
    if (!moved) {
      toggleReaderNav();
    } else {
      snapNavToggle();
    }
  });

  navToggle.addEventListener("pointercancel", () => {
    dragging = false;
    snapNavToggle();
  });

  window.addEventListener("resize", () => {
    fitPageToViewport();
    applyZoom();
    snapNavToggle();
  });
}

function toggleReaderNav() {
  const hidden = readerNav.classList.toggle("hidden");
  navToggle.textContent = hidden ? "Show" : "Hide";
}

function snapNavToggle() {
  const margin = 12;
  const rect = navToggle.getBoundingClientRect();
  const maxY = Math.max(margin, window.innerHeight - navToggle.offsetHeight - margin);
  const y = Math.min(maxY, Math.max(margin, rect.top));
  const leftEdge = margin;
  const rightEdge = Math.max(margin, window.innerWidth - navToggle.offsetWidth - margin);
  const x = rect.left + rect.width / 2 < window.innerWidth / 2 ? leftEdge : rightEdge;

  navToggle.style.left = `${x}px`;
  navToggle.style.top = `${y}px`;
  navToggle.style.right = "auto";
  navToggle.style.bottom = "auto";
}

async function pollTranslationJob() {
  clearInterval(activeTranslationPoll);
  translationPollFailures = 0;
  activeTranslationPoll = setInterval(async () => {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) {
        throw new Error("Translation job not found.");
      }
      translationPollFailures = 0;
      const job = await response.json();
      const itemText = job.totalItems ? `Text ${job.currentItem || 0} of ${job.totalItems}` : "Checking translations";
      setTranslationProgress(job.percent || 0, `${itemText} | ${job.percent || 0}% | ${job.message}`);
      if (Date.now() - lastTranslationReload > 1200) {
        lastTranslationReload = Date.now();
        await reloadTranslations({ preserveSelection: true });
        await reloadTranslationLog();
        currentLanguage = targetLanguageSelect.value;
        languageSelect.value = currentLanguage;
        applyLanguage();
        updateLanguageStatus(job.status === "processing");
      }
      if (job.status === "done" || job.status === "failed" || job.status === "canceled") {
        clearInterval(activeTranslationPoll);
        activeTranslationPoll = null;
        translateDocumentButton.disabled = false;
        abortTranslationButton.disabled = true;
        if (job.status === "done" || job.status === "canceled") {
          const translatedLanguage = targetLanguageSelect.value;
          await reloadTranslations({ preserveSelection: true });
          await reloadTranslationLog();
          currentLanguage = translatedLanguage;
          languageSelect.value = currentLanguage;
          targetLanguageSelect.value = currentLanguage;
          applyLanguage();
          if (job.status === "done") {
            showReaderMessage("success", "Translation complete. All available strings were saved.");
            readerMessage.textContent = "Translation complete. Click any text to edit it.";
          } else {
            updateLanguageStatus(false);
            readerMessage.classList.add("warning");
            readerMessage.textContent = "Translation aborted. Completed strings are visible and saved.";
            showReaderMessage("info", "Translation aborted. Completed strings are visible and saved.");
          }
        } else {
          showReaderMessage("error", job.error || job.message);
          readerMessage.textContent = job.error || job.message;
        }
      }
    } catch (error) {
      translationPollFailures += 1;
      readerMessage.classList.add("warning");
      readerMessage.textContent = `Translation is still running, but status refresh failed (${translationPollFailures}/10): ${error.message}`;
      if (translationPollFailures >= 10) {
        clearInterval(activeTranslationPoll);
        activeTranslationPoll = null;
        translateDocumentButton.disabled = false;
        abortTranslationButton.disabled = true;
        showReaderMessage("error", error.message);
      }
    }
  }, 1200);
}

async function reloadTranslations(options = {}) {
  const previousSource = sourceLanguageSelect.value;
  const previousTarget = targetLanguageSelect.value;
  const previousDisplay = languageSelect.value;
  const response = await fetch(`${dataBaseUrl()}/translations.json?ts=${Date.now()}`);
  translationData = await response.json();
  buildLanguageOptions();
  buildTranslateOptions();
  if (options.preserveSelection) {
    sourceLanguageSelect.value = previousSource || sourceLanguageSelect.value;
    targetLanguageSelect.value = previousTarget || targetLanguageSelect.value;
    if (Array.from(languageSelect.options).some((option) => option.value === previousDisplay)) {
      languageSelect.value = previousDisplay;
    }
  }
}

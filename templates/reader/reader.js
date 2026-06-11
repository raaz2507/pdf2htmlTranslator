const reader = document.querySelector("#reader");
const languageSelect = document.querySelector("#languageSelect");
const themeSelect = document.querySelector("#themeSelect");
const zoomIn = document.querySelector("#zoomIn");
const zoomOut = document.querySelector("#zoomOut");
const documentTitle = document.querySelector("#documentTitle");
const pageCount = document.querySelector("#pageCount");

let documentData = null;
let translationData = null;
let currentLanguage = "en";
let zoom = 1;
let observer = null;
const renderedPages = new Map();

init();

async function init() {
  const [documentJson, translationsJson] = await Promise.all([
    fetch("../data/document.json").then((response) => response.json()),
    fetch("../data/translations.json").then((response) => response.json()),
  ]);

  documentData = documentJson;
  translationData = translationsJson;
  currentLanguage = translationData.defaultLanguage || "en";

  documentTitle.textContent = documentData.source || "PDF Reader";
  pageCount.textContent = `${documentData.pages.length} page${documentData.pages.length === 1 ? "" : "s"}`;
  buildLanguageOptions();
  renderPlaceholders();
  applyZoom();
}

function buildLanguageOptions() {
  languageSelect.innerHTML = "";

  for (const language of translationData.availableLanguages) {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.label;
    languageSelect.appendChild(option);
  }

  languageSelect.value = currentLanguage;
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
  image.src = imageItem.src;
  image.alt = "";
  image.style.left = `${imageItem.x}px`;
  image.style.top = `${imageItem.y}px`;
  image.style.width = `${Math.max(imageItem.w, 1)}px`;
  image.style.height = `${Math.max(imageItem.h, 1)}px`;
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
  textEl.textContent = translatedText(item.id);
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
  return item?.[currentLanguage] || item?.en || "";
}

function applyLanguage() {
  for (const el of document.querySelectorAll(".text-item")) {
    el.textContent = translatedText(el.dataset.id);
    applyTextScale(el);
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

languageSelect.addEventListener("change", () => {
  currentLanguage = languageSelect.value;
  applyLanguage();
});

themeSelect.addEventListener("change", () => {
  document.documentElement.dataset.theme = themeSelect.value;
});

zoomIn.addEventListener("click", () => {
  zoom = Math.min(2, zoom + 0.1);
  applyZoom();
});

zoomOut.addEventListener("click", () => {
  zoom = Math.max(0.4, zoom - 0.1);
  applyZoom();
});

const pdfForm = document.querySelector("#pdfForm");
const htmlForm = document.querySelector("#htmlForm");
const pdfFile = document.querySelector("#pdfFile");
const htmlFile = document.querySelector("#htmlFile");
const pdfFileLabel = document.querySelector("#pdfFileLabel");
const htmlFileLabel = document.querySelector("#htmlFileLabel");
const statusBox = document.querySelector("#status");
const jobsBox = document.querySelector("#jobs");
const refreshJobs = document.querySelector("#refreshJobs");
const sourceLanguage = document.querySelector("#sourceLanguage");
const targetLanguage = document.querySelector("#targetLanguage");
const translateButton = document.querySelector("#translateButton");

let activePoll = null;
let selectedJobId = null;

pdfFile.addEventListener("change", () => {
  pdfFileLabel.textContent = pdfFile.files[0]?.name || "Choose a PDF file";
});

htmlFile.addEventListener("change", () => {
  htmlFileLabel.textContent = htmlFile.files[0]?.name || "Choose an HTML file";
});

pdfForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!pdfFile.files[0]) {
    statusBox.textContent = "Select a PDF first.";
    return;
  }

  const submitter = event.submitter;
  const kind = submitter?.dataset.kind || "reader";
  const formData = new FormData();
  formData.append("file", pdfFile.files[0]);
  formData.append("kind", kind);

  try {
    statusBox.textContent = kind === "pdf_html" ? "Converting PDF to HTML..." : "Processing reader package...";
    const data = await postForm("/api/pdf/jobs", formData);
    selectedJobId = data.jobId;
    pollJob(data.jobId);
    loadJobs();
  } catch (error) {
    statusBox.textContent = error.message;
  }
});

htmlForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!htmlFile.files[0]) {
    statusBox.textContent = "Select an HTML file first.";
    return;
  }

  const formData = new FormData();
  formData.append("file", htmlFile.files[0]);

  try {
    statusBox.textContent = "Extracting HTML structure...";
    const data = await postForm("/api/html/jobs", formData);
    selectedJobId = data.jobId;
    pollJob(data.jobId);
    loadJobs();
  } catch (error) {
    statusBox.textContent = error.message;
  }
});

translateButton.addEventListener("click", async () => {
  if (!selectedJobId) {
    statusBox.textContent = "Select a completed reader job first.";
    return;
  }

  const formData = new FormData();
  formData.append("source_language", sourceLanguage.value);
  formData.append("target_language", targetLanguage.value);

  try {
    statusBox.textContent = "Translating selected reader...";
    const data = await postForm(`/api/jobs/${selectedJobId}/translate`, formData);
    pollJob(data.jobId);
  } catch (error) {
    statusBox.textContent = error.message;
  }
});

refreshJobs.addEventListener("click", loadJobs);

async function postForm(url, formData) {
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

async function pollJob(jobId) {
  clearInterval(activePoll);
  activePoll = setInterval(async () => {
    try {
      const job = await fetchJson(`/api/jobs/${jobId}`);
      statusBox.textContent = `${job.filename}: ${job.message}`;
      loadJobs();

      if (job.status === "done" || job.status === "failed") {
        clearInterval(activePoll);
        activePoll = null;
      }
    } catch (error) {
      clearInterval(activePoll);
      activePoll = null;
      statusBox.textContent = "Job not found. Server may have restarted; upload again or refresh jobs.";
    }
  }, 1200);
}

async function loadJobs() {
  const jobs = await fetchJson("/api/jobs");
  jobsBox.innerHTML = "";

  if (!jobs.length) {
    jobsBox.innerHTML = '<div class="job-meta">No jobs yet.</div>';
    return;
  }

  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = `job ${job.id === selectedJobId ? "selected" : ""}`;
    row.addEventListener("click", () => {
      selectedJobId = job.id;
      loadJobs();
    });

    const info = document.createElement("div");
    info.innerHTML = `
      <div class="job-title"></div>
      <div class="job-meta"></div>
    `;
    info.querySelector(".job-title").textContent = job.filename;
    info.querySelector(".job-meta").textContent = `${job.kind} | ${job.status} | ${job.message}`;

    if (job.error) {
      const error = document.createElement("div");
      error.className = "job-meta error";
      error.textContent = job.error;
      info.appendChild(error);
    }

    const links = document.createElement("div");
    links.className = "job-links";

    if (job.readerUrl) {
      const readerLink = document.createElement("a");
      readerLink.href = job.readerUrl;
      readerLink.target = "_blank";
      readerLink.rel = "noreferrer";
      readerLink.textContent = "Open Reader";
      links.appendChild(readerLink);
    }

    if (job.downloadUrl) {
      const link = document.createElement("a");
      link.href = job.downloadUrl;
      link.textContent = "Download ZIP";
      links.appendChild(link);
    }

    row.appendChild(info);
    row.appendChild(links);
    jobsBox.appendChild(row);
  }
}

async function loadLanguages() {
  const languages = await fetchJson("/api/languages");
  for (const select of [sourceLanguage, targetLanguage]) {
    select.innerHTML = "";
    for (const language of languages) {
      const option = document.createElement("option");
      option.value = language.code;
      option.textContent = language.label;
      select.appendChild(option);
    }
  }
  sourceLanguage.value = "en";
  targetLanguage.value = "hi";
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${url}`);
  }
  return response.json();
}

loadLanguages();
loadJobs();

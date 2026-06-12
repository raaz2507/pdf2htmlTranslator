document.addEventListener("DOMContentLoaded", () => {
	new App();
});

class App {
	constructor() {
		this.#getElement();
		this.#init();

		this.selectedJobId = null;

		this.poller = new JobPoller(
			this.jobService,
			(job, panel) => this.handleJobUpdate(job, panel),
			(job) => this.handleJobDone(job),
			() => this.handleJobError(),
		);
	}
	#getElement() {
		this.api = new ApiClient();
		this.jobService = new JobService(this.api);

		this.pdfForm = document.querySelector("#pdfForm");
		this.pdfPackageForm = document.querySelector("#pdfPackageForm");
		this.htmlForm = document.querySelector("#htmlForm");

		this.pdfFile = document.querySelector("#pdfFile");
		this.pdfPackageFile = document.querySelector("#pdfPackageFile");
		this.htmlFile = document.querySelector("#htmlFile");
		this.translationFile = document.querySelector("#translationFile");

		this.pdfFileLabel = document.querySelector("#pdfFileLabel");
		this.pdfPackageFileLabel = document.querySelector("#pdfPackageFileLabel");
		this.htmlFileLabel = document.querySelector("#htmlFileLabel");
		this.translationFileLabel = document.querySelector("#translationFileLabel");

		this.statusBox = document.querySelector("#status");
		this.jobsBox = document.querySelector("#jobs");
		this.refreshJobs = document.querySelector("#refreshJobs");

		this.sourceLanguage = document.querySelector("#sourceLanguage");
		this.targetLanguage = document.querySelector("#targetLanguage");
		this.translationProvider = document.querySelector("#translationProvider");
		this.providerPreset = document.querySelector("#providerPreset");
		this.openPackageButton = document.querySelector("#openPackageButton");
		this.estimateButton = document.querySelector("#estimateButton");
		this.translateButton = document.querySelector("#translateButton");
		this.translationEstimate = document.querySelector("#translationEstimate");

		this.pdfProgress = new ProgressView(document.querySelector("#pdfProgress"));
		this.pdfPackageProgress = new ProgressView(document.querySelector("#pdfPackageProgress"));
		this.translationProgress = new ProgressView(document.querySelector("#translationProgress"));
		this.messages = new MessageView(document.querySelector("#messages"));
	}
	#init() {
		this.bindEvents();
		this.loadLanguages();
		this.loadJobs();
	}

	bindEvents() {
		this.pdfFile.addEventListener("change", () => {
			this.pdfFileLabel.textContent = this.pdfFile.files[0]?.name || "Choose a PDF file";
		});

		this.pdfPackageFile.addEventListener("change", () => {
			this.pdfPackageFileLabel.textContent = this.pdfPackageFile.files[0]?.name || "Choose a PDF file";
		});

		this.htmlFile.addEventListener("change", () => {
			this.htmlFileLabel.textContent = this.htmlFile.files[0]?.name || "Choose an HTML file";
		});

		this.translationFile.addEventListener("change", () => {
			this.translationFileLabel.textContent = this.translationFile.files[0]?.name || "Choose a .phjz package";
		});

		this.pdfForm.addEventListener("submit", (event) => this.submitPdf(event));
		this.pdfPackageForm.addEventListener("submit", (event) => this.submitPdfPackage(event));
		this.htmlForm.addEventListener("submit", (event) => this.submitHtml(event));
		this.openPackageButton.addEventListener("click", () => this.openPackage());
		this.estimateButton.addEventListener("click", () => this.estimatePackage());
		this.translateButton.addEventListener("click", () => this.translatePackage());
		this.refreshJobs.addEventListener("click", () => this.loadJobs());
		this.providerPreset.addEventListener("change", () => this.applyProviderPreset());
	}

	applyProviderPreset() {
		const preset = {
			"fast-online": { provider: "gemini", target: "hi_modern" },
			"accurate-online": { provider: "google", target: "hi_modern" },
			"private-offline": { provider: "indictrans2", target: "hi_pure" },
			"modern-offline": { provider: "indictrans2", target: "hi_modern" },
		}[this.providerPreset.value];
		if (!preset) {
			return;
		}
		this.translationProvider.value = preset.provider;
		this.targetLanguage.value = preset.target;
	}

	async submitPdf(event) {
		event.preventDefault();

		if (!this.pdfFile.files[0]) {
			this.statusBox.textContent = "Select a PDF first.";
			return;
		}

		const formData = new FormData();
		formData.append("file", this.pdfFile.files[0]);
		formData.append("kind", "pdf_html");

		try {
			this.statusBox.textContent = "Converting PDF to HTML ZIP...";
			this.messages.show("info", "PDF processing started.");
			this.pdfProgress.update(0, "Preparing PDF.");

			const data = await this.jobService.createPdfJob(formData);

			this.selectedJobId = data.jobId;
			this.poller.start(data.jobId, "pdf");
			this.loadJobs();
		} catch (error) {
			this.statusBox.textContent = error.message;
			this.messages.show("error", error.message);
		}
	}

	async submitPdfPackage(event) {
		event.preventDefault();

		if (!this.pdfPackageFile.files[0]) {
			this.statusBox.textContent = "Select a PDF first.";
			return;
		}

		const formData = new FormData();
		formData.append("file", this.pdfPackageFile.files[0]);
		formData.append("kind", "reader");

		try {
			this.statusBox.textContent = "Converting PDF to .phjz...";
			this.messages.show("info", "PDF to .phjz processing started.");
			this.pdfPackageProgress.update(0, "Preparing PDF.");

			const data = await this.jobService.createPdfJob(formData);

			this.selectedJobId = data.jobId;
			this.poller.start(data.jobId, "pdfPackage");
			this.loadJobs();
		} catch (error) {
			this.statusBox.textContent = error.message;
			this.messages.show("error", error.message);
		}
	}

	async submitHtml(event) {
		event.preventDefault();

		if (!this.htmlFile.files[0]) {
			this.statusBox.textContent = "Select an HTML file first.";
			return;
		}

		const formData = new FormData();
		formData.append("file", this.htmlFile.files[0]);

		try {
			this.statusBox.textContent = "Converting HTML to .phjz...";

			const data = await this.jobService.createHtmlJob(formData);

			this.selectedJobId = data.jobId;
			this.poller.start(data.jobId);
			this.loadJobs();
		} catch (error) {
			this.statusBox.textContent = error.message;
		}
	}

	async translatePackage() {
		if (!this.translationFile.files[0]) {
			this.statusBox.textContent = "Select a .phjz package first.";
			this.messages.show("error", "Translation accepts only a .phjz package.");
			return;
		}

		const formData = new FormData();
		formData.append("file", this.translationFile.files[0]);
		formData.append("source_language", this.sourceLanguage.value);
		formData.append("target_language", this.targetLanguage.value);
		formData.append("provider", this.translationProvider.value);

		try {
			this.statusBox.textContent = "Translating package...";
			this.messages.show("info", `Translation started with ${this.translationProvider.options[this.translationProvider.selectedIndex].textContent}. Already translated strings will be skipped.`);
			this.translationProgress.update(0, "Reading .phjz package.");

			const data = await this.jobService.createTranslationJob(formData);

			this.selectedJobId = data.jobId;
			this.poller.start(data.jobId, "translation");
			this.loadJobs();
		} catch (error) {
			this.statusBox.textContent = error.message;
			this.messages.show("error", error.message);
		}
	}

	async estimatePackage() {
		if (!this.translationFile.files[0]) {
			this.statusBox.textContent = "Select a .phjz package first.";
			this.messages.show("error", "Estimate accepts only a .phjz package.");
			return;
		}

		const formData = new FormData();
		formData.append("file", this.translationFile.files[0]);
		formData.append("source_language", this.sourceLanguage.value);
		formData.append("target_language", this.targetLanguage.value);
		formData.append("provider", this.translationProvider.value);

		try {
			this.translationEstimate.hidden = false;
			this.translationEstimate.textContent = "Estimating...";
			const data = await this.jobService.estimateTranslation(formData);
			this.renderEstimate(data);
		} catch (error) {
			this.messages.show("error", error.message);
			this.translationEstimate.hidden = true;
		}
	}

	renderEstimate(data) {
		const minutes = Math.ceil((data.estimatedSeconds || 0) / 60);
		const quota = data.geminiQuota
			? `<div>Gemini quota: <strong>${data.geminiQuota.usedToday}/${data.geminiQuota.dailyLimit}</strong> used, <strong>${data.geminiQuota.remainingToday}</strong> left today</div>`
			: "";
		this.translationEstimate.innerHTML = `
			<div><strong>${data.provider}</strong> estimate</div>
			<div>Pending: <strong>${data.pendingItems}</strong>, skipped junk: <strong>${data.skippedItems}</strong>, duplicates: <strong>${data.duplicateItems}</strong></div>
			<div>Words: <strong>${data.words}</strong>, tokens: <strong>${data.estimatedTotalTokens}</strong></div>
			<div>Requests: <strong>${data.estimatedRequests}</strong>, batch size: <strong>${data.batchSize}</strong>, time: <strong>${minutes} min</strong></div>
			${quota}
		`;
	}

	async openPackage() {
		if (!this.translationFile.files[0]) {
			this.statusBox.textContent = "Select a .phjz package first.";
			this.messages.show("error", "Open Package accepts only a .phjz package.");
			return;
		}

		const formData = new FormData();
		formData.append("file", this.translationFile.files[0]);

		try {
			this.statusBox.textContent = "Opening package...";
			this.messages.show("info", "Opening .phjz package in universal reader.");
			this.translationProgress.update(0, "Opening package.");

			const data = await this.jobService.openPackageJob(formData);

			this.selectedJobId = data.jobId;
			this.poller.start(data.jobId, "translation");
			this.loadJobs();
		} catch (error) {
			this.statusBox.textContent = error.message;
			this.messages.show("error", error.message);
		}
	}

	handleJobUpdate(job, panel) {
		this.statusBox.textContent = `${job.filename}: ${this.progressText(job)}`;

		if (panel === "pdf") {
			const text = job.totalPages ? `Page ${job.currentPage || 0} of ${job.totalPages}` : "Preparing pages";

			this.pdfProgress.update(job.percent || 0, `${text} | ${job.percent || 0}%`);
		}

		if (panel === "pdfPackage") {
			const text = job.totalPages ? `Page ${job.currentPage || 0} of ${job.totalPages}` : "Preparing pages";

			this.pdfPackageProgress.update(job.percent || 0, `${text} | ${job.percent || 0}%`);
		}

		if (panel === "translation") {
			const text = job.totalItems ? `Text ${job.currentItem || 0} of ${job.totalItems}` : "Checking translations";

			this.translationProgress.update(job.percent || 0, `${text} | ${job.percent || 0}%`);
		}

		this.loadJobs();
	}

	handleJobDone(job) {
		const kind = job.status === "done" ? "success" : job.status === "canceled" ? "info" : "error";
		this.messages.show(kind, `${job.filename}: ${job.message}`);
	}

	handleJobError() {
		this.statusBox.textContent = "Job not found. Server may have restarted; upload again or refresh jobs.";
	}

	async loadLanguages() {
		const languages = await this.jobService.getLanguages();

		for (const select of [this.sourceLanguage, this.targetLanguage]) {
			select.innerHTML = "";

			for (const language of languages) {
				const option = document.createElement("option");
				option.value = language.code;
				option.textContent = language.label;
				select.appendChild(option);
			}
		}

		this.sourceLanguage.value = "en";
		this.targetLanguage.value = "hi_modern";
	}

	async loadJobs() {
		const jobs = await this.jobService.getJobs();

		this.jobsBox.innerHTML = "";

		if (!jobs.length) {
			this.jobsBox.innerHTML = '<div class="job-meta">No jobs yet.</div>';
			return;
		}

		for (const job of jobs) {
			this.jobsBox.appendChild(this.createJobRow(job));
		}
	}

	createJobRow(job) {
		const row = document.createElement("div");
		row.className = `job ${job.id === this.selectedJobId ? "selected" : ""}`;

		row.addEventListener("click", () => {
			this.selectedJobId = job.id;
			this.loadJobs();
		});

		const info = document.createElement("div");

		const title = document.createElement("div");
		title.className = "job-title";
		title.textContent = job.filename;

		const meta = document.createElement("div");
		meta.className = "job-meta";
		meta.textContent = this.progressText(job);

		const progress = document.createElement("div");
		progress.className = "progress";
		progress.setAttribute("aria-label", `Progress ${job.percent || 0}%`);
		progress.innerHTML = `<span></span>`;
		progress.querySelector("span").style.width = `${job.percent || 0}%`;

		info.append(title, meta, progress);

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
			const downloadLink = document.createElement("a");
			downloadLink.href = job.downloadUrl;
			downloadLink.textContent = job.kind === "pdf_html" ? "Download ZIP" : "Download Package";
			links.appendChild(downloadLink);
		}

		if (job.status === "processing" && this.isTranslationJob(job)) {
			const abortButton = document.createElement("button");
			abortButton.type = "button";
			abortButton.className = "secondary";
			abortButton.textContent = "Abort";
			abortButton.addEventListener("click", async (event) => {
				event.stopPropagation();
				try {
					await this.jobService.cancelJob(job.id);
					this.messages.show("info", "Abort requested. Completed work will stay saved.");
					this.loadJobs();
				} catch (error) {
					this.messages.show("error", error.message);
				}
			});
			links.appendChild(abortButton);
		}

		row.append(info, links);
		return row;
	}

	progressText(job) {
		const percent = Number.isFinite(job.percent) ? `${job.percent}%` : "0%";

		if (job.kind === "translation") {
			const itemText = job.totalItems ? `Text ${job.currentItem || 0} of ${job.totalItems}` : "Checking translations";

			return `${job.status} | ${itemText} | ${percent} | ${job.message}`;
		}

		const pageText = job.totalPages ? `Page ${job.currentPage || 0} of ${job.totalPages}` : "Preparing pages";

		return `${job.status} | ${pageText} | ${percent} | ${job.message}`;
	}

	isTranslationJob(job) {
		return job.kind === "translation" || (job.message || "").toLowerCase().includes("translat");
	}
}
class JobPoller {
  constructor(jobService, onUpdate, onDone, onError) {
    this.jobService = jobService;
    this.onUpdate = onUpdate;
    this.onDone = onDone;
    this.onError = onError;
    this.activePoll = null;
  }

  start(jobId, panel = null) {
    this.stop();

    this.activePoll = setInterval(async () => {
      try {
        const job = await this.jobService.getJob(jobId);

        this.onUpdate(job, panel);

        if (job.status === "done" || job.status === "failed" || job.status === "canceled") {
          this.stop();
          this.onDone(job);
        }
      } catch (error) {
        this.stop();
        this.onError(error);
      }
    }, 1200);
  }

  stop() {
    if (this.activePoll) {
      clearInterval(this.activePoll);
      this.activePoll = null;
    }
  }
}

class JobService {
  constructor(apiClient) {
    this.api = apiClient;
  }

  createPdfJob(formData) {
    return this.api.postForm("/api/pdf/jobs", formData);
  }

  createHtmlJob(formData) {
    return this.api.postForm("/api/html/jobs", formData);
  }

  createTranslationJob(formData) {
    return this.api.postForm("/api/package/translate", formData);
  }

  estimateTranslation(formData) {
    return this.api.postForm("/api/package/estimate", formData);
  }

  openPackageJob(formData) {
    return this.api.postForm("/api/package/open", formData);
  }

  cancelJob(jobId) {
    return this.api.postForm(`/api/jobs/${jobId}/cancel`, new FormData());
  }

  getJob(jobId) {
    return this.api.getJson(`/api/jobs/${jobId}`);
  }

  getJobs() {
    return this.api.getJson("/api/jobs");
  }

  getLanguages() {
    return this.api.getJson("/api/languages");
  }
}

class MessageView {
  constructor(messagesBox) {
    this.messagesBox = messagesBox;
  }

  show(kind, text) {
    const message = document.createElement("div");
    message.className = `message ${kind}`;
    message.textContent = text;
    this.messagesBox.prepend(message);
  }
}

class ProgressView {
  constructor(container) {
    this.container = container;
  }

  update(percent = 0, text = "") {
    this.container.querySelector(".progress span").style.width = `${percent}%`;
    this.container.querySelector(".progress-label").textContent = text;
  }
}

class ApiClient {
  async postForm(url, formData) {
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

  async getJson(url) {
    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`Request failed: ${url}`);
    }

    return response.json();
  }
}

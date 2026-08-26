/* IssueBell dashboard interactions. All enhanced controls fall back to manual input. */

const STARTER_PACKS = Object.freeze({
  web: {
    repo: "freeCodeCamp/freeCodeCamp",
    labels: ["first timers only", "help wanted"],
  },
  data: {
    repo: "scikit-learn/scikit-learn",
    labels: ["help wanted", "Documentation"],
  },
  docs: {
    repo: "mdn/content",
    labels: ["good first issue", "help wanted"],
  },
  devtools: {
    repo: "microsoft/vscode",
    labels: ["good first issue", "help wanted"],
  },
});

const STARTER_STORAGE_KEY = "issuebell.starterPack";
const addForm = document.getElementById("add-form");
const formError = document.getElementById("form-error");
const formSuccess = document.getElementById("form-success");
const repoInput = document.getElementById("repo");
const repoFeedback = document.getElementById("repo-feedback");
const lookupRepoBtn = document.getElementById("lookup-repo-btn");
const repoLabelSuggestions = document.getElementById("repo-label-suggestions");
const repoLabelList = document.getElementById("repo-label-list");
const labelInput = document.getElementById("label");
const tagInputEl = document.getElementById("label-tag-input");
const previewAlertsBtn = document.getElementById("preview-alerts-btn");
const alertPreview = document.getElementById("alert-preview");
const alertPreviewTitle = document.getElementById("alert-preview-title");
const alertPreviewCount = document.getElementById("alert-preview-count");
const alertPreviewSummary = document.getElementById("alert-preview-summary");
const alertPreviewWarnings = document.getElementById("alert-preview-warnings");
const alertPreviewIssues = document.getElementById("alert-preview-issues");
const testDmBtn = document.getElementById("test-dm-btn");
const testDmResult = document.getElementById("test-dm-result");
const dashboardIntro = document.querySelector(".dashboard-intro");
const githubConnected = dashboardIntro?.dataset.githubConnected === "true";
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const confirmDialog = document.getElementById("confirm-dialog");
const confirmTitle = document.getElementById("confirm-dialog-title");
const confirmMessage = document.getElementById("confirm-dialog-message");
const confirmAction = document.getElementById("confirm-dialog-action");
const confirmCancel = document.getElementById("confirm-dialog-cancel");
const appToast = document.getElementById("app-toast");
const appToastMessage = document.getElementById("app-toast-message");
const appToastClose = document.getElementById("app-toast-close");

let pendingLabels = [];
let activeLabelRequest = null;
let activePreviewRequest = null;
let previewSignature = null;
let confirmationResolver = null;
let toastTimer = null;

function parseRepo(raw) {
  const value = String(raw || "").trim().replace(/\/+$/, "");
  const web = value.match(/^https?:\/\/(?:www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[\/#?].*)?$/i);
  if (web) return `${web[1]}/${web[2]}`;
  const ssh = value.match(/^git@github\.com:([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/i);
  if (ssh) return `${ssh[1]}/${ssh[2]}`;
  const short = value.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  return short ? `${short[1]}/${short[2]}` : null;
}

function csrfHeaders(extra = {}) {
  return csrfToken ? { ...extra, "X-CSRF-Token": csrfToken } : extra;
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

function detailText(data, fallback) {
  const detail = data?.detail ?? data?.message;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || String(item)).filter(Boolean).join("; ") || fallback;
  }
  if (detail && typeof detail === "object") return detail.message || fallback;
  return fallback;
}

function setRepoFeedback(message, state = "") {
  if (!repoFeedback) return;
  repoFeedback.textContent = message;
  repoFeedback.classList.remove("form-hint--loading", "form-hint--success", "form-hint--error");
  if (state) repoFeedback.classList.add(`form-hint--${state}`);
}

function showFeedback(element, message, state = "") {
  if (!element) return;
  element.textContent = message;
  element.hidden = false;
  element.classList.remove("inline-feedback--success", "inline-feedback--error");
  if (state) element.classList.add(`inline-feedback--${state}`);
}

function hideFeedback(element) {
  if (element) element.hidden = true;
}

function requestConfirmation({ title, message, confirmLabel = "Confirm" }) {
  if (!confirmDialog || typeof confirmDialog.showModal !== "function") return Promise.resolve(false);
  if (confirmDialog.open) confirmDialog.close("cancel");
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  confirmAction.textContent = confirmLabel;

  return new Promise((resolve) => {
    confirmationResolver = resolve;
    confirmDialog.showModal();
    confirmCancel?.focus();
  });
}

confirmDialog?.addEventListener("close", () => {
  const resolve = confirmationResolver;
  confirmationResolver = null;
  resolve?.(confirmDialog.returnValue === "confirm");
});

confirmDialog?.addEventListener("click", (event) => {
  const bounds = confirmDialog.getBoundingClientRect();
  const inside = event.clientX >= bounds.left && event.clientX <= bounds.right
    && event.clientY >= bounds.top && event.clientY <= bounds.bottom;
  if (!inside) confirmDialog.close("cancel");
});

function hideToast() {
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = null;
  if (appToast) appToast.hidden = true;
}

function showToast(message, state = "success") {
  if (!appToast || !appToastMessage) return;
  hideToast();
  appToastMessage.textContent = message;
  appToast.classList.remove("app-toast--success", "app-toast--error");
  appToast.classList.add(`app-toast--${state}`);
  appToast.setAttribute("role", state === "error" ? "alert" : "status");
  appToast.hidden = false;
  toastTimer = window.setTimeout(hideToast, state === "error" ? 7000 : 4000);
}

appToastClose?.addEventListener("click", hideToast);

repoInput?.addEventListener("input", () => {
  invalidateAlertPreview();
  const value = repoInput.value.trim();
  repoInput.classList.remove("form-input--valid", "form-input--invalid");
  repoLabelSuggestions?.setAttribute("hidden", "");
  if (!value) {
    setRepoFeedback("Paste a GitHub URL or enter owner/repo.");
  } else if (parseRepo(value)) {
    repoInput.classList.add("form-input--valid");
    setRepoFeedback("Repository format looks good. Load its labels to verify access.");
  } else {
    repoInput.classList.add("form-input--invalid");
    setRepoFeedback("Use owner/repo or a full github.com repository URL.", "error");
  }
});

lookupRepoBtn?.addEventListener("click", () => loadRepoLabels());

async function loadRepoLabels(repoOverride = null) {
  if (!repoInput || !repoLabelList || !repoLabelSuggestions) return false;
  if (repoOverride) repoInput.value = repoOverride;
  const repo = parseRepo(repoInput.value);
  if (!repo) {
    repoInput.classList.add("form-input--invalid");
    setRepoFeedback("Enter a valid public GitHub repository first.", "error");
    repoInput.focus();
    return false;
  }

  activeLabelRequest?.abort();
  activeLabelRequest = new AbortController();
  lookupRepoBtn.disabled = true;
  lookupRepoBtn.textContent = "Loading…";
  setRepoFeedback(`Checking ${repo}…`, "loading");

  const [owner, name] = repo.split("/");
  try {
    const response = await fetch(`/subscriptions/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/labels`, {
      headers: csrfHeaders({ Accept: "application/json" }),
      signal: activeLabelRequest.signal,
    });
    const data = await safeJson(response);
    if (!response.ok) {
      const messages = {
        401: "Sign in again to load repository labels.",
        403: "GitHub did not grant access to this repository.",
        404: "That public repository could not be found.",
        429: "GitHub's rate limit is busy. Try again shortly, or enter a label manually.",
      };
      throw new RepoLookupError(messages[response.status] || detailText(data, "Labels are unavailable right now; manual entry still works."));
    }

    const labels = Array.isArray(data.labels) ? data.labels : [];
    repoInput.value = data.repo_full_name || repo;
    repoInput.classList.add("form-input--valid");
    repoInput.classList.remove("form-input--invalid");
    renderRepoLabels(labels);
    repoLabelSuggestions.hidden = false;
    setRepoFeedback(
      labels.length ? `Verified ${data.repo_full_name || repo}. Choose from ${labels.length} labels below.` : `Verified ${data.repo_full_name || repo}. This repository has no labels yet.`,
      "success",
    );
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    repoLabelList.replaceChildren();
    repoLabelSuggestions.hidden = true;
    const message = error instanceof RepoLookupError
      ? error.message
      : "Could not load labels right now. You can still enter a label manually.";
    setRepoFeedback(message, "error");
    return false;
  } finally {
    lookupRepoBtn.disabled = false;
    lookupRepoBtn.textContent = "Load labels";
  }
}

class RepoLookupError extends Error {}

function renderRepoLabels(labels) {
  repoLabelList.replaceChildren();
  labels.forEach((label) => {
    const name = String(label?.name || "").trim();
    if (!name) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "repo-label-option";
    button.title = label.description || `Watch the ${name} label`;
    const color = /^[0-9a-f]{6}$/i.test(label.color || "") ? `#${label.color}` : "#94a3b8";
    button.style.setProperty("--label-color", color);
    button.innerHTML = `<span class="repo-label-option__dot" aria-hidden="true"></span><span>${escHtml(name)}</span>`;
    button.addEventListener("click", () => {
      addPendingLabel(name);
      button.classList.add("repo-label-option--chosen");
      labelInput?.focus();
    });
    repoLabelList.appendChild(button);
  });
}

function renderPendingChips() {
  if (!tagInputEl) return;
  invalidateAlertPreview();
  tagInputEl.querySelectorAll(".tag-chip").forEach((element) => element.remove());
  pendingLabels.forEach((label, index) => {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.innerHTML = `<span class="tag-chip__text">${escHtml(label)}</span><button type="button" class="tag-chip__remove" aria-label="Remove ${escHtml(label)}">×</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      pendingLabels.splice(index, 1);
      renderPendingChips();
      labelInput?.focus();
    });
    tagInputEl.insertBefore(chip, labelInput);
  });
  if (labelInput) labelInput.placeholder = pendingLabels.length ? "Add another" : "good-first-issue";
}

function addPendingLabel(raw) {
  const value = String(raw || "").trim();
  if (!value) return;
  if (!pendingLabels.includes(value)) pendingLabels.push(value);
  if (labelInput) labelInput.value = "";
  renderPendingChips();
}

tagInputEl?.addEventListener("click", (event) => {
  if (event.target === tagInputEl) labelInput?.focus();
});

labelInput?.addEventListener("keydown", (event) => {
  if (event.key === "," || event.key === "Enter") {
    event.preventDefault();
    addPendingLabel(labelInput.value);
  } else if (event.key === "Backspace" && !labelInput.value && pendingLabels.length) {
    pendingLabels.pop();
    renderPendingChips();
  }
});

labelInput?.addEventListener("blur", () => {
  if (labelInput.value.trim()) addPendingLabel(labelInput.value);
});

labelInput?.addEventListener("input", invalidateAlertPreview);

document.querySelectorAll(".label-preset-btn").forEach((button) => {
  button.addEventListener("click", () => {
    addPendingLabel(button.dataset.label);
    labelInput?.focus();
  });
});

document.querySelectorAll(".starter-pack-trigger").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    const key = trigger.dataset.starterPack;
    if (!STARTER_PACKS[key]) return;
    if (!addForm) {
      try { localStorage.setItem(STARTER_STORAGE_KEY, key); } catch (_) { /* optional enhancement */ }
      return;
    }
    event.preventDefault();
    applyStarterPack(key);
  });
});

function applyStarterPack(key) {
  const pack = STARTER_PACKS[key];
  if (!pack || !repoInput) return;
  repoInput.value = pack.repo;
  pendingLabels = [...pack.labels];
  renderPendingChips();
  repoInput.dispatchEvent(new Event("input"));
  if (githubConnected) loadRepoLabels(pack.repo);
  addForm?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function restoreStarterPack() {
  if (!addForm) return;
  let stored = null;
  try {
    stored = localStorage.getItem(STARTER_STORAGE_KEY);
    if (stored && githubConnected) localStorage.removeItem(STARTER_STORAGE_KEY);
  } catch (_) { /* storage can be blocked */ }
  const query = new URLSearchParams(window.location.search);
  const repo = parseRepo(query.get("repo") || "");
  const label = query.get("label");
  if (stored && STARTER_PACKS[stored]) applyStarterPack(stored);
  else if (repo) {
    repoInput.value = repo;
    if (label) addPendingLabel(label);
    repoInput.dispatchEvent(new Event("input"));
    if (githubConnected) loadRepoLabels(repo);
  }
}

function currentPreviewSignature(repo, labels) {
  return JSON.stringify([String(repo || "").toLowerCase(), ...labels]);
}

function invalidateAlertPreview() {
  activePreviewRequest?.abort();
  activePreviewRequest = null;
  previewSignature = null;
  if (previewAlertsBtn) {
    previewAlertsBtn.disabled = false;
    previewAlertsBtn.textContent = "Preview alerts";
  }
  if (alertPreview) {
    alertPreview.hidden = true;
    alertPreview.setAttribute("aria-busy", "false");
  }
}

function setPreviewLoading() {
  if (!alertPreview) return;
  alertPreview.hidden = false;
  alertPreview.classList.add("alert-preview--loading");
  alertPreview.setAttribute("aria-busy", "true");
  if (alertPreviewTitle) alertPreviewTitle.textContent = "Checking recent activity…";
  if (alertPreviewCount) alertPreviewCount.textContent = "";
  if (alertPreviewSummary) alertPreviewSummary.textContent = "Reviewing recent open issues with the same rules used for Discord alerts.";
  alertPreviewWarnings?.replaceChildren();
  if (alertPreviewWarnings) alertPreviewWarnings.hidden = true;
  alertPreviewIssues?.replaceChildren();
}

function renderPreviewError(message) {
  if (!alertPreview) return;
  alertPreview.hidden = false;
  alertPreview.classList.remove("alert-preview--loading");
  alertPreview.setAttribute("aria-busy", "false");
  if (alertPreviewTitle) alertPreviewTitle.textContent = "Preview unavailable";
  if (alertPreviewCount) alertPreviewCount.textContent = "Try again";
  if (alertPreviewSummary) alertPreviewSummary.textContent = message;
  alertPreviewWarnings?.replaceChildren();
  if (alertPreviewWarnings) alertPreviewWarnings.hidden = true;
  alertPreviewIssues?.replaceChildren();
}

function previewTime(value, fallback) {
  if (!value) return fallback;
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return fallback;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function renderAlertPreview(data) {
  if (!alertPreview || !alertPreviewIssues) return;
  const issues = Array.isArray(data.issues) ? data.issues : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const estimate = Number(data.estimated_notification_count) || 0;
  const examined = Number(data.examined_issue_count) || 0;
  const matching = Number(data.matching_issue_count) || 0;
  const windowDays = Number(data.window_days) || 30;

  alertPreview.hidden = false;
  alertPreview.classList.remove("alert-preview--loading");
  alertPreview.setAttribute("aria-busy", "false");
  if (alertPreviewTitle) alertPreviewTitle.textContent = `${windowDays}-day alert preview`;
  if (alertPreviewCount) alertPreviewCount.textContent = `${estimate} alert${estimate === 1 ? "" : "s"}`;
  if (alertPreviewSummary) {
    const partial = data.is_partial ? " This is a partial estimate for a busy repository." : "";
    alertPreviewSummary.textContent = `Checked ${examined} recently updated open issue${examined === 1 ? "" : "s"}; ${matching} currently match your labels and ${estimate} would have triggered a DM.${partial}`;
  }

  alertPreviewWarnings?.replaceChildren();
  if (alertPreviewWarnings) {
    warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = String(warning);
      alertPreviewWarnings.appendChild(item);
    });
    alertPreviewWarnings.hidden = warnings.length === 0;
  }

  alertPreviewIssues.replaceChildren();
  if (!issues.length) {
    const empty = document.createElement("li");
    empty.className = "alert-preview__empty";
    empty.textContent = "No recent open issue would have triggered an alert with these labels.";
    alertPreviewIssues.appendChild(empty);
    return;
  }

  const safeRepo = parseRepo(data.repo_full_name || "");
  issues.forEach((issue) => {
    const item = document.createElement("li");
    item.className = "alert-preview-issue";

    const header = document.createElement("div");
    header.className = "alert-preview-issue__header";
    const link = document.createElement("a");
    link.className = "alert-preview-issue__title";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `#${issue.issue_number} — ${issue.title || "(no title)"}`;
    link.href = safeRepo && Number.isInteger(issue.issue_number)
      ? `https://github.com/${safeRepo}/issues/${issue.issue_number}`
      : "#";
    header.appendChild(link);

    const reason = document.createElement("p");
    reason.className = "alert-preview-issue__reason";
    reason.textContent = issue.trigger_reason || "Issue became actionable";

    const meta = document.createElement("p");
    meta.className = "alert-preview-issue__meta";
    const assigned = Array.isArray(issue.assigned_to) && issue.assigned_to.length
      ? `Assigned to ${issue.assigned_to.join(", ")}`
      : "Unassigned";
    meta.textContent = `Label: ${issue.matched_label || "—"} · ${assigned} · Triggered ${previewTime(issue.triggered_at, "recently")} · Opened ${previewTime(issue.created_at, "unknown")}`;

    item.append(header, reason, meta);
    alertPreviewIssues.appendChild(item);
  });
}

previewAlertsBtn?.addEventListener("click", async () => {
  hideFeedback(formError);
  hideFeedback(formSuccess);
  if (labelInput?.value.trim()) addPendingLabel(labelInput.value);

  const repo = parseRepo(repoInput?.value || "");
  if (!repo) {
    showFeedback(formError, "Enter a valid public GitHub repository.", "error");
    repoInput?.focus();
    return;
  }
  if (!pendingLabels.length) {
    showFeedback(formError, "Choose or enter at least one label.", "error");
    labelInput?.focus();
    return;
  }

  const labels = [...pendingLabels];
  const signature = currentPreviewSignature(repo, labels);
  activePreviewRequest?.abort();
  const previewRequest = new AbortController();
  activePreviewRequest = previewRequest;
  previewAlertsBtn.disabled = true;
  previewAlertsBtn.textContent = "Checking…";
  setPreviewLoading();

  try {
    const response = await fetch("/subscriptions/preview", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json", Accept: "application/json" }),
      body: JSON.stringify({ repo_full_name: repo, labels }),
      signal: previewRequest.signal,
    });
    const data = await safeJson(response);
    if (!response.ok) {
      const defaults = {
        401: "Sign in again and reconnect GitHub.",
        403: "GitHub did not grant access to this repository.",
        404: "That public repository could not be found.",
        422: "One of these patterns does not match a current repository label.",
        429: "Preview is cooling down or GitHub's rate limit is busy. Try again shortly.",
      };
      throw new RepoLookupError(defaults[response.status] || detailText(data, "Could not build the alert preview."));
    }
    if (signature !== currentPreviewSignature(parseRepo(repoInput?.value || ""), pendingLabels)) return;
    previewSignature = signature;
    renderAlertPreview(data);
  } catch (error) {
    if (error.name === "AbortError") return;
    renderPreviewError(error.message || "Could not build the alert preview. Try again.");
  } finally {
    if (activePreviewRequest === previewRequest) {
      activePreviewRequest = null;
      previewAlertsBtn.disabled = false;
      previewAlertsBtn.textContent = previewSignature === signature ? "Refresh preview" : "Preview alerts";
    }
  }
});

addForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideFeedback(formError);
  hideFeedback(formSuccess);
  if (labelInput.value.trim()) addPendingLabel(labelInput.value);

  const repo = parseRepo(repoInput.value);
  if (!repo) {
    showFeedback(formError, "Enter a valid public GitHub repository.", "error");
    repoInput.focus();
    return;
  }
  if (!pendingLabels.length) {
    showFeedback(formError, "Choose or enter at least one label.", "error");
    labelInput.focus();
    return;
  }

  const labels = [...pendingLabels];
  const submitButton = addForm.querySelector('button[type="submit"]');
  const defaultLabel = submitButton.dataset.defaultLabel || "Start watching";
  submitButton.disabled = true;
  submitButton.textContent = labels.length > 1 ? `Adding ${labels.length} watches…` : "Adding watch…";

  const failures = [];
  let added = 0;
  for (const label of labels) {
    try {
      const requestOptions = {
        method: "POST",
        headers: csrfHeaders({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ repo_full_name: repo, label }),
      };
      let response = await fetch("/subscriptions/", requestOptions);
      if (response.status === 429) {
        const retryAfter = Math.min(3, Math.max(1, Number.parseInt(response.headers.get("Retry-After") || "1", 10)));
        await new Promise((resolve) => window.setTimeout(resolve, retryAfter * 1000));
        response = await fetch("/subscriptions/", requestOptions);
      }
      const data = await safeJson(response);
      if (!response.ok) {
        failures.push({ label, message: subscriptionError(response.status, data) });
        continue;
      }
      appendSubItem(data);
      updateBadge(1);
      hideEmptyState();
      added += 1;
    } catch (_) {
      failures.push({ label, message: "Network error; try again." });
    }
  }

  const failedLabels = new Set(failures.map((failure) => failure.label));
  pendingLabels = labels.filter((label) => failedLabels.has(label));
  renderPendingChips();
  if (failures.length) {
    showFeedback(formError, failures.map((failure) => `“${failure.label}”: ${failure.message}`).join(" · "), "error");
  }
  if (added) {
    showFeedback(formSuccess, `${added} watch${added === 1 ? "" : "es"} added. The first check should run within a few minutes.`, "success");
    window.setTimeout(refreshMonitoringStatus, 800);
  }
  submitButton.disabled = false;
  submitButton.textContent = defaultLabel;
});

function subscriptionError(status, data) {
  const defaults = {
    401: "Sign in again and reconnect GitHub.",
    403: "GitHub access was denied.",
    404: "Repository not found.",
    409: "You already watch this label pattern.",
    422: "This pattern does not match a current repository label.",
    429: "GitHub's rate limit is busy; try again shortly.",
  };
  return detailText(data, defaults[status] || "Could not create this watch.");
}

async function deleteSub(id, button) {
  const chip = [...document.querySelectorAll(".label-chip")].find((item) => String(item.dataset.id) === String(id));
  const label = chip?.querySelector(".label-chip__text")?.textContent?.trim() || "this label";
  const confirmed = await requestConfirmation({
    title: "Stop watching this label?",
    message: `IssueBell will stop sending alerts for “${label}”. You can add this watch again at any time.`,
    confirmLabel: "Stop watching",
  });
  if (!confirmed) return;

  button.disabled = true;
  try {
    const response = await fetch(`/subscriptions/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: csrfHeaders({ Accept: "application/json" }),
    });
    const data = await safeJson(response);
    if (!response.ok) throw new Error(detailText(data, "Could not remove this watch."));

    const group = chip?.closest(".repo-group");
    chip?.remove();
    if (group && !group.querySelector(".label-chip")) group.remove();
    updateBadge(-1);
    checkEmptyState();
    refreshMonitoringStatus();
    showToast(`Stopped watching “${label}”.`);
  } catch (error) {
    showToast(error.message || "Could not remove this watch.", "error");
    button.disabled = false;
  }
}

window.deleteSub = deleteSub;

function createLabelChip(subscription) {
  const chip = document.createElement("span");
  chip.className = "label-chip";
  chip.dataset.id = subscription.id;
  if (subscription.last_checked_at) chip.dataset.lastChecked = subscription.last_checked_at;
  chip.innerHTML = `<span class="label-chip__text">${escHtml(subscription.label)}</span><button type="button" class="label-chip__remove" title="Stop watching ${escHtml(subscription.label)}" aria-label="Stop watching ${escHtml(subscription.label)}">×</button>`;
  chip.querySelector("button").addEventListener("click", (event) => deleteSub(subscription.id, event.currentTarget));
  return chip;
}

function appendSubItem(subscription) {
  const panel = document.querySelector(".panel--subs");
  if (!panel) return;
  let list = document.getElementById("sub-list");
  if (!list) {
    list = document.createElement("ul");
    list.id = "sub-list";
    list.className = "sub-list";
    panel.appendChild(list);
  }
  const duplicate = [...list.querySelectorAll(".label-chip")].some((chip) => String(chip.dataset.id) === String(subscription.id));
  if (duplicate) return;

  let group = [...list.querySelectorAll(".repo-group")].find((item) => item.dataset.repo === subscription.repo_full_name);
  if (!group) {
    group = document.createElement("li");
    group.className = "repo-group";
    group.dataset.repo = subscription.repo_full_name;
    const header = document.createElement("div");
    header.className = "repo-group__header";
    const link = document.createElement("a");
    link.className = "repo-group__name";
    link.href = `https://github.com/${String(subscription.repo_full_name).split("/").map(encodeURIComponent).join("/")}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = subscription.repo_full_name;
    const state = document.createElement("span");
    state.className = "watch-state";
    state.innerHTML = '<span class="status-dot status-dot--idle"></span> Waiting';
    header.append(link, state);
    const meta = document.createElement("div");
    meta.className = "repo-group__meta";
    meta.textContent = "Last checked waiting for first check";
    const labels = document.createElement("div");
    labels.className = "repo-group__labels";
    const error = document.createElement("p");
    error.className = "repo-group__error";
    error.hidden = true;
    group.append(header, meta, labels, error);
    list.prepend(group);
  }
  group.querySelector(".repo-group__labels").appendChild(createLabelChip(subscription));
}

function updateBadge(delta) {
  const badge = document.getElementById("subscription-count") || document.querySelector(".badge");
  if (!badge) return;
  const count = Number.parseInt(badge.textContent, 10) || 0;
  badge.textContent = String(Math.max(0, count + delta));
}

function hideEmptyState() {
  document.getElementById("empty-state")?.remove();
}

function checkEmptyState() {
  const list = document.getElementById("sub-list");
  if (list && list.children.length) return;
  list?.remove();
  const panel = document.querySelector(".panel--subs");
  if (!panel || document.getElementById("empty-state")) return;
  const empty = document.createElement("div");
  empty.id = "empty-state";
  empty.className = "empty-state";
  empty.innerHTML = '<span class="empty-state__icon">🔕</span><strong>No watches yet</strong><p>Choose a starter pack or add a repository to begin.</p>';
  panel.appendChild(empty);
}

testDmBtn?.addEventListener("click", async () => {
  const original = testDmBtn.textContent;
  testDmBtn.disabled = true;
  testDmBtn.textContent = "Sending…";
  hideFeedback(testDmResult);
  try {
    const response = await fetch("/subscriptions/test-dm", {
      method: "POST",
      headers: csrfHeaders({ Accept: "application/json" }),
    });
    const data = await safeJson(response);
    if (!response.ok) {
      let message;
      if (response.status === 429) {
        const retry = response.headers.get("Retry-After");
        message = retry ? `A test was just sent. Try again in ${retry} seconds.` : "A test was just sent. Try again in about a minute.";
      } else if (response.status === 502) {
        message = "Discord could not deliver the test DM. Check that DMs are allowed, then try again.";
      } else if (response.status === 404) {
        message = "Test DMs are not available on this deployment yet.";
      } else {
        message = detailText(data, "Could not send a test DM.");
      }
      throw new Error(message);
    }
    const sentAt = data.sent_at ? parseServerDate(data.sent_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "just now";
    showFeedback(testDmResult, `Test DM sent ${sentAt}. Check Discord.`, "success");
  } catch (error) {
    showFeedback(testDmResult, error.message || "Could not send a test DM. Try again.", "error");
  } finally {
    testDmBtn.disabled = false;
    testDmBtn.textContent = original;
  }
});

async function refreshMonitoringStatus() {
  if (!document.querySelector(".dashboard-page")) return;
  try {
    const response = await fetch("/subscriptions/status", { headers: csrfHeaders({ Accept: "application/json" }) });
    if (!response.ok) return;
    const data = await safeJson(response);
    const statuses = Array.isArray(data.subscriptions) ? data.subscriptions : [];
    applyMonitoringStatus(statuses, data.github_connected !== false);
  } catch (_) {
    // The server-rendered state remains useful when the optional status API is unavailable.
  }
}

function applyMonitoringStatus(statuses, isGithubConnected = githubConnected) {
  const reconnectCodes = new Set(["github_authentication_error", "github_forbidden", "token_decryption_error"]);
  const byRepo = new Map();
  statuses.forEach((status) => {
    const key = status.repo_full_name;
    if (!byRepo.has(key)) byRepo.set(key, []);
    byRepo.get(key).push(status);
    const chip = [...document.querySelectorAll(".label-chip")].find((item) => String(item.dataset.id) === String(status.id));
    if (chip) {
      const partialFailure = (status.poll?.last_event_failure_count || 0) > 0;
      chip.classList.toggle("label-chip--error", Boolean(status.poll?.error_code) || partialFailure);
      chip.title = status.poll?.error_message || (partialFailure ? "Some issue timelines were unavailable during the last check." : "");
    }
  });

  document.querySelectorAll(".repo-group").forEach((group) => {
    const repoStatuses = byRepo.get(group.dataset.repo) || [];
    if (!repoStatuses.length) return;
    const errors = repoStatuses.filter((status) => status.poll?.error_code);
    const reconnectRequired = !isGithubConnected || errors.some((status) => reconnectCodes.has(status.poll?.error_code));
    const deliveryProblem = repoStatuses.some((status) => (status.delivery?.dead_count || 0) > 0 || (status.delivery?.failed_count || 0) > 0);
    const eventFailureCount = Math.max(...repoStatuses.map((status) => status.poll?.last_event_failure_count || 0));
    const partialEventFailure = eventFailureCount > 0;
    const candidates = repoStatuses
      .flatMap((status) => [status.last_checked_at, status.poll?.last_success_at, status.poll?.last_attempt_at])
      .filter(Boolean)
      .sort();
    const latest = candidates.at(-1);
    const meta = group.querySelector(".repo-group__meta");
    if (meta && latest) {
      meta.replaceChildren(document.createTextNode("Last checked "));
      const time = document.createElement("time");
      time.className = "relative-time";
      time.dateTime = latest;
      meta.appendChild(time);
      const poll = repoStatuses[0]?.poll;
      const actionable = poll?.last_actionable_count || 0;
      const ignored = poll?.last_ignored_update_count || 0;
      meta.append(document.createTextNode(` · ${actionable} actionable · ${ignored} routine update${ignored === 1 ? "" : "s"} ignored`));
      updateRelativeTime(time);
    }
    const state = group.querySelector(".watch-state");
    if (state) state.innerHTML = reconnectRequired
      ? '<span class="status-dot status-dot--warning"></span> Paused'
      : errors.length || deliveryProblem || partialEventFailure
        ? '<span class="status-dot status-dot--warning"></span> Needs attention'
      : '<span class="status-dot status-dot--healthy"></span> Watching';
    const errorElement = group.querySelector(".repo-group__error");
    if (errorElement) {
      errorElement.hidden = !errors.length && !reconnectRequired && !deliveryProblem && !partialEventFailure;
      errorElement.replaceChildren();
      if (reconnectRequired) {
        errorElement.append(document.createTextNode("GitHub authorization needs attention. "));
        const link = document.createElement("a");
        link.href = "/auth/github";
        link.textContent = "Reconnect GitHub";
        errorElement.append(link);
      } else if (errors.length) {
        errorElement.textContent = errors[0].poll?.error_message || `Polling error: ${errors[0].poll?.error_code}`;
      } else if (deliveryProblem) {
        errorElement.append(document.createTextNode("Discord delivery needs attention. "));
        const link = document.createElement("a");
        link.href = "#test-dm-btn";
        link.textContent = "Run a test DM";
        errorElement.append(link);
      } else if (partialEventFailure) {
        errorElement.textContent = `${eventFailureCount} issue timeline${eventFailureCount === 1 ? " was" : "s were"} unavailable during the last check. Other issues were still checked.`;
      }
    }
  });

  const summary = document.getElementById("monitor-summary");
  if (summary) {
    const errorCount = statuses.filter((status) => status.poll?.error_code || (status.poll?.last_event_failure_count || 0) > 0 || (status.delivery?.dead_count || 0) > 0 || (status.delivery?.failed_count || 0) > 0).length;
    const reconnectRequired = !isGithubConnected || statuses.some((status) => reconnectCodes.has(status.poll?.error_code));
    const dotClass = reconnectRequired || errorCount ? "status-dot--warning" : statuses.length ? "status-dot--healthy" : "status-dot--idle";
    const label = reconnectRequired && statuses.length
      ? "Paused — reconnect GitHub"
      : errorCount
      ? `${errorCount} watch${errorCount === 1 ? "" : "es"} need attention`
      : statuses.length
        ? `${statuses.length} active watch${statuses.length === 1 ? "" : "es"}`
        : "Ready for your first watch";
    summary.innerHTML = `<span class="status-dot ${dotClass}"></span><span>${label}</span>`;
  }
}

function parseServerDate(value) {
  const text = String(value || "");
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text) ? text : `${text}Z`;
  return new Date(normalized);
}

function updateRelativeTime(time) {
  const date = parseServerDate(time.dateTime);
  if (Number.isNaN(date.getTime())) return;
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  let text;
  if (seconds < 45) text = "just now";
  else if (seconds < 90) text = "1 minute ago";
  else if (seconds < 3600) text = `${Math.round(seconds / 60)} minutes ago`;
  else if (seconds < 86400) text = `${Math.round(seconds / 3600)} hours ago`;
  else if (seconds < 604800) text = `${Math.round(seconds / 86400)} days ago`;
  else text = date.toLocaleDateString();
  time.textContent = text;
  time.title = date.toLocaleString();
}

function updateRelativeTimes() {
  document.querySelectorAll("time.relative-time").forEach(updateRelativeTime);
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const confirmed = await requestConfirmation({
      title: form.dataset.confirmTitle || "Are you sure?",
      message: form.dataset.confirm,
      confirmLabel: form.dataset.confirmLabel || "Confirm",
    });
    if (confirmed) form.submit();
  });
});

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

restoreStarterPack();
updateRelativeTimes();
refreshMonitoringStatus();
window.setInterval(updateRelativeTimes, 60_000);
window.setInterval(refreshMonitoringStatus, 60_000);

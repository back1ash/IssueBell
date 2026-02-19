/* IssueBell - dashboard interactions */

// --- Inputs ------------------------------------------------------------------
const addForm    = document.getElementById("add-form");
const formError  = document.getElementById("form-error");
const repoInput  = document.getElementById("repo");
const labelInput = document.getElementById("label");
const tagInputEl = document.getElementById("label-tag-input");

// pending labels (not yet submitted)
let pendingLabels = [];

// --- Repo input parsing ------------------------------------------------------
function parseRepo(raw) {
  const s = raw.trim();
  const httpsMatch = s.match(/^https?:\/\/github\.com\/([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+?)(\.git)?(?:\/.*)?$/);
  if (httpsMatch) return httpsMatch[1];
  const sshMatch = s.match(/^git@github\.com:([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+?)(\.git)?$/);
  if (sshMatch) return sshMatch[1];
  if (/^[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+$/.test(s)) return s;
  return null;
}

// --- Real-time repo format validation ----------------------------------------
repoInput?.addEventListener("input", () => {
  const val = repoInput.value.trim();
  if (val.length === 0) {
    repoInput.classList.remove("form-input--valid", "form-input--invalid");
  } else if (parseRepo(val) !== null) {
    repoInput.classList.add("form-input--valid");
    repoInput.classList.remove("form-input--invalid");
  } else {
    repoInput.classList.add("form-input--invalid");
    repoInput.classList.remove("form-input--valid");
  }
});

// --- Pending label chips (multi-label input) ---------------------------------
function renderPendingChips() {
  tagInputEl?.querySelectorAll(".tag-chip").forEach((el) => el.remove());
  pendingLabels.forEach((lbl, i) => {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.innerHTML =
      `<span class="tag-chip__text">${escHtml(lbl)}</span>` +
      `<button type="button" class="tag-chip__remove" aria-label="Remove">x</button>`;
    chip.querySelector(".tag-chip__remove").addEventListener("click", () => {
      pendingLabels.splice(i, 1);
      renderPendingChips();
    });
    tagInputEl.insertBefore(chip, labelInput);
  });
  if (labelInput) labelInput.placeholder = pendingLabels.length ? "" : "good-first-issue";
}

function addPendingLabel(raw) {
  const val = raw.trim();
  if (!val) return;
  if (pendingLabels.includes(val)) { if (labelInput) labelInput.value = ""; return; }
  pendingLabels.push(val);
  renderPendingChips();
  if (labelInput) labelInput.value = "";
}

// Comma or Enter inside label field -> commit to chip
labelInput?.addEventListener("keydown", (e) => {
  if (e.key === "," || e.key === "Enter") {
    e.preventDefault();
    addPendingLabel(labelInput.value);
  } else if (e.key === "Backspace" && labelInput.value === "" && pendingLabels.length) {
    pendingLabels.pop();
    renderPendingChips();
  }
});

// Commit on blur so pasted values are not lost
labelInput?.addEventListener("blur", () => {
  if (labelInput.value.trim()) addPendingLabel(labelInput.value);
});

// Quick-pick -> add directly as pending chip
document.querySelectorAll(".label-preset-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    addPendingLabel(btn.dataset.label);
    labelInput?.focus();
  });
});

// --- Add Subscription --------------------------------------------------------
addForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;

  // If the user typed something without pressing comma, treat it as a label too
  if (labelInput.value.trim()) addPendingLabel(labelInput.value);

  const labels = [...pendingLabels];
  if (labels.length === 0) {
    formError.textContent = "Add at least one label.";
    formError.hidden = false;
    return;
  }

  const repo = parseRepo(repoInput.value) ?? repoInput.value.trim();

  const submitBtn = addForm.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  submitBtn.textContent = "Adding...";

  const errors = [];
  for (const label of labels) {
    try {
      const resp = await fetch("/subscriptions/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_full_name: repo, label }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        errors.push(`"${label}": ${data.detail ?? "failed"}`);
        continue;
      }
      const sub = await resp.json();
      appendSubItem(sub);
      updateBadge(+1);
      hideEmptyState();
    } catch (err) {
      errors.push(`"${label}": ${err.message}`);
    }
  }

  // Keep only failed labels in pending; clear the rest
  const failedLabels = errors.map((e) => e.match(/"(.+?)"/)?.[1]).filter(Boolean);
  pendingLabels = labels.filter((l) => failedLabels.includes(l));
  renderPendingChips();
  labelInput?.focus();

  if (errors.length) {
    formError.textContent = errors.join(" / ");
    formError.hidden = false;
  }

  submitBtn.disabled = false;
  submitBtn.textContent = "+ Add Subscription";
});

// --- Delete Subscription -----------------------------------------------------
async function deleteSub(id, btn) {
  if (!confirm("Remove this subscription?")) return;
  btn.disabled = true;

  try {
    const resp = await fetch(`/subscriptions/${id}`, { method: "DELETE" });
    if (!resp.ok) throw new Error("Failed to remove");

    const chip = document.querySelector(`.label-chip[data-id="${id}"]`);
    if (chip) {
      const group = chip.closest(".repo-group");
      chip.remove();
      if (group && group.querySelector(".repo-group__labels").children.length === 0) {
        group.remove();
      }
    }

    updateBadge(-1);
    checkEmptyState();
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
  }
}

// --- Helpers -----------------------------------------------------------------
function createLabelChip(sub) {
  const chip = document.createElement("span");
  chip.className = "label-chip";
  chip.dataset.id = sub.id;
  chip.innerHTML =
    `<span class="label-chip__text">${escHtml(sub.label)}</span>` +
    `<button class="label-chip__remove" onclick="deleteSub(${sub.id}, this)" title="Remove">x</button>`;
  return chip;
}

function appendSubItem(sub) {
  let list = document.getElementById("sub-list");

  if (!list) {
    list = document.createElement("ul");
    list.id = "sub-list";
    list.className = "sub-list";
    document.querySelector(".panel--subs").appendChild(list);
  }

  const safeRepo = sub.repo_full_name.replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
  const existing = list.querySelector(`.repo-group[data-repo="${safeRepo}"]`);
  if (existing) {
    existing.querySelector(".repo-group__labels").appendChild(createLabelChip(sub));
    return;
  }

  const li = document.createElement("li");
  li.className = "repo-group";
  li.dataset.repo = sub.repo_full_name;
  li.innerHTML = `
    <div class="repo-group__header">
      <a class="repo-group__name"
         href="https://github.com/${escHtml(sub.repo_full_name)}"
         target="_blank" rel="noopener">${escHtml(sub.repo_full_name)}</a>
    </div>
    <div class="repo-group__labels"></div>
  `;
  li.querySelector(".repo-group__labels").appendChild(createLabelChip(sub));
  list.prepend(li);
}

function updateBadge(delta) {
  const badge = document.querySelector(".badge");
  if (!badge) return;
  badge.textContent = Math.max(0, parseInt(badge.textContent, 10) + delta);
}

function hideEmptyState() {
  document.getElementById("empty-state")?.remove();
}

function checkEmptyState() {
  const list = document.getElementById("sub-list");
  if (list && list.children.length === 0) {
    list.remove();
    const panel = document.querySelector(".panel--subs");
    const empty = document.createElement("div");
    empty.id = "empty-state";
    empty.className = "empty-state";
    empty.innerHTML = `
      <span class="empty-state__icon">&#x1F515;</span>
      <p>No subscriptions yet.<br/>Add one above to start receiving notifications.</p>
    `;
    panel.appendChild(empty);
  }
}

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
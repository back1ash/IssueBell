/* IssueBell — dashboard interactions */

// ─── Inputs ───────────────────────────────────────────────────────────────────
const addForm   = document.getElementById("add-form");
const formError = document.getElementById("form-error");
const repoInput  = document.getElementById("repo");
const labelInput = document.getElementById("label");

// ─── Repo input parsing ───────────────────────────────────────────────────────────────────
// Accepts: owner/repo  OR  https://github.com/owner/repo[.git]  OR  git@github.com:owner/repo[.git]
function parseRepo(raw) {
  const s = raw.trim();
  // HTTPS URL: https://github.com/owner/repo or https://github.com/owner/repo.git
  const httpsMatch = s.match(/^https?:\/\/github\.com\/([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+?)(\.git)?(?:\/.*)?$/);
  if (httpsMatch) return httpsMatch[1];
  // SSH URL: git@github.com:owner/repo.git
  const sshMatch = s.match(/^git@github\.com:([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+?)(\.git)?$/);
  if (sshMatch) return sshMatch[1];
  // Plain owner/repo
  if (/^[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+$/.test(s)) return s;
  return null;
}

// ─── Real-time repo format validation ─────────────────────────────────────────────────────
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

// ─── Quick-pick label presets ─────────────────────────────────────────────────
document.querySelectorAll(".label-preset-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (labelInput) {
      labelInput.value = btn.dataset.label;
      labelInput.focus();
    }
  });
});

// ─── Add Subscription ─────────────────────────────────────────────────────────
addForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;

  const repo  = parseRepo(repoInput.value) ?? repoInput.value.trim();
  const label = labelInput.value.trim();

  const submitBtn = addForm.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  submitBtn.textContent = "Adding…";

  try {
    const resp = await fetch("/subscriptions/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_full_name: repo, label }),
    });

    if (!resp.ok) {
      const data = await resp.json();
      throw new Error(data.detail ?? "Failed to add subscription");
    }

    const sub = await resp.json();
    appendSubItem(sub);
    // Only clear the label so users can quickly add more labels to the same repo
    labelInput.value = "";
    labelInput.focus();
    updateBadge(+1);
    hideEmptyState();
  } catch (err) {
    formError.textContent = err.message;
    formError.hidden = false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "+ Add Subscription";
  }
});

// ─── Delete Subscription ──────────────────────────────────────────────────────
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
      // Remove the whole repo group if it has no labels left
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

// ─── Helpers ──────────────────────────────────────────────────────────────────
function createLabelChip(sub) {
  const chip = document.createElement("span");
  chip.className = "label-chip";
  chip.dataset.id = sub.id;
  chip.innerHTML = `<span class="label-chip__text">${escHtml(sub.label)}</span><button class="label-chip__remove" onclick="deleteSub(${sub.id}, this)" title="Remove">×</button>`;
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

  // Add to existing repo group if present
  const safeRepo = sub.repo_full_name.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const existing = list.querySelector(`.repo-group[data-repo="${safeRepo}"]`);
  if (existing) {
    existing.querySelector(".repo-group__labels").appendChild(createLabelChip(sub));
    return;
  }

  // Create a new repo group
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
      <span class="empty-state__icon">🔕</span>
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

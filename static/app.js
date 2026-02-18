/* IssueBell — dashboard interactions */

// ─── Add Subscription ─────────────────────────────────────────────────────────
const addForm = document.getElementById("add-form");
const formError = document.getElementById("form-error");

addForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;

  const repo = document.getElementById("repo").value.trim();
  const label = document.getElementById("label").value.trim();

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
    addForm.reset();
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

    const item = document.querySelector(`.sub-item[data-id="${id}"]`);
    item?.remove();
    updateBadge(-1);
    checkEmptyState();
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function appendSubItem(sub) {
  let list = document.getElementById("sub-list");

  if (!list) {
    // Create the list if it didn't exist (first item)
    list = document.createElement("ul");
    list.id = "sub-list";
    list.className = "sub-list";
    const panel = document.querySelector(".panel--subs");
    panel.appendChild(list);
  }

  const li = document.createElement("li");
  li.className = "sub-item";
  li.dataset.id = sub.id;
  li.innerHTML = `
    <div class="sub-item__info">
      <a class="sub-item__repo"
         href="https://github.com/${sub.repo_full_name}"
         target="_blank" rel="noopener">
        ${escHtml(sub.repo_full_name)}
      </a>
      <span class="sub-item__label">${escHtml(sub.label)}</span>
    </div>
    <button class="btn btn--ghost btn--sm btn--danger"
            onclick="deleteSub(${sub.id}, this)">Remove</button>
  `;
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

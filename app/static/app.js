"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  page: "overview",
  config: null,
  ingredients: [],
  batches: [],
  ingredientRows: [],
  reconciliationRows: [],
  requests: new Map(),
  pages: { ingredients: 1, batches: 1, reconciliations: 1, movements: 1 },
};
const unitLabel = { kg: "kg", litre: "L", piece: "pcs" };
const pageSize = 12;
let toastTimer;

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
}
function qty(value) {
  const s = String(value ?? "0");
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
function amount(value, unit) {
  return `${esc(qty(value))} <span class="muted">${esc(unitLabel[unit] || unit)}</span>`;
}
function positive(value) {
  return (
    !/^[-+]?0*(?:\.0*)?$/.test(String(value)) && !String(value).startsWith("-")
  );
}
function dateLabel(value) {
  if (!value) return "—";
  const parts = value.slice(0, 10).split("-");
  if (parts.length !== 3) return value;
  return new Date(
    Date.UTC(...[Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])]),
  ).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
function badge(text, style = "") {
  return `<span class="badge ${style}">${esc(text)}</span>`;
}
function empty(title, description, icon = "✓") {
  return `<div class="empty-state"><div class="empty-icon" aria-hidden="true">${esc(icon)}</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
}
function showToast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 5000);
}
function report(error) {
  const node = $("#global-error");
  node.textContent = error.message || "Something went wrong. Please try again.";
  node.hidden = false;
}
function feedback(form, message, isError = false) {
  const node = $(".form-feedback", form);
  node.textContent = message;
  node.className = `form-feedback ${isError ? "error" : "success"}`;
  node.hidden = false;
}
function clearFeedback(form) {
  const node = $(".form-feedback", form);
  if (node) node.hidden = true;
}
function values(form) {
  return Object.fromEntries(new FormData(form).entries());
}
function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== "" && value != null) search.set(key, value);
  const q = search.toString();
  return q ? `?${q}` : "";
}
function dateFilters(form) {
  const data = values(form);
  if (data.start_date && data.end_date && data.start_date > data.end_date)
    throw new Error("The start date must be on or before the end date.");
  return data;
}
function clearRequest(name) {
  state.requests.delete(name);
  try {
    sessionStorage.removeItem(`canteen:pending:${name}`);
  } catch {}
}
function requestKey(name, payload) {
  const signature = JSON.stringify(payload);
  let existing = state.requests.get(name);
  if (!existing) {
    try {
      existing = JSON.parse(
        sessionStorage.getItem(`canteen:pending:${name}`) || "null",
      );
    } catch {}
  }
  if (existing?.signature === signature && typeof existing.key === "string")
    return existing.key;
  const key = crypto.randomUUID();
  const entry = { signature, key };
  state.requests.set(name, entry);
  try {
    sessionStorage.setItem(`canteen:pending:${name}`, JSON.stringify(entry));
  } catch {}
  return key;
}
function lockForm(form) {
  const controls = [...form.elements];
  const previous = controls.map((control) => control.disabled);
  controls.forEach((control) => {
    control.disabled = true;
  });
  form.setAttribute("aria-busy", "true");
  return () => {
    controls.forEach((control, index) => {
      control.disabled = previous[index];
    });
    form.removeAttribute("aria-busy");
  };
}
function readableError(body, status) {
  const error = body?.error;
  let message = error?.message || body?.detail || `Request failed (${status}).`;
  if (typeof message !== "string")
    message = "Please check the values and try again.";
  if (Array.isArray(error?.details) && error.details.length) {
    const fields = error.details.slice(0, 3).map((item) => {
      if (typeof item === "string") return item;
      const loc = item.loc || item.field || [];
      const field = Array.isArray(loc)
        ? loc.filter((x) => !["body", "query", "path"].includes(x)).join(".")
        : loc;
      return `${field ? `${field}: ` : ""}${item.msg || item.message || "Invalid value"}`;
    });
    message += ` ${fields.join("; ")}`;
  }
  return message;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error(
      "Could not reach the server. If this was a submission, retry with the same values; its request key will be reused to prevent a duplicate.",
    );
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "The server returned an unreadable response. Retry with the same values if you were submitting stock.",
    );
  }
  if (!response.ok) {
    const error = new Error(readableError(body, response.status));
    error.status = response.status;
    error.code = body?.error?.code;
    throw error;
  }
  return body;
}

async function allItems(endpoint) {
  const result = [];
  let page = 1;
  while (true) {
    const data = await api(`${endpoint}${query({ page, page_size: 100 })}`);
    result.push(...data.items);
    if (result.length >= data.total || data.items.length === 0) return result;
    page++;
  }
}
function restorePendingForms() {
  $$("form[data-idempotent]").forEach((form) => {
    try {
      const pending = JSON.parse(
        sessionStorage.getItem(`canteen:pending:${form.id}`) || "null",
      );
      if (!pending?.signature || !pending?.key) return;
      const payload = JSON.parse(pending.signature);
      for (const [name, value] of Object.entries(payload)) {
        const control = form.elements.namedItem(name);
        if (control) control.value = value ?? "";
      }
      state.requests.set(form.id, pending);
      feedback(
        form,
        "An earlier submission may need retrying. Review these values and submit again; the original request key will prevent a duplicate.",
      );
    } catch {}
  });
  updateContext();
}
function updateSelect(select, options, placeholder) {
  const old = select.value;
  select.innerHTML = `<option value="">${esc(placeholder)}</option>${options}`;
  if ([...select.options].some((option) => option.value === old))
    select.value = old;
}
async function refreshChoices() {
  const [ingredients, batches] = await Promise.all([
    allItems("/ingredients"),
    allItems("/batches"),
  ]);
  state.ingredients = ingredients;
  state.batches = batches;
  const ingredientOptions = ingredients
    .map(
      (item) =>
        `<option value="${item.id}">${esc(item.name)} (${esc(unitLabel[item.unit])})</option>`,
    )
    .join("");
  $$(".ingredient-picker").forEach((select) =>
    updateSelect(select, ingredientOptions, "Choose ingredient"),
  );
  $$(".ingredient-filter").forEach((select) =>
    updateSelect(select, ingredientOptions, "All ingredients"),
  );
  $$(".batch-picker").forEach((select) => {
    const forWaste = select.closest("form")?.id === "waste-form";
    const options = batches
      .filter((batch) => !forWaste || positive(batch.balance))
      .map(
        (batch) =>
          `<option value="${batch.id}">${esc(batch.ingredient_name)} · ${esc(batch.reference)} · ${esc(qty(batch.balance))} ${esc(unitLabel[batch.unit])}${batch.is_expired ? " · expired" : ""}</option>`,
      )
      .join("");
    updateSelect(select, options, "Choose batch");
  });
  updateContext();
}
function setQuantityUnit(form, unit, field = "quantity") {
  const input = form.elements.namedItem(field);
  if (!input) return;
  input.step = unit === "piece" ? "1" : "0.001";
  input.min =
    field === "counted_quantity" || field === "low_stock_threshold"
      ? "0"
      : unit === "piece"
        ? "1"
        : "0.001";
}
function updateContext() {
  const consume = $("#consume-form");
  const ingredient = state.ingredients.find(
    (item) => String(item.id) === consume.elements.ingredient_id.value,
  );
  $("#consumption-availability").textContent = ingredient
    ? `${qty(ingredient.usable_stock)} ${unitLabel[ingredient.unit]} usable · expired stock is excluded.`
    : "Select an ingredient to see its usable stock.";
  setQuantityUnit(consume, ingredient?.unit);
  const receipt = $("#receipt-form");
  setQuantityUnit(
    receipt,
    state.ingredients.find(
      (item) => String(item.id) === receipt.elements.ingredient_id.value,
    )?.unit,
  );
  const waste = $("#waste-form");
  const batch = state.batches.find(
    (item) => String(item.id) === waste.elements.batch_id.value,
  );
  $("#wastage-availability").textContent = batch
    ? `${qty(batch.balance)} ${unitLabel[batch.unit]} on hand · ${batch.is_expired ? "expired" : `expires ${dateLabel(batch.expiry_date)}`}.`
    : "Expired batches can be recorded as wastage.";
  setQuantityUnit(waste, batch?.unit);
  const reconciliation = $("#reconcile-form");
  setQuantityUnit(
    reconciliation,
    state.batches.find(
      (item) => String(item.id) === reconciliation.elements.batch_id.value,
    )?.unit,
    "counted_quantity",
  );
}

function pagination(id, data, loader) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  const node = $(`#${id}-pagination`);
  node.innerHTML = `<span>${data.total ? `${(data.page - 1) * data.page_size + 1}–${Math.min(data.page * data.page_size, data.total)} of ${data.total}` : "0 records"}</span><div class="pagination-controls"><button type="button" data-step="-1" ${data.page <= 1 ? "disabled" : ""} aria-label="Previous page">←</button><span>Page ${data.page} of ${totalPages}</span><button type="button" data-step="1" ${data.page >= totalPages ? "disabled" : ""} aria-label="Next page">→</button></div>`;
  $$("button", node).forEach((button) =>
    button.addEventListener("click", () => {
      state.pages[id] += Number(button.dataset.step);
      loader().catch(report);
    }),
  );
}
function loading(target) {
  $(target).innerHTML =
    '<div class="empty-state skeleton" role="status">Loading records…</div>';
}
function table(headers, rows) {
  return `<table><thead><tr>${headers.map((item) => `<th scope="col">${item}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

async function loadDashboard() {
  const filters = dateFilters($("#dashboard-filter"));
  // Filters affect the metrics only. Fetch an unfiltered snapshot for inventory cards.
  const activeFilters = Object.values(filters).some(Boolean);
  const [snapshot, metrics] = await Promise.all([
    api("/dashboard"),
    activeFilters ? api(`/dashboard${query(filters)}`) : Promise.resolve(null),
  ]);
  const data = metrics || snapshot;
  $("#business-date").textContent = `· ${dateLabel(snapshot.business_date)}`;
  const cards = [
    [
      "Ingredients tracked",
      snapshot.ingredient_count,
      "Across your canteen catalogue",
      "▤",
      false,
    ],
    [
      "Batches on record",
      snapshot.batch_count,
      "Traceable from receipt to count",
      "◫",
      false,
    ],
    [
      "Ingredients running low",
      snapshot.low_stock.length,
      "Based on usable stock",
      "↓",
      snapshot.low_stock.length > 0,
    ],
    [
      "Pending counts",
      snapshot.pending_reconciliations,
      "Awaiting reconciliation review",
      "✓",
      false,
    ],
  ];
  $("#overview-stats").innerHTML = cards
    .map(
      ([label, value, note, icon, attention]) =>
        `<div class="stat ${attention ? "attention" : ""}"><span class="mini-icon" aria-hidden="true">${icon}</span><div class="stat-label">${label}</div><div class="stat-value numeric">${value}</div><div class="stat-note">${note}</div></div>`,
    )
    .join("");
  $("#low-stock").innerHTML = snapshot.low_stock.length
    ? snapshot.low_stock
        .map(
          (item) =>
            `<div class="item-row"><div><div class="item-name">${esc(item.name)}</div><div class="item-detail">Threshold: ${esc(qty(item.low_stock_threshold))} ${esc(unitLabel[item.unit])}</div></div><div class="item-value">${amount(item.usable_stock, item.unit)}<div class="item-detail">${badge("Low stock", "warning")}</div></div></div>`,
        )
        .join("")
    : empty(
        "Stock levels look good",
        "No ingredients are below their low-stock threshold.",
      );
  const batchRow = (item, expired) =>
    `<div class="item-row"><div><div class="item-name">${esc(item.ingredient_name)} <span class="muted">· ${esc(item.reference)}</span></div><div class="item-detail">${expired ? "Expired" : "Expires"} ${esc(dateLabel(item.expiry_date))}</div></div><div class="item-value">${amount(item.balance, item.unit)}<div class="item-detail">${badge(expired ? "Unavailable for use" : "Use first", expired ? "danger" : "warning")}</div></div></div>`;
  $("#expiring-stock").innerHTML = snapshot.expiring_batches.length
    ? snapshot.expiring_batches.map((item) => batchRow(item, false)).join("")
    : empty(
        "No imminent expiries",
        "Available batches nearing expiry will appear here.",
        "◷",
      );
  $("#expired-stock").innerHTML = snapshot.expired_batches.length
    ? snapshot.expired_batches.map((item) => batchRow(item, true)).join("")
    : '<p class="form-help">No expired stock with a remaining balance.</p>';
  $("#activity-metrics").innerHTML =
    `<div class="metric-block"><h3>Wastage recorded</h3>${data.wastage_by_unit.length ? data.wastage_by_unit.map((item) => `<div class="metric-unit"><span>${esc(unitLabel[item.unit])}</span><strong>${amount(item.quantity, item.unit)}</strong></div>`).join("") : '<p class="form-help">No wastage in this period.</p>'}</div><div class="metric-block"><h3>Confirmed reconciliation</h3>${data.reconciliation_by_unit.length ? data.reconciliation_by_unit.map((item) => `<div class="metric-unit"><span>${esc(unitLabel[item.unit])} · ${item.count} count${item.count === 1 ? "" : "s"}</span><strong>${amount(item.net_adjustment, item.unit)} net</strong></div><p class="form-help">Total absolute discrepancy: ${esc(qty(item.absolute_discrepancy))} ${esc(unitLabel[item.unit])}</p>`).join("") : '<p class="form-help">No confirmed reconciliations in this period.</p>'}</div>`;
}
async function loadIngredients() {
  loading("#ingredients-table");
  const data = await api(
    `/ingredients${query({ ...values($("#ingredient-filter")), page: state.pages.ingredients, page_size: pageSize })}`,
  );
  state.ingredientRows = data.items;
  $("#ingredients-table").innerHTML = data.items.length
    ? table(
        [
          "Ingredient",
          "Total on hand",
          "Usable",
          "Expired",
          "Threshold",
          "Status",
          "",
        ],
        data.items.map(
          (item) =>
            `<tr><td><strong>${esc(item.name)}</strong><span class="sub">${esc(unitLabel[item.unit])}</span></td><td class="numeric">${amount(item.total_stock, item.unit)}</td><td class="numeric">${amount(item.usable_stock, item.unit)}</td><td class="numeric">${amount(item.expired_stock, item.unit)}</td><td>${amount(item.low_stock_threshold, item.unit)}</td><td>${badge(item.is_low_stock ? "Low stock" : "In range", item.is_low_stock ? "warning" : "")}</td><td><button type="button" class="text-button" data-edit="${item.id}" aria-label="Edit ${esc(item.name)}">Edit</button></td></tr>`,
        ),
      )
    : empty(
        "No ingredients found",
        "Add your first ingredient, or try a different search.",
        "▤",
      );
  $$("[data-edit]").forEach((button) =>
    button.addEventListener("click", () =>
      openIngredient(
        state.ingredientRows.find(
          (item) => item.id === Number(button.dataset.edit),
        ),
      ),
    ),
  );
  pagination("ingredients", data, loadIngredients);
}
async function loadBatches() {
  loading("#batches-table");
  const data = await api(
    `/batches${query({ ...values($("#batch-filter")), page: state.pages.batches, page_size: pageSize })}`,
  );
  $("#batches-table").innerHTML = data.items.length
    ? table(
        ["Batch", "Balance", "Expiry", "Received"],
        data.items.map(
          (item) =>
            `<tr><td><strong>${esc(item.reference)}</strong><span class="sub">${esc(item.ingredient_name)}</span></td><td>${amount(item.balance, item.unit)}</td><td>${esc(dateLabel(item.expiry_date))}<span class="sub">${item.is_expired ? badge("Expired", "danger") : badge(positive(item.balance) ? "Available" : "Depleted", positive(item.balance) ? "" : "neutral")}</span></td><td>${esc(dateLabel(item.received_date))}</td></tr>`,
        ),
      )
    : empty(
        "No batches found",
        "Receive a batch, or change the filters to see existing stock.",
        "◫",
      );
  pagination("batches", data, loadBatches);
}
async function loadReconciliations() {
  loading("#reconciliations-table");
  const data = await api(
    `/reconciliations${query({ ...values($("#reconciliation-filter")), page: state.pages.reconciliations, page_size: pageSize })}`,
  );
  state.reconciliationRows = data.items;
  $("#reconciliations-table").innerHTML = data.items.length
    ? table(
        [
          "Batch / ingredient",
          "Recorded",
          "Counted",
          "Difference",
          "Status",
          "",
        ],
        data.items.map(
          (item) =>
            `<tr><td><strong>${esc(item.reference)}</strong><span class="sub">${esc(item.ingredient_name)} · ${esc(dateLabel(item.created_at))}</span></td><td>${amount(item.expected_quantity, item.unit)}</td><td>${amount(item.counted_quantity, item.unit)}</td><td class="${String(item.discrepancy).startsWith("-") ? "negative" : "positive"}">${amount(item.discrepancy, item.unit)}</td><td>${badge(item.status === "confirmed" ? "Confirmed" : "Pending", item.status === "confirmed" ? "" : "warning")}</td><td><button type="button" class="text-button" data-review="${item.id}">Review</button></td></tr>`,
        ),
      )
    : empty(
        "No counts yet",
        "Create a physical count to review the recorded and actual quantities.",
      );
  $$("[data-review]").forEach((button) =>
    button.addEventListener("click", () => {
      renderReconciliation(
        state.reconciliationRows.find(
          (item) => item.id === Number(button.dataset.review),
        ),
      );
      $("#reconcile-preview").scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    }),
  );
  pagination("reconciliations", data, loadReconciliations);
}
async function loadMovements() {
  const filters = dateFilters($("#history-filter"));
  loading("#movements-table");
  const data = await api(
    `/movements${query({ ...filters, page: state.pages.movements, page_size: pageSize })}`,
  );
  $("#movements-table").innerHTML = data.items.length
    ? table(
        [
          "Date",
          "Ingredient / batch",
          "Movement",
          "Quantity",
          "Note",
          "Reference",
        ],
        data.items.map(
          (item) =>
            `<tr><td>${esc(dateLabel(item.business_date))}</td><td><strong>${esc(item.ingredient_name)}</strong><span class="sub">${esc(item.reference)}</span></td><td>${badge(item.kind, item.kind === "wastage" ? "warning" : item.kind === "adjustment" ? "neutral" : "")}</td><td class="numeric ${String(item.quantity).startsWith("-") ? "negative" : "positive"}">${!String(item.quantity).startsWith("-") && positive(item.quantity) ? "+" : ""}${amount(item.quantity, item.unit)}</td><td><span class="sub">${esc(item.note || "—")}</span></td><td title="${esc(item.operation_id)}"><span class="sub">#${item.id} · ${esc(String(item.operation_id).slice(0, 8))}</span></td></tr>`,
        ),
      )
    : empty(
        "No movements in this view",
        "Record a receipt or change the filters to see stock activity.",
        "◷",
      );
  pagination("movements", data, loadMovements);
}

async function loadPage() {
  $("#global-error").hidden = true;
  if (state.page === "overview") await loadDashboard();
  if (state.page === "stock")
    await Promise.all([loadIngredients(), loadBatches()]);
  if (state.page === "reconcile") await loadReconciliations();
  if (state.page === "history") await loadMovements();
}
function navigate() {
  const hash = location.hash.slice(1);
  state.page = [
    "overview",
    "stock",
    "activity",
    "reconcile",
    "history",
  ].includes(hash)
    ? hash
    : "overview";
  $$(".page").forEach((page) => {
    page.hidden = page.id !== `page-${state.page}`;
  });
  $$("nav a").forEach((link) => {
    const active = link.dataset.page === state.page;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (state.config) {
    Promise.all([refreshChoices(), loadPage()]).catch(report);
  }
}

function openIngredient(ingredient = null) {
  const form = $("#ingredient-form");
  form.reset();
  clearFeedback(form);
  form.elements.id.value = ingredient?.id || "";
  form.elements.name.value = ingredient?.name || "";
  form.elements.unit.value = ingredient?.unit || "kg";
  form.elements.low_stock_threshold.value =
    ingredient?.low_stock_threshold || "0";
  setQuantityUnit(form, form.elements.unit.value, "low_stock_threshold");
  $("#ingredient-dialog-title").textContent = ingredient
    ? "Edit ingredient"
    : "Add ingredient";
  $("#ingredient-dialog").showModal();
}
async function afterMutation() {
  try {
    await refreshChoices();
    await loadPage();
  } catch (error) {
    report(
      new Error(
        `Saved successfully, but refreshing the display failed: ${error.message}`,
      ),
    );
  }
}
function movementResult(result) {
  const node = $("#operation-result");
  node.hidden = false;
  node.innerHTML = `<h2>${result.kind === "wastage" ? "Wastage" : "Consumption"} recorded</h2><p>${amount(result.quantity, result.unit)} · ${esc(dateLabel(result.business_date))} · reference ${esc(String(result.operation_id).slice(0, 8))}</p><ul>${(result.allocations || []).map((item) => `<li>${esc(item.reference)}: ${amount(item.quantity, result.unit)}</li>`).join("")}</ul>`;
}
function renderReconciliation(item) {
  const node = $("#reconcile-preview");
  node.innerHTML = `<div class="panel-head"><div><h2>${item.status === "confirmed" ? "Count confirmed" : "Review this count"}</h2><p>${esc(item.ingredient_name)} · ${esc(item.reference)}</p></div>${badge(item.status === "confirmed" ? "Confirmed" : "Preview", item.status === "confirmed" ? "" : "warning")}</div><div class="panel-body"><div class="reconciliation-summary"><div><span>Recorded</span><strong>${esc(qty(item.expected_quantity))}</strong><span>${esc(unitLabel[item.unit])}</span></div><div><span>Counted</span><strong>${esc(qty(item.counted_quantity))}</strong><span>${esc(unitLabel[item.unit])}</span></div><div><span>Adjustment</span><strong class="${String(item.discrepancy).startsWith("-") ? "negative" : "positive"}">${esc(qty(item.discrepancy))}</strong><span>${esc(unitLabel[item.unit])}</span></div></div><p class="preview-reason"><strong>Reason:</strong> ${esc(item.reason)}</p><div class="form-feedback" role="status" hidden></div>${item.status === "confirmed" ? '<p class="context-note">This count is confirmed. Its adjustment is preserved in the movement history.</p>' : `<div class="preview-actions"><button type="button" class="button primary" id="confirm-count">Confirm adjustment</button><span class="form-help">Stock is rechecked before saving.</span></div>`}</div>`;
  const button = $("#confirm-count");
  if (button)
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Confirming…";
      clearFeedback(node);
      const keyName = `confirm-${item.id}`;
      try {
        const result = await api(`/reconciliations/${item.id}/confirm`, {
          method: "POST",
          body: "{}",
          headers: { "Idempotency-Key": requestKey(keyName, {}) },
        });
        clearRequest(keyName);
        renderReconciliation(result);
        showToast("Reconciliation confirmed.");
        await afterMutation();
      } catch (error) {
        feedback(node, error.message, true);
        button.disabled = false;
        button.textContent = "Confirm adjustment";
      }
    });
}

function wireForm(id, endpoint, buildPayload, onSuccess, successMessage) {
  const form = $(`#${id}`);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFeedback(form);
    const button = $("button[type=submit]", form);
    if (button.disabled) return;
    const original = button.textContent;
    const capturedValues = values(form);
    const unlock = lockForm(form);
    button.textContent = "Saving…";
    try {
      const payload = buildPayload(capturedValues);
      const result = await api(endpoint, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Idempotency-Key": requestKey(id, payload) },
      });
      clearRequest(id);
      form.reset();
      if (id === "receipt-form")
        form.elements.received_date.value = state.config.business_date;
      if (onSuccess) onSuccess(result);
      feedback(form, successMessage);
      showToast(successMessage);
      await afterMutation();
    } catch (error) {
      feedback(form, error.message, true);
    } finally {
      unlock();
      button.textContent = original;
    }
  });
  // An edit means the operator intends a new request. Network retries without edits retain the key.
  form.addEventListener("input", () => {
    clearRequest(id);
    clearFeedback(form);
  });
}

function wireFilters(id, pageName, loader) {
  const form = $(`#${id}`);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (pageName) state.pages[pageName] = 1;
    $("#global-error").hidden = true;
    loader().catch(report);
  });
  form.addEventListener("reset", () => {
    setTimeout(() => {
      if (pageName) state.pages[pageName] = 1;
      loader().catch(report);
    }, 0);
  });
}

async function initialize() {
  navigate();
  window.addEventListener("hashchange", navigate);
  $("#add-ingredient").addEventListener("click", () => openIngredient());
  $("#close-ingredient").addEventListener("click", () =>
    $("#ingredient-dialog").close(),
  );
  $("#ingredient-form").elements.unit.addEventListener("change", (event) =>
    setQuantityUnit(
      $("#ingredient-form"),
      event.target.value,
      "low_stock_threshold",
    ),
  );
  $$(".ingredient-picker,.batch-picker").forEach((select) =>
    select.addEventListener("change", updateContext),
  );
  $("#refresh-overview").addEventListener("click", () =>
    loadDashboard().catch(report),
  );
  wireFilters("dashboard-filter", null, loadDashboard);
  wireFilters("ingredient-filter", "ingredients", loadIngredients);
  wireFilters("batch-filter", "batches", loadBatches);
  wireFilters("reconciliation-filter", "reconciliations", loadReconciliations);
  wireFilters("history-filter", "movements", loadMovements);
  $("#ingredient-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type=submit]", form);
    button.disabled = true;
    clearFeedback(form);
    const fields = values(form);
    const payload = {
      name: fields.name.trim(),
      unit: fields.unit,
      low_stock_threshold: fields.low_stock_threshold,
    };
    try {
      await api(fields.id ? `/ingredients/${fields.id}` : "/ingredients", {
        method: fields.id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      $("#ingredient-dialog").close();
      showToast(fields.id ? "Ingredient updated." : "Ingredient added.");
      await afterMutation();
    } catch (error) {
      feedback(form, error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  wireForm(
    "receipt-form",
    "/batches",
    (data) => ({
      ...data,
      ingredient_id: Number(data.ingredient_id),
      reference: data.reference.trim(),
      note: data.note.trim(),
    }),
    null,
    "Batch received and stock updated.",
  );
  wireForm(
    "consume-form",
    "/consumptions",
    (data) => ({
      ...data,
      ingredient_id: Number(data.ingredient_id),
      note: data.note.trim(),
    }),
    movementResult,
    "Consumption recorded.",
  );
  wireForm(
    "waste-form",
    "/wastages",
    (data) => ({
      ...data,
      batch_id: Number(data.batch_id),
      reason: data.reason.trim(),
    }),
    movementResult,
    "Wastage recorded.",
  );
  wireForm(
    "reconcile-form",
    "/reconciliations",
    (data) => ({
      ...data,
      batch_id: Number(data.batch_id),
      reason: data.reason.trim(),
    }),
    renderReconciliation,
    "Preview created. Review it before confirming.",
  );
  try {
    state.config = await api("/config");
    $("#connection").textContent = "Connected to local workspace";
    $("#connection").className = "connection good";
    $("#timezone-label").textContent = state.config.timezone;
    $("#receipt-form").elements.received_date.value =
      state.config.business_date;
    $("#receipt-form").elements.received_date.max = state.config.business_date;
    await Promise.all([refreshChoices(), loadPage()]);
    restorePendingForms();
  } catch (error) {
    $("#connection").textContent = "Connection unavailable";
    $("#connection").className = "connection bad";
    report(error);
  }
}
initialize();

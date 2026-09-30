/* No framework, telemetry, API keys or third-party assets. Source text is always
   inserted with textContent, never HTML, because job descriptions are untrusted. */
"use strict";
const $ = id => document.getElementById(id);
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
function link(label, url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) throw new Error();
    const anchor = el("a", label);
    anchor.href = parsed.href; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
    return anchor;
  } catch { return el("span", "Link needs review"); }
}
const dateUTC = () => new Date().toISOString().slice(0, 10);
function fresh(job) {
  // Re-evaluate freshness in the browser, even when someone serves an old export.
  const age = (Date.parse(dateUTC()) - Date.parse((job.last_verified || "").slice(0, 10))) / 86400000;
  return Number.isFinite(age) && age >= 0 && age < 7;
}
function studentVisible(job) {
  return job.status === "open" && job.review_state === "approved" && fresh(job) &&
    ["United States of America", "United States", "USA"].includes(job.country) && job.entry_level !== "No" &&
    !(job.date_kind === "closing" && job.date_to_note && job.date_to_note < dateUTC());
}
const isBay = job => /bay area|san francisco|foster city|santa clara|mountain view/i.test(`${job.hub || ""} ${job.location}`);
let board;
function addDetail(list, label, value) {
  list.append(el("dt", label), el("dd", value || "Not stated"));
}
function drawJobs() {
  const query = $("search").value.toLowerCase().trim();
  const rows = board.jobs.filter(job => {
    const text = [job.title, job.company, job.skills, job.degree, job.department, job.location].join(" ").toLowerCase();
    return ($("review").checked || studentVisible(job)) && (!query || text.includes(query)) &&
      (!$("type").value || job.role_type === $("type").value) && (!$("track").value || job.track === $("track").value) &&
      ($("geography").value !== "bay" || isBay(job)) &&
      ($("geography").value !== "remote" || /remote/i.test(`${job.location} ${job.work_mode}`));
  }).sort((a,b) => {
    const d = j => j.date_kind === "closing" && j.date_to_note ? j.date_to_note : "9999";
    return d(a).localeCompare(d(b)) || Number(isBay(b))-Number(isBay(a)) || a.company.localeCompare(b.company);
  });
  $("jobs").replaceChildren();
  $("results").textContent = `${rows.length} opportunities · ${$("review").checked ? "all record states included" : "fresh, approved and open"}`;
  $("empty").hidden = rows.length > 0;
  for (const job of rows) {
    const tr = el("tr"), role = el("td"), details = el("details"), summary = el("summary", job.title);
    details.append(summary);
    const list = el("dl");
    for (const [label, field] of [["Department", "department"], ["Degree / experience", "degree"],
      ["Eligibility", "eligibility"], ["Relocation", "relocation_detail"], ["Duration / term", "duration_text"],
      ["Start / program", "program_term"], ["Skills", "skills"], ["Source notes", "source_notes"], ["Curator notes", "notes"]]) {
      addDetail(list, label, job[field]);
    }
    addDetail(list, "Record key", job.key); details.append(list); role.append(details);
    role.append(el("span", job.role_type || "Type needs review", "pill"));
    if (!studentVisible(job)) role.append(el("span", `${job.status} · ${job.review_state}${!fresh(job) ? " · stale" : ""}`, "pill warning"));
    if (job.date_to_note) {
      const meaning = job.date_kind === "minimum_acceptance" ? "Accepted at least until" : job.date_kind === "closing" ? "Closing date" : "Date to note";
      role.append(el("p", `${meaning}: ${job.date_to_note}`, "date-note"));
    }
    if (job.time_left_to_apply) {
      // Workday's own countdown, only accurate as of last_verified -- never live.
      role.append(el("p", `${job.time_left_to_apply} (as of last check)`, "date-note past"));
    }
    const company = el("td", job.company); company.append(el("span", job.location, "secondary"));
    const track = el("td", job.track || "Unclassified"); track.append(el("span", job.term_bucket || "Duration unknown", "secondary"));
    const checked = el("td", (job.last_verified || "Unverified").slice(0, 10));
    checked.append(el("span", fresh(job) ? "Fresh" : "Recheck needed", "secondary"));
    const apply = el("td"); apply.append(link("View role ↗", job.url));
    tr.append(role, company, track, checked, apply); $("jobs").append(tr);
  }
}
function drawCoverage() {
  for (const company of board.companies) {
    const article = el("article", undefined, "company");
    article.append(el("h2", company.name), el("p", company.rationale));
    article.append(el("span", company.collector.adapter === "workday" ? "Automated collector" : "Manual source", "pill"));
    article.append(el("span", `${company.tracked} tracked roles`, "pill"));
    article.append(el("p", `Opens: ${company.cycle.opening}`, "window"), el("p", `Closes / hires: ${company.cycle.closing}`, "window"));
    article.append(el("p", `Evidence: ${company.cycle.evidence}. Researched ${company.cycle.researched_at}.`, "secondary"));
    const gaps = el("ul");
    for (const issue of company.issues) gaps.append(el("li", issue));
    article.append(gaps);
    if (company.latest_collection?.gaps?.length) {
      const details = el("details"), list = el("ul"); details.append(el("summary", "Collection issues"));
      for (const gap of company.latest_collection.gaps) list.append(el("li", gap));
      details.append(list); article.append(details);
    }
    article.append(link("Official program source ↗", company.cycle.source_url));
    $("companies").append(article);
  }
}
function setTab(name) {
  for (const tab of ["jobs", "coverage"]) {
    const active = tab === name;
    $(`${tab}-tab`).classList.toggle("active", active); $(`${tab}-tab`).setAttribute("aria-pressed", String(active));
    $(`${tab}-panel`).hidden = !active;
  }
}
async function start() {
  const response = await fetch("board.json", {cache:"no-store"});
  if (!response.ok) throw new Error("Data could not be loaded. Run export, then serve.");
  board = await response.json();
  $("snapshot").textContent = `Exported ${board.generated_at.slice(0,10)} · data checked per role`;
  const visible = board.jobs.filter(studentVisible);
  const stats = [[visible.length,"Fresh opportunities"],[visible.filter(isBay).length,"Bay Area options"],
    [board.jobs.filter(j=>j.review_state!=="approved").length,"Awaiting curator review"],
    [`${board.summary.automated_companies} / ${board.summary.total_companies}`,"Companies with automated collection"]];
  for (const [number,label] of stats) {const stat=el("div",undefined,"stat");stat.append(el("strong",String(number)),el("span",label));$("stats").append(stat);}
  for (const [id,field] of [["type","role_type"],["track","track"]]) {
    for (const value of [...new Set(board.jobs.map(j=>j[field]).filter(Boolean))].sort()) {
      const option=el("option",value);option.value=value;$(id).append(option);
    }
  }
  for (const id of ["search","type","track","geography","review"]) $(id).addEventListener("input",drawJobs);
  $("jobs-tab").addEventListener("click",()=>setTab("jobs"));
  $("coverage-tab").addEventListener("click",()=>setTab("coverage"));
  drawJobs(); drawCoverage();
}
start().catch(error=>{$("error").hidden=false;$("error").textContent=error.message;$("snapshot").textContent="Snapshot unavailable";});

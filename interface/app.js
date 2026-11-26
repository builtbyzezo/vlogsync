"use strict";
const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const token = fragment.get("token") ?? location.hash.slice(1);
const labels = {ready:"Ready", scanning:"Scanning", review:"Review ready", processing:"Processing", paused:"Paused",
  cancelled:"Cancelled", complete:"Complete", error:"Needs attention", interrupted:"Needs reconciliation"};
let current;
let sending = false;
let noticeTimer;
let tab = "import";

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6900);
}

async function request(path, data) {
  const response = await fetch("/api/" + path, {
    method: data ? "POST" : "GET",
    headers: {"X-VlogSync":token, ...(data ? {"Content-Type":"application/json"} : {})},
    body: data ? JSON.stringify(data) : undefined
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error);
  return value;
}

function button(label, callback) {
  const item = document.createElement("button");
  item.className = "outline";
  item.textContent = label;
  item.onclick = callback;
  return item;
}

function switchTab(name) {
  tab = name;
  for (const value of ["import","export"]) {
    $(value+"-tab").setAttribute("aria-pressed", String(value===tab));
    $(value+"-page").hidden = value!==tab;
  }
  $("review").hidden = tab!=="import" || current?.activity.status!=="review";
}

async function send(action, data={}) {
  if (sending) return;
  sending = true;
  try {
    const response = await request(action,data);
    await refresh();
    return response;
  } catch (error) {
    notice(error.message);
  } finally {
    sending = false;
  }
}

function item(text) {
  const li = document.createElement("li");
  li.textContent = text;
  return li;
}

function render(s) {
  current = s;
  const a = s.activity;
  const idle = ["ready","review","complete","error","cancelled"].includes(a.status);
  const camera = s.camera ? (a.status==="processing" || a.status==="scanning" ? "Camera in use" : "Camera connected") : "Camera disconnected";
  $("camera").lastChild.textContent = " "+camera;
  $("camera").classList.toggle("connected",s.camera);
  $("last").textContent = "Vlog "+s.numbering.last_assigned.number;
  $("last-date").textContent = s.numbering.last_assigned.recording_date ?? "";
  $("next").textContent = "Vlog "+s.numbering.next_number;
  $("scan").disabled = !s.camera || (!["ready","review","complete","error"].includes(a.status) || a.status==="error" && Boolean(a.run));
  $("edit-number").disabled = (!["ready","complete","error"].includes(a.status) || a.status==="error" && Boolean(a.run));
  $("status").className = "status "+a.status;
  $("status").textContent = labels[a.status] ?? "Check operation";
  $("activity-message").textContent = [
    a.operation, a.vlog ? "Vlog "+a.vlog : "", a.role,
    a.total ? `${a.completed} complete · ${a.remaining} remaining · ${a.failed} failed` : "",
    a.message
  ].filter(Boolean).join(" · ") || "Ready to scan recordings.";
  $("progress").hidden = !a.total || !["processing","paused","complete","error"].includes(a.status);
  $("progress").max = a.total || 1;
  $("progress").value = a.done || 0;
  $("mode").textContent = s.demo ? "Isolated demo · "+s.version : s.version;
  $("review").hidden = tab!=="import" || a.status!=="review";
  if (a.status==="review") {
    $("review-info").textContent = s.projects.length ? "Confirm the dates and outputs below." : "No incomplete exports were found for this preview.";
    if (a.existing_pairs) $("review-info").textContent += " Existing completed pairs skipped: "+a.existing_pairs+".";
    $("review-items").replaceChildren(...s.projects.map(p => item(
      [p.vlog_number ? "Vlog "+p.vlog_number : null,p.date,p.clips+" recordings",
        p.external_count ? p.external_count+" extra" : null,
        "Needs "+p.missing.join(" and "),p.missing.length<2 ? "Existing output will be reused" : null].filter(Boolean).join(" · ")
    )),...s.held.map(d=>item(d+" needs review and is excluded.")));
    $("approve").disabled = !s.projects.length;
  }
  const controls = $("controls");
  const controlKey = JSON.stringify([a.status,a.run,a.retryable,Boolean(s.issue)]);
  if (controls.dataset.key!==controlKey) {
    controls.dataset.key=controlKey;
    controls.replaceChildren();
    if (a.status==="processing" && a.run) {
      controls.append(button("Pause",()=>send("pause")),
        button("Cancel",()=>{ if (confirm("Cancel new submissions? The controller will preserve its current progress.")) send("cancel"); }));
    }
    if (a.status==="paused") controls.append(button("Resume",()=>send("resume")));
    if (a.status==="error") for (const r of a.retryable ?? [])
      controls.append(button(`Retry Failed Exports · ${r.date} ${r.role}`,()=>send("retry",r)));
    if (s.issue) controls.append(
      button("Copy Troubleshooting Prompt for ChatGPT", async()=>{
        await navigator.clipboard.writeText("Help diagnose this VlogSync operation. Preserve original footage and saved work.\n"+JSON.stringify(current.issue,null,2));
        notice("Troubleshooting prompt copied.");
      }),
      button("Open Diagnostic Report",()=>send("diagnostic"))
    );
  }
  for (const b of document.querySelectorAll("[data-folder]")) b.disabled=!s.folders[b.dataset.folder].available;
  $("settings-save").disabled = !idle;
  $("clearance").disabled = !idle;
}
async function refresh() {
  try { render(await request("state")); }
  catch (error) { $("activity-message").textContent=error.message; }
}
for (const name of ["import","export"]) $(name+"-tab").onclick=()=>switchTab(name);
$("scan").onclick = ()=>send("scan");
$("approve").onclick = ()=>{
  const power=current.settings.power ?? "none";
  const data={review:current.review};
  if (!current.demo && power!=="none") {
    if (!confirm(`Allow Windows to ${power} after this verified run?`)) return;
    data.confirm_power=power;
  }
  send("approve", data);
};
for (const b of document.querySelectorAll("[data-folder]")) b.onclick=()=>send("folder",{key:b.dataset.folder});
$("settings-open").onclick=()=>{
  if (!current) return;
  $("power").value = current.settings.power ?? "none";
  $("auto-open").checked = current.settings.auto_open ?? false;
  $("clearance-message").textContent="";
  const details=$("settings-details");
  details.replaceChildren();
  for (const [label,value] of [["Camera","Insta360 · DCIM/Camera01"],["Extra Footage",current.folders.extra.path],
      ["Masters",current.folders.master.path],["Proxies",current.folders.proxy.path],
      ["Edited Videos",current.folders.edited.path],["Available workspace storage",current.free_gib+" GiB"],
      ["Version",current.version]]) {
    const dt=document.createElement("dt"), dd=document.createElement("dd");
    dt.textContent=label;
    dd.textContent=value;
    details.append(dt,dd);
  }
  $("settings").showModal();
};
$("settings-save").onclick=async()=>{
  if (await send("settings",{power:$("power").value,auto_open:$("auto-open").checked})) $("settings").close();
};
$("clearance").onclick=async()=>{
  $("clearance-message").textContent="Checking saved coverage…";
  const response=await send("clearance");
  if (!response) { $("clearance-message").textContent="Clearance is unavailable."; return; }
  const r=response.result;
  $("clearance-message").textContent=[r.safe_to_disconnect?"Safe to disconnect":"Disconnect clearance unavailable",
    r.safe_to_format?"Safe to format":"Format clearance unavailable",...(r.issues ?? [])].join(" · ");
};
$("edit-number").onclick=()=>{
  $("number-form").reset();
  $("number").value = current.numbering.last_assigned.number;
  $("date").value = current.numbering.last_assigned.recording_date ?? "";
  $("number-dialog").showModal();
};
$("number-form").onsubmit=async event=>{
  event.preventDefault();
  if (await send("number",{number:$("number").valueAsNumber,date:$("date").value,confirm:true})) $("number-dialog").close();
};
if (fragment.get("view")==="export") switchTab("export");
refresh();
setInterval(refresh,2200);

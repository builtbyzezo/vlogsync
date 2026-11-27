"use strict";
const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const token = fragment.get("token") ?? location.hash.slice(1);
const labels = {ready:"Ready", scanning:"Scanning", review:"Review ready", processing:"Processing", paused:"Paused",
  cancelled:"Cancelled", complete:"Complete", error:"Needs attention", interrupted:"Needs reconciliation"};
let current;
let sending = false;
let connected = false;
let refreshSequence = 0;
let noticeTimer;
let tab = "import";

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6900);
}

async function request(path, data) {

  let response;
  try {
    response = await fetch("/api/" + path, {
      method: data ? "POST" : "GET",
      headers: {"X-VlogSync":token, ...(data ? {"Content-Type":"application/json"} : {})},
      body: data ? JSON.stringify(data) : undefined,
      signal: AbortSignal.timeout(6000)
    });
  } catch {
    throw Error(data ? "The controller did not confirm the action. Check Activity before trying again." :
      "The local controller could not be reached. Open VlogSync from its shortcut.");
  }
  const value = await response.json().catch(() => null);
  if (response.status===403) throw Error("This panel session is no longer valid. Reopen it from the VlogSync shortcut.");
  if (!value) throw Error("The controller returned an unreadable response. Reopen VlogSync.");
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
  if (sending || !connected) return;
  sending = true;
  try {
    const response = await request(action,data);
    await refresh();
    return response;
  } catch (error) {
    await refresh();
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
  connected = true;
  current = s;
  $("settings-open").disabled = false;
  const a = s.activity;
  const idle = ["ready","review","complete","error","cancelled"].includes(a.status);
  const camera = s.camera ? (a.status==="processing" || a.status==="scanning" ? "Camera in use" : "Camera connected") : "Camera disconnected";
  $("camera").lastChild.textContent = " "+camera;
  $("camera").classList.toggle("connected",s.camera);
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
  $("mode").textContent = s.native_pilot ? "Disposable native pilot · "+s.version : s.demo ? "Isolated demo · "+s.version : s.version;
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
  const sequence = ++refreshSequence;
  try {
    const state = await request("state");
    if (sequence===refreshSequence) render(state);
  } catch (error) {
    if (sequence!==refreshSequence) return;
    connected = false;
    current = undefined;
    $("status").className = "status offline";
    $("status").textContent = "Controller unavailable";
    $("activity-message").textContent = error.message+" A running operation may still be active.";
    $("camera").lastChild.textContent = " Camera status unavailable";
    $("camera").classList.remove("connected");
    $("next").textContent = "—";
    $("progress").hidden = true;
    $("review").hidden = true;
    const controls=$("controls");
    controls.replaceChildren();
    delete controls.dataset.key;
    for (const id of ["scan","approve","edit-number","settings-open","settings-save","clearance"]) $(id).disabled=true;
    for (const button of document.querySelectorAll("[data-folder]")) button.disabled=true;
    for (const id of ["settings","number-dialog"]) if ($(id).open) $(id).close();
  }
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
  function card(label, value, detail, folder) {
    const box=document.createElement(detail ? "details" : "div");
    box.className="setting-card"+(detail ? "" : " setting-row");
    const row=detail ? document.createElement("summary") : box;
    const title=document.createElement("strong"), summary=document.createElement("span");
    title.textContent=label;
    summary.textContent=value;
    row.append(title,summary);
    if (detail) {
      box.append(row);
      const long=document.createElement("p");
      long.className="path muted";
      long.textContent=detail;
      box.append(long);
      if (folder) {
        const open=button("Open folder",()=>send("folder",{key:folder}));
        open.disabled=!current.folders[folder].available;
        box.append(open);
      }
    }
    details.append(box);
  }
  card("Camera",current.camera ? "Connected" : "Disconnected","Insta360 · DCIM/Camera01");
  for (const [name,key] of [["Extra Footage","extra"],["Masters","master"],["Proxies","proxy"],["Edited Videos","edited"]]) {
    const folder=current.folders[key];
    card(name,folder.available ? folder.path.split(/[\\/]/).pop() : "Unavailable",folder.path,key);
  }
  card("Export settings","1080p / 720p","1080p HEVC master and 720p H.264 proxy. Uses the native CapCut queue.");
  card("Workspace storage",current.free_gib+" GiB free");
  $("power-value").textContent=$("power").selectedOptions[0].textContent;
  $("auto-value").textContent=$("auto-open").checked ? "On" : "Off";
  $("version").textContent=current.version;
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
    r.safe_to_format?"Safe to format":"Format clearance unavailable",...(r.safe_to_disconnect || !r.disconnect_status ? [] : [r.disconnect_status]),
    ...(r.issues ?? []),
    ...[["missing","Missing recordings"],["changed","Changed recordings"],["extra","Uncovered recordings"],
        ["missing_strong_content_proof","Missing content evidence"]]
      .filter(([key])=>r[key]?.length).map(([key,label])=>label+": "+r[key].join(", "))
  ].join(" · ");
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
async function poll() {
  await refresh();
  setTimeout(poll,2200);
}
poll();

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
let settingsDirty=false, numberDirty=false, editorMode;
const editorInputs=new Map();
for (const dialog of document.querySelectorAll("dialog")) {
  const outside = event => {
    const box=dialog.getBoundingClientRect();
    return event.target===dialog && (event.clientX<box.left || event.clientX>box.right ||
      event.clientY<box.top || event.clientY>box.bottom);
  };
  let startedOutside=false;
  dialog.addEventListener("pointerdown",event=>{startedOutside=outside(event);});
  dialog.addEventListener("click",event=>{if(startedOutside && outside(event)) dialog.close();});
}
$("settings").addEventListener("input",()=>{settingsDirty=true;});
$("number-form").addEventListener("input",()=>{numberDirty=true;});

function confirmAction(message) {
  const dialog=$("confirm-dialog");
  if (dialog.open) return Promise.resolve(false);
  $("confirm-message").textContent=message;
  dialog.returnValue="cancel";
  return new Promise(resolve=>{
    dialog.addEventListener("close",()=>resolve(dialog.returnValue==="accept"),{once:true});
    dialog.showModal();
  });
}

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
      signal: AbortSignal.timeout(path.startsWith("editor-") ? 600000 : 6000)
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
  for (const id of ["editor-upload","editor-sync","editor-review","auto-prepare"]) $(id).disabled=!idle;
  if (a.editor_operation && ["processing","interrupted","error"].includes(a.status))
    $("editor-review").disabled=false;
  $("auto-prepare").checked = s.settings.auto_prepare === true;
  const retrieval=$("editor-progress");
  retrieval.hidden=editorMode!=="sync" || !a.editor_retrieval || !a.total;
  retrieval.textContent=retrieval.hidden ? "" : [`${a.done ?? 0} of ${a.total} returns checked`,a.message].filter(Boolean).join(" · ");
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
    a.editor_retrieval && a.total ? `${a.done ?? 0}/${a.total} returns checked` : a.total ? `${a.completed} complete · ${a.remaining} remaining · ${a.failed} failed` : "",
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
        button("Cancel",async()=>{ if (await confirmAction("Cancel new submissions? The controller will preserve its current progress.")) send("cancel"); }));
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
    for (const id of ["scan","approve","edit-number","settings-open","settings-save","clearance","editor-upload","editor-sync","editor-review","auto-prepare"]) $(id).disabled=true;
    for (const button of document.querySelectorAll("[data-folder]")) button.disabled=true;
    for (const id of ["confirm-dialog","settings","number-dialog","editor-dialog"]) if ($(id).open) $(id).close();
  }
}
for (const name of ["import","export"]) $(name+"-tab").onclick=()=>switchTab(name);
$("scan").onclick = ()=>send("scan");
$("approve").onclick = async()=>{
  const power=current.settings.power ?? "none";
  const data={review:current.review};
  if (!current.demo && power!=="none") {
    if (!await confirmAction(`Allow Windows to ${power} after this verified run?`)) return;
    data.confirm_power=power;
  }
  send("approve", data);
};
for (const b of document.querySelectorAll("[data-folder]")) b.onclick=()=>send("folder",{key:b.dataset.folder});
$("settings-open").onclick=()=>{
  if (!current) return;
  const space = spaceSettings();
  if (!settingsDirty) {
  $("space-name").value=space.space;
  $("space-upload").value=space.upload;
  $("space-completed").value=space.completed;
  $("space-value").textContent=space.space;
  $("power").value = current.settings.power ?? "none";
  $("auto-open").checked = current.settings.auto_open ?? false;
  }
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
  if (await send("settings",{power:$("power").value,auto_open:$("auto-open").checked,auto_prepare:$("auto-prepare").checked,space:{space:$("space-name").value,upload:$("space-upload").value,completed:$("space-completed").value}})) {settingsDirty=false; $("settings").close();}
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
  if (!numberDirty) {
    $("number-form").reset();
    $("number").value=current.numbering.last_assigned.number;
    $("date").value=current.numbering.last_assigned.recording_date ?? "";
  }
  $("number-dialog").showModal();
};
$("number-form").onsubmit=async event=>{
  event.preventDefault();
  if (await send("number",{number:$("number").valueAsNumber,date:$("date").value,confirm:true})) {numberDirty=false; $("number-dialog").close();}
};
if (fragment.get("view")==="export") switchTab("export");
async function poll() {
  if (!sending) {
    const previous=current?.activity.status;
    await refresh();
    if (previous==="processing" && current?.activity.status!=="processing" &&
        editorMode && $("editor-dialog").open) await editorPanel(editorMode,false);
  }
  setTimeout(poll,2200);
}
poll();


function spaceSettings() {
  return {space:"builtbyzezo",upload:"New Vlogs ⏩",completed:"Edited ✅",...current?.settings.space};
}
$("auto-prepare").onchange=() => send("settings",{power:current.settings.power ?? "none",
  auto_open:current.settings.auto_open ?? false, auto_prepare:$("auto-prepare").checked, space:spaceSettings()});
const editorLabels={unshared:"Unshared · eligible",uncertain:"Previous submission uncertain",prepared:"Prepared locally",
  submitted:"Submission confirmed",review:"Ready for review",approved:"Approved",triggered:"Reconcile native export",
  publishing:"Publication pending",exported:"Delivered"};
const selectedByMode={upload:new Set(),sync:new Set(),review:new Set()};
let historyOpen=false;

async function editorPanel(mode, open=true) {
  const dialog=$("editor-dialog");
  editorMode=mode;
  const response=await send("editor-list");
  if (!response || (!open && !dialog.open) || editorMode!==mode) return;
  const {rows,space}=response.result;
  $("editor-progress").hidden=mode!=="sync" || !current?.activity.editor_retrieval || !current?.activity.total;
  $("editor-heading").textContent={upload:"Upload to Editor",sync:"Sync CapCut Edited Vlogs",review:"Review & Export"}[mode];
  $("editor-destination").textContent=mode==="review" ? "Final delivery · Edited Videos" :
    `${space.space} / ${mode==="upload" ? space.upload : space.completed}`;
  $("editor-guide").textContent={
    upload:"Prepare eligible drafts, then select them in CapCut’s Upload project dialog. Confirm completed uploads here individually.",
    sync:"Use Keep both in CapCut when downloading edits. Select detected local returns to prepare their master-backed copies.",
    review:"Open each working copy, inspect the edit, then close it before approving the saved revision."
  }[mode];
  const list=$("editor-rows"), actions=$("editor-actions"), selected=new Map(), returns=new Map();
  const run=async (name,data)=>{if(await send(name,data) && dialog.open) await editorPanel(mode,false);};
  list.replaceChildren(); actions.replaceChildren();
  const history=document.createElement("details");
  const summary=document.createElement("summary");
  const submitted=rows.filter(row=>row.state==="submitted");
  summary.textContent=`Submission history (${submitted.length})`;
  history.className="setting-card editor-history";
  history.open=historyOpen;
  history.ontoggle=()=>{historyOpen=history.open;};
  history.append(summary);
  const selectRow=(card,row)=>{
    const pick=document.createElement("input");
    pick.type="checkbox";
    pick.setAttribute("aria-label",`Select Vlog ${row.n}`);
    pick.checked=selectedByMode[mode].has(row.n);
    pick.onchange=()=>pick.checked ? selectedByMode[mode].add(row.n) : selectedByMode[mode].delete(row.n);
    selected.set(row.n,pick);
    card.querySelector(".editor-card-head").prepend(pick);
  };
  for (const row of rows) {
    const states={upload:["unshared","uncertain","prepared","submitted"],sync:["submitted","review"],
      review:["review","approved","triggered","publishing","exported"]};
    if (!states[mode].includes(row.state)) continue;
    const card=document.createElement("section"); card.className="setting-card editor-card";
    const head=document.createElement("div"); head.className="editor-card-head";
    const name=document.createElement("strong"); name.textContent=`Vlog ${row.n}`;
    const state=document.createElement("span"); state.textContent=editorLabels[row.state] ?? row.state;
    head.append(name,state); card.append(head);
    const controls=document.createElement("div"); controls.className="controls"; card.append(controls);
    if (mode==="upload") {
      if (row.state==="unshared") selectRow(card,row);
      if (row.state==="uncertain") controls.append(button("Mark unshared",async()=>{
        if(await confirmAction(`I checked Space and Vlog ${row.n} has no previous submission.`))
          run("editor-mark-unshared",{n:row.n,checked_space:true});
      }));
      if (["prepared","uncertain","unshared"].includes(row.state)) controls.append(button(
        row.state==="prepared" ? "Confirm completed upload" : "Record existing submission",async()=>{
          const dest=row.destination ?? space;
          if(await confirmAction(`Confirm Vlog ${row.n} finished uploading to ${dest.space} / ${dest.upload}?`))
            run("editor-submitted",{n:row.n,confirmed:true});
        }));
    }
    if (mode==="sync" && row.state==="submitted") {
      const options=row.local_returns ?? [];
      if (options.length===1) card.append(editorDetail(options[0]));
      if (!options.length) card.append(editorDetail("Waiting for the completed local download."));
      if (options.length) selectRow(card,row);
      if (options.length>1) {
        const picker=document.createElement("select");
        picker.setAttribute("aria-label",`Returned revision for Vlog ${row.n}`);
        picker.append(new Option("Choose the returned revision",""));
        for (const name of options) picker.append(new Option(name,name));
        const key=`return-${row.n}`;
        picker.value=editorInputs.get(key) ?? "";
        picker.onchange=()=>editorInputs.set(key,picker.value);
        controls.append(picker);
        returns.set(row.n,picker);
      }
      if (row.retrieval_error) card.append(editorDetail(row.retrieval_error));
    }
    if (mode==="review" && ["review","approved"].includes(row.state)) {
      if (row.working) card.append(editorDetail(row.working));
      card.append(editorDetail(row.master_verified ? "1080p master verified" : "Master verification unavailable"));
      const title=document.createElement("input");
      const key=`title-${row.n}-${row.revision}`;
      title.value=editorInputs.get(key) ?? row.title ?? `Vlog ${row.n}`;
      title.maxLength=80; title.setAttribute("aria-label",`Final video title for Vlog ${row.n}`);
      controls.append(button("Open in CapCut",()=>send("editor-open",{n:row.n})),title,
        button("Approve revision",async()=>{
          if(await confirmAction("Approve the saved working edit and title for final export?"))
            run("editor-approve",{n:row.n,revision:row.revision,title:title.value});
        }));
      if (row.state==="approved") selectRow(card,row);
      const pick=selected.get(row.n);
      function protectTitle() {
        if (!pick) return;
        pick.disabled=title.value!==row.title;
        if (pick.disabled) {pick.checked=false;selectedByMode[mode].delete(row.n);}
      }
      title.oninput=()=>{editorInputs.set(key,title.value);protectTitle();};
      protectTitle();
    }
    (mode==="upload" && row.state==="submitted" ? history : list).append(card);
  }
  if (!list.childElementCount) list.append(editorDetail("No projects at this step."));
  if (mode==="upload") list.append(history);
  const numbers=()=>[...selected].filter(([,pick])=>pick.checked && !pick.disabled).map(([n])=>n);
  if (mode==="upload" && selected.size) {
    actions.append(button("Select all eligible",()=>{
      for (const [n,pick] of selected) {pick.checked=true;selectedByMode[mode].add(n);}
    }),button("Prepare selected",()=>{
      if(numbers().length) run("editor-prepare",{numbers:numbers()});
    }));
  }
  if (mode==="sync") {
    actions.append(button("Refresh local downloads",()=>editorPanel(mode,false)));
    if (selected.size) actions.append(button("Prepare selected returns",async()=>{
      const picks=numbers().map(n=>({n,returned:returns.get(n)?.value || undefined}));
      if (picks.some(p=>returns.has(p.n) && !p.returned)) return notice("Choose a revision for each ambiguous return.");
      if (picks.length && await send("editor-retrieve-batch",{picks}) && dialog.open)
        $("editor-guide").textContent="Preparing selected returns. Progress is shown in Activity.";
    }));
  }
  if (mode==="review" && selected.size) actions.append(button("Export selected approved",async()=>{
    if(numbers().length && await confirmAction("Start native 1080p HEVC export for the selected approved projects?"))
      send("editor-export",{numbers:numbers()}).then(result=>{if(result)dialog.close();});
  }));
  if (mode==="review" && rows.some(row=>["triggered","publishing"].includes(row.state)))
    actions.append(button("Reconcile saved export",()=>send("editor-resume")));
  if (!dialog.open && open) dialog.showModal();
}
function editorDetail(value) {
  const text=document.createElement("p"); text.className="muted"; text.textContent=value; return text;
}
$("editor-upload").onclick=()=>editorPanel("upload");
$("editor-sync").onclick=()=>editorPanel("sync");
$("editor-review").onclick=()=>editorPanel("review");

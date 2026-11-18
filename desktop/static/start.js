// The app's start page. It runs first-run setup when the app's copy of agentop isn't
// installed, then starts the service. The app replaces it with the service's own page
// once the service answers.
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const $ = (id) => document.getElementById(id);
const ICONS = { pending: "○", running: "…", ok: "✓", warning: "!", missing: "✗", failed: "✗" };

let mode = "start";
let timer = null;

function show(ids, visible) {
  for (const id of ids) $(id).hidden = !visible;
}

function heading(title, detail) {
  $("title").textContent = title;
  $("detail").textContent = detail;
}

function schedule(fn) {
  clearTimeout(timer);
  timer = setTimeout(fn, 300);
}

async function pollStartup() {
  let status;
  try {
    status = await invoke("startup_status");
  } catch (error) {
    status = { state: "failed", message: String(error) };
  }
  if (status.state === "needs_setup") return enterSetup(true);
  if (status.state === "failed") {
    heading("agentop couldn't start", status.message);
    $("log").textContent = status.log ? `Its output is in ${status.log}` : "";
    show(["log"], !!status.log);
    show(["retry", "resetup"], true);
    return;
  }
  // "ready" too: the app is about to open the service's page.
  schedule(pollStartup);
}

async function enterSetup(run) {
  mode = "setup";
  show(["setup"], true);
  show(["log", "retry", "resetup"], false);
  if (run) {
    const current = await invoke("setup_status");
    if (current.phase !== "running" && current.phase !== "done") await invoke("setup_run");
  }
  pollSetup();
}

function rowElement(row) {
  const li = document.createElement("li");
  li.className = `row ${row.state}`;
  const icon = document.createElement("span");
  icon.className = "icon";
  icon.textContent = ICONS[row.state] ?? "○";
  const body = document.createElement("div");
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = row.label;
  body.append(label);
  if (row.detail) {
    const detail = document.createElement("div");
    detail.className = "small";
    detail.textContent = row.detail;
    body.append(detail);
  }
  if (row.fix) {
    const fix = document.createElement("code");
    fix.textContent = row.fix;
    body.append(fix);
  }
  li.append(icon, body);
  return li;
}

async function pollSetup() {
  const status = await invoke("setup_status");
  for (const group of ["tools", "agents", "install"]) {
    $(group).replaceChildren(...status.rows.filter((r) => r.group === group).map(rowElement));
  }
  const blocked = status.phase === "blocked";
  const done = status.phase === "done";
  if (status.phase === "running" || status.phase === "idle") {
    heading("Setting up agentop-desktop", "Checking the tools agentop needs, then installing it in ~/.agentop-desktop.");
  } else if (blocked) {
    heading("Setup needs a hand", "Fix what's marked ✗, then check again. The app doesn't install these tools itself.");
  } else {
    heading("Ready to launch", "agentop is installed. Launch it to start the service and sign in.");
  }
  $("tail").textContent = blocked ? status.tail : "";
  show(["tail"], blocked && !!status.tail);
  $("setup-log").textContent = status.log ? `Setup output: ${status.log}` : "";
  show(["setup-log"], blocked && !!status.log);
  show(["path-fix"], blocked && !!status.path_fix);
  if (status.path_fix) {
    $("path-line").textContent = status.path_fix.line;
    $("add-path").textContent = `Add it to ${status.path_fix.file}`;
  }
  show(["check"], blocked);
  show(["launch"], done);
  if (!blocked && !done) schedule(pollSetup);
}

$("check").addEventListener("click", async () => {
  await invoke("setup_run");
  pollSetup();
});

$("add-path").addEventListener("click", async () => {
  try {
    await invoke("setup_add_path");
  } catch (error) {
    $("detail").textContent = String(error);
  }
  pollSetup();
});

async function startService() {
  mode = "start";
  show(["setup", "launch", "check", "retry", "resetup", "log"], false);
  heading("Starting agentop…", "Starting the service on this computer.");
  await invoke("service_start");
  schedule(pollStartup);
}

$("launch").addEventListener("click", startService);
$("retry").addEventListener("click", startService);
$("resetup").addEventListener("click", async () => {
  await invoke("setup_run");
  enterSetup(false);
});

pollStartup();

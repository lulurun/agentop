// The app's start page. The app replaces it with the service's own page once the
// service answers, so this only ever says that it is starting, or why it failed.
const invoke = (command) => window.__TAURI_INTERNALS__.invoke(command);
const $ = (id) => document.getElementById(id);

async function poll() {
  let status;
  try {
    status = await invoke("startup_status");
  } catch (error) {
    status = { state: "failed", message: String(error) };
  }
  if (status.state === "failed") {
    $("title").textContent = "agentop couldn't start";
    $("detail").textContent = status.message;
    $("log").hidden = !status.log;
    $("log").textContent = status.log ? `Its output is in ${status.log}` : "";
    $("actions").hidden = false;
    $("help").hidden = !/isn't installed/.test(status.message);
    return;
  }
  // "ready" too: the app is about to open the service's page.
  setTimeout(poll, 300);
}

$("retry").addEventListener("click", async () => {
  $("title").textContent = "Starting agentop…";
  $("detail").textContent = "Starting the service on this computer.";
  $("actions").hidden = true;
  $("log").hidden = true;
  $("help").hidden = true;
  await invoke("service_start");
  setTimeout(poll, 300);
});

poll();

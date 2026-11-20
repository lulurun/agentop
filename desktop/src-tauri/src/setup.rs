//! First-run setup: checks the tools agentop needs, then installs the app's own copy in
//! `~/.agentop-desktop` (a checkout at the newest v* tag, a venv, the UI build) and links
//! it into `~/.local/bin`. The app installs no tools itself. An update checks the same
//! tools, then has the running service update itself (`agentop service update`).

use serde::Serialize;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const REPO_URL: &str = "https://github.com/lulurun/agentop.git";
const PROBE_TIMEOUT: Duration = Duration::from_secs(20);
const VERSION_TIMEOUT: Duration = Duration::from_secs(10);
const TAIL_LINES: usize = 20;
const AGENT_TOOLS: [&str; 3] = ["claude", "codex", "agy"];
const PATH_LINE: &str = "export PATH=\"$HOME/.local/bin:$PATH\"";
/// The steps update.sh reports, in order, each with its row.
const UPDATE_STEPS: [(&str, &str); 6] = [
    ("checking", "Find the newest release"),
    ("pulling", "Download it"),
    ("installing", "Install it in the venv"),
    ("building", "Build the UI"),
    ("restarting", "Restart the service"),
    ("verifying", "Check the service answers"),
];

#[derive(Serialize, Clone, Copy, PartialEq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Pending,
    Running,
    Ok,
    /// Optional and absent: shown, but setup goes on.
    Warning,
    Missing,
    Failed,
}

#[derive(Serialize, Clone, Debug)]
pub struct Row {
    pub id: String,
    pub label: String,
    pub group: &'static str,
    pub state: State,
    pub detail: String,
    pub fix: Option<String>,
}

#[derive(Serialize, Clone, Copy, PartialEq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Idle,
    Running,
    /// Stopped at a row that isn't ok: fix it, then check again.
    Blocked,
    Done,
}

#[derive(Serialize, Clone, Debug)]
pub struct PathFix {
    pub file: String,
    pub line: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct Status {
    pub phase: Phase,
    pub rows: Vec<Row>,
    pub log: Option<String>,
    /// The end of the failed step's output.
    pub tail: String,
    /// Offered when `~/.local/bin` isn't on the login shell's PATH.
    pub path_fix: Option<PathFix>,
}

pub struct Setup {
    status: Mutex<Status>,
}

pub fn app_dir() -> Option<PathBuf> {
    home().map(|home| home.join(".agentop-desktop"))
}

fn home() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

pub fn agentop_bin() -> Option<PathBuf> {
    app_dir().map(|dir| dir.join("venv/bin/agentop"))
}

fn marker() -> Option<PathBuf> {
    app_dir().map(|dir| dir.join("setup-done"))
}

fn log_path() -> Option<PathBuf> {
    app_dir().map(|dir| dir.join("setup.log"))
}

/// Set up before, and the copy is still there.
pub fn installed() -> bool {
    marker().is_some_and(|m| m.exists()) && agentop_bin().is_some_and(|b| b.exists())
}

impl Setup {
    pub fn new() -> Self {
        Setup {
            status: Mutex::new(Status { phase: Phase::Idle, rows: Vec::new(), log: None, tail: String::new(), path_fix: None }),
        }
    }

    pub fn status(&self) -> Status {
        self.status.lock().unwrap().clone()
    }

    /// Check and install from the top, in the background. Does nothing while a run is going.
    pub fn run(self: &Arc<Self>) {
        if !self.begin(initial_rows(), log_path()) {
            return;
        }
        let setup = self.clone();
        std::thread::spawn(move || {
            let phase = setup.pipeline();
            if phase == Phase::Done {
                if let Some(marker) = marker() {
                    let _ = std::fs::write(marker, "");
                }
            }
            setup.status.lock().unwrap().phase = phase;
        });
    }

    /// Check the tools, then update the running service to the newest release, in the background.
    pub fn run_update(self: &Arc<Self>) {
        if !self.begin(update_rows(), update_log_path()) {
            return;
        }
        let setup = self.clone();
        std::thread::spawn(move || {
            let phase = setup.update_pipeline();
            setup.status.lock().unwrap().phase = phase;
        });
    }

    fn begin(&self, rows: Vec<Row>, log: Option<PathBuf>) -> bool {
        let mut status = self.status.lock().unwrap();
        if status.phase == Phase::Running {
            return false;
        }
        *status = Status {
            phase: Phase::Running,
            rows,
            log: log.map(|p| p.display().to_string()),
            tail: String::new(),
            path_fix: None,
        };
        true
    }

    fn row(&self, id: &str, state: State, detail: impl Into<String>, fix: Option<String>) {
        let mut status = self.status.lock().unwrap();
        if let Some(row) = status.rows.iter_mut().find(|r| r.id == id) {
            row.state = state;
            row.detail = detail.into();
            row.fix = fix;
        }
    }

    fn fail(&self, id: &str, detail: impl Into<String>, fix: Option<String>) -> Phase {
        self.row(id, State::Failed, detail, fix);
        self.status.lock().unwrap().tail = log_tail();
        Phase::Blocked
    }

    fn pipeline(&self) -> Phase {
        let (Some(home), Some(dir)) = (home(), app_dir()) else {
            return self.fail("python", "No home folder (HOME isn't set).", None);
        };
        let _ = std::fs::create_dir_all(&dir);
        let shell_path = match login_path() {
            Ok(path) => path,
            Err(message) => return self.fail("python", message, None),
        };
        let env = Env { path: shell_path };
        let Some(python) = self.check_tools(&env) else { return Phase::Blocked };

        let repo = dir.join("agentop");
        let venv = dir.join("venv");
        if let Err(message) = self.download(&env, &repo) {
            return self.fail("download", message, None);
        }
        self.row("install", State::Running, "", None);
        if !venv.join("bin/python").exists() {
            if let Err(message) = env.run(Command::new(&python).arg("-m").arg("venv").arg(&venv)) {
                return self.fail("install", message, None);
            }
        }
        let pip = env.run(Command::new(venv.join("bin/python")).args(["-m", "pip", "install", "--upgrade", "-e"]).arg(&repo));
        if let Err(message) = pip {
            return self.fail("install", message, None);
        }
        self.row("install", State::Ok, format!("In {}", venv.display()), None);

        self.row("ui", State::Running, "", None);
        let ui = repo.join("ui");
        for args in [["ci", "--no-audit"], ["run", "build"]] {
            if let Err(message) = env.run(env.command("npm").args(args).current_dir(&ui)) {
                return self.fail("ui", message, None);
            }
        }
        self.row("ui", State::Ok, "Built", None);

        self.check_path(&env, &home, &venv.join("bin/agentop"))
    }

    /// Python, if it and every other tool is there.
    fn check_tools(&self, env: &Env) -> Option<PathBuf> {
        let python = self.check_python(env);
        let mut ready = python.is_some();
        ready &= self.check_node(env);
        for (id, args) in [("npm", ["--version"]), ("git", ["--version"]), ("tmux", ["-V"])] {
            ready &= self.check_tool(env, id, &args);
        }
        ready &= self.check_agents(env);
        python.filter(|_| ready)
    }

    fn update_pipeline(&self) -> Phase {
        let shell_path = match login_path() {
            Ok(path) => path,
            Err(message) => return self.fail("python", message, None),
        };
        let env = Env { path: shell_path };
        if self.check_tools(&env).is_none() {
            return Phase::Blocked;
        }
        let Some(bin) = agentop_bin().filter(|b| b.exists()) else {
            return self.fail("checking", "The app's copy of agentop is missing. Run setup again.", None);
        };
        let before = read_update_status();
        let mut command = Command::new(&bin);
        command.args(["service", "update"]).env("PATH", &env.path).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        crate::service::detach(&mut command);
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(e) => return self.fail("checking", format!("Couldn't run agentop service update: {e}"), None),
        };
        self.row("checking", State::Running, "", None);
        let code = loop {
            if let Some(run) = read_update_status().filter(|run| Some(run) != before.as_ref()) {
                self.show_update_step(&run);
            }
            match child.try_wait() {
                Ok(Some(status)) => break status.code(),
                Ok(None) => std::thread::sleep(Duration::from_millis(300)),
                Err(e) => return self.fail("checking", e.to_string(), None),
            }
        };
        let run = read_update_status().filter(|run| Some(run) != before.as_ref());
        let state = run.as_ref().and_then(|r| r.get("state")).and_then(|s| s.as_str()).unwrap_or("");
        let message = run.as_ref().and_then(|r| r.get("message")).and_then(|m| m.as_str()).unwrap_or("").to_string();
        if code == Some(0) && state == "done" {
            for (id, _) in UPDATE_STEPS {
                self.row(id, State::Ok, "", None);
            }
            let detail = if message.is_empty() { format!("Now {}", installed_version().unwrap_or_default()) } else { message };
            self.row("verifying", State::Ok, detail, None);
            return Phase::Done;
        }
        let at = self.running_step().unwrap_or("checking");
        let why = if message.is_empty() { "The update failed.".to_string() } else { message };
        self.row(at, State::Failed, why, None);
        self.status.lock().unwrap().tail = tail_of(update_log_path());
        Phase::Blocked
    }

    /// Steps before the current one are done.
    fn show_update_step(&self, run: &serde_json::Value) {
        let step = run.get("step").and_then(|s| s.as_str()).unwrap_or("");
        let step = if step == "rolling_back" { "verifying" } else { step };
        let Some(at) = UPDATE_STEPS.iter().position(|(id, _)| *id == step) else { return };
        for (i, (id, _)) in UPDATE_STEPS.iter().enumerate() {
            let state = if i < at { State::Ok } else if i == at { State::Running } else { State::Pending };
            let detail = if run.get("step").and_then(|s| s.as_str()) == Some("rolling_back") && i == at {
                "Going back to the previous version"
            } else {
                ""
            };
            self.row(id, state, detail, None);
        }
    }

    fn running_step(&self) -> Option<&'static str> {
        let status = self.status.lock().unwrap();
        UPDATE_STEPS.iter().map(|(id, _)| *id).find(|id| status.rows.iter().any(|r| r.id == *id && r.state == State::Running))
    }

    fn check_python(&self, env: &Env) -> Option<PathBuf> {
        let mut too_old = None;
        for name in ["python3.14", "python3.13", "python3"] {
            let Some(path) = env.which(name) else { continue };
            let script = "import sys; print('%d.%d.%d' % sys.version_info[:3])";
            let Some(version) = env.version(Command::new(&path).args(["-c", script])) else { continue };
            if parse_version(&version) >= (3, 13, 0) {
                self.row("python", State::Ok, format!("{version} at {}", path.display()), None);
                return Some(path);
            }
            too_old.get_or_insert(format!("{version} at {}, too old", path.display()));
        }
        let detail = too_old.unwrap_or_else(|| "Not found".into());
        self.row("python", State::Missing, detail, Some(install_hint("python@3.13", "python3.13")));
        None
    }

    fn check_node(&self, env: &Env) -> bool {
        let found = env.which("node").and_then(|path| Some((env.version(Command::new(&path).arg("--version"))?, path)));
        let Some((version, path)) = found else {
            self.row("node", State::Missing, "Not found", Some(install_hint("node", "nodejs")));
            return false;
        };
        let v = parse_version(version.trim_start_matches('v'));
        if (v >= (20, 19, 0) && v < (21, 0, 0)) || v >= (22, 12, 0) {
            self.row("node", State::Ok, format!("{version} at {}", path.display()), None);
            return true;
        }
        let detail = format!("{version} at {}, too old: needs 20.19+ or 22.12+", path.display());
        self.row("node", State::Missing, detail, Some(install_hint("node", "nodejs")));
        false
    }

    fn check_tool(&self, env: &Env, name: &str, args: &[&str]) -> bool {
        let version = env.which(name).and_then(|path| Some((env.version(Command::new(&path).args(args))?, path)));
        match version {
            Some((version, path)) => {
                self.row(name, State::Ok, format!("{version} at {}", path.display()), None);
                true
            }
            None => {
                let package = if name == "npm" { "node" } else { name };
                self.row(name, State::Missing, "Not found", Some(install_hint(package, package)));
                false
            }
        }
    }

    /// Each is optional, but `agentop serve` needs at least one.
    fn check_agents(&self, env: &Env) -> bool {
        let found: Vec<(&str, Option<PathBuf>)> = AGENT_TOOLS.iter().map(|name| (*name, env.which(name))).collect();
        let any = found.iter().any(|(_, path)| path.is_some());
        for (name, path) in found {
            match path {
                Some(path) => self.row(name, State::Ok, format!("At {}", path.display()), None),
                None if any => self.row(name, State::Warning, "Not installed (optional)", None),
                None => self.row(name, State::Missing, "Not installed", Some("Install at least one agent tool: claude, codex, or agy.".into())),
            }
        }
        any
    }

    /// Clone at the newest release tag. A checkout that exists is left as it is: the
    /// service's own Update moves it on.
    fn download(&self, env: &Env, repo: &Path) -> Result<(), String> {
        self.row("download", State::Running, "", None);
        if repo.join(".git").exists() {
            let head = env.version(env.command("git").args(["describe", "--tags", "--always"]).current_dir(repo));
            self.row("download", State::Ok, format!("{} at {}", head.unwrap_or_default(), repo.display()), None);
            return Ok(());
        }
        if repo.exists() {
            // A clone that was cut off.
            std::fs::remove_dir_all(repo).map_err(|e| format!("Can't remove {}: {e}", repo.display()))?;
        }
        env.run(env.command("git").args(["clone", "--quiet", REPO_URL]).arg(repo))?;
        let tags = env.version(env.command("git").args(["tag", "-l", "v*", "--sort=-v:refname"]).current_dir(repo)).unwrap_or_default();
        let tag = tags.lines().find(|t| is_release_tag(t)).ok_or("The repository has no release tag (vX.Y.Z).")?.to_string();
        env.run(env.command("git").args(["checkout", "--quiet", &tag]).current_dir(repo))?;
        self.row("download", State::Ok, format!("{tag} at {}", repo.display()), None);
        Ok(())
    }

    /// Link `~/.local/bin/agentop` to the app's copy, then check the login shell finds it,
    /// as agent sessions in tmux do (their shell is the login shell too).
    fn check_path(&self, env: &Env, home: &Path, bin: &Path) -> Phase {
        self.row("path", State::Running, "", None);
        let link = home.join(".local/bin/agentop");
        if let Err(message) = make_link(&link, bin) {
            return self.fail("path", message, None);
        }
        let bin_dir = home.join(".local/bin");
        if !std::env::split_paths(&env.path).any(|dir| dir == bin_dir) {
            let fix = path_fix(home);
            self.row(
                "path",
                State::Failed,
                "~/.local/bin isn't on your login shell's PATH, so agents can't run agentop.",
                Some(format!("Add this line to {}:", fix.file)),
            );
            self.status.lock().unwrap().path_fix = Some(fix);
            return Phase::Blocked;
        }
        let resolved = env.which("agentop").and_then(|p| p.canonicalize().ok());
        if resolved != bin.canonicalize().ok() {
            let other = env.which("agentop").map(|p| p.display().to_string()).unwrap_or_default();
            return self.fail(
                "path",
                format!("Another agentop comes first on PATH: {other}"),
                Some("Remove it, or put ~/.local/bin earlier on PATH.".into()),
            );
        }
        self.row("path", State::Ok, format!("{} → {}", link.display(), bin.display()), None);
        Phase::Done
    }

    /// Add `~/.local/bin` to PATH in the login shell's profile, then check again.
    pub fn add_path(self: &Arc<Self>) -> Result<(), String> {
        let home = home().ok_or("No home folder")?;
        let fix = path_fix(&home);
        let file = PathBuf::from(fix.file.replacen('~', &home.display().to_string(), 1));
        let current = std::fs::read_to_string(&file).unwrap_or_default();
        if !current.contains(&fix.line) {
            let gap = if current.is_empty() || current.ends_with('\n') { "" } else { "\n" };
            let text = format!("{current}{gap}\n# Added by agentop-desktop\n{}\n", fix.line);
            if let Some(dir) = file.parent() {
                std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
            }
            std::fs::write(&file, text).map_err(|e| format!("Can't write {}: {e}", file.display()))?;
        }
        self.run();
        Ok(())
    }
}

fn initial_rows() -> Vec<Row> {
    let row = |id: &str, label: &str, group| Row {
        id: id.into(),
        label: label.into(),
        group,
        state: State::Pending,
        detail: String::new(),
        fix: None,
    };
    let mut rows = vec![
        row("python", "Python 3.13+", "tools"),
        row("node", "Node.js 20.19+ or 22.12+", "tools"),
        row("npm", "npm", "tools"),
        row("git", "git", "tools"),
        row("tmux", "tmux", "tools"),
    ];
    rows.extend(AGENT_TOOLS.iter().map(|name| row(name, name, "agents")));
    rows.extend([
        row("download", "Download agentop", "install"),
        row("install", "Install it in a venv", "install"),
        row("ui", "Build the UI", "install"),
        row("path", "agentop on PATH", "install"),
    ]);
    rows
}

fn update_rows() -> Vec<Row> {
    let mut rows: Vec<Row> = initial_rows().into_iter().filter(|r| r.group != "install").collect();
    rows.extend(UPDATE_STEPS.iter().map(|(id, label)| Row {
        id: (*id).into(),
        label: (*label).into(),
        group: "install",
        state: State::Pending,
        detail: String::new(),
        fix: None,
    }));
    rows
}

/// The version of the app's copy of agentop, from its pyproject.toml.
pub fn installed_version() -> Option<String> {
    let text = std::fs::read_to_string(app_dir()?.join("agentop/pyproject.toml")).ok()?;
    version_in_pyproject(&text)
}

fn version_in_pyproject(text: &str) -> Option<String> {
    text.lines()
        .find_map(|line| line.strip_prefix("version = \""))
        .and_then(|rest| rest.strip_suffix('"'))
        .map(str::to_string)
}

/// Whether the app's copy of agentop is older than this app, as after the app is replaced by a newer one.
pub fn copy_is_older(app_version: &str) -> bool {
    installed_version().is_some_and(|copy| parse_version(&copy) < parse_version(app_version))
}

fn read_update_status() -> Option<serde_json::Value> {
    let path = crate::service::data_dir()?.join("state/update-status.json");
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

fn update_log_path() -> Option<PathBuf> {
    crate::service::data_dir().map(|dir| dir.join("logs/update.log"))
}

fn install_hint(brew: &str, other: &str) -> String {
    if cfg!(target_os = "macos") {
        format!("brew install {brew}")
    } else {
        format!("Install {other} with your package manager.")
    }
}

/// The profile a login shell reads, and the line that puts `~/.local/bin` on PATH.
fn path_fix(home: &Path) -> PathFix {
    let shell = std::env::var("SHELL").unwrap_or_default();
    let name = Path::new(&shell).file_name().and_then(|n| n.to_str()).unwrap_or("");
    let (file, line) = match name {
        "zsh" => ("~/.zprofile", PATH_LINE),
        "bash" if home.join(".bash_profile").exists() => ("~/.bash_profile", PATH_LINE),
        "fish" => ("~/.config/fish/config.fish", "fish_add_path ~/.local/bin"),
        _ => ("~/.profile", PATH_LINE),
    };
    PathFix { file: file.into(), line: line.into() }
}

/// A symlink is replaced; a real file there is someone else's agentop, and is left alone.
fn make_link(link: &Path, target: &Path) -> Result<(), String> {
    if let Ok(meta) = std::fs::symlink_metadata(link) {
        if !meta.file_type().is_symlink() {
            return Err(format!("{} already exists and isn't a link. Remove it to use the app's copy.", link.display()));
        }
        if std::fs::read_link(link).ok().as_deref() == Some(target) {
            return Ok(());
        }
        std::fs::remove_file(link).map_err(|e| format!("Can't replace {}: {e}", link.display()))?;
    }
    if let Some(dir) = link.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("Can't create {}: {e}", dir.display()))?;
    }
    #[cfg(unix)]
    return std::os::unix::fs::symlink(target, link).map_err(|e| format!("Can't link {}: {e}", link.display()));
    #[cfg(not(unix))]
    return Err("Linking agentop isn't supported on this system.".into());
}

/// The PATH a terminal gets: an app opened from the desktop gets a short one.
fn login_path() -> Result<String, String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into());
    let mut command = Command::new(&shell);
    command.args(["-ilc", "env"]);
    crate::service::detach(&mut command);
    let output = output_within(&mut command, PROBE_TIMEOUT).ok_or_else(|| format!("Your login shell ({shell}) didn't start."))?;
    output
        .lines()
        .filter_map(|line| line.strip_prefix("PATH="))
        .last()
        .map(str::to_string)
        .ok_or_else(|| format!("Couldn't read PATH from your login shell ({shell})."))
}

struct Env {
    path: String,
}

impl Env {
    /// A command found on the login shell's PATH; a missing one fails when run.
    fn command(&self, name: &str) -> Command {
        Command::new(self.which(name).unwrap_or_else(|| PathBuf::from(name)))
    }

    fn which(&self, name: &str) -> Option<PathBuf> {
        std::env::split_paths(&self.path).map(|dir| dir.join(name)).find(|p| is_executable(p))
    }

    /// The first line a command prints, if it succeeds.
    fn version(&self, command: &mut Command) -> Option<String> {
        command.env("PATH", &self.path);
        let output = output_within(command, VERSION_TIMEOUT)?;
        Some(output.lines().next().unwrap_or("").trim().to_string())
    }

    /// Run a step, its output appended to setup.log.
    fn run(&self, command: &mut Command) -> Result<(), String> {
        let log = log_path().ok_or("No home folder")?;
        let open = || std::fs::OpenOptions::new().create(true).append(true).open(&log).map_err(|e| e.to_string());
        let (out, err) = (open()?, open()?);
        let shown = format!("{command:?}");
        {
            use std::io::Write;
            let _ = writeln!(open()?, "\n$ {shown}");
        }
        let status = command
            .env("PATH", &self.path)
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            .status()
            .map_err(|e| format!("Couldn't run {shown}: {e}"))?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("{shown} failed ({status})."))
        }
    }
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata().is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    path.is_file()
}

/// Stdout of a command that succeeds within the timeout. A slow one is killed.
fn output_within(command: &mut Command, timeout: Duration) -> Option<String> {
    let mut child = command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let mut stdout = child.stdout.take()?;
    // Read on another thread, so that a full pipe can't block the child.
    let reader = std::thread::spawn(move || {
        let mut text = String::new();
        let _ = stdout.read_to_string(&mut text);
        text
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let text = reader.join().ok()?;
    status.success().then_some(text)
}

fn log_tail() -> String {
    tail_of(log_path())
}

fn tail_of(path: Option<PathBuf>) -> String {
    let text = path.and_then(|p| std::fs::read_to_string(p).ok()).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(TAIL_LINES)..].join("\n")
}

fn parse_version(text: &str) -> (u32, u32, u32) {
    let mut parts = text.split(|c: char| !c.is_ascii_digit()).filter(|p| !p.is_empty()).map(|p| p.parse().unwrap_or(0));
    (parts.next().unwrap_or(0), parts.next().unwrap_or(0), parts.next().unwrap_or(0))
}

fn is_release_tag(tag: &str) -> bool {
    let Some(rest) = tag.strip_prefix('v') else { return false };
    let parts: Vec<&str> = rest.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_version_is_read_from_pyproject() {
        let text = "[project]\nname = \"agentop\"\nversion = \"1.3.0\"\n[tool.x]\nversion = \"9\"\n";
        assert_eq!(version_in_pyproject(text).as_deref(), Some("1.3.0"));
        assert_eq!(version_in_pyproject("name = \"x\"\n"), None);
    }

    #[test]
    fn update_rows_replace_the_install_rows() {
        let rows = update_rows();
        assert!(rows.iter().any(|r| r.id == "git"));
        assert!(!rows.iter().any(|r| r.id == "download"));
        assert_eq!(rows.iter().filter(|r| r.group == "install").count(), UPDATE_STEPS.len());
    }

    #[test]
    fn versions_parse_loosely() {
        assert_eq!(parse_version("3.13.1"), (3, 13, 1));
        assert_eq!(parse_version("22.12.0"), (22, 12, 0));
        assert_eq!(parse_version("tmux 3.5a"), (3, 5, 0));
        assert_eq!(parse_version(""), (0, 0, 0));
    }

    #[test]
    fn only_plain_version_tags_are_releases() {
        assert!(is_release_tag("v1.1.2"));
        assert!(!is_release_tag("v1"));
        assert!(!is_release_tag("v2.0.0-rc1"));
        assert!(!is_release_tag("v0.1.0-dialogue-milestone"));
        assert!(!is_release_tag("desktop-v1.0.0"));
    }

    #[test]
    fn a_link_replaces_a_link_but_not_a_file() {
        let dir = std::env::temp_dir().join(format!("agentop-setup-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (link, a, b) = (dir.join("bin/agentop"), dir.join("a"), dir.join("b"));
        make_link(&link, &a).unwrap();
        make_link(&link, &b).unwrap();
        assert_eq!(std::fs::read_link(&link).unwrap(), b);
        std::fs::remove_file(&link).unwrap();
        std::fs::write(&link, "").unwrap();
        assert!(make_link(&link, &a).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn which_finds_executables_only() {
        let dir = std::env::temp_dir().join(format!("agentop-which-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let tool = dir.join("tool");
        std::fs::write(&tool, "").unwrap();
        let env = Env { path: dir.display().to_string() };
        assert!(env.which("tool").is_none());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(env.which("tool"), Some(tool));
        }
        std::fs::remove_dir_all(&dir).unwrap();
    }
}

#[cfg(test)]
mod end_to_end {
    use super::*;

    /// Clones from GitHub and installs for real. Run with HOME and SHELL pointing at a
    /// scratch home and a stand-in login shell: `cargo test setup_installs -- --ignored`.
    #[test]
    #[ignore]
    fn setup_installs() {
        let setup = Arc::new(Setup::new());
        setup.run();
        while setup.status().phase == Phase::Running {
            std::thread::sleep(Duration::from_millis(500));
        }
        let status = setup.status();
        for row in &status.rows {
            eprintln!("{:?} {} {} {:?}", row.state, row.label, row.detail, row.fix);
        }
        eprintln!("{}", status.tail);
        assert_eq!(status.phase, Phase::Done);
        assert!(installed());
    }
}

//! This computer's `agentop serve`, run by the app for as long as it is open.
//!
//! On open, the app uses a service that already answers on the configured port, such as
//! one run by systemd or started by hand, and leaves it running on close. Otherwise it
//! starts `agentop serve --host 127.0.0.1` itself and stops it gracefully on close. tmux
//! owns the sessions, so they keep running either way.
//!
//! The PID of a service the app started is kept in `state/desktop-serve.pid`, so that a
//! copy of the app that replaced itself in an update, or that crashed, still stops it.

use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const DEFAULT_PORT: u16 = 9775;
const START_TIMEOUT: Duration = Duration::from_secs(30);
/// The service gives open terminals 5 seconds to close, then stops.
const STOP_TIMEOUT: Duration = Duration::from_secs(10);

pub fn data_dir() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("AGENTOP_HOME") {
        return Some(PathBuf::from(path));
    }
    home_dir().map(|home| home.join(".agentop"))
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

#[derive(Deserialize, Default)]
pub struct ConfigFile {
    pub port: Option<u16>,
    pub tls_cert: Option<String>,
}

pub fn read_config() -> ConfigFile {
    data_dir()
        .and_then(|dir| std::fs::read_to_string(dir.join("config.toml")).ok())
        .and_then(|text| toml::from_str(&text).ok())
        .unwrap_or_default()
}

/// Where this computer's service answers. Always loopback: the app starts it on
/// 127.0.0.1, whatever `host` the config gives for `agentop serve` run by hand.
pub fn local_url(config: &ConfigFile) -> String {
    let scheme = if config.tls_cert.is_some() { "https" } else { "http" };
    format!("{scheme}://127.0.0.1:{}", config.port.unwrap_or(DEFAULT_PORT))
}

pub fn log_path() -> Option<PathBuf> {
    data_dir().map(|dir| dir.join("logs").join("desktop-serve.log"))
}

fn pid_path() -> Option<PathBuf> {
    data_dir().map(|dir| dir.join("state").join("desktop-serve.pid"))
}

#[derive(Serialize, Clone, Debug, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Startup {
    Starting,
    Ready { url: String },
    Failed { message: String, log: Option<String> },
}

pub struct Service {
    inner: Mutex<Inner>,
}

struct Inner {
    startup: Startup,
    /// Set while a service this app started is running.
    owned: Option<u32>,
    /// Set while starting, to notice a service that exits before it answers.
    child: Option<Child>,
}

impl Service {
    pub fn new() -> Self {
        Service {
            inner: Mutex::new(Inner { startup: Startup::Starting, owned: None, child: None }),
        }
    }

    pub fn startup(&self) -> Startup {
        self.inner.lock().unwrap().startup.clone()
    }

    /// Before starting in the background, so that nobody reads an earlier failure meanwhile.
    pub fn mark_starting(&self) {
        self.set(Startup::Starting);
    }

    pub fn owns_service(&self) -> bool {
        self.inner.lock().unwrap().owned.is_some()
    }

    /// Use the service that answers, or start one. Blocks until it answers or fails.
    pub fn start(&self) -> Startup {
        let config = read_config();
        let url = local_url(&config);
        let port = config.port.unwrap_or(DEFAULT_PORT);
        let https = config.tls_cert.is_some();
        self.set(Startup::Starting);

        if answers(port, https) {
            // Started by an earlier copy of this app: still this app's to stop.
            let adopted = read_pid().filter(|pid| alive(*pid));
            self.inner.lock().unwrap().owned = adopted;
            return self.set(Startup::Ready { url });
        }
        {
            let mut inner = self.inner.lock().unwrap();
            if let Some(child) = inner.child.as_mut() {
                if matches!(child.try_wait(), Ok(None)) {
                    // Already starting, from another call.
                    drop(inner);
                    return self.wait(port, https, url);
                }
            }
        }
        match spawn(port) {
            Ok(child) => {
                let pid = child.id();
                write_pid(pid);
                let mut inner = self.inner.lock().unwrap();
                inner.owned = Some(pid);
                inner.child = Some(child);
            }
            Err(message) => return self.set(Startup::Failed { message, log: log_display() }),
        }
        self.wait(port, https, url)
    }

    fn wait(&self, port: u16, https: bool, url: String) -> Startup {
        let deadline = Instant::now() + START_TIMEOUT;
        while Instant::now() < deadline {
            if answers(port, https) {
                return self.set(Startup::Ready { url });
            }
            let exited = {
                let mut inner = self.inner.lock().unwrap();
                match inner.child.as_mut().map(|child| child.try_wait()) {
                    Some(Ok(Some(status))) => {
                        inner.child = None;
                        inner.owned = None;
                        Some(status.code())
                    }
                    _ => None,
                }
            };
            if let Some(code) = exited {
                remove_pid();
                return self.set(Startup::Failed { message: exit_message(code), log: log_display() });
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        self.set(Startup::Failed {
            message: format!("The service didn't answer on port {port} within {} seconds.", START_TIMEOUT.as_secs()),
            log: log_display(),
        })
    }

    fn set(&self, startup: Startup) -> Startup {
        self.inner.lock().unwrap().startup = startup.clone();
        startup
    }

    /// Stop the service if this app started it. A service it found running stays.
    pub fn stop(&self) {
        let (pid, child) = {
            let mut inner = self.inner.lock().unwrap();
            (inner.owned.take(), inner.child.take())
        };
        let Some(pid) = pid else { return };
        terminate(pid, child);
        remove_pid();
    }
}

fn exit_message(code: Option<i32>) -> String {
    match code {
        // What a shell returns for a command it can't find.
        Some(127) => "agentop isn't installed on this computer, or isn't on the PATH of your login shell.".into(),
        Some(code) => format!("agentop serve exited with status {code} before it answered."),
        None => "agentop serve was stopped before it answered.".into(),
    }
}

fn log_display() -> Option<String> {
    log_path().map(|path| path.display().to_string())
}

/// `agentop serve` on loopback, with its output in the log.
///
/// Through the user's login shell, interactive so that it reads the same files a
/// terminal does: an app opened from the desktop gets a short PATH, which may lack
/// agentop, tmux, and the agent tools. `exec` keeps the PID the service's own.
fn spawn(port: u16) -> Result<Child, String> {
    let log_file = log_path().ok_or("No home folder to keep the service's log in.")?;
    if let Some(dir) = log_file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("Can't create {}: {e}", dir.display()))?;
    }
    let open = || {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_file)
            .map_err(|e| format!("Can't write {}: {e}", log_file.display()))
    };
    let (out, err) = (open()?, open()?);
    let args = ["serve".to_string(), "--host".into(), "127.0.0.1".into(), "--port".into(), port.to_string()];

    let mut command = if let Some(bin) = std::env::var_os("AGENTOP_BIN") {
        let mut command = Command::new(bin);
        command.args(&args);
        command
    } else {
        login_shell_command(&args)
    };
    command.stdin(Stdio::null()).stdout(out).stderr(err);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own process group: a Ctrl+C meant for `npm run dev` doesn't reach it.
        command.process_group(0);
    }
    command.spawn().map_err(|e| format!("Couldn't start agentop serve: {e}"))
}

#[cfg(unix)]
fn login_shell_command(args: &[String]) -> Command {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into());
    let mut command = Command::new(shell);
    command.arg("-ilc").arg(format!("exec agentop {}", args.join(" ")));
    command
}

#[cfg(not(unix))]
fn login_shell_command(args: &[String]) -> Command {
    let mut command = Command::new("agentop");
    command.args(args);
    command
}

/// Whether the service answers its health check. With TLS, whether the port is open.
pub fn answers(port: u16, https: bool) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    if https {
        return true;
    }
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /api/v2/health HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut head = [0u8; 16];
    let mut read = 0;
    while read < head.len() {
        match stream.read(&mut head[read..]) {
            Ok(0) | Err(_) => break,
            Ok(n) => read += n,
        }
    }
    is_ok_status(&head[..read])
}

fn is_ok_status(head: &[u8]) -> bool {
    let text = String::from_utf8_lossy(head);
    text.starts_with("HTTP/1.") && text.get(9..12) == Some("200")
}

fn read_pid() -> Option<u32> {
    std::fs::read_to_string(pid_path()?).ok()?.trim().parse().ok()
}

fn write_pid(pid: u32) {
    if let Some(path) = pid_path() {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(path, pid.to_string());
    }
}

fn remove_pid() {
    if let Some(path) = pid_path() {
        let _ = std::fs::remove_file(path);
    }
}

#[cfg(unix)]
fn alive(pid: u32) -> bool {
    // Signal 0 checks that the process exists and can be signalled, without sending anything.
    unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
}

#[cfg(not(unix))]
fn alive(_pid: u32) -> bool {
    false
}

/// SIGTERM, which the service handles by closing terminals and exiting; SIGKILL if it hasn't
/// gone by the timeout. On Windows, which has no SIGTERM, it is killed.
#[cfg(unix)]
fn terminate(pid: u32, child: Option<Child>) {
    let mut child = child;
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGTERM);
    }
    let deadline = Instant::now() + STOP_TIMEOUT;
    while Instant::now() < deadline {
        let gone = match child.as_mut() {
            // Reaps our own child, so that it doesn't linger as a zombie that looks alive.
            Some(child) => !matches!(child.try_wait(), Ok(None)),
            None => !alive(pid),
        };
        if gone {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGKILL);
    }
    if let Some(mut child) = child {
        let _ = child.wait();
    }
}

#[cfg(not(unix))]
fn terminate(_pid: u32, child: Option<Child>) {
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn local_url_is_always_loopback() {
        assert_eq!(local_url(&ConfigFile::default()), "http://127.0.0.1:9775");
        let config = ConfigFile { port: Some(9000), tls_cert: Some("c".into()) };
        assert_eq!(local_url(&config), "https://127.0.0.1:9000");
    }

    #[test]
    fn only_a_200_is_healthy() {
        assert!(is_ok_status(b"HTTP/1.1 200 OK\r\n"));
        assert!(is_ok_status(b"HTTP/1.0 200 OK\r\n"));
        assert!(!is_ok_status(b"HTTP/1.1 404 Not"));
        assert!(!is_ok_status(b"SSH-2.0-OpenSSH"));
        assert!(!is_ok_status(b""));
    }

    fn serve_once(response: &'static [u8]) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buffer = [0u8; 512];
                let _ = stream.read(&mut buffer);
                let _ = stream.write_all(response);
            }
        });
        port
    }

    #[test]
    fn answers_when_the_health_check_does() {
        let port = serve_once(b"HTTP/1.1 200 OK\r\ncontent-length: 15\r\n\r\n{\"status\":\"ok\"}");
        assert!(answers(port, false));
    }

    #[test]
    fn something_else_on_the_port_does_not_answer() {
        let port = serve_once(b"HTTP/1.1 404 Not Found\r\n\r\n");
        assert!(!answers(port, false));
    }

    #[test]
    fn a_closed_port_does_not_answer() {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        assert!(!answers(port, false));
    }

    #[test]
    fn a_missing_command_is_explained() {
        assert!(exit_message(Some(127)).contains("isn't installed"));
        assert!(exit_message(Some(1)).contains("status 1"));
    }

    /// Starts the real `agentop serve` in a throwaway home on a spare port, then stops it.
    /// Run with `AGENTOP_BIN=$(which agentop) cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn starts_and_stops_a_real_service() {
        let home = std::env::temp_dir().join(format!("agentop-desktop-it-{}", std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        std::fs::write(home.join("config.toml"), format!("port = {port}\n")).unwrap();
        std::env::set_var("AGENTOP_HOME", &home);

        let service = Service::new();
        let startup = service.start();
        assert_eq!(startup, Startup::Ready { url: format!("http://127.0.0.1:{port}") }, "log: {:?}", log_display());
        assert!(service.owns_service());
        let pid = read_pid().expect("pid file");

        // A second copy of the app, as after an update, finds it running and adopts it.
        let again = Service::new();
        assert!(matches!(again.start(), Startup::Ready { .. }));
        assert!(again.owns_service());

        service.stop();
        assert!(!answers(port, false));
        assert!(!alive(pid));
        assert!(read_pid().is_none());

        std::env::remove_var("AGENTOP_HOME");
        let _ = std::fs::remove_dir_all(&home);
    }
}

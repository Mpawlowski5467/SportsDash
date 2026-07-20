//! SportsDash desktop shell.
//!
//! On launch this spawns the bundled FastAPI backend on a fixed loopback
//! port, waits until it is accepting connections, then reveals the main
//! window — so the UI never paints against a backend that isn't listening
//! yet. The backend process is killed when the app exits.
//!
//! The backend is a PyInstaller *onedir* bundle (frozen exe + `_internal/`
//! dependencies). Tauri's `externalBin` sidecar mechanism only ships single
//! files, so the directory is bundled under the app's Resources instead
//! (see `bundle.resources` in tauri.conf.json) and the executable inside it
//! is spawned directly. Onedir skips onefile's per-launch self-extraction,
//! which is what makes desktop launches faster (ROADMAP.md).

use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

/// Loopback port the bundled backend listens on. Kept in sync with
/// `VITE_API_BASE` in `frontend/.env.tauri` and the backend's default.
const BACKEND_PORT: u16 = 8765;

/// How long to wait for the backend to start listening before showing the
/// window anyway (first launch can be slow: cold disk caches, Gatekeeper
/// checks on an unsigned bundle).
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

/// Holds the running backend child so it can be killed on app exit.
struct Backend(Mutex<Option<Child>>);

/// Path of the backend executable inside the onedir bundle.
///
/// In a bundled app it lives under `Contents/Resources/sportsdash-backend/`
/// (put there by `bundle.resources`). In `tauri dev` there is no bundle,
/// so fall back to the staging directory `scripts/build-desktop.sh` fills
/// (`src-tauri/binaries/sportsdash-backend/`).
fn backend_executable(app: &tauri::App) -> PathBuf {
    let mut candidates = Vec::new();
    if let Ok(resources) = app.path().resource_dir() {
        candidates.push(resources.join("sportsdash-backend/sportsdash-backend"));
    }
    if cfg!(debug_assertions) {
        candidates.push(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("binaries/sportsdash-backend/sportsdash-backend"),
        );
    }
    candidates
        .iter()
        .find(|path| path.is_file())
        .cloned()
        .unwrap_or_else(|| {
            panic!(
                "SportsDash backend bundle is missing — looked in: {candidates:?}. \
                 Run scripts/build-desktop.sh to freeze and stage it."
            )
        })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Spawn the bundled backend on the loopback port.
            let backend_path = backend_executable(app);
            let mut command = Command::new(&backend_path);
            command
                .env("SPORTSDASH_HOST", "127.0.0.1")
                .env("SPORTSDASH_PORT", BACKEND_PORT.to_string())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let mut child = command.spawn().unwrap_or_else(|error| {
                panic!(
                    "failed to spawn the SportsDash backend at {}: {error}",
                    backend_path.display()
                )
            });

            // Surface backend stdout/stderr in the Tauri log for debugging.
            if let Some(stdout) = child.stdout.take() {
                thread::spawn(move || {
                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        log::info!("[backend] {line}");
                    }
                });
            }
            if let Some(stderr) = child.stderr.take() {
                thread::spawn(move || {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        log::info!("[backend] {line}");
                    }
                });
            }
            app.manage(Backend(Mutex::new(Some(child))));

            // Reveal the window once the backend accepts connections.
            let window = app
                .get_webview_window("main")
                .expect("main window is missing");
            thread::spawn(move || {
                let deadline = Instant::now() + STARTUP_TIMEOUT;
                while Instant::now() < deadline {
                    if TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok() {
                        break;
                    }
                    thread::sleep(Duration::from_millis(250));
                }
                let _ = window.show();
                let _ = window.set_focus();
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the SportsDash application")
        .run(|app_handle, event| {
            // Tear the backend down with the app so it never outlives the UI.
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}

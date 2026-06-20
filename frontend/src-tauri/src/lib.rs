//! SportsDash desktop shell.
//!
//! On launch this spawns the bundled FastAPI backend (a PyInstaller binary
//! shipped as a Tauri sidecar) on a fixed loopback port, waits until it is
//! accepting connections, then reveals the main window — so the UI never
//! paints against a backend that isn't listening yet. The backend process is
//! killed when the app exits.

use std::net::TcpStream;
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Loopback port the bundled backend listens on. Kept in sync with
/// `VITE_API_BASE` in `frontend/.env.tauri` and the sidecar's default.
const BACKEND_PORT: u16 = 8765;

/// How long to wait for the backend to start listening before showing the
/// window anyway (a onefile PyInstaller binary self-extracts on first run).
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

/// Holds the running backend child so it can be killed on app exit.
struct Backend(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Spawn the bundled backend on the loopback port.
            let sidecar = app
                .shell()
                .sidecar("sportsdash-backend")
                .expect("sportsdash-backend sidecar is missing from the bundle")
                .env("SPORTSDASH_HOST", "127.0.0.1")
                .env("SPORTSDASH_PORT", BACKEND_PORT.to_string());
            let (mut rx, child) = sidecar
                .spawn()
                .expect("failed to spawn the SportsDash backend sidecar");
            app.manage(Backend(Mutex::new(Some(child))));

            // Surface backend stdout/stderr in the Tauri log for debugging.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            log::info!("[backend] {}", String::from_utf8_lossy(&line));
                        }
                        _ => {}
                    }
                }
            });

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
                    if let Some(child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}

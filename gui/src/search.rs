use crate::options::Options;
use std::{ffi::OsString, path::PathBuf};

/// How long the backend may stay completely silent before the GUI treats it as
/// stalled. Indexing a large library legitimately runs for hours, so this is a
/// progress deadline rather than a total runtime limit: any backend output
/// (per-file and per-chunk progress, download percentages) restarts it, and a
/// waiting consent dialog is not a stalled backend.
pub const STALL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(900);

pub fn arguments(o: &Options) -> anyhow::Result<Vec<OsString>> {
    anyhow::ensure!(!o.directory.is_empty(), "Choose a directory first");
    let root = PathBuf::from(&o.directory).canonicalize()?;
    anyhow::ensure!(root.is_dir(), "Not a directory");
    anyhow::ensure!(o.limit > 0, "Limit must be positive");
    anyhow::ensure!(
        !o.query.trim().is_empty() || !o.references.is_empty(),
        "Enter a query or attach references"
    );
    let mut args: Vec<OsString> = ["-m", "search_tool.cli", "--json", "--limit"]
        .map(Into::into)
        .into();
    args.push(o.limit.to_string().into());
    if !o.cache_dir.is_empty() {
        args.extend(["--cache-dir".into(), o.cache_dir.clone().into()]);
    }
    if o.rebuild {
        args.push("--rebuild".into());
    }
    for r in &o.references {
        let r = r.canonicalize()?;
        anyhow::ensure!(r.is_file(), "Reference is not a file");
        args.extend(["--reference".into(), r.into_os_string()]);
    }
    args.extend(["--".into(), root.into_os_string(), o.query.clone().into()]);
    Ok(args)
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Permission {
    Processing,
    Download,
}
#[derive(Clone, Debug, serde::Deserialize)]
pub struct Hit {
    pub path: PathBuf,
    pub score: f64,
    #[serde(default)]
    pub snippet: String,
    #[serde(default)]
    pub location: String,
    #[serde(default)]
    pub kind: String,
}
pub enum Event {
    Consent(Permission, String),
    Progress(String),
    Finished(Result<Vec<Hit>, String>),
}
// Progress cannot consume the single reserved terminal-event slot.
pub struct EventReceiver {
    progress: std::sync::mpsc::Receiver<Event>,
    completion: std::sync::mpsc::Receiver<Event>,
}
impl EventReceiver {
    pub fn try_recv(&self) -> Result<Event, std::sync::mpsc::TryRecvError> {
        use std::sync::mpsc::TryRecvError;
        let progress = self.progress.try_recv();
        if progress.is_ok() {
            return progress;
        }
        match self.completion.try_recv() {
            Ok(event) => Ok(event),
            Err(TryRecvError::Disconnected)
                if matches!(progress, Err(TryRecvError::Disconnected)) =>
            {
                Err(TryRecvError::Disconnected)
            }
            Err(_) => Err(TryRecvError::Empty),
        }
    }
    pub fn try_iter(&self) -> impl Iterator<Item = Event> + '_ {
        std::iter::from_fn(|| self.try_recv().ok())
    }
    pub fn recv_timeout(
        &self,
        timeout: std::time::Duration,
    ) -> Result<Event, std::sync::mpsc::RecvTimeoutError> {
        let start = std::time::Instant::now();
        loop {
            match self.try_recv() {
                Ok(event) => return Ok(event),
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    return Err(std::sync::mpsc::RecvTimeoutError::Disconnected);
                }
                Err(_) if start.elapsed() >= timeout => {
                    return Err(std::sync::mpsc::RecvTimeoutError::Timeout);
                }
                Err(_) => std::thread::sleep(std::time::Duration::from_millis(1)),
            }
        }
    }
}
pub struct Worker {
    pub events: EventReceiver,
    pub answers: std::sync::mpsc::Sender<bool>,
    pub cancel: std::sync::Arc<std::sync::atomic::AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.cancel
            .store(true, std::sync::atomic::Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
pub fn start(o: &Options) -> anyhow::Result<Worker> {
    let python = std::env::var_os("FINDMATCH_GUI_PYTHON").ok_or_else(|| {
        anyhow::anyhow!("FINDMATCH_GUI_PYTHON must name an absolute Python executable")
    })?;
    let python = PathBuf::from(python);
    anyhow::ensure!(
        python.is_absolute() && python.is_file(),
        "FINDMATCH_GUI_PYTHON must name an absolute Python executable"
    );
    let mut command = std::process::Command::new(python);
    command.args(arguments(o)?);
    Ok(spawn_command(
        command,
        PathBuf::from(&o.directory).canonicalize()?,
        o.limit,
        STALL_TIMEOUT,
    ))
}

/// `stall` is the maximum period without any backend output, not a total runtime
/// limit: a search that keeps reporting progress runs until it finishes.
pub fn spawn_command(
    mut command: std::process::Command,
    root: PathBuf,
    limit: usize,
    stall: std::time::Duration,
) -> Worker {
    let (tx, progress) = std::sync::mpsc::sync_channel(127);
    let (finished, completion) = std::sync::mpsc::sync_channel(1);
    let events = EventReceiver {
        progress,
        completion,
    };
    let (answers, rx) = std::sync::mpsc::channel();
    let cancel = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop = cancel.clone();
    let thread = std::thread::spawn(move || {
        let result =
            run(&mut command, &root, limit, stall, &stop, &tx, &rx).map_err(|e| format!("{e:#}"));
        // Exactly one completion is produced, so this never waits on UI polling.
        let _ = finished.try_send(Event::Finished(result));
    });
    Worker {
        events,
        answers,
        cancel,
        thread: Some(thread),
    }
}
fn send_critical(
    tx: &std::sync::mpsc::SyncSender<Event>,
    mut event: Event,
    stop: &std::sync::atomic::AtomicBool,
) -> bool {
    loop {
        match tx.try_send(event) {
            Ok(()) => return true,
            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => return false,
            Err(std::sync::mpsc::TrySendError::Full(e)) => {
                if stop.load(std::sync::atomic::Ordering::Relaxed) {
                    return false;
                }
                event = e;
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    }
}
#[cfg(target_os = "linux")]
fn backend_parent_death_signal(command: &mut std::process::Command, parent: u32) {
    use std::os::unix::process::CommandExt;
    unsafe extern "C" {
        fn prctl(option: i32, ...) -> i32;
        fn getppid() -> i32;
    }
    // Only async-signal-safe libc calls run between fork and exec. Capture the
    // GUI identity before fork: death before prctl otherwise misses the signal.
    unsafe {
        command.pre_exec(move || {
            const PR_SET_PDEATHSIG: i32 = 1;
            const SIGKILL: usize = 9;
            if prctl(PR_SET_PDEATHSIG, SIGKILL, 0usize, 0usize, 0usize) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if getppid() as u32 != parent {
                return Err(std::io::Error::from_raw_os_error(10)); // ECHILD
            }
            Ok(())
        });
    }
}
fn run(
    command: &mut std::process::Command,
    root: &std::path::Path,
    limit: usize,
    stall: std::time::Duration,
    stop: &std::sync::atomic::AtomicBool,
    events: &std::sync::mpsc::SyncSender<Event>,
    answers: &std::sync::mpsc::Receiver<bool>,
) -> anyhow::Result<Vec<Hit>> {
    use std::{
        io::{Read, Write},
        os::unix::process::CommandExt,
        process::Stdio,
        sync::atomic::Ordering,
        time::{Duration, Instant},
    };
    command.process_group(0);
    #[cfg(target_os = "linux")]
    backend_parent_death_signal(command, std::process::id());
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = child.stdout.take().unwrap();
    let mut stderr = child.stderr.take().unwrap();
    let out = std::thread::spawn(move || -> std::io::Result<(Vec<u8>, bool)> {
        let mut data = Vec::new();
        let mut overflow = false;
        let mut buffer = [0; 8192];
        loop {
            let n = stdout.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            let remain = (8 * 1024 * 1024usize).saturating_sub(data.len());
            data.extend_from_slice(&buffer[..n.min(remain)]);
            overflow |= n > remain;
        }
        Ok((data, overflow))
    });
    let (etx, erx) = std::sync::mpsc::sync_channel(64);
    let err = std::thread::spawn(move || {
        let mut b = [0; 2048];
        loop {
            match stderr.read(&mut b) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if etx.send(b[..n].to_vec()).is_err() {
                        break;
                    }
                }
            }
        }
    });
    // Deadline for silence, not for the run: every backend line restarts it.
    let mut last_progress = Instant::now();
    let mut diagnostics = Vec::new();
    let mut pending = Vec::new();
    let mut waiting = false;
    let mut failure = None;
    let mut status = None;
    loop {
        if stop.load(Ordering::Relaxed) {
            failure = Some("Search cancelled".to_string());
            break;
        }
        if waiting {
            // Blocked on the user's consent decision, not on stalled work.
            last_progress = Instant::now();
        } else if last_progress.elapsed() > stall {
            failure = Some(format!(
                "Search timed out: no backend progress for {:.0}s",
                stall.as_secs_f64()
            ));
            break;
        }
        for chunk in erx.try_iter() {
            last_progress = Instant::now();
            diagnostics.extend_from_slice(&chunk);
            if diagnostics.len() > 16384 {
                diagnostics.drain(..diagnostics.len() - 16384);
            }
            for byte in chunk {
                pending.push(byte);
                if pending.len() > 8192 {
                    pending.remove(0);
                }
                if pending.ends_with(b"[y/N]") {
                    let prompt = String::from_utf8_lossy(&pending).into_owned();
                    let permission = if prompt.contains("Process these files before searching?") {
                        Some(Permission::Processing)
                    } else if prompt.contains("Download EmbeddingGemma 2") {
                        Some(Permission::Download)
                    } else {
                        None
                    };
                    if let Some(p) = permission {
                        waiting = true;
                        let _ = send_critical(events, Event::Consent(p, prompt), stop);
                    } else {
                        let _ = stdin.write_all(b"n\n");
                        let _ = stdin.flush();
                    }
                    pending.clear();
                } else if byte == b'\n' {
                    let text = String::from_utf8_lossy(&pending).trim().to_string();
                    if text.starts_with("findmatch: skipped ") {
                        let _ = send_critical(events, Event::Progress(text), stop);
                    } else {
                        let _ = events.try_send(Event::Progress(text));
                    }
                    pending.clear();
                }
            }
        }
        if waiting {
            match answers.try_recv() {
                Ok(yes) => {
                    if let Err(e) = stdin
                        .write_all(if yes { b"y\n" } else { b"n\n" })
                        .and_then(|_| stdin.flush())
                    {
                        failure = Some(e.to_string());
                        break;
                    }
                    waiting = false;
                }
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    failure = Some("Permission dialog closed".into());
                    break;
                }
                _ => {}
            }
        }
        match child.try_wait() {
            Ok(Some(s)) => status = Some(s),
            Ok(None) => {}
            Err(e) => {
                failure = Some(e.to_string());
                break;
            }
        }
        if status.is_some() && out.is_finished() && err.is_finished() {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    if failure.is_some() {
        unsafe extern "C" {
            fn kill(pid: i32, signal: i32) -> i32;
        }
        unsafe {
            kill(-(child.id() as i32), 9);
        }
        let _ = child.kill();
    }
    let _ = child.wait();
    drop(erx);
    err.join()
        .map_err(|_| anyhow::anyhow!("stderr reader failed"))?;
    let (data, overflow) = out
        .join()
        .map_err(|_| anyhow::anyhow!("stdout reader failed"))??;
    if let Some(f) = failure {
        anyhow::bail!("{f}");
    }
    anyhow::ensure!(!overflow, "Result exceeds 8 MiB");
    anyhow::ensure!(
        status.unwrap().success(),
        "findmatch failed: {}",
        String::from_utf8_lossy(&diagnostics)
    );
    let mut hits: Vec<Hit> = serde_json::from_slice(&data)?;
    for h in &hits {
        anyhow::ensure!(
            h.score.is_finite()
                && h.path.is_absolute()
                && !h
                    .path
                    .components()
                    .any(|c| matches!(c, std::path::Component::ParentDir)),
            "Invalid result"
        );
    }
    hits.retain(|h| {
        h.path
            .canonicalize()
            .is_ok_and(|p| p.starts_with(root) && p.is_file())
    });
    hits.sort_by(|a, b| b.score.total_cmp(&a.score));
    hits.truncate(limit);
    Ok(hits)
}
#[cfg(test)]
mod tests {
    #[cfg(target_os = "linux")]
    #[test]
    fn parent_identity_race_guard_refuses_to_exec_backend() {
        let root = tempfile::tempdir().unwrap();
        let marker = root.path().join("executed");
        let mut command = std::process::Command::new("python3");
        command
            .args(["-c", "import sys; open(sys.argv[1], 'w').close()"])
            .arg(&marker);
        super::backend_parent_death_signal(&mut command, 0);
        let error = command
            .spawn()
            .expect_err("Parent identity changed: backend must not exec");
        assert_eq!(error.raw_os_error(), Some(10));
        assert!(!marker.exists());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn parent_death_fixture() {
        let Some(root) = std::env::var_os("FINDMATCH_PARENT_DEATH_FIXTURE") else {
            return;
        };
        let root = PathBuf::from(root);
        let mut command = std::process::Command::new("python3");
        command.args(["-c", "import os,sys,time; open(sys.argv[1]+'/pid','w').write(str(os.getpid())); time.sleep(0.5); open(sys.argv[1]+'/continued','w').close(); time.sleep(60)"]).arg(&root);
        let _worker = spawn_command(command, root, 10, std::time::Duration::from_secs(90));
        std::thread::sleep(std::time::Duration::from_secs(60));
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn abrupt_gui_death_kills_backend_and_allows_supervisor_to_reap_it() {
        let root = tempfile::tempdir().unwrap();
        let output = std::process::Command::new("python3").args(["-c", r#"
import ctypes, os, pathlib, signal, subprocess, sys, time
assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0 # isolated child subreaper
root = pathlib.Path(sys.argv[2])
env = dict(os.environ, FINDMATCH_PARENT_DEATH_FIXTURE=str(root))
owner = subprocess.Popen([sys.argv[1], '--exact', 'search::tests::parent_death_fixture', '--nocapture'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pid = None
try:
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try:
            pid = int((root/'pid').read_text())
            break
        except (FileNotFoundError, ValueError):
            time.sleep(.01)
    assert pid is not None, 'backend did not start'
    owner.kill()
    owner.wait(timeout=2)
    end = time.monotonic() + 2
    reaped = None
    while time.monotonic() < end:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            reaped = status
            break
        time.sleep(.01)
    assert reaped is not None, 'backend survived abrupt GUI death'
    assert os.WIFSIGNALED(reaped) and os.WTERMSIG(reaped) == signal.SIGKILL, reaped
    assert not (root/'continued').exists(), 'orphan continued executing'
    assert not pathlib.Path('/proc/'+str(pid)).exists(), 'backend was not reaped'
finally:
    if owner.poll() is None:
        owner.kill()
        owner.wait()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except ProcessLookupError:
            pass
"#]).arg(std::env::current_exe().unwrap()).arg(root.path()).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    #[test]
    fn cancellation_with_full_progress_queue_still_delivers_finished_without_polling() {
        let root = tempfile::tempdir().unwrap();
        let ready = root.path().join("ready");
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import sys,time; sys.stderr.write('Processing file\\n'*10000); sys.stderr.flush(); open(sys.argv[1], 'w').close(); time.sleep(60)"]).arg(&ready);
        let mut w = spawn_command(
            c,
            root.path().into(),
            10,
            std::time::Duration::from_secs(90),
        );
        for _ in 0..200 {
            if ready.exists() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(ready.exists(), "Backend must have emitted progress");
        std::thread::sleep(std::time::Duration::from_millis(100));
        w.cancel.store(true, std::sync::atomic::Ordering::Relaxed);
        let thread = w.thread.take().unwrap();
        for _ in 0..200 {
            if thread.is_finished() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(
            thread.is_finished(),
            "Completion must not wait for GUI polling"
        );
        thread.join().unwrap();
        let events: Vec<_> = w.events.try_iter().collect();
        assert_eq!(
            events.len(),
            128,
            "Exercise a completely full event backlog"
        );
        assert!(
            events
                .into_iter()
                .any(|e| matches!(e, Event::Finished(Err(ref e)) if e.contains("cancelled"))),
            "Cancelled worker must deliver Finished even when progress fills the queue"
        );
    }
    #[test]
    fn skipped_summary_is_delivered_even_after_progress_queue_fills() {
        let root = tempfile::tempdir().unwrap();
        let ready = root.path().join("ready");
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import sys; sys.stderr.write('Processing file\\n'*1000); sys.stderr.flush(); open(sys.argv[1], 'w').close(); print('findmatch: skipped 1 file(s); results exclude these files. Failed files remain pending for retry.', file=sys.stderr, flush=True); print('[]')"]).arg(&ready);
        let w = spawn_command(c, root.path().into(), 10, std::time::Duration::from_secs(5));
        for _ in 0..200 {
            if ready.exists() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(ready.exists());
        std::thread::sleep(std::time::Duration::from_millis(100));
        let mut summary = false;
        loop {
            match w
                .events
                .recv_timeout(std::time::Duration::from_secs(3))
                .unwrap()
            {
                Event::Progress(text) if text.starts_with("findmatch: skipped ") => summary = true,
                Event::Finished(result) => {
                    assert!(result.unwrap().is_empty());
                    assert!(summary, "Skipped summary must arrive before completion");
                    break;
                }
                _ => {}
            }
        }
    }
    #[test]
    fn progress_backlog_is_bounded_while_gui_is_not_polling() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c","import sys; sys.stderr.write('Processing file\\n'*10000); sys.stderr.flush(); print('[]')"]);
        let w = spawn_command(c, root.path().into(), 10, std::time::Duration::from_secs(5));
        std::thread::sleep(std::time::Duration::from_millis(300));
        let count = w.events.try_iter().count();
        assert!(count <= 128, "Unbounded progress event backlog: {count}");
    }
    #[test]
    fn recursive_hits_keep_metadata_rank_limit_and_reject_escape_paths() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("nested")).unwrap();
        std::fs::write(root.path().join("nested/a.txt"), "a").unwrap();
        std::fs::write(root.path().join("b.txt"), "b").unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c","import sys,json; r=sys.argv[1]; print(json.dumps([{'path':r+'/b.txt','score':0.1,'snippet':'low','location':'line 1','kind':'text'},{'path':r+'/nested/a.txt','score':0.9,'snippet':'high','location':'line 7','kind':'text'},{'path':'/etc/passwd','score':1}]))"]).arg(root.path());
        let w = spawn_command(c, root.path().into(), 1, std::time::Duration::from_secs(2));
        loop {
            if let Event::Finished(r) = w
                .events
                .recv_timeout(std::time::Duration::from_secs(3))
                .unwrap()
            {
                let h = r.unwrap();
                assert_eq!(h.len(), 1);
                assert_eq!(h[0].snippet, "high");
                assert_eq!(h[0].location, "line 7");
                break;
            }
        }
    }
    #[test]
    fn unknown_prompts_are_denied_without_gui_authorization() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c","import sys; print('Erase everything? [y/N]',end='',file=sys.stderr,flush=True); assert input()=='n'; print('[]')"]);
        let w = spawn_command(c, root.path().into(), 1, std::time::Duration::from_secs(2));
        match w
            .events
            .recv_timeout(std::time::Duration::from_secs(3))
            .unwrap()
        {
            Event::Finished(r) => assert!(r.unwrap().is_empty()),
            _ => panic!("Unknown permission must never be presented as processing/download"),
        }
    }
    #[test]
    fn progressing_backend_is_not_timed_out_by_total_runtime() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import sys,time\nfor i in range(15):\n    print(f'Processing {i+1}/15: file.txt', file=sys.stderr, flush=True)\n    time.sleep(0.04)\nprint('[]')"]);
        let start = std::time::Instant::now();
        // 15 reporting windows of work, each shorter than the stall window.
        let w = spawn_command(
            c,
            root.path().into(),
            10,
            std::time::Duration::from_millis(100),
        );
        let mut finished = None;
        while let Ok(event) = w.events.recv_timeout(std::time::Duration::from_secs(10)) {
            if let Event::Finished(result) = event {
                finished = Some(result);
                break;
            }
        }
        let hits = finished
            .expect("worker must deliver a completion")
            .expect("a backend that keeps reporting progress must not be timed out");
        assert!(hits.is_empty());
        assert!(
            start.elapsed() > std::time::Duration::from_millis(500),
            "the run must outlive the stall window to prove the point"
        );
    }
    #[test]
    fn waiting_for_user_consent_is_not_a_stall() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import sys\nprint('Process these files before searching? [y/N]', end='', file=sys.stderr, flush=True)\nprint('[]' if input() == 'y' else '')"]);
        let w = spawn_command(
            c,
            root.path().into(),
            10,
            std::time::Duration::from_millis(150),
        );
        assert!(matches!(
            w.events
                .recv_timeout(std::time::Duration::from_secs(2))
                .unwrap(),
            Event::Consent(Permission::Processing, _)
        ));
        // The user deliberates for longer than the stall window.
        std::thread::sleep(std::time::Duration::from_millis(450));
        w.answers.send(true).unwrap();
        loop {
            if let Event::Finished(r) = w
                .events
                .recv_timeout(std::time::Duration::from_secs(3))
                .unwrap()
            {
                assert!(r.unwrap().is_empty());
                break;
            }
        }
    }
    #[test]
    fn timeout_kills_backend_and_inherited_pipe_holders() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import os,time; os.fork(); time.sleep(60)"]);
        let start = std::time::Instant::now();
        let w = spawn_command(
            c,
            root.path().into(),
            1,
            std::time::Duration::from_millis(80),
        );
        match w
            .events
            .recv_timeout(std::time::Duration::from_secs(2))
            .unwrap()
        {
            Event::Finished(r) => assert!(r.unwrap_err().contains("timed out")),
            _ => panic!("Unexpected event"),
        };
        drop(w);
        assert!(start.elapsed() < std::time::Duration::from_secs(1));
    }
    #[test]
    fn dropping_worker_cancels_and_reaps_backend_process_group() {
        let root = tempfile::tempdir().unwrap();
        let pid = root.path().join("pid");
        let mut c = std::process::Command::new("python3");
        c.args([
            "-c",
            "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)",
        ])
        .arg(&pid);
        let w = spawn_command(
            c,
            root.path().into(),
            10,
            std::time::Duration::from_secs(90),
        );
        for _ in 0..100 {
            if pid.exists() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let id = std::fs::read_to_string(pid).unwrap();
        drop(w);
        assert!(
            !PathBuf::from(format!("/proc/{id}")).exists(),
            "Worker drop must kill and reap before returning"
        );
    }

    #[test]
    fn subprocess_waits_for_separate_explicit_processing_and_download_consent() {
        let root = tempfile::tempdir().unwrap();
        let mut c = std::process::Command::new("python3");
        c.args(["-c", "import sys; print('Process these files before searching? [y/N]',end='',file=sys.stderr,flush=True); assert input()=='y'; print('Download EmbeddingGemma 2 (approximately 485 MB) for local inference? [y/N]',end='',file=sys.stderr,flush=True); assert input()=='n'; print('[]')"]);
        let w = spawn_command(c, root.path().into(), 10, std::time::Duration::from_secs(2));
        assert!(matches!(
            w.events
                .recv_timeout(std::time::Duration::from_secs(1))
                .unwrap(),
            Event::Consent(Permission::Processing, _)
        ));
        assert!(w.events.try_recv().is_err());
        w.answers.send(true).unwrap();
        assert!(matches!(
            w.events
                .recv_timeout(std::time::Duration::from_secs(1))
                .unwrap(),
            Event::Consent(Permission::Download, _)
        ));
        w.answers.send(false).unwrap();
        loop {
            if let Event::Finished(r) = w
                .events
                .recv_timeout(std::time::Duration::from_secs(2))
                .unwrap()
            {
                assert!(r.unwrap().is_empty());
                break;
            }
        }
    }

    use super::*;
    #[test]
    fn backend_arguments_use_python_module_and_preserve_reference_only() {
        let root = tempfile::tempdir().unwrap();
        let a = root.path().join("a file");
        let b = root.path().join("b");
        std::fs::write(&a, "a").unwrap();
        std::fs::write(&b, "b").unwrap();
        let o = Options {
            directory: root.path().to_string_lossy().into(),
            references: vec![a.clone(), b.clone()],
            limit: 3,
            cache_dir: "/cache".into(),
            rebuild: true,
            ..Default::default()
        };
        let args = arguments(&o).unwrap();
        assert_eq!(
            args,
            vec![
                OsString::from("-m"),
                "search_tool.cli".into(),
                "--json".into(),
                "--limit".into(),
                "3".into(),
                "--cache-dir".into(),
                "/cache".into(),
                "--rebuild".into(),
                "--reference".into(),
                a.into_os_string(),
                "--reference".into(),
                b.into_os_string(),
                "--".into(),
                root.path().as_os_str().into(),
                "".into()
            ]
        );
    }
}

use crate::{
    options::Options,
    search::{self, Event, Hit, Permission, Worker},
};
use gpui::{prelude::*, *};
use std::{path::PathBuf, sync::atomic::Ordering, time::Duration};
const BG: u32 = 0x111317;
const PANEL: u32 = 0x1b1e24;
const ORANGE: u32 = 0xff9234;
const MUTED: u32 = 0x9097a5;
struct TextField {
    text: String,
    cursor: usize,
    all: bool,
    focus: FocusHandle,
    placeholder: &'static str,
}
impl TextField {
    fn new(placeholder: &'static str, cx: &mut Context<Self>) -> Self {
        Self {
            text: String::new(),
            cursor: 0,
            all: false,
            focus: cx.focus_handle(),
            placeholder,
        }
    }
    fn set(&mut self, text: String, cx: &mut Context<Self>) {
        self.cursor = text.len();
        self.text = text;
        self.all = false;
        cx.notify();
    }
    fn insert(&mut self, text: &str) {
        if self.all {
            self.text.clear();
            self.cursor = 0;
            self.all = false;
        }
        let text = text.replace(['\n', '\r'], " ");
        self.text.insert_str(self.cursor, &text);
        self.cursor += text.len();
    }
    fn key(&mut self, e: &KeyDownEvent, _: &mut Window, cx: &mut Context<Self>) {
        let k = &e.keystroke;
        if k.modifiers.control || k.modifiers.platform {
            match k.key.as_str() {
                "a" => self.all = true,
                "v" => {
                    if let Some(s) = cx.read_from_clipboard().and_then(|c| c.text()) {
                        self.insert(&s);
                    }
                }
                "c" | "x" if self.all => {
                    cx.write_to_clipboard(ClipboardItem::new_string(self.text.clone()));
                    if k.key == "x" {
                        self.text.clear();
                        self.cursor = 0;
                        self.all = false;
                    }
                }
                _ => return,
            }
        } else {
            match k.key.as_str() {
                "backspace" => {
                    if self.all {
                        self.text.clear();
                        self.cursor = 0;
                        self.all = false;
                    } else if self.cursor > 0 {
                        let prev = self.text[..self.cursor].char_indices().last().unwrap().0;
                        self.text.replace_range(prev..self.cursor, "");
                        self.cursor = prev;
                    }
                }
                "delete" => {
                    if self.all {
                        self.text.clear();
                        self.cursor = 0;
                        self.all = false;
                    } else if self.cursor < self.text.len() {
                        let end = self.cursor
                            + self.text[self.cursor..].chars().next().unwrap().len_utf8();
                        self.text.replace_range(self.cursor..end, "");
                    }
                }
                "left" => {
                    self.all = false;
                    if self.cursor > 0 {
                        self.cursor = self.text[..self.cursor].char_indices().last().unwrap().0;
                    }
                }
                "right" => {
                    self.all = false;
                    if self.cursor < self.text.len() {
                        self.cursor += self.text[self.cursor..].chars().next().unwrap().len_utf8();
                    }
                }
                "home" => {
                    self.cursor = 0;
                    self.all = false;
                }
                "end" => {
                    self.cursor = self.text.len();
                    self.all = false;
                }
                "space" => self.insert(" "),
                _ => {
                    if let Some(s) = &k.key_char {
                        self.insert(s);
                    } else {
                        return;
                    }
                }
            }
        }
        cx.stop_propagation();
        cx.notify();
    }
}
impl Render for TextField {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let focused = self.focus.is_focused(window);
        let display = if self.text.is_empty() {
            self.placeholder.to_string()
        } else if focused && !self.all {
            format!(
                "{}│{}",
                &self.text[..self.cursor],
                &self.text[self.cursor..]
            )
        } else {
            self.text.clone()
        };
        div()
            .id("text-field")
            .track_focus(&self.focus)
            .w_full()
            .px_3()
            .py_2()
            .rounded_md()
            .bg(rgb(if self.all { 0x5c3924 } else { BG }))
            .border_1()
            .border_color(rgb(if focused { ORANGE } else { 0x343941 }))
            .text_color(rgb(if self.text.is_empty() {
                MUTED
            } else {
                0xe9eaf0
            }))
            .cursor(CursorStyle::IBeam)
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|s, _, w, cx| {
                    w.focus(&s.focus);
                    cx.notify();
                }),
            )
            .on_key_down(cx.listener(Self::key))
            .child(display)
    }
}

struct SearchApp {
    directory: Entity<TextField>,
    query: Entity<TextField>,
    cache: Entity<TextField>,
    limit: Entity<TextField>,
    references: Vec<PathBuf>,
    rebuild: bool,
    hits: Vec<Hit>,
    selected: Option<usize>,
    dialog: Option<(Permission, String)>,
    modal_focused: bool,
    worker: Option<Worker>,
    status: String,
    skipped_summary: Option<String>,
    focus: FocusHandle,
}
impl SearchApp {
    fn new(o: Options, cx: &mut Context<Self>) -> Self {
        let s = Self {
            directory: cx.new(|cx| TextField::new("Directory…", cx)),
            query: cx.new(|cx| TextField::new("Query…", cx)),
            cache: cx.new(|cx| TextField::new("Default cache", cx)),
            limit: cx.new(|cx| TextField::new("10", cx)),
            references: o.references.clone(),
            rebuild: o.rebuild,
            hits: vec![],
            selected: None,
            dialog: None,
            modal_focused: false,
            worker: None,
            status: "Ready".into(),
            skipped_summary: None,
            focus: cx.focus_handle(),
        };
        s.directory.update(cx, |f, cx| f.set(o.directory, cx));
        s.query.update(cx, |f, cx| f.set(o.query, cx));
        s.cache.update(cx, |f, cx| f.set(o.cache_dir, cx));
        s.limit.update(cx, |f, cx| f.set(o.limit.to_string(), cx));
        s
    }
}
fn button(id: &'static str, label: impl Into<SharedString>, accent: bool) -> Stateful<Div> {
    div()
        .id(id)
        .debug_selector(move || id.to_string())
        .px_3()
        .py_2()
        .rounded_md()
        .cursor_pointer()
        .bg(rgb(if accent { ORANGE } else { 0x2a2e36 }))
        .text_color(rgb(if accent { 0x18130f } else { 0xe5e8ed }))
        .text_sm()
        .child(label.into())
}
fn is_image(path: &std::path::Path) -> bool {
    path.extension().and_then(|x| x.to_str()).is_some_and(|x| {
        matches!(
            x.to_ascii_lowercase().as_str(),
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "tif" | "tiff"
        )
    })
}
impl Drop for SearchApp {
    fn drop(&mut self) {
        if let Some(w) = &self.worker {
            w.cancel.store(true, Ordering::Relaxed);
        }
    }
}
impl Render for SearchApp {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let modal = self.dialog.is_some();
        if modal != self.modal_focused {
            if modal {
                window.focus(&self.focus);
            } else {
                window.focus(&self.query.read(cx).focus);
            }
            self.modal_focused = modal;
        }
        let mut refs = div()
            .id("references")
            .max_h(px(120.))
            .overflow_y_scroll()
            .flex()
            .flex_col()
            .gap_1();
        for (i, p) in self.references.iter().enumerate() {
            refs = refs.child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        div()
                            .flex_1()
                            .text_sm()
                            .child(p.to_string_lossy().into_owned()),
                    )
                    .child(
                        div()
                            .id(("remove-reference", i))
                            .debug_selector(move || {
                                if i == 0 {
                                    "remove-first".into()
                                } else {
                                    "remove-other".into()
                                }
                            })
                            .px_2()
                            .cursor_pointer()
                            .text_color(rgb(ORANGE))
                            .child("Remove")
                            .on_click(cx.listener(move |s, _, _, cx| {
                                if s.worker.is_none() {
                                    s.references.remove(i);
                                    cx.notify();
                                }
                            })),
                    ),
            );
        }
        let rows = uniform_list(
            "results",
            self.hits.len(),
            cx.processor(|s, range: std::ops::Range<usize>, _, cx| {
                range
                    .map(|i| {
                        let h = &s.hits[i];
                        let preview = if is_image(&h.path) {
                            img(h.path.clone())
                                .w(px(70.))
                                .h(px(70.))
                                .object_fit(ObjectFit::Contain)
                                .into_any_element()
                        } else {
                            div()
                                .w(px(70.))
                                .child(if h.kind.is_empty() {
                                    "FILE".to_string()
                                } else {
                                    h.kind.to_uppercase()
                                })
                                .text_color(rgb(MUTED))
                                .into_any_element()
                        };
                        div()
                            .id(("result", i))
                            .debug_selector(move || {
                                if i == 1 {
                                    "result-second".into()
                                } else {
                                    "result-first".into()
                                }
                            })
                            .h(px(100.))
                            .p_2()
                            .flex()
                            .gap_3()
                            .bg(rgb(if s.selected == Some(i) {
                                0x34302a
                            } else {
                                PANEL
                            }))
                            .cursor_pointer()
                            .on_click(cx.listener(move |s, _, _, cx| {
                                s.selected = Some(i);
                                cx.notify();
                            }))
                            .child(preview)
                            .child(
                                div()
                                    .min_w_0()
                                    .flex_1()
                                    .flex()
                                    .flex_col()
                                    .child(format!(
                                        "{}. {}",
                                        i + 1,
                                        h.path.file_name().unwrap_or_default().to_string_lossy()
                                    ))
                                    .child(
                                        div().text_xs().text_color(rgb(ORANGE)).child(format!(
                                            "Score {:.4} · {}",
                                            h.score, h.location
                                        )),
                                    )
                                    .child(
                                        div()
                                            .text_xs()
                                            .text_color(rgb(MUTED))
                                            .overflow_hidden()
                                            .child(h.snippet.chars().take(180).collect::<String>()),
                                    ),
                            )
                    })
                    .collect::<Vec<_>>()
            }),
        )
        .size_full();
        let mut preview = div()
            .id("selected-preview")
            .flex_1()
            .min_w_0()
            .h_full()
            .overflow_y_scroll()
            .p_4()
            .flex()
            .flex_col()
            .gap_3()
            .bg(rgb(PANEL));
        if let Some(h) = self.selected.and_then(|i| self.hits.get(i)) {
            preview = preview
                .child(
                    div().text_lg().child(
                        h.path
                            .file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .into_owned(),
                    ),
                )
                .child(div().text_sm().text_color(rgb(ORANGE)).child(format!(
                    "Score {:.4} · {} · {}",
                    h.score, h.kind, h.location
                )))
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(MUTED))
                        .child(h.path.to_string_lossy().into_owned()),
                );
            if is_image(&h.path) {
                preview = preview.child(
                    img(h.path.clone())
                        .w_full()
                        .h(px(320.))
                        .object_fit(ObjectFit::Contain),
                );
            }
            preview = preview.child(div().text_sm().child(h.snippet.clone()));
            let path = h.path.clone();
            preview = preview.child(
                button("open-external", "Open in default application", false).on_click(
                    cx.listener(move |s, _, _, cx| {
                        let path = path.clone();
                        s.status = "Opening default application…".into();
                        let task = cx.background_executor().spawn(async move {
                            std::process::Command::new("xdg-open")
                                .arg(path)
                                .stdin(std::process::Stdio::null())
                                .stdout(std::process::Stdio::null())
                                .stderr(std::process::Stdio::null())
                                .status()
                        });
                        cx.spawn(async move |this, cx| {
                            let result = task.await;
                            let _ = this.update(cx, |s, cx| {
                                s.status = match result {
                                    Ok(status) if status.success() => {
                                        "Opened in default application".into()
                                    }
                                    other => format!("Could not open file: {other:?}"),
                                };
                                cx.notify();
                            });
                        })
                        .detach();
                        cx.notify();
                    }),
                ),
            );
        } else {
            preview=preview.child("Select a result to preview text, location and image. Audio, video and documents open in your default application.");
        }
        let mut app=div().track_focus(&self.focus).relative().size_full().flex().flex_col().bg(rgb(BG)).text_color(rgb(0xe9eaf0)).font_family("sans-serif").on_key_down(cx.listener(|s,e:&KeyDownEvent,_,cx|{match e.keystroke.key.as_str(){"escape"=>{if s.dialog.is_some(){s.answer(false,cx);}else{s.cancel(cx);}},"enter" if s.dialog.is_none()=>s.search(cx),_=>{}}}))
.child(div().p_4().flex().flex_col().gap_3().child(div().text_xl().text_color(rgb(ORANGE)).child("FINDMATCH · Local search"))
.child(div().flex().gap_3().items_center().child(div().w(px(85.)).child("Directory")).child(div().flex_1().child(self.directory.clone())).child(button("choose-directory","Choose directory",false).on_click(cx.listener(|s,_,_,cx|s.choose(false,cx)))))
.child(div().flex().gap_3().items_center().child(div().w(px(85.)).child("Query")).child(div().flex_1().child(self.query.clone())).child(button("attach-files","Attach files…",false).on_click(cx.listener(|s,_,_,cx|s.choose(true,cx)))))
.child(refs).child(div().text_xs().text_color(rgb(MUTED)).child("Leave query empty for reference-only search. Combine multiple attached files with text."))
.child(div().flex().gap_3().items_center().child(div().child("Cache")).child(div().flex_1().child(self.cache.clone())).child(div().child("Limit")).child(div().w(px(70.)).child(self.limit.clone())).child(button("rebuild",if self.rebuild{"☑ Rebuild index"}else{"☐ Rebuild index"},false).on_click(cx.listener(|s,_,_,cx|{if s.worker.is_none(){s.rebuild = !s.rebuild;cx.notify();}}))).child(button("search",if self.worker.is_some(){"Searching…"}else{"Search"},true).on_click(cx.listener(|s,_,_,cx|s.search(cx)))).child(button("cancel-search","Cancel",false).on_click(cx.listener(|s,_,_,cx|s.cancel(cx))))))
.child(div().flex_1().min_h_0().flex().gap_3().child(div().w(px(450.)).h_full().child(rows)).child(preview)).child(div().p_3().text_sm().text_color(rgb(MUTED)).child(self.status.clone()));
        if let Some((p, prompt)) = &self.dialog {
            let (title, body, confirm) = match p {
                Permission::Processing => (
                    "Allow local processing?",
                    "Read files and write their local search index. This permission does not authorize a model download.",
                    "Allow processing",
                ),
                Permission::Download => (
                    "Allow model download?",
                    "Separately download EmbeddingGemma 2 (approximately 485 MB). Your search files are not uploaded.",
                    "Allow download",
                ),
            };
            app =
                app.child(
                    div()
                        .id("consent-overlay")
                        .occlude()
                        .absolute()
                        .inset_0()
                        .bg(rgba(0x000000c0))
                        .flex()
                        .items_center()
                        .justify_center()
                        .child(
                            div()
                                .w(px(540.))
                                .p_6()
                                .bg(rgb(PANEL))
                                .rounded_lg()
                                .flex()
                                .flex_col()
                                .gap_4()
                                .child(div().text_xl().child(title))
                                .child(body)
                                .child(div().text_sm().text_color(rgb(MUTED)).child(prompt.clone()))
                                .child(
                                    div()
                                        .flex()
                                        .gap_3()
                                        .justify_end()
                                        .child(button("deny-consent", "Decline", false).on_click(
                                            cx.listener(|s, _, _, cx| s.answer(false, cx)),
                                        ))
                                        .child(button("allow-consent", confirm, true).on_click(
                                            cx.listener(|s, _, _, cx| s.answer(true, cx)),
                                        )),
                                ),
                        ),
                );
        }
        app
    }
}
pub fn launch(options: Options) {
    Application::new().run(move |cx: &mut App| {
        let bounds = Bounds::centered(None, size(px(1200.), px(850.)), cx);
        match cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                titlebar: Some(TitlebarOptions {
                    title: Some("Findmatch".into()),
                    ..Default::default()
                }),
                app_id: Some("findmatch-gui".into()),
                window_min_size: Some(size(px(900.), px(600.))),
                ..Default::default()
            },
            move |window, cx| {
                window.set_window_title("Findmatch");
                cx.new(|cx| {
                    let app = SearchApp::new(options, cx);
                    window.focus(&app.query.read(cx).focus);
                    app
                })
            },
        ) {
            Ok(_) => cx.activate(true),
            Err(e) => {
                eprintln!("Could not open native window: {e:#}");
                cx.quit();
            }
        }
        cx.on_window_closed(|cx| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
    });
}
impl SearchApp {
    fn attach(&mut self, paths: Vec<PathBuf>, cx: &mut Context<Self>) {
        if self.worker.is_some() {
            return;
        }
        for p in paths {
            if !self.references.contains(&p) {
                self.references.push(p);
            }
        }
        cx.notify();
    }
    fn handle(&mut self, event: Event, cx: &mut Context<Self>) {
        match event {
            Event::Consent(p, text) => self.dialog = Some((p, text)),
            Event::Progress(text) => {
                if text.starts_with("findmatch: skipped ") {
                    self.skipped_summary = Some(text.clone());
                }
                self.status = text;
            }
            Event::Finished(result) => {
                self.worker = None;
                self.dialog = None;
                match result {
                    Ok(hits) => {
                        self.status = format!("{} ranked results", hits.len());
                        if let Some(summary) = &self.skipped_summary {
                            self.status.push_str(&format!(" · {summary}"));
                        }
                        self.hits = hits;
                        self.selected = (!self.hits.is_empty()).then_some(0);
                    }
                    Err(e) => self.status = e,
                }
            }
        }
        cx.notify();
    }
    fn answer(&mut self, yes: bool, cx: &mut Context<Self>) {
        if self.dialog.take().is_some()
            && let Some(w) = &self.worker
        {
            let _ = w.answers.send(yes);
        }
        cx.notify();
    }
    fn cancel(&mut self, cx: &mut Context<Self>) {
        self.dialog = None;
        if let Some(w) = &self.worker {
            w.cancel.store(true, Ordering::Relaxed);
            self.status = "Cancelling…".into();
        }
        cx.notify();
    }
    fn search(&mut self, cx: &mut Context<Self>) {
        if self.worker.is_some() {
            return;
        }
        let limit = self.limit.read(cx).text.parse::<usize>();
        let Ok(limit) = limit else {
            self.status = "Limit must be a positive integer".into();
            cx.notify();
            return;
        };
        let o = Options {
            directory: self.directory.read(cx).text.clone(),
            query: self.query.read(cx).text.clone(),
            cache_dir: self.cache.read(cx).text.clone(),
            limit,
            references: self.references.clone(),
            rebuild: self.rebuild,
        };
        match search::start(&o) {
            Ok(w) => {
                self.worker = Some(w);
                self.skipped_summary = None;
                self.status = "Searching locally…".into();
                cx.spawn(async move |this, cx| {
                    loop {
                        cx.background_executor()
                            .timer(Duration::from_millis(30))
                            .await;
                        let keep = this
                            .update(cx, |s, cx| {
                                let events = s
                                    .worker
                                    .as_ref()
                                    .map(|w| w.events.try_iter().collect::<Vec<_>>())
                                    .unwrap_or_default();
                                for e in events {
                                    s.handle(e, cx);
                                }
                                s.worker.is_some()
                            })
                            .unwrap_or(false);
                        if !keep {
                            break;
                        }
                    }
                })
                .detach();
            }
            Err(e) => self.status = format!("{e:#}"),
        }
        cx.notify();
    }
    fn choose(&mut self, references: bool, cx: &mut Context<Self>) {
        if self.worker.is_some() {
            return;
        }
        let picker = cx.prompt_for_paths(PathPromptOptions {
            files: references,
            directories: !references,
            multiple: references,
            prompt: Some(
                if references {
                    "Attach reference files"
                } else {
                    "Choose search directory"
                }
                .into(),
            ),
        });
        cx.spawn(async move |this, cx| {
            let result = picker.await;
            let _ = this.update(cx, |s, cx| {
                match result {
                    Ok(Ok(Some(paths))) => {
                        if references {
                            s.attach(paths, cx);
                        } else if let Some(p) = paths.first() {
                            s.directory
                                .update(cx, |f, cx| f.set(p.to_string_lossy().into_owned(), cx));
                        }
                    }
                    Ok(Ok(None)) => {}
                    other => s.status = format!("Native chooser unavailable: {other:?}"),
                }
                cx.notify();
            });
        })
        .detach();
    }
}
#[cfg(test)]
mod tests {
    #[gpui::test]
    fn skipped_file_summary_survives_completion(cx: &mut TestAppContext) {
        let (view, cx) = cx.add_window_view(|_, cx| {
            SearchApp::new(
                Options {
                    limit: 10,
                    ..Default::default()
                },
                cx,
            )
        });
        cx.update(|_, cx| {
            view.update(cx, |s, cx| {
                s.handle(super::Event::Progress("findmatch: skipped 1 file(s); results exclude these files. Failed files remain pending for retry.".into()), cx);
                s.handle(super::Event::Finished(Ok(vec![])), cx);
                assert!(s.status.contains("skipped 1 file(s)"));
            })
        });
    }
    #[gpui::test]
    fn consent_dismissal_restores_query_keyboard_focus(cx: &mut TestAppContext) {
        let (view, cx) = cx.add_window_view(|window, cx| {
            let s = SearchApp::new(
                Options {
                    query: "initial".into(),
                    limit: 10,
                    ..Default::default()
                },
                cx,
            );
            window.focus(&s.query.read(cx).focus);
            s
        });
        cx.simulate_resize(gpui::size(gpui::px(1200.), gpui::px(850.)));
        for close in 0..4 {
            cx.update(|_, cx| {
                view.update(cx, |s, cx| {
                    s.handle(
                        super::Event::Consent(
                            super::Permission::Processing,
                            "Process? [y/N]".into(),
                        ),
                        cx,
                    )
                })
            });
            cx.run_until_parked();
            match close {
                0 | 1 => {
                    let control = if close == 0 {
                        "deny-consent"
                    } else {
                        "allow-consent"
                    };
                    let b = cx.debug_bounds(control).unwrap();
                    cx.simulate_click(b.center(), gpui::Modifiers::none());
                }
                2 => cx.update(|_, cx| view.update(cx, |s, cx| s.cancel(cx))),
                _ => cx.update(|_, cx| {
                    view.update(cx, |s, cx| {
                        s.handle(super::Event::Finished(Err("cancelled".into())), cx)
                    })
                }),
            }
            cx.run_until_parked();
            cx.simulate_keystrokes("x");
            cx.update(|window, cx| {
                let s = view.read(cx);
                assert!(
                    s.query.read(cx).focus.is_focused(window),
                    "Query focus not restored after dismissal {close}"
                );
                assert_eq!(
                    s.query.read(cx).text,
                    format!("initial{}", "x".repeat(close + 1))
                );
            });
        }
    }
    #[gpui::test]
    fn consent_requires_rendered_button_click_and_removal_targets_one_reference(
        cx: &mut TestAppContext,
    ) {
        let (view, cx) = cx.add_window_view(|_, cx| {
            SearchApp::new(
                Options {
                    limit: 10,
                    references: vec!["a".into(), "b".into()],
                    ..Default::default()
                },
                cx,
            )
        });
        cx.simulate_resize(gpui::size(gpui::px(1200.), gpui::px(850.)));
        cx.run_until_parked();
        let b = cx
            .debug_bounds("remove-first")
            .expect("Remove control rendered");
        cx.simulate_click(b.center(), gpui::Modifiers::none());
        cx.update(|_, cx| {
            assert_eq!(
                view.read(cx).references,
                vec![std::path::PathBuf::from("b")]
            )
        });
        cx.update(|_, cx| {
            view.update(cx, |s, cx| {
                s.handle(
                    super::Event::Consent(
                        super::Permission::Download,
                        "Download EmbeddingGemma 2 [y/N]".into(),
                    ),
                    cx,
                )
            })
        });
        cx.run_until_parked();
        let b = cx.debug_bounds("deny-consent").expect("Decline rendered");
        cx.simulate_click(b.center(), gpui::Modifiers::none());
        cx.update(|_, cx| assert!(view.read(cx).dialog.is_none()));
    }
    #[gpui::test]
    fn ranked_results_render_and_real_click_selects_preview(cx: &mut TestAppContext) {
        let (view, cx) = cx.add_window_view(|_, cx| {
            SearchApp::new(
                Options {
                    limit: 10,
                    ..Default::default()
                },
                cx,
            )
        });
        cx.simulate_resize(gpui::size(gpui::px(1200.), gpui::px(850.)));
        cx.update(|_, cx| {
            view.update(cx, |s, cx| {
                s.handle(
                    super::Event::Finished(Ok(vec![
                        super::Hit {
                            path: "/fixture/first.txt".into(),
                            score: 0.9,
                            snippet: "First snippet".into(),
                            location: "line 4".into(),
                            kind: "text".into(),
                        },
                        super::Hit {
                            path: "/fixture/second.jpg".into(),
                            score: 0.8,
                            snippet: "Second snippet".into(),
                            location: "image".into(),
                            kind: "image".into(),
                        },
                    ])),
                    cx,
                );
            })
        });
        cx.run_until_parked();
        let b = cx
            .debug_bounds("result-second")
            .expect("Second ranked card rendered");
        cx.simulate_click(b.center(), gpui::Modifiers::none());
        cx.update(|_, cx| assert_eq!(view.read(cx).selected, Some(1)));
    }
    #[gpui::test]
    fn references_remove_individually_and_backend_consent_renders(cx: &mut TestAppContext) {
        let (view, cx) = cx.add_window_view(|_, cx| {
            SearchApp::new(
                Options {
                    limit: 10,
                    ..Default::default()
                },
                cx,
            )
        });
        cx.update(|_, cx| {
            view.update(cx, |s, cx| {
                s.attach(vec!["a".into(), "b".into(), "a".into()], cx);
                assert_eq!(s.references.len(), 2);
                s.references.remove(0);
                assert_eq!(s.references, vec![std::path::PathBuf::from("b")]);
                s.handle(
                    super::Event::Consent(
                        super::Permission::Processing,
                        "Process these files before searching? [y/N]".into(),
                    ),
                    cx,
                );
            })
        });
        cx.run_until_parked();
        cx.update(|_, cx| {
            assert!(matches!(
                view.read(cx).dialog,
                Some((super::Permission::Processing, _))
            ))
        });
        cx.simulate_keystrokes("escape");
        cx.update(|_, cx| assert!(view.read(cx).dialog.is_none()));
    }

    use super::{Options, SearchApp};
    use gpui::{KeyDownEvent, Keystroke, TestAppContext};
    #[gpui::test]
    fn initialized_query_receives_real_unicode_keyboard_events(cx: &mut TestAppContext) {
        let (view, cx) = cx.add_window_view(|window, cx| {
            let s = SearchApp::new(
                Options {
                    query: "initial ".into(),
                    limit: 8,
                    ..Default::default()
                },
                cx,
            );
            window.focus(&s.query.read(cx).focus);
            s
        });
        cx.run_until_parked();
        let mut key = Keystroke::parse("x").unwrap();
        key.key_char = Some("λ".into());
        cx.simulate_event(KeyDownEvent {
            keystroke: key,
            is_held: false,
        });
        cx.update(|_, cx| assert_eq!(view.read(cx).query.read(cx).text, "initial λ"));
    }
}

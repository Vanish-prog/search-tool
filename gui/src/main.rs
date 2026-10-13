fn wants_help(args: &[std::ffi::OsString]) -> bool {
    let mut args = args.iter();
    while let Some(flag) = args.next() {
        match flag.to_str() {
            Some("--help" | "-h") => return true,
            Some("--rebuild") => {}
            Some("--directory" | "--query" | "--reference" | "--cache-dir" | "--limit") => {
                // A value is literal even when it resembles a flag.
                args.next();
            }
            _ => return false,
        }
    }
    false
}
fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if wants_help(&args) {
        println!(
            "findmatch-gui [--directory PATH] [--query TEXT] [--reference PATH]…\n              [--cache-dir PATH] [--limit N] [--rebuild]\n\nNative local search. Empty directory is allowed at launch. Attach multiple\nreferences; leave query empty for reference-only searches. Processing and\nmodel downloads require separate explicit permission.\n\nBackend: FINDMATCH_GUI_PYTHON (absolute executable) -m search_tool.cli.\nPYTHONPATH and native-library environment are inherited."
        );
        return;
    }
    match findmatch_gui::options::parse(args) {
        Ok(options) => findmatch_gui::ui::launch(options),
        Err(e) => {
            eprintln!("findmatch-gui: {e:#}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::wants_help;
    use std::ffi::OsString;
    #[test]
    fn help_tokens_as_option_values_remain_literal() {
        for flag in [
            "--query",
            "--directory",
            "--reference",
            "--cache-dir",
            "--limit",
        ] {
            for value in ["--help", "-h"] {
                let args = [OsString::from(flag), OsString::from(value)];
                assert!(
                    !wants_help(&args),
                    "{flag} value {value} must not exit for help"
                );
            }
        }
        assert!(wants_help(&["--help".into()]));
        assert!(wants_help(&[
            "--query".into(),
            "literal".into(),
            "-h".into()
        ]));
    }
}

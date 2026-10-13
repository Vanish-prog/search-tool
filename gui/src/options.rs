use std::ffi::OsString;
use std::path::PathBuf;
#[derive(Default, Clone, Debug)]
pub struct Options {
    pub directory: String,
    pub query: String,
    pub references: Vec<PathBuf>,
    pub cache_dir: String,
    pub limit: usize,
    pub rebuild: bool,
}
pub fn parse(args: impl IntoIterator<Item = OsString>) -> anyhow::Result<Options> {
    let mut out = Options {
        limit: 10,
        ..Options::default()
    };
    let mut args = args.into_iter();
    while let Some(flag) = args.next() {
        if flag == "--rebuild" {
            out.rebuild = true;
            continue;
        }
        let value = args
            .next()
            .ok_or_else(|| anyhow::anyhow!("Missing value for {}", flag.to_string_lossy()))?;
        match flag.to_str() {
            Some("--directory") => {
                out.directory = value
                    .into_string()
                    .map_err(|_| anyhow::anyhow!("Directory must be UTF-8"))?
            }
            Some("--query") => {
                out.query = value
                    .into_string()
                    .map_err(|_| anyhow::anyhow!("Query must be UTF-8"))?
            }
            Some("--reference") => out.references.push(value.into()),
            Some("--cache-dir") => {
                out.cache_dir = value
                    .into_string()
                    .map_err(|_| anyhow::anyhow!("Cache directory must be UTF-8"))?
            }
            Some("--limit") => {
                out.limit = value.to_string_lossy().parse()?;
                anyhow::ensure!(out.limit > 0, "Limit must be positive");
            }
            _ => anyhow::bail!("Unknown option: {}", flag.to_string_lossy()),
        }
    }
    Ok(out)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_launch_arguments_are_rejected() {
        for args in [
            vec!["--limit", "0"],
            vec!["--limit", "-1"],
            vec!["--directory"],
            vec!["--wat", "x"],
        ] {
            assert!(parse(args.into_iter().map(OsString::from)).is_err());
        }
    }
    #[test]
    fn launch_options_preserve_multiple_references_and_literal_query() {
        let args = [
            "--directory",
            "",
            "--query",
            "x; $(touch nope)",
            "--reference",
            "a b.png",
            "--reference",
            "b.txt",
            "--cache-dir",
            "/cache",
            "--limit",
            "7",
            "--rebuild",
        ];
        let got = parse(args.map(OsString::from)).unwrap();
        assert_eq!(got.query, "x; $(touch nope)");
        assert_eq!(got.directory, "");
        assert_eq!(
            got.references,
            vec![PathBuf::from("a b.png"), PathBuf::from("b.txt")]
        );
        assert_eq!(got.limit, 7);
        assert_eq!(got.cache_dir, "/cache");
        assert!(got.rebuild);
    }
}

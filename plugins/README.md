# Local plugins

Drop-in plugins for this project live here. Nvox also loads plugins from
`~/.config/nvox/plugins` and from installed packages that expose the
`nvox.plugins` entry point.

Plugin directories are configured in `nvox.toml`:

```toml
[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
```

See [docs/plugins.md](../docs/plugins.md) for the plugin API and the declarative
TOML command-plugin format (available from M1/M5).

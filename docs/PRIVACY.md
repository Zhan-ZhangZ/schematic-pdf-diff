# Repository privacy and data hygiene

This repository must not contain customer or project-confidential material.

Never commit:

- Source or exported PDF schematics.
- Bill-of-material or change-list spreadsheets.
- Before/after screenshots or generated comparison reports.
- Web job uploads, logs, manifests, or downloaded archives.
- Customer names, project names, design references, net lists, or internal paths.
- API keys, access tokens, passwords, tunnel configuration, or environment files.

The `.gitignore` blocks common binary document formats and generated output, but
it is not a substitute for review. Before every push:

```bash
git status --short
git diff --cached --stat
git diff --cached
rg -n -i 'customer-project-name|product-name|secret|token|password' .
```

Use synthetic fixtures containing generic references such as `R1`, `C2`, `U3`,
and `VCC`.

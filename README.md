# BrandLab Skill Distribution

Private distribution repository for the BrandLab Translation Skill.

## Customer installation

Install with Codex:

```text
Install the Codex skill from:
https://github.com/jundihuang/agenticplus/tree/main/skills/brandlab
```

Codex installs it to:

```bash
~/.codex/skills/brandlab
```

Then install Python dependencies:

```bash
python3 -m pip install PyYAML
```

Start a new Codex session and ask:

```text
Use BrandLab to initialize a translation workspace in this directory.
```

## Shopify Controlled Execution

Pure Skill Mode works after Python setup. Shopify Controlled Execution additionally requires Node.js 18+, Shopify CLI, the Shopify AI Toolkit plugin, and explicit Shopify CLI authentication.

See `skills/brandlab/docs/SHOPIFY-SETUP.md`.

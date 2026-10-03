# Agent Guidelines & Operating Procedures

## Git Author Identity
- All git commits in this repository MUST be committed as:
  - **Author Name**: `agent-trevors-bot`
  - **Author Email**: `agent-trevors-bot@users.noreply.github.com`
- Before creating a commit, verify repository git configuration:
  `git config user.name "agent-trevors-bot"`
  `git config user.email "agent-trevors-bot@users.noreply.github.com"`

## Security & Pipeline Standards
- Any new features, changes, or pull requests must pass the security pipeline:
  - Secret scanning (Gitleaks)
  - Python SAST (Bandit)
  - Vulnerability & misconfiguration scanning (Trivy)
  - CodeQL semantic code analysis
  - Dependency CVE audit (pip-audit)
  - Automated test suite (`python3 -m unittest discover`)

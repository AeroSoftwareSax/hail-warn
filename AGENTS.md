# Agent Guidelines & Operating Procedures

## Git Author Identity & Branching Convention
- All git commits in this repository MUST be committed to dedicated local branches named `agent/<agent-slug>/<feature-slug>`.
- Commits must NEVER be made directly to `master`, and branches must not be pushed to remote without authorization.
- Every commit's author name and email MUST identify the individual agent performing the work (e.g. `security-auditor <security-auditor@agents.noreply.local>`), ensuring `git log --format='%an %ae'` accurately reflects individual agent attribution.
- Before creating a commit, verify local repository git configuration:
  `git config user.name "<agent-slug>"`
  `git config user.email "<agent-slug>@agents.noreply.local"`

## Security & Pipeline Standards
- Any new features, changes, or pull requests must pass the security pipeline:
  - Secret scanning (Gitleaks)
  - Python SAST (Bandit)
  - Vulnerability & misconfiguration scanning (Trivy)
  - CodeQL semantic code analysis
  - Dependency CVE audit (pip-audit)
  - Automated test suite (`python3 -m unittest discover`)

# Code signing policy

Current status: **installer development and code signing are on hold; install the CLI with uv**.
The previously published Windows 1.1.1 installer remains unsigned.
Noneto has not yet been accepted by SignPath Foundation. An unsigned build or a successful
build workflow does not mean a certificate has been issued or a file has been signed.

## Maintainers and approval

- Author, committer, reviewer, and release approver: [VincentPelos](https://github.com/VincentPelos).
- Contributions from other authors require maintainer review before entering a release.
- Every production signing request must be approved manually under the provider's policy.
- Maintainers must use multi-factor authentication for GitHub and the signing service.

## Source and scope

The public `Build release candidate` workflow builds on GitHub-hosted Windows runners from
this repository. Python packages and the Windows application share the version declared
in `pyproject.toml`. The build recipes and dependency hashes are published under `packaging/`.

The prepared signing targets are Noneto's `noneto.exe` and Windows Setup. Their product name
and version must match the source. Unsigned files are uploaded as GitHub workflow artifacts
before a signing request; returned signatures must validate with a trusted chain and timestamp.
Checksums are computed after the last signing operation. Upstream Python, DLLs, WebView2
bindings, and mpv must not be re-signed under Noneto's identity.

The current Inno Setup recipe generates an unsigned uninstaller. Its signing integration
must be reviewed with SignPath before claiming complete installer/uninstaller coverage.
The application-signing and installer-signing configurations are preparation templates;
they have not yet been accepted or exercised against a production SignPath account.

## Privacy

Noneto connects to TIDAL only when the user launches the application and authorizes their
account. It does not request or store the account password. Local tokens use Windows DPAPI;
logs remain on the user's computer and exclude credentials and signed media URLs.
Package managers contact their package sources; Setup downloads the pinned mpv distribution
from its maintainer's SourceForge archive. Music is streamed, not saved.

See [TIDAL's privacy notice](https://tidal.com/privacy),
[GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement),
[PyPI's privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/), and
[SourceForge's privacy statement](https://slashdotmedia.com/privacy-statement/).

## Planned provider

An application draft to [SignPath Foundation](https://signpath.org/) was prepared but has
not been submitted. There is no active signing application while this work is on hold.
If accepted, this policy and release pages will identify the actual signing provider and
certificate publisher, including the attribution required by their terms. No sponsorship,
certificate, approval, or removal of SmartScreen warnings is claimed while that is pending.

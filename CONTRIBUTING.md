# Contributing to tether

You are welcome to contribute. There is no contributor license agreement to sign and no sign-off to add. Open an issue or a pull request.

## How contributions work

**A contribution is a prompt.** Tell us the change you want, precisely enough that a coding agent working under this repository's rules could implement it. A maintainer implements it, under the repository's checks, and credits you in the commit with a `Co-authored-by:` trailer. We do this so the code stays consistent, reviewable, and free of questions about where it came from.

A good prompt has:

1. **What** you want changed, in terms of observable behavior.
2. **Why**: the problem you hit, or the use you have in mind.
3. **Acceptance criteria**: how we will know it works, as concrete cases (inputs and expected results).
4. **Context**: the files, contracts, or decisions that matter (see `docs/helix/`), and anything that must not change.
5. **Reference code** (optional): a sketch or a diff that illustrates the idea. It informs the implementation; it is not merged as written.

Open it as an issue using the "Contribution prompt" template, or as a pull request that carries the prompt in its description. A pull request with code and no prompt will be asked to state the prompt.

## Before you write it

- Read the [product vision](docs/helix/00-discover/product-vision.md) and its non-goals. A change that adds a server, a synchronous interface, orchestration graphs, or a manifest format is outside the project.
- Read [AGENTS.md](AGENTS.md): it holds the rules every change follows, including the handling rules (no credentials, no private data, fabricated examples only).
- Check the [contracts](docs/helix/02-design/contracts/) and [decisions](docs/helix/02-design/adr/). A change to a contract needs the reason stated in the prompt.

## Licensing

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this project, as defined in the Apache-2.0 license, is dual licensed under MIT OR Apache-2.0, without any additional terms or conditions (see [LICENSE-MIT](LICENSE-MIT) and [LICENSE-APACHE](LICENSE-APACHE)). The copyright holder is FocusedDiversity LLC.

## What to expect

A maintainer may ask questions in the issue to sharpen the prompt. Implementation time depends on maintainer capacity. Decisions and their reasons stay in the open, in the issue and in `docs/helix/`.

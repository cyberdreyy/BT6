The actual TypeScript source files for `@privy-io/js-sdk-core` (session manager, auth API, embedded wallet iframe proxy, delegated actions, etc.) are not present in this repository's indexed content — only `src/README.md`, `src/package.json`, `src/LICENSE`, `SECURITY.md`, and `RESEARCHER.md` are available via search and file-reading tools.

I confirmed this by attempting a glob search for `src/**/*.ts` and a grep for `class Privy` (referenced in the wiki's architecture diagram), both of which returned no results, even though the wiki's table of contents references pages like "Session and Token Management," "Iframe Proxy and Secure Context Communication," "Wallet Recovery and Entropy," and "Delegated Wallet Actions and Authorization Signatures" [1](#0-0) .

Because the analog-mapping task requires proving root cause with exact file/function support in the actual implementation (per the "Validate" instructions in the prompt), and none of that implementation code is retrievable through the current index, I cannot responsibly produce a concrete finding — doing so would require speculation rather than evidence.

Due to index size limits, some file contents may be excluded from this Ask interface. If you need to analyze the real session-management, iframe-proxy, delegated-action-signing, or wallet-recovery code for this bug-class analog, please start a Devin session, which will have full filesystem access to the repository's actual source files.

### Citations

**File:** src/README.md (L1-5)
```markdown
# @privy-io/js-sdk-core

> 🔧 The Privy JS SDK is a low-level library and **not intended for general consumption.**
>
> **Before building, please reach out to the Privy team** to discuss your project and which Privy SDK options may be better suited to it.
```

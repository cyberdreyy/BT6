The repository index only contains `src/README.md`, `src/package.json`, `src/LICENSE`, `SECURITY.md`, and `RESEARCHER.md` — none of the actual TypeScript source files (auth, session, embedded wallet, iframe RPC bridge, funding/on-ramp, recovery, smart wallet logic) are present in the index for this repo.

Due to index size limits, the actual implementation files that would be needed to validate a fund-destination-resolution or transfer-failure analog (e.g., funding/on-ramp deposit address generation, embedded wallet transaction signing/serialization, MFA/recovery flows) are not available here. I cannot confirm or rule out a concrete analog without access to that code, and I won't speculate without it. [1](#0-0) 

If you need this analyzed against the real implementation, a Devin session with full repository access would be needed to inspect the actual funding/wallet-provider and transaction-serialization code paths.

### Citations

**File:** src/README.md (L1-5)
```markdown
# @privy-io/js-sdk-core

> 🔧 The Privy JS SDK is a low-level library and **not intended for general consumption.**
>
> **Before building, please reach out to the Privy team** to discuss your project and which Privy SDK options may be better suited to it.
```

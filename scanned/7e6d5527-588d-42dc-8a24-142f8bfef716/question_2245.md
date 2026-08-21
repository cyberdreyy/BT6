# Q2245: remove clears every signer in session-signers.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use addSessionSigners (getWallet then updateWallet with additional_signers.concat) to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call addSessionSigners (getWallet then updateWallet with additional_signers.concat) with multiple signers present and assert only the requested one is removed.

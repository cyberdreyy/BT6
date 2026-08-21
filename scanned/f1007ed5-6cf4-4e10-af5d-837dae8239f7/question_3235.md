# Q3235: digest injected through constructor options in session-signers.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through addSessionSigners (getWallet then updateWallet with additional_signers.concat) that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert the flow refuses or the challenge stays unique.

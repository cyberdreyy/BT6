# Q2249: remove clears every signer in resolve.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use resolveCrypto: digest and randomUUID defaults from globalThis.crypto to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call resolveCrypto: digest and randomUUID defaults from globalThis.crypto with multiple signers present and assert only the requested one is removed.

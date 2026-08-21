# Q2359: delegated fallback path for on-device wallets in resolve.ts

## Question
addSessionSigners falls back to delegateWallets when the wallet is not TEE-backed; can an attacker use resolveCrypto: digest and randomUUID defaults from globalThis.crypto to convert a session-signer request into a full delegation the user never approved?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Call the add path with an on-device wallet and an empty signers array.
- Invariant to test: A session-signer request must not silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run resolveCrypto: digest and randomUUID defaults from globalThis.crypto on an on-device wallet and assert the consent prompt describes delegation.

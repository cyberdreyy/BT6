# Q3234: digest injected through constructor options in walletCreate.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through createWalletApiWallet that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to createWalletApiWallet and assert the flow refuses or the challenge stays unique.

# Q2244: remove clears every signer in walletCreate.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use createWalletApiWallet to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call createWalletApiWallet with multiple signers present and assert only the requested one is removed.

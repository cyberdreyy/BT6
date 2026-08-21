# Q1307: session-signer add falls back to delegation in revokeWallets.ts

## Question
addSessionSigners delegates instead when the wallet is not TEE-backed; can an attacker use revokeWallets: requires at least one delegated wallet so a request the app described as adding a server signer instead grants a full delegation?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call the add path with an on-device wallet.
- Invariant to test: A session-signer request must never silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call revokeWallets: requires at least one delegated wallet on an on-device wallet and assert the consent text matches the action.

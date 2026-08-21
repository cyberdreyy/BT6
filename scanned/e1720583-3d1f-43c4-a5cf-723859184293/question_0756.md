# Q0756: revoke removes every delegation in delegateWallet.ts

## Question
revokeWallets calls the revoke route with no arguments, dropping all delegations; can an attacker trigger delegateWallet: checks address belongs to user so a user's unrelated legitimate delegation is destroyed while the attacker's session-signer access persists via another path?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call revoke while both delegation and TEE session signers exist.
- Invariant to test: Revocation must be scoped and must cover every access path it claims to remove.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call delegateWallet: checks address belongs to user with mixed access types and assert full, scoped revocation.

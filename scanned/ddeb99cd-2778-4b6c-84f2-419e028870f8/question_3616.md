# Q3616: revoke result not verified against server in delegateWallet.ts

## Question
revokeWallets returns the refreshed user without asserting that no delegation remains; can an attacker leave a residual delegation that delegateWallet: checks address belongs to user reports as revoked?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Return a refresh that still shows a delegated wallet.
- Invariant to test: Revocation must be verified in the result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh to delegateWallet: checks address belongs to user and assert failure is reported.

# Q0536: already-delegated short circuit in delegateWallet.ts

## Question
delegateWallet returns the user unchanged when wallet.delegated is already true; can an attacker exploit that early return in delegateWallet: checks address belongs to user so the app believes a fresh consent occurred when none did?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call delegate twice and inspect what the second call reports.
- Invariant to test: A no-op must be distinguishable from a fresh authorisation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call delegateWallet: checks address belongs to user twice and assert the second result is marked as a no-op.

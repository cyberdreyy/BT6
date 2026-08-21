# Q1856: delegate then revoke race in delegateWallet.ts

## Question
delegate and revoke both mutate the same server-side state with no client-side ordering; can an attacker interleave them through delegateWallet: checks address belongs to user so the final state differs from the user's last intent?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Fire both concurrently and inspect the final state.
- Invariant to test: Concurrent authorisation mutations must be serialised or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: race delegateWallet: checks address belongs to user calls and assert the last intent wins deterministically.

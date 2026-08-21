# Q1746: delegation state confirmed by refresh only in delegateWallet.ts

## Question
Both flows end by re-reading the user; can an attacker return a refresh that misreports delegation so delegateWallet: checks address belongs to user reports success for an operation that failed?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Return a refresh with the delegated flag flipped.
- Invariant to test: Reported success must be derived from the operation result, not a subsequent read.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh and assert delegateWallet: checks address belongs to user reports failure.

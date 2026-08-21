# Q0646: delegated flag read from a stale user in delegateWallet.ts

## Question
The delegated flag comes from the user object fetched at the start of the call; can an attacker revoke between the read and the consent so delegateWallet: checks address belongs to user skips a needed consent or performs a duplicate one?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Revoke during the call and observe the outcome.
- Invariant to test: Delegation state must be re-validated immediately before the mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke mid-call in delegateWallet: checks address belongs to user and assert abort.

# Q2296: revoke route takes no body in delegateWallet.ts

## Question
DelegatedWalletsApi.revoke posts an empty body; can an attacker trigger delegateWallet: checks address belongs to user repeatedly so a user's re-established delegation is immediately removed each time, keeping them dependent on a flow the attacker controls?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call revoke repeatedly around the user's delegate calls.
- Invariant to test: Revocation must be an authenticated, user-initiated action with a clear audit result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: interleave repeated delegateWallet: checks address belongs to user calls with delegation and assert user intent prevails.

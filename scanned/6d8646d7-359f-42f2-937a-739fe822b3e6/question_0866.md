# Q0866: revoke refuses when nothing is delegated in delegateWallet.ts

## Question
revokeWallets throws delegated_actions_no_wallet_to_revoke when no wallet is delegated; can an attacker exploit that precondition through delegateWallet: checks address belongs to user so a partially applied delegation cannot be revoked?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Create a state where the server has a delegation the client-side user object does not show, then revoke.
- Invariant to test: Revocation must not depend on a client-side view of delegation state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: desynchronise the user object and assert delegateWallet: checks address belongs to user still issues the revoke.

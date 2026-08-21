# Q3066: delegation status cached in the user object in delegateWallet.ts

## Question
Apps read `delegated` from the cached user; can an attacker cause delegateWallet: checks address belongs to user to leave a stale flag so the app shows delegation as revoked while it is active?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Revoke and inspect the cached user in the app.
- Invariant to test: Authorisation state shown to users must be freshly read after each mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user returns a freshly fetched user.

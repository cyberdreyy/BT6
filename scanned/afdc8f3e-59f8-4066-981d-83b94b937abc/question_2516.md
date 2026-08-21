# Q2516: classification used for both listing and authorization in delegateWallet.ts

## Question
The same predicate powers wallet listings and delegation eligibility; can an attacker exploit that dual use through delegateWallet: checks address belongs to user so a wallet visible in a list is wrongly assumed delegable?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Compare the listing set with the delegable set for a mixed user.
- Invariant to test: Listing and authorization predicates must be separately justified.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user re-checks eligibility independently of listing.

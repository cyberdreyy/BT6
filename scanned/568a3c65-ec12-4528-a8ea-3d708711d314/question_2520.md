# Q2520: classification used for both listing and authorization in embedded-wallets.ts

## Question
The same predicate powers wallet listings and delegation eligibility; can an attacker exploit that dual use through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so a wallet visible in a list is wrongly assumed delegable?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Compare the listing set with the delegable set for a mixed user.
- Invariant to test: Listing and authorization predicates must be separately justified.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) re-checks eligibility independently of listing.

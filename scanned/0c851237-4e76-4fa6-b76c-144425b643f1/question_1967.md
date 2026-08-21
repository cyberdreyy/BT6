# Q1967: delegated wallets carry a wallet index in revokeWallets.ts

## Question
The delegation payload includes walletIndex from the account object; can an attacker submit an index through revokeWallets: requires at least one delegated wallet that points at a different wallet than the address?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Submit an address and index that disagree.
- Invariant to test: Address and index in the delegation payload must be verified consistent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing pair to revokeWallets: requires at least one delegated wallet and assert rejection.

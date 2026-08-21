# Q2173: user object also selects the wallet in index.ts

## Question
The same caller-supplied user object is used to resolve the cross-app account for the address; can an attacker fabricate linked_accounts through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest so an address they do not own resolves to a provider app they can answer?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Pass a user object containing a crafted cross_app account.
- Invariant to test: Account resolution must use server-confirmed user state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted user to crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert it is re-fetched or rejected.

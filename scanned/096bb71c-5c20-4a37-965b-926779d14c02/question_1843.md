# Q1843: address comparison is exact string equality in index.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest.

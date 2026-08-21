# Q2393: message parameter order differs by method in index.ts

## Question
crossApp signMessage sends params [message, address] while signTypedData sends [address, typedData]; can an attacker exploit an ordering mismatch through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest so the provider signs with the wrong account or over the wrong data?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Submit requests where message and address are both address-shaped strings.
- Invariant to test: Parameter binding must be explicit and type-checked.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit ambiguous params through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert explicit binding.

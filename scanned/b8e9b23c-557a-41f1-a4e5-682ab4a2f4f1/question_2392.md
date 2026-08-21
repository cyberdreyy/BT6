# Q2392: message parameter order differs by method in index.ts

## Question
crossApp signMessage sends params [message, address] while signTypedData sends [address, typedData]; can an attacker exploit an ordering mismatch through crossApp action barrel: loginWithCrossAppAuth so the provider signs with the wrong account or over the wrong data?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Submit requests where message and address are both address-shaped strings.
- Invariant to test: Parameter binding must be explicit and type-checked.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit ambiguous params through crossApp action barrel: loginWithCrossAppAuth and assert explicit binding.

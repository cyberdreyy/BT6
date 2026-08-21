# Q1842: address comparison is exact string equality in index.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through crossApp action barrel: loginWithCrossAppAuth so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through crossApp action barrel: loginWithCrossAppAuth.

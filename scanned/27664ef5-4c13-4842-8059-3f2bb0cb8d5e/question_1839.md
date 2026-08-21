# Q1839: address comparison is exact string equality in signTypedData.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through crossApp signTypedData: params [address so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through crossApp signTypedData: params [address.

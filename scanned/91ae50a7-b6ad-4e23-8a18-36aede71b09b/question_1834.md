# Q1834: address comparison is exact string equality in getProviderAccessTokenOrRelink.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through getProviderAccessTokenOrRelink: cached token from storage else relink so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through getProviderAccessTokenOrRelink: cached token from storage else relink.

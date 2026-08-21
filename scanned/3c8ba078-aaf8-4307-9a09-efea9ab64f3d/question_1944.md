# Q1944: smart-wallet method chosen by address membership in getProviderAccessTokenOrRelink.ts

## Question
isCrossAppWalletSmart decides between personal_sign and privy_signSmartWalletMessage purely by address membership in smart_wallets; can an attacker cause the wrong method to be selected in getProviderAccessTokenOrRelink: cached token from storage else relink so the signature has different semantics than the user approved?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Place the address in both lists and observe the chosen method.
- Invariant to test: Signing method selection must be explicit and verified against the wallet type.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct an ambiguous account and assert getProviderAccessTokenOrRelink: cached token from storage else relink rejects rather than guessing.

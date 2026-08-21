# Q1953: smart-wallet method chosen by address membership in index.ts

## Question
isCrossAppWalletSmart decides between personal_sign and privy_signSmartWalletMessage purely by address membership in smart_wallets; can an attacker cause the wrong method to be selected in crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest so the signature has different semantics than the user approved?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Place the address in both lists and observe the chosen method.
- Invariant to test: Signing method selection must be explicit and verified against the wallet type.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct an ambiguous account and assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest rejects rather than guessing.

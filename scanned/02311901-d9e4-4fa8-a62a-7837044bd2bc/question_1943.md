# Q1943: smart-wallet method chosen by address membership in sendCrossAppRequest.ts

## Question
isCrossAppWalletSmart decides between personal_sign and privy_signSmartWalletMessage purely by address membership in smart_wallets; can an attacker cause the wrong method to be selected in sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type so the signature has different semantics than the user approved?

## Target
- File/function: [src/action/crossApp/wallet/utils/sendCrossAppRequest.ts](src/action/crossApp/wallet/utils/sendCrossAppRequest.ts) - sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type
- Entrypoint: any privy.crossApp.wallet.* call
- Attacker controls: the request payload, callbackUrl, and the privy_cross_app_type / privy_cross_app_payload pair returned to the SDK
- Exploit idea: Place the address in both lists and observe the chosen method.
- Invariant to test: Signing method selection must be explicit and verified against the wallet type.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct an ambiguous account and assert sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type rejects rather than guessing.

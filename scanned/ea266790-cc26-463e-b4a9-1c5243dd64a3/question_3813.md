# Q3813: smart wallet detection scans every account in sendCrossAppRequest.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/wallet/utils/sendCrossAppRequest.ts](src/action/crossApp/wallet/utils/sendCrossAppRequest.ts) - sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type
- Entrypoint: any privy.crossApp.wallet.* call
- Attacker controls: the request payload, callbackUrl, and the privy_cross_app_type / privy_cross_app_payload pair returned to the SDK
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type resolves ownership first.

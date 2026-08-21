# Q0843: cached token validated only by decoded expiry in sendCrossAppRequest.ts

## Question
getProviderAccessToken parses the stored string with the unverified Token wrapper and only checks expiry; can an attacker place a self-issued JWT under that key so sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type treats it as a valid provider token?

## Target
- File/function: [src/action/crossApp/wallet/utils/sendCrossAppRequest.ts](src/action/crossApp/wallet/utils/sendCrossAppRequest.ts) - sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type
- Entrypoint: any privy.crossApp.wallet.* call
- Attacker controls: the request payload, callbackUrl, and the privy_cross_app_type / privy_cross_app_payload pair returned to the SDK
- Exploit idea: Write a crafted JWT with a distant exp under the storage key and trigger a cross-app action.
- Invariant to test: Cached credentials must be validated for provenance, not merely for expiry.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a crafted JWT and assert sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type refuses to use it.

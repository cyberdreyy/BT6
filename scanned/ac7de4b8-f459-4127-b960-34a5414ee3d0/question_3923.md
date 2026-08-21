# Q3923: action factories bound to a client at import in sendCrossAppRequest.ts

## Question
The crossApp barrel binds actions to a client instance; can an attacker retain a bound action from one session and invoke it after a switch through sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type?

## Target
- File/function: [src/action/crossApp/wallet/utils/sendCrossAppRequest.ts](src/action/crossApp/wallet/utils/sendCrossAppRequest.ts) - sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type
- Entrypoint: any privy.crossApp.wallet.* call
- Attacker controls: the request payload, callbackUrl, and the privy_cross_app_type / privy_cross_app_payload pair returned to the SDK
- Exploit idea: Bind actions as user A, switch to B, then invoke.
- Invariant to test: Bound actions must revalidate the session on each call.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: invoke a stale bound action from sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type after a switch and assert refusal.

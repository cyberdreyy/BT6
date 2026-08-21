import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 10
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = "privy-io/js-sdk-core"
# todo: the name of the repository
REPO_NAME = "js-sdk-core"

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Session, access/identity tokens, and client-side auth state storage
    # =================================================================================
    "src/Session.ts",
    "src/Token.ts",
    "src/session/keys.ts",
    "src/storage/LocalStorage.ts",
    "src/storage/InMemoryStorage.ts",
    "src/client/Privy.ts",
    "src/client/PrivyInternal.ts",
    "src/client/UserApi.ts",
    "src/client/AppApi.ts",
    "src/client/logger.ts",
    "src/Error.ts",
    "src/toAbortSignalTimeout.ts",
    "src/utils/toSearchParams.ts",

    # =================================================================================
    # Login / linking flows and identity binding
    # =================================================================================
    "src/client/auth/AuthApi.ts",
    "src/client/auth/EmailApi.ts",
    "src/client/auth/PhoneApi.ts",
    "src/client/auth/OAuthApi.ts",
    "src/pkce.ts",
    "src/client/auth/PasskeyApi.ts",
    "src/client/auth/SiweApi.ts",
    "src/client/auth/SiwsApi.ts",
    "src/solana/createSiwsMessage.ts",
    "src/client/auth/FarcasterApi.ts",
    "src/client/auth/FarcasterV2Api.ts",
    "src/client/auth/TelegramApi.ts",
    "src/client/auth/CustomProviderApi.ts",
    "src/client/auth/GuestApi.ts",
    "src/client/auth/SmartWalletApi.ts",
    "src/client/auth/maybeCreateWalletOnLogin.ts",
    "src/utils/phoneNumberUtils.ts",

    # =================================================================================
    # MFA gating and wallet recovery paths
    # =================================================================================
    "src/client/mfa/MfaApi.ts",
    "src/client/mfa/MfaSmsApi.ts",
    "src/client/mfa/MfaPasskeyApi.ts",
    "src/client/MfaPromises.ts",
    "src/embedded/withMfa.ts",
    "src/client/recovery/RecoveryApi.ts",
    "src/client/recovery/RecoveryOAuthApi.ts",
    "src/client/recovery/RecoveryICloudApi.ts",
    "src/embedded/utils/index.ts",
    "src/embedded/errors.ts",

    # =================================================================================
    # Embedded wallet key material and the iframe/provider trust boundary
    # =================================================================================
    "src/embedded/EmbeddedWalletProxy.ts",
    "src/embedded/EmbeddedWalletProvider.ts",
    "src/embedded/EmbeddedSolanaWalletProvider.ts",
    "src/embedded/EmbeddedBitcoinWalletProvider.ts",
    "src/embedded/EventCallbackQueue.ts",
    "src/embedded/stack/walletRpc.ts",
    "src/embedded/stack/walletCreate.ts",
    "src/embedded/stack/session-signers.ts",
    "src/embedded/stack/wallet-api-eth-transaction.ts",
    "src/embedded/stack/wallet-api-eth-typed-data.ts",
    "src/client/EmbeddedWalletApi.ts",
    "src/utils/entropy.ts",
    "src/crypto/resolve.ts",
    "src/utils/encodings.ts",
    "src/utils/generateWalletIdempotencyKey.ts",

    # =================================================================================
    # Wallet API signing, authorization signatures, and payload serialization
    # =================================================================================
    "src/wallet-api/generate-authorization-signature.ts",
    "src/wallet-api/rpc.ts",
    "src/wallet-api/raw-sign.ts",
    "src/wallet-api/create.ts",
    "src/wallet-api/get-wallet.ts",
    "src/wallet-api/update-wallet.ts",
    "src/wallet-api/unified-wallet.ts",
    "src/utils/typedData/generateDomainType.ts",
    "src/solana/offchain-message.ts",
    "src/solana/getWalletPublicKeyFromTransaction.ts",
    "src/solana/isVersionedTransaction.ts",
    "src/solana/ConnectedStandardSolanaWallet.ts",
    "src/solana/client.ts",
    "src/smart-wallets.ts",

    # =================================================================================
    # Delegated actions, cross-app wallet requests, deposit addresses, funding
    # =================================================================================
    "src/action/delegatedActions/delegateWallet.ts",
    "src/action/delegatedActions/revokeWallets.ts",
    "src/action/delegatedActions/utils.ts",
    "src/client/DelegatedWalletsApi.ts",
    "src/action/crossApp/loginWithCrossAppAuth.ts",
    "src/action/crossApp/linkWithCrossAppAuth.ts",
    "src/action/crossApp/wallet/signMessage.ts",
    "src/action/crossApp/wallet/signTypedData.ts",
    "src/action/crossApp/wallet/sendTransaction.ts",
    "src/action/crossApp/wallet/utils/sendCrossAppRequest.ts",
    "src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts",
    "src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts",
    "src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts",
    "src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts",
    "src/client/CrossAppApi.ts",
    "src/action/depositAddress/generate.ts",
    "src/action/depositAddress/resolve-refund-address.ts",
    "src/action/depositAddress/polling.ts",
    "src/utils/poll.ts",
    "src/client/funding/FundingApi.ts",
    "src/client/funding/MoonpayOnRampApi.ts",
    "src/client/funding/CoinbaseOnRampApi.ts",
    "src/funding/moonpay.ts",
    "src/funding/coinbase.ts",
    "src/utils/getIsTokenUsdc.ts",
    "src/solana/getSolanaRpcEndpointForCluster.ts",
    "src/solana/getSolanaUsdcMintAddressForCluster.ts",

    # =================================================================================
    # Account/wallet selection helpers that decide which key signs
    # =================================================================================
    "src/utils/getUserEmbeddedEthereumWallet.ts",
    "src/utils/getAllUserEmbeddedEthereumWallets.ts",
    "src/utils/getUserEmbeddedSolanaWallet.ts",
    "src/utils/getAllUserEmbeddedSolanaWallets.ts",
    "src/utils/getAllUserEmbeddedBitcoinWallets.ts",
    "src/utils/getUserSmartWallet.ts",
    "src/utils/embedded-wallets.ts",
    "src/utils/shouldCreateEmbeddedEthWallet.ts",
    "src/utils/shouldCreateEmbeddedSolWallet.ts",
    "src/utils/formatters.ts",
    "src/utils/toObjectKeys.ts",
]


target_scopes = [
    "Critical. An unprivileged attacker, using only public SDK APIs, their own Privy account, or a web origin they control, can obtain another user's embedded-wallet key material (entropy, key shares, session-signer or authorization private keys) or otherwise gain the ability to sign with that user's wallet.",
    "Critical. An unprivileged attacker can make the SDK sign or submit a transaction, typed-data, SIWE/SIWS or raw payload that differs from what the user approved (different recipient, amount, chain/cluster, EIP-712 domain, or wallet), so the victim's funds or account are taken.",
    "Critical. An unprivileged attacker can defeat client-side identity binding, so a session, access/identity token, user object, or linked account belonging to another user or another app is accepted and used to authenticate or authorize wallet actions as that victim.",
    "Critical. An unprivileged attacker can bypass or downgrade MFA, recovery, or delegated-action authorization gates (withMfa, MfaPromises, recovery upgrade paths, delegate/revoke flows), so a privileged wallet operation completes without the required user approval.",
    "High. An attacker-controlled origin, iframe/postMessage peer, or cross-app provider can inject or replay wallet RPC responses, OAuth/PKCE state, or cross-app access tokens that the SDK accepts without origin, nonce, or request-identity validation.",
    "High. An unprivileged attacker can cause user funds or credentials to be routed to attacker-controlled destinations or storage (deposit/refund address, on-ramp URL parameters, RPC endpoint, token/mint selection), or can recover another user's tokens and wallet state left in browser storage, logs, or URLs after logout or session switch.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one js-sdk-core target.

    ```
    target_file format:
    "'File Name: src/embedded/withMfa.ts -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact @privy-io/js-sdk-core target:

    {target_file}

    Project focus:
    js-sdk-core is the vanilla TypeScript client for the Privy Auth API and embedded wallets. Focus on login/linking identity binding, session and token handling, MFA and recovery gating, embedded-wallet key material and the iframe RPC boundary, wallet-API authorization signatures, transaction/typed-data serialization, delegated and cross-app wallet actions, and funding/deposit-address destinations.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact TypeScript symbols (class, method, function, type, field) when possible.
    * Attacker is unprivileged only: an ordinary SDK user with their own Privy account, or a web origin/page the attacker controls. No app secrets or API keys, no compromised Privy backend or iframe, no stolen private keys, no browser exploits, no malicious node/peer or RPC-operator assumptions, no phishing or social engineering, no physical device access.
    * Allowed attacker inputs are normal external surfaces: arguments passed to public SDK methods (addresses, chainId/cluster, transaction, typed-data, message, CAIP/token identifiers), OAuth/PKCE redirect and state parameters, SIWE/SIWS messages, postMessage traffic from an attacker origin, cross-app provider responses, and API/iframe responses reachable inside the attacker's own session.
    * Ignore test files, mocks, docs, generated or dist files, build/config files, and dependency-only issues.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target key-material exposure, signing-intent integrity, identity/session binding, MFA or recovery bypass, cross-app/iframe trust, or fund-destination integrity.
    * Every question must be testable by unit test, integration test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Key material never escapes its boundary: entropy, key shares, session-signer and authorization private keys are never logged, stored in plaintext storage, placed in URLs, or sent to a non-Privy destination.
    * Signed bytes equal approved bytes: the serialized transaction, typed data, or message that reaches the signer matches the caller's input exactly, including chainId, domain, nonce, recipient, and amount.
    * Identity binding is exact: tokens, sessions, user objects, and selected wallets always belong to the currently authenticated user and app; a session switch or logout fully clears prior state.
    * Authorization gates fail closed: missing, errored, timed-out, cancelled, or unrecognized MFA/recovery/delegation results are never treated as approval.
    * Untrusted responses are validated: iframe/postMessage, cross-app, OAuth callback, and API payloads are checked for origin, request id, nonce/state, and expected shape before use.
    * Destinations are derived, not attacker-supplied: refund/deposit addresses, on-ramp URLs, and RPC endpoints resolve only to values bound to the authenticated user's wallet and configured chain.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/integration/fuzz PARAMETERS and assert KEY_CONTAINMENT, SIGNING_INTENT, IDENTITY_BINDING, or AUTHORIZATION_GATE.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused js-sdk-core exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: no app secrets or API keys, no compromised Privy backend or iframe, no stolen keys, no malicious node/peer, no phishing or social engineering, no physical device access.
- Reject anything that depends only on test/mock/docs/generated/dist/build/config files, dependency bugs alone, or best-practice cleanup without exploitable impact.
- Focus on real paths reachable from public SDK method arguments, an attacker-controlled origin or cross-app provider, OAuth/PKCE callback parameters, or the attacker's own Privy session.

## Validate
- Trace the exact reachable TypeScript path from the attacker input into key handling, signing, session/token binding, MFA or recovery gating, or fund-destination logic.
- Check whether existing validation, origin/state/nonce checks, type guards, or server-side enforcement already stop it.
- Accept only real key-material exposure, unauthorized signing, signing-intent mismatch, identity/session confusion, MFA or recovery bypass, cross-app/iframe trust break, or fund misrouting.
- Require exact file/function support and a reproducible unit/integration/fuzz/invariant PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Privy bounty impact]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Unit/integration test or fuzz/invariant test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for js-sdk-core security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-node/peer, compromised-backend or compromised-iframe, leaked key or app-secret, browser-bug, physical-access, local-network, dependency-only, docs/style, generated or dist file, test/mock/config-only, self-XSS, missing-header, DoS-only, and purely theoretical issues.
- Reject if the exploit needs victim social engineering, unprompted user actions outside normal SDK flows, or impossible setup.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged SDK caller, an ordinary Privy user with their own account, or an attacker-controlled web origin, unless the claim proves privilege escalation from such a path.
- The final impact must map to an in-scope Privy impact such as embedded-wallet key exposure, unauthorized signing or transaction/typed-data intent mismatch, authentication or session/identity confusion, MFA or recovery bypass, delegated or cross-app authorization break, or misrouting of user funds.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: unit PoC, integration test, invariant/fuzz test, or exact manual steps against the SDK.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary SDK user or an attacker-controlled origin trigger this without privileged access?
- Does the code actually behave as claimed?
- Is the impact caused by this code, not by the app integrator, the Privy backend, the iframe, or a dependency alone?
- Is the key exposure, signing, identity, bypass, or fund-routing impact concrete, not hypothetical?
- Would a bounty triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and bounty category]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for js-sdk-core.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-attacker analogs in auth/login binding, session and token handling, MFA and recovery gating, embedded-wallet key material, iframe/postMessage and cross-app trust boundaries, wallet-API authorization signatures, transaction/typed-data serialization, or fund-destination resolution.
- Reject malicious-node/peer, compromised-backend, leaked-key, browser-bug, physical-access, test-only, dependency-only, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable js-sdk-core path.
- Prove root cause with exact file/function support.
- Accept only concrete key exposure, unauthorized or mismatched signing, identity/session confusion, MFA or recovery bypass, cross-app/iframe trust break, or fund misrouting impact.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt

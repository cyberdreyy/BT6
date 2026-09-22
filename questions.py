import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'aera-finance/aera-contracts-public'
# todo: the name of the repository
REPO_NAME = 'aera-contracts-public'

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
    # v1 first-party contracts, libraries, and interfaces
    "v1/AeraVaultV1.sol",
    "v1/PermissiveWithdrawalValidator.sol",
    "v1/interfaces/IAeraVaultV1.sol",
    "v1/interfaces/IBManagedPool.sol",
    "v1/interfaces/IBManagedPoolController.sol",
    "v1/interfaces/IBManagedPoolFactory.sol",
    "v1/interfaces/IBMerkleOrchard.sol",
    "v1/interfaces/IBVault.sol",
    "v1/interfaces/IGuardianAPI.sol",
    "v1/interfaces/IMultiAssetVault.sol",
    "v1/interfaces/IProtocolAPI.sol",
    "v1/interfaces/IUserAPI.sol",
    "v1/interfaces/IWithdrawalValidator.sol",

    # v2 first-party contracts, libraries, and interfaces
    "v2/AeraV2Factory.sol",
    "v2/AeraVaultAssetRegistry.sol",
    "v2/AeraVaultHooks.sol",
    "v2/AeraVaultModulesFactory.sol",
    "v2/AeraVaultV2.sol",
    "v2/Constants.sol",
    "v2/Sweepable.sol",
    "v2/TargetSighashLib.sol",
    "v2/Types.sol",
    "v2/interfaces/IAeraV2Factory.sol",
    "v2/interfaces/IAeraVaultAssetRegistryFactory.sol",
    "v2/interfaces/IAeraVaultHooksEvents.sol",
    "v2/interfaces/IAeraVaultHooksFactory.sol",
    "v2/interfaces/IAssetRegistry.sol",
    "v2/interfaces/IHooks.sol",
    "v2/interfaces/ISweepable.sol",
    "v2/interfaces/IVault.sol",
    "v2/interfaces/IVaultEvents.sol",
    "v2/periphery/AbstractAssetOracle.sol",
    "v2/periphery/Executor.sol",
    "v2/periphery/LlamaPayRouterOracle.sol",
    "v2/periphery/Math.sol",
    "v2/periphery/interfaces/IAeraV2Oracle.sol",
    "v2/periphery/interfaces/IExecutor.sol",
    "v2/periphery/interfaces/ILlamaPayRouterOracle.sol",
    "v2/periphery/interfaces/ILlamaPayRouterOracleTypes.sol",

    # v3 first-party contracts, libraries, and interfaces
    "v3/src/core/Auth2Step.sol",
    "v3/src/core/BaseFeeCalculator.sol",
    "v3/src/core/BaseVault.sol",
    "v3/src/core/BaseVaultDeployer.sol",
    "v3/src/core/BaseVaultFactory.sol",
    "v3/src/core/CallbackHandler.sol",
    "v3/src/core/Constants.sol",
    "v3/src/core/DelayedFeeCalculator.sol",
    "v3/src/core/FeeVault.sol",
    "v3/src/core/FeeVaultDeployer.sol",
    "v3/src/core/HasNumeraire.sol",
    "v3/src/core/MultiDepositorVault.sol",
    "v3/src/core/MultiDepositorVaultDeployDelegate.sol",
    "v3/src/core/MultiDepositorVaultFactory.sol",
    "v3/src/core/PriceAndFeeCalculatorV2.sol",
    "v3/src/core/ProvisionerV2.sol",
    "v3/src/core/SingleDepositorVault.sol",
    "v3/src/core/SingleDepositorVaultDeployDelegate.sol",
    "v3/src/core/SingleDepositorVaultFactory.sol",
    "v3/src/core/Sweepable.sol",
    "v3/src/core/Types.sol",
    "v3/src/core/VaultAuth.sol",
    "v3/src/core/Whitelist.sol",
    "v3/src/core/interfaces/IAuth2Step.sol",
    "v3/src/core/interfaces/IBaseFeeCalculator.sol",
    "v3/src/core/interfaces/IBaseVault.sol",
    "v3/src/core/interfaces/IBaseVaultDeployer.sol",
    "v3/src/core/interfaces/IBaseVaultFactory.sol",
    "v3/src/core/interfaces/IBeforeTransferHook.sol",
    "v3/src/core/interfaces/ICallbackHandler.sol",
    "v3/src/core/interfaces/IDelayedFeeCalculator.sol",
    "v3/src/core/interfaces/IFeeCalculator.sol",
    "v3/src/core/interfaces/IFeeVault.sol",
    "v3/src/core/interfaces/IFeeVaultDeployer.sol",
    "v3/src/core/interfaces/IHasNumeraire.sol",
    "v3/src/core/interfaces/IMultiDepositorVault.sol",
    "v3/src/core/interfaces/IMultiDepositorVaultFactory.sol",
    "v3/src/core/interfaces/IPriceAndFeeCalculatorV2.sol",
    "v3/src/core/interfaces/IProvisionerV2.sol",
    "v3/src/core/interfaces/ISingleDepositorVault.sol",
    "v3/src/core/interfaces/ISingleDepositorVaultFactory.sol",
    "v3/src/core/interfaces/ISolvingGate.sol",
    "v3/src/core/interfaces/ISubmitHooks.sol",
    "v3/src/core/interfaces/ISweepable.sol",
    "v3/src/core/interfaces/IVaultDeployDelegate.sol",
    "v3/src/core/interfaces/IVersioned.sol",
    "v3/src/core/interfaces/IWhitelist.sol",
    "v3/src/core/libraries/CalldataExtractor.sol",
    "v3/src/core/libraries/CalldataReader.sol",
    "v3/src/core/libraries/Pipeline.sol",
    "v3/src/periphery/Constants.sol",
    "v3/src/periphery/Executor.sol",
    "v3/src/periphery/OracleRegistry.sol",
    "v3/src/periphery/interfaces/IExecutor.sol",
    "v3/src/periphery/interfaces/IOracleRegistry.sol",
    "v3/src/periphery/libraries/HooksLibrary.sol",

]


target_scopes = [
    "Critical. In v3/src/core/ProvisionerV2.sol and MultiDepositorVault.sol, can an ordinary depositor use deposit/mint/redeem/withdraw with chosen token, amount, receiver, and allowance to mint unbacked units, burn another holder's units, or withdraw more vault assets than paid for? Trace pricing, rounding, cap accounting, and enter/exit authorization to direct theft of user funds.",
    "Critical. In v3/src/core/ProvisionerV2.sol, can a user who created an async request use requestDeposit/requestRedeem, cancelRequest, or refundRequest with crafted request fields or repeated calls to recover escrow while keeping units or assets, or claim another user's escrow? Trace request hashes, sender/receiver binding, deadlines, and fee transfers to direct theft or permanent freezing of funds.",
    "Critical. In v3/src/core/ProvisionerV2.sol and PriceAndFeeCalculatorV2.sol, can a normal user choose sync or async entry and exit timing, token, and amount to exploit price conversion, stale prices, fee accrual, or cap resets and extract value from other vault holders? Require an actual pricing/accounting code flaw, not a faulty oracle or ordinary market movement.",
    "Critical. In v3/src/core/MultiDepositorVault.sol, v3/src/core/ProvisionerV2.sol, and v3/src/core/interfaces/IBeforeTransferHook.sol, can a share holder transfer, approve, deposit for a receiver, or redeem through a valid public route that evades unit locks or transfer restrictions, steals another holder's claim, or permanently traps holder funds? Test receiver approvals and hook behavior without assuming a malicious hook administrator.",
    "Critical. In v3/src/core/BaseVault.sol, CallbackHandler.sol, libraries/CalldataReader.sol, libraries/CalldataExtractor.sol, and libraries/Pipeline.sol, can an unprivileged caller craft submit calldata or a public callback to bypass caller, proof, operation, or callback validation and move vault assets? Prove the path is reachable without a guardian/solver key or a malicious guardian/solver.",
    "High/Critical. In v3/src/core/FeeVault.sol, BaseFeeCalculator.sol, DelayedFeeCalculator.sol, and PriceAndFeeCalculatorV2.sol, can a normal user manipulate supply, token balances, or call timing through permitted actions so fee accrual or claimFees/claimProtocolFees transfers unearned funds or permanently freezes earned yield? Check fee recipient and vault binding; exclude accountant control.",
    "High/Critical. In v3/src/core/ProvisionerV2.sol, v3/src/core/PriceAndFeeCalculatorV2.sol, and v3/src/periphery/OracleRegistry.sol, can a public caller exploit quote direction, unit/decimal conversion, user-specific override identity, or public commit timing to misprice a valid deposit/redeem and steal or freeze funds? Do not rely on false third-party oracle data, an attacker-chosen oracle, or privileged registry changes.",
    "High/Critical. In v3/src/core/ProvisionerV2.sol and MultiDepositorVault.sol, can a normal holder use the sync redemption cap, refund lock, cancellation fee, or yield-source pull path to make honest holders' assets or unclaimed yield permanently inaccessible? Establish the exact repeatable public transaction and affected balance.",
    "High/Critical. In v1/AeraVaultV1.sol, v2/AeraVaultV2.sol, v2/AeraVaultAssetRegistry.sol, v2/AeraVaultHooks.sol, and v2/periphery/LlamaPayRouterOracle.sol, can an ordinary external caller exploit a public deposit/withdraw/claim/stream path to steal or freeze assets or unclaimed yield in a deployed, bounty-listed legacy vault? Exclude owner, guardian, and treasury-only actions and check current deployment relevance.",
    "Critical/High blind spot. Across v3/src/core/ProvisionerV2.sol, MultiDepositorVault.sol, BaseVault.sol, CallbackHandler.sol, and core/libraries, test a normal user's valid sequence that crosses modules or transaction boundaries: a check binds a request, unit lock, price, receiver, callback, or asset balance to one state, then a later public action consumes different state. Find an overlooked route to theft or permanent freezing, with an in-scope deployed asset and no privileged actor.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """Generate Aera exploit questions for one exact file and scope."""
    prompt = f"""Generate 40-80 distinct, high-signal Aera security questions for:
{target_file}

Treat `File Name:` as the exact target and `Scope:` as the only target impact. Use full repo context and exact Solidity functions. Start each question with a real unprivileged action: deposit, mint, request/cancel/refund, redeem, withdraw, transfer, approve, claim, public callback, or other actually callable entry point. State attacker-owned inputs/units, transaction sequence, failed invariant, affected user funds or yield, and a local-fork/Foundry proof assertion. Trace through connected contracts. Prefer direct theft or permanent freezing; include temporary freezing when the live bounty accepts it. Do not force questions where this file has no reachable path.

No owner, guardian, accountant, solver, treasury, privileged key, role collusion, malicious peer/node, faulty third-party oracle/token, or `execute` by treasury. No MEV-only, generic unbounded memory/gas, config, test/mock/generated, or speculative issue. Respect Aera's live Immunefi assets, impacts, exclusions, and SECURITY.md/Researcher.Md when present. Avoid repeats and known disclosed findings; do not ask for files.

Output only valid Python:
questions = [
    "[File: {target_file}] [Function: exact_symbol] Can an ordinary user perform SPECIFIC_TRANSACTION and SEQUENCE to violate INVARIANT and cause SCOPED_IMPACT? Proof: local-fork/Foundry setup and decisive assertion.",
]
"""
    return prompt


def audit_format(security_question: str) -> str:
    """Generate an Aera exploit validation prompt."""
    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
Analyze this question only in in-scope deployed Aera production contracts. An attacker is an ordinary user with their own tokens, shares, approvals, and transactions; no privileged role or key. Trace the actual public entry point through provisioner, vault, price/fee, hook, callback, and oracle code as relevant. Check access control, hashes, locks, accounting, rounding, caps, freshness, reentrancy, and existing reverts. Exclude malicious guardian/accountant/solver, role collusion, treasury `execute`, faulty external oracle/token, MEV-only, peer/node, and test/config-only paths. Apply live Immunefi scope and SECURITY.md/Researcher.Md where present. Require a concrete in-scope loss or freeze and a local-fork or Foundry PoC; do not invent impact.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])
### Summary
[2-3 sentences]
### Finding Description
[Exact functions, attacker transaction, root cause, and failed checks]
### Impact Explanation
[Funds or yield affected; live bounty impact and severity]
### Likelihood Explanation
[Realistic attacker prerequisites and repeatability]
### Recommendation
[Specific fix]
### Proof of Concept
[Minimal local-fork/Foundry steps and decisive assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """Validate an Aera report against the live bounty."""
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Validate only this claim against deployed assets and current Aera Immunefi rules; read SECURITY.md and Researcher.Md if present. Require exact production file/function/line evidence, a reachable ordinary-user transaction, why guards fail, affected balance, and reproducible local-fork/Foundry PoC. Check prior public audits/disclosures and current deployment. Reject privileged guardian/accountant/solver/treasury paths, role collusion, treasury `execute`, faulty oracle or third-party token, MEV-only, peer/node, test/mock/generated/config-only, and hypothetical loss. Do not replace a weak claim with a new one.

Accepted impacts: Critical direct theft of user funds or permanent freezing of funds; Critical/High theft or permanent freezing of unclaimed yield; Medium temporary freezing of funds or yield for less than one week. Use the live program's impact/likelihood rules for final severity; do not discard a proven Medium. Report only if the asset and impact are in scope.

If valid, output exactly:
Audit Report

## Title
[Bug statement] - ([File: file_path])
## Summary
[2-3 sentences]
## Finding Description
[Code path, root cause, exploit, failed checks]
## Impact Explanation
[Amount, impact class, severity rationale]
## Likelihood Explanation
[Attacker prerequisites and feasibility]
## Recommendation
[Specific fix]
## Proof of Concept
[Reproducible local-fork/Foundry steps and assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """Scan Aera for a reachable analog of an external report."""
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Use the report only to extract its root-cause pattern, required preconditions, and broken invariant. Search Aera production code for a real analog on deployed, bounty-listed assets. Prioritize ProvisionerV2 deposit/mint/request/solveRequestsDirect/cancel/refund/redeem/withdraw, MultiDepositorVault share mint/burn/transfer/locks, BaseVault submit/callback checks where publicly reachable, price/fee conversion, and oracle quote binding; inspect v1/v2 paths only where deployed and in scope. Map the pattern to exact functions and the strongest ordinary-user transaction sequence. Test each guard, state transition, and cross-contract assumption; check whether an alternate public path bypasses a protection. A shared keyword or conceptual similarity is insufficient.

No privileged guardian/accountant/solver/treasury, role collusion, treasury `execute`, malicious peer/node, faulty third-party oracle/token, MEV-only, or generic unbounded resource claim. Require concrete theft or freezing of user funds or unclaimed yield, live Immunefi impact/severity (including Medium temporary freezes), and a local-fork/Foundry PoC. Follow SECURITY.md/Researcher.Md if present. Do not ask for code, report known disclosed issues, or manufacture an analog.

If valid, output exactly:
### Title
[Bug statement] - ([File: file_path])
### Summary
[2-3 sentences]
### Finding Description
[Exact path, ordinary-user inputs, root cause, failed guards]
### Impact Explanation
[Funds/yield affected and live bounty severity]
### Likelihood Explanation
[Prerequisites and feasibility]
### Recommendation
[Specific fix]
### Proof of Concept
[Minimal local-fork/Foundry steps and decisive assertions]

If none, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt

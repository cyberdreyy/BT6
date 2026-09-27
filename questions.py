import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 12
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'Idle-Labs/idle-tranches'
# todo: the name of the repository
REPO_NAME = 'idle-tranches'

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
    # Credit Vault contract (CDO): epoch lifecycle, deposits, withdraw requests, default handling
    # =================================================================================
    "contracts/IdleCDOEpochVariant.sol",
    "contracts/IdleCDOEpochVariantPrefunded.sol",
    "contracts/IdleCDOCreditVault.sol",
    "contracts/GuardedLaunchUpgradable.sol",
    "contracts/IdleCDOStorage.sol",

    # =================================================================================
    # Credit Vault LP token (AA/BB tranches)
    # =================================================================================
    "contracts/IdleCDOTranche.sol",

    # =================================================================================
    # Credit Vault strategy: receipts, claims, loss and default recovery reserve
    # =================================================================================
    "contracts/strategies/idle/IdleCreditVault.sol",

    # =================================================================================
    # Borrower side: programmable borrower and its ERC4626 idle-liquidity sleeve
    # =================================================================================
    "contracts/strategies/idle/ProgrammableBorrower.sol",

    # =================================================================================
    # User-facing periphery: write-off escrow, KYC gate, default distribution, implied price
    # =================================================================================
    "contracts/IdleCreditVaultWriteOffEscrow.sol",
    "contracts/KeyringIdleWhitelist.sol",
    "contracts/DefaultDistributor.sol",
    "contracts/IdleCreditVaultImpliedPrice.sol",
]


target_scopes = [
    "Critical. A lender mints more AA/BB tranche tokens than the underlying they add is worth, because _deposit/_mintSharesAtCurrPrice price at a stale or zero-supply tranchePrice, or depositDuringEpoch's discounted mint ((amount + trancheInterest) * supply / expectedFinal) mis-derives expectedEpochInterest, pendingWithdrawFees, management fee or remaining time, letting the depositor redeem at epoch end more principal or interest than they funded and directly stealing other LPs' deposits.",
    "Critical. A withdraw receipt is worth more than the NAV it removes, because requestWithdraw (including _amount == 0 full-balance mode), _calcInterestWithdrawRequest, _totalWithdrawFees, interestForOverUnderPerformance, or the instant path taken when lastEpochApr > unscaledApr + instantWithdrawAprDelta over-credits interest or under-charges fees versus the _withdrawOps burn, so the claim at stopEpoch is paid from other LPs' principal and leaves the vault insolvent.",
    "Critical. The same receipt is paid twice, early, or at the wrong price, because IdleCreditVault claimWithdrawRequest/claimInstantWithdrawRequest, lastWithdrawRequest/epochNumber gating, withdrawsRequestsByEpoch, apr0Users settlement (_settleApr0/_requestWithdrawApr0/prepareStopEpochWithApr0), lossRecoveryPriceByEpoch or _clearWithdrawClaimForEpoch lets a lender stack or reorder requests across epochs and withdraw underlying that belongs to other receipts or to the default recovery reserve.",
    "Critical. Balance-derived accounting is steered from outside, because getContractValue/_managedContractValue, unclaimedFees, lastNAVAA/lastNAVBB, _skimDonatedAssets placement, _virtualPriceAux or _forceUpdateAccounting read raw underlying or strategy-token balances that a direct transfer, a zero-amount call, or an ordering of permissionless calls can shift, re-pricing tranches in the attacker's favor or tripping the Default revert or emergency path to freeze every depositor.",
    "Critical. A lender extracts more than their pro-rata share after a borrower default, or leaves others unable to claim, because _handleBorrowerDefault, finalizeDefault, finalizeDefaultRecovery, defaultPendingClaimBasis, _defaultPrefundedInstantReserve, _defaultBBBasis, postDefaultRequests, _claimDefaultedWithdrawRequest/_claimDefaultedInstantWithdrawRequest, _transferFundedClaim or _transferDefaultRecovery mis-split recovered funds between active LPs, pending receipts and instant receipts, draining defaultRecoveryReserve and permanently freezing the remaining claimants.",
    "Critical. A lender escapes a realized loss or pushes it onto others, because stopEpochWithDuration's _lossAmount path, previewLossAdjustedWithdrawFunds, collectWithdrawFunds, the BB-first waterfall in _virtualPriceAux, the shutdown in _updateAccounting/_emergencyShutdown, or restoreOperations let them lock a par receipt, redeem, or keep a pre-loss price before the loss is crystallized, stealing principal from the remaining AA/BB holders.",
    "High. Unclaimed yield or fees are stolen, because _calcInterest/_calcInterestWithApr truncation, trancheAPRSplitRatio and _updateSplitRatio with AYS, _calcTrancheInterestShare, _accrueManagementFee/_calculateManagementFee, minted-interest mode (isInterestMinted, mintStrategyTokens, fee shares minted to feeReceiver/owner) or lastEpochInterest let a lender time deposits, withdraw requests or tranche choice to capture interest accrued by other LPs, or permanently strand it.",
    "Critical. Any user reaches a state-changing path meant only for the CDO, borrower, manager or owner, or the periphery moves value it should not, because only-CDO checks and the _transfer override in IdleCreditVault, IdleCDOTranche mint/burn, the self-call guards on sendFundsToBorrower/getFundsFromBorrower, writeOffDeposit's borrower check, initializers on proxies and implementations, IdleCreditVaultWriteOffEscrow create/delete/fullfillWriteOffRequest accounting, DefaultDistributor claim/rate, or KeyringIdleWhitelist/isWalletAllowed are bypassable, letting tokens be minted, burned, moved or claimed without the matching underlying.",
    "Critical. Programmable-borrower or prefunded-epoch accounting mints value that has no backing, because ProgrammableBorrower's totalInterestDueNow, vaultInterestAccrued, vaultLoss or borrowerInterestOwedNow read an ERC4626 share price or balance an outside party can move, or onStartEpoch/onStopEpoch/settleBorrowerInterest/_depositToVault and IdleCDOEpochVariantPrefunded's _beforeStopEpoch/_afterStopEpochWithDuration/checkPrefunding mis-sequence principal, so tranche prices rise on unrealized interest, a healthy vault is flipped to defaulted, or LP principal becomes unredeemable.",
    "Critical/High blind spot. A lender breaks an assumption the credit vault never wrote down: a value computed in one epoch phase (live, buffer, closed with epochDuration == 0, defaulted, finalized, emergency) and trusted in another; per-user state keyed by one epoch but settled in another; an invariant checked on the ordinary path but not on its instant, APR0, minted-interest, prefunded, programmable, closed-pool or post-default twin; tranche tokens, receipts or escrowed positions changing hands between KYC-gated steps; or a rounding or ordering gap that repeats every epoch. Any of these must yield theft of principal or yield, protocol insolvency, or permanent or temporary freezing of funds.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit questions for one idle-tranches (Pareto Credit Vault) target.

    ```
    target_file format:
    "'File Name: contracts/IdleCDOEpochVariant.sol -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact idle-tranches target:

    {target_file}

    Project focus:
    idle-tranches holds the Pareto Credit Vaults: KYC-gated (Keyring) epoch vaults where lenders deposit USDC/USDT into AA/BB tranches (IdleCDOEpochVariant over IdleCDOCreditVault), funds go to an off-chain or programmable borrower for fixed-APR epochs, and exits are withdraw receipts in the IdleCreditVault strategy that are claimed after stopEpoch. Also covered: default recovery, loss waterfalls, fees, the write-off escrow and the default distributor.

    Rules:
    * Treat `File Name:` as the exact contract and `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible. Do not ask for code or say anything is missing. Use exact Solidity symbols.
    * Attacker is unprivileged only: any EOA or contract; a KYC-passing lender who deposits, calls depositDuringEpoch, requestWithdraw and the claims; a holder or recipient of freely transferable tranche tokens; anyone calling fullfillWriteOffRequest or DefaultDistributor.claim; anyone sending tokens directly to a contract; a normal user of the ERC4626 vault the programmable borrower uses.
    * Owner, manager, guardian, borrower, Keyring admin, feeReceiver and the epoch queue are trusted. They may be front-run or back-run but never act maliciously. Treat their calls (startEpoch, stopEpoch*, finalizeDefault, setters) as honest and sequence the attacker around them.
    * Out of scope, never ask: malicious owner/manager/borrower, freezing caused by a borrower default itself, IdleCDOEpochQueue internals, legacy/deprecated tranche and strategy contracts, governance/utilities, ERC-4626 wrappers, wrong third-party oracle data, stablecoin depeg, centralization, DoS/griefing with no fund impact, gas, best practices, anything acknowledged in prior audits.
    * No unbounded-loop, memory, array-growth or "huge input" questions. Every question must be a concrete real-world sequence with real amounts, epoch phase and caller.
    * Generate 40 to 80 high-signal questions. At least 70% must target direct theft of principal, protocol insolvency, or permanent freezing of funds. The rest target theft or permanent freezing of unclaimed yield, or temporary freezing.
    * Cover every epoch phase: buffer (open), running, stopped, closed (epochEndDate == 0), defaulted, finalized, emergency shutdown. Also cover every mode: fixed-APR, APR0, minted interest, instant withdraw, prefunded, programmable borrower.
    * Every question must be provable with a Foundry test (test/foundry, local mainnet fork). Avoid repeated root causes.

    Core invariants:
    * Solvency: underlying held by strategy + CDO + borrower obligations >= active tranche NAV + all unpaid receipts + unclaimedFees.
    * Fair mint/burn: shares minted or burned move lastNAVAA/lastNAVBB by exactly the underlying value, at the correct price for the phase.
    * One receipt, one payout: each withdraw/instant/APR0/post-default receipt is paid once, only when funded, at its own epoch's loss or recovery price.
    * Waterfall: ordinary losses hit BB before AA, default recovery uses one aggregate multiplier, and no one can exit a loss by timing.
    * Isolation: raw donations never become tranche gains, and the recovery reserve is never spent by ordinary claims.
    * Access: only the CDO moves strategy tokens and mints or burns tranches, and only KYC'd wallets enter or request exits.

    Each question must include:
    1. target contract/function;
    2. attacker action (calls, amounts, token transfers);
    3. preconditions (epoch phase, mode, tranche balances, pending receipts);
    4. execution sequence (including honest manager calls in between);
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: contract.function] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Foundry fork test PARAMETERS and assert SOLVENCY, FAIR_MINT_BURN, ONE_RECEIPT_ONE_PAYOUT, WATERFALL, ISOLATION, or ACCESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused idle-tranches exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and its scoped impact.
- Attacker is unprivileged only: any EOA or contract, a KYC-passing lender, a tranche-token holder, a write-off fulfiller, a direct token sender, or a user of the programmable borrower's ERC4626 vault.
- Owner, manager, guardian, borrower, Keyring admin, feeReceiver and the epoch queue are trusted. They may be front-run or back-run, but never act maliciously.
- Reject: malicious privileged roles, freezing caused by a borrower default itself, IdleCDOEpochQueue internals, deprecated/legacy tranches and strategies, governance/utilities, ERC-4626 wrappers, third-party oracle data, depeg, centralization, DoS or gas without fund impact, unbounded-loop/memory speculation, issues acknowledged in prior audits, and test/mock/script/config findings.
- Focus on real impact: theft of principal, protocol insolvency, permanent freezing, theft or permanent freezing of unclaimed yield, or temporary freezing of funds.

## Validate
- Trace the exact call path from the attacker's transaction through IdleCDOEpochVariant / IdleCDOCreditVault / IdleCreditVault (and the escrow, distributor or ProgrammableBorrower if involved).
- Place it in a concrete epoch phase and mode, and interleave only honest startEpoch, stopEpoch*, getInstantWithdrawFunds or finalizeDefault calls.
- Check whether _skimDonatedAssets, _updateAccounting, the whenNotPaused/allow*WithdrawRequest flags, epochNumber gating, isWalletAllowed, only-CDO checks or reserve checks already stop it.
- Quantify: who loses how much, and whether it repeats.
- Require a reproducible Foundry fork PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why existing checks fail]

### Impact Explanation
[Concrete impact and Immunefi category: Direct theft of funds, Protocol insolvency, Permanent freezing, Theft/Permanent freezing of unclaimed yield, or Temporary freezing]

### Likelihood Explanation
[Preconditions, epoch phase and mode, capital needed, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Foundry fork test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict Immunefi-style validation prompt for idle-tranches (Pareto Credit Vault) claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim. Check SECURITY.md and Researcher.Md for scope, exclusions and impact classes.
- Do not invent a new vulnerability, and do not upgrade severity without proof.
- In-scope assets: Pareto Credit Vault Contract (IdleCDOEpochVariant / IdleCDOEpochVariantPrefunded and their IdleCDOCreditVault base), Strategy (IdleCreditVault) and LP Token (IdleCDOTranche), plus the code they call (ProgrammableBorrower, write-off escrow, Keyring gate, DefaultDistributor).
- Accepted impacts (Immunefi):
  - Critical: direct theft of any user funds other than unclaimed yield; permanent freezing of funds; protocol insolvency; MEV that causes freezing or insolvency.
  - High: theft of unclaimed yield; permanent freezing of unclaimed yield.
  - Medium: contract unable to operate due to lack of token funds; temporary freezing of funds. Theft of yield is Medium or High depending on the amount at risk.
- Reject Low, informational, gas and best-practice claims.
- Reject anything that needs a malicious or compromised owner, manager, guardian, borrower, Keyring admin, feeReceiver or queue, or any other privileged address.
- Reject: freezing of funds after a borrower default; Queue contracts (IdleCDOEpochQueue); paused, deprecated or decommissioned vaults and legacy tranches/strategies; governance, utilities and ERC-4626 wrappers; incorrect third-party oracle data (oracle manipulation and flash-loan attacks stay in scope); stablecoin depeg; Sybil, 51% or centralization attacks; DoS without fund impact; leaked keys; issues acknowledged in previous audits; and test/mock/script/config issues.
- The attacker must be unprivileged: any EOA or contract, a KYC-passing lender, a tranche holder, a write-off fulfiller, or a direct token sender.
- Prefer #NoVulnerability over speculation.

## Required Validation Checks
All must pass:
1. Exact in-scope file, contract, function and line references.
2. Clear root cause and broken invariant (solvency, fair mint/burn, one receipt one payout, loss waterfall, donation isolation, access).
3. Reachable path: preconditions (epoch phase, mode, balances) -> attacker transactions -> honest privileged calls, if any -> bad result.
4. Existing guards reviewed and shown insufficient: _skimDonatedAssets, _updateAccounting/Default revert, pause/allow flags, epochNumber gating, isWalletAllowed, only-CDO checks, recovery reserve checks.
5. Quantified loss, with the severity matching the accepted impacts above.
6. Reproducible Foundry PoC on a local fork (required for all severities).

## Silent Triage Questions
- Can an unprivileged user do this while every privileged actor behaves honestly?
- Does the code behave this way on the deployed configuration, not only under an unreachable setting?
- Is the loss real and not just a known borrower-default or credit risk?
- Is it absent from prior audit acknowledgements?
- What exact test proves it?

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
[Concrete impact, Immunefi category, and severity rationale]

## Likelihood Explanation
[Attacker capability, epoch phase and mode, capital needed, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal Foundry fork test plan with assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for idle-tranches (Pareto Credit Vaults).
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint. The analog must stand on idle-tranches' own code.
- Attacker is unprivileged only: any EOA or contract, a KYC-passing lender, a tranche-token holder, a write-off fulfiller, a direct token sender, or a user of the programmable borrower's ERC4626 vault. Owner, manager, guardian, borrower, Keyring admin, feeReceiver and queue are honest; sequence around their calls, and never make them the attacker.
- Reject: malicious privileged roles, freezing after a borrower default, IdleCDOEpochQueue, legacy tranches/strategies, governance/utilities, ERC-4626 wrappers, third-party oracle data, depeg, DoS or gas without fund impact, unbounded-loop/memory speculation, previously acknowledged issues, and test/mock/script code.

## Map the Bug Class
Translate the external bug to the strongest matching credit-vault surface. Vault/ERC4626 share inflation, first-depositor, rounding or donation bugs map to _deposit, _mintSharesAtCurrPrice, depositDuringEpoch, _virtualPriceAux, getContractValue, _skimDonatedAssets and unclaimedFees. Queued or delayed withdrawal, double-claim or stale-epoch bugs map to requestWithdraw, _calcInterestWithdrawRequest, IdleCreditVault claimWithdrawRequest/claimInstantWithdrawRequest, lastWithdrawRequest/epochNumber, withdrawsRequestsByEpoch, the apr0Users flow and lossRecoveryPriceByEpoch. Then pick the closest remaining group and name the exact function:
- Tranche waterfall, loss socialization and bad-debt escape: _updateAccounting, BB-first loss, stopEpochWithDuration(_lossAmount), previewLossAdjustedWithdrawFunds, collectWithdrawFunds, _emergencyShutdown, restoreOperations.
- Default and recovery distribution: _handleBorrowerDefault, finalizeDefault, finalizeDefaultRecovery, defaultPendingClaimBasis, postDefaultRequests, _claimDefaulted*, _transferFundedClaim, _transferDefaultRecovery, DefaultDistributor.claim.
- Interest, fees and yield split: _calcInterest(WithApr), trancheAPRSplitRatio/_updateSplitRatio (AYS), _calculateManagementFee, _totalWithdrawFees, isInterestMinted/mintStrategyTokens, instant-withdraw APR delta.
- Epoch state machine: startEpoch, stopEpoch, getInstantWithdrawFunds, closed pool (epochEndDate == 0), pause/allow flags, prefunded _beforeStopEpoch/_afterStopEpochWithDuration.
- External vault and hooks: ProgrammableBorrower totalInterestDueNow, vaultInterestAccrued, vaultLoss, onStopEpoch/onStartEpoch, _depositToVault, ERC4626 price or liquidity moves.
- Access, tokens and periphery: IdleCreditVault _onlyIdleCDO/_transfer, IdleCDOTranche mint/burn, self-call guards, initializers, isWalletAllowed/KeyringIdleWhitelist, WriteOffEscrow create/delete/fullfill, writeOffDeposit.

## Validate
- Trace the analog from concrete attacker transactions in a named epoch phase (buffer, running, stopped, closed, defaulted, finalized, emergency) and mode (fixed-APR, APR0, minted interest, instant, prefunded, programmable).
- Show the broken invariant: solvency, fair mint/burn, one receipt one payout, loss waterfall, donation isolation, or access.
- Confirm that existing guards (skim, Default revert, flags, epoch gating, KYC, only-CDO, reserve checks) do not already stop it.
- Accept only direct theft, insolvency, permanent freezing, theft or permanent freezing of unclaimed yield, or temporary freezing, with a quantified loss.
- Require a reproducible Foundry fork PoC.

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

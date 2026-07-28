# Copyright (c) Mysten Labs, Inc.
# SPDX-License-Identifier: Apache-2.0

import json
import os

MAX_REPO = 25
SOURCE_REPO = '1inch/cross-chain-swap'
REPO_NAME = 'cross-chain-swap'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'contracts/BaseEscrow.sol',
    'contracts/BaseEscrowFactory.sol',
    'contracts/Escrow.sol',
    'contracts/EscrowDst.sol',
    'contracts/EscrowFactory.sol',
    'contracts/EscrowFactoryContext.sol',
    'contracts/EscrowSrc.sol',
    'contracts/MerkleStorageInvalidator.sol',
    'contracts/interfaces/IBaseEscrow.sol',
    'contracts/interfaces/IEscrow.sol',
    'contracts/interfaces/IEscrowDst.sol',
    'contracts/interfaces/IEscrowFactory.sol',
    'contracts/interfaces/IEscrowSrc.sol',
    'contracts/interfaces/IMerkleStorageInvalidator.sol',
    'contracts/libraries/ImmutablesLib.sol',
    'contracts/libraries/ProxyHashLib.sol',
    'contracts/libraries/TimelocksLib.sol',
    'contracts/zkSync/EscrowDstZkSync.sol',
    'contracts/zkSync/EscrowFactoryZkSync.sol',
    'contracts/zkSync/EscrowSrcZkSync.sol',
    'contracts/zkSync/EscrowZkSync.sol',
    'contracts/zkSync/MinimalProxyZkSync.sol',
    'contracts/zkSync/ZkSyncLib.sol',
]

target_scopes = [
    'Critical. Unauthorized withdrawal, release, or redirection of maker funds, taker funds, escrowed ERC20/native value, or safety deposits from source or destination escrows by an unprivileged caller',
    'Critical. Unauthorized escrow creation, clone/address binding bypass, hashlock bypass, Merkle partial-fill bypass, or immutable-parameter corruption that lets an unprivileged actor steal funds in motion or at rest',
    'Critical. Permanent freezing of maker funds, taker funds, destination payout, or safety deposits because withdrawal, cancellation, rescue, funding, or timelock logic becomes irrecoverably unusable',
    'Critical. Protocol insolvency, undercollateralized payout, or unbacked obligation where factory, escrow, or fee accounting can promise or release more value than was actually funded',
    'High. Replay, double-withdraw, double-cancel, stale-secret reuse, stale-proof reuse, or partial-fill accounting failure that causes duplicated execution or wrong payout',
    'High. Fee-routing, refund, or settlement-accounting corruption that misdirects protocol fees, integrator fees, maker proceeds, taker payouts, or native refund paths',
    'High. Temporary freezing of funds or failure of the live swap lifecycle through reachable mistakes in createDstEscrow, postInteraction, withdraw, publicWithdraw, cancel, publicCancel, or rescueFunds',
    'Medium. Theft of coins or tokens intended for transaction fees, native safety-deposit refunds, or other value paid only to make protocol operations succeed',
    'Medium. Smart contract becomes unable to operate because an unprivileged actor can drain, lock, or desynchronize required balances for escrow creation, withdrawal, cancellation, or settlement',
]

CROSS_CHAIN_ALLOWED_IMPACT_SCOPE = '## 1inch Smart-Contract Bounty Gate\nOnly accept impacts that fit the active 1inch Smart Contracts bounty scope for Cross-chain Swap as of July 28, 2026:\n- Critical: direct theft of user funds at rest or in motion, permanent freezing of funds, or protocol insolvency caused by these production contracts.\n- High: theft or permanent loss of unclaimed fee-like value, or temporary freezing of funds during the live swap lifecycle.\n- Medium: smart contract unable to operate because required token/native balances can be broken by an unprivileged actor, or theft of coins/tokens meant for transaction fees or native refund paths.\nFocus on reentrancy, reordering, arithmetic mistakes, stealing or loss of funds, unauthorized transactions, transaction manipulation, and business-logic failures.\nOut of scope: owner/admin/governance or privileged resolver assumptions, leaked keys, malicious peers/nodes/validators/bridges, third-party token issues, liquidity-only claims, imported-contract-only bugs, FoT-token support gaps, gas-only issues, style or best-practice complaints, and tests, mocks, scripts, deployments, docs, readmes, toml, generated artifacts, or theory-only claims.'

CROSS_CHAIN_AUDIT_PIVOTS = '## Smart Audit Pivots\n- Source path: Limit Order Protocol fill -> `_postInteraction` -> Merkle secret validation -> source immutables -> CREATE2 funding and clone deployment.\n- Destination path: `createDstEscrow` -> timelock binding -> native/ERC20 funding -> destination withdraw/cancel flow.\n- Secret path: hashlock validation, Merkle root/leaf/index progression, partial-fill ordering, and secret reuse prevention.\n- Accounting path: maker, taker, amount, dstToken, safety deposits, protocol fee, integrator fee, and native refund recipients.\n- Address path: immutables hashing, proxy bytecode hash, CREATE2 or zkSync computed address, and implementation binding.\n- Attacker model: unprivileged user only, entering through live order fill, destination escrow creation, withdraw/cancel/publicWithdraw/publicCancel/rescueFunds, or Merkle-validation flow; never owner, governance, privileged resolver, malicious relayer, or malicious node.'


def question_generator(target_file: str) -> str:
    """
    Generate focused 1inch Cross-chain Swap security questions for one scoped target.
    """

    prompt = f"""
    Generate 1inch Cross-chain Swap security questions for this exact scoped file:

    {target_file}

    Project lens:
    Focus on source/destination escrow lifecycle, LOP `postInteraction`, `createDstEscrow`, withdraw/cancel/rescue paths, Merkle multi-fill invalidation, fee routing, deterministic clone deployment, and zkSync-specific address binding.

    Bounty gate:
    {CROSS_CHAIN_ALLOWED_IMPACT_SCOPE}

    {CROSS_CHAIN_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is available. Do not ask for code.
    * Attacker is unprivileged only: maker, taker, destination escrow creator, ordinary caller, or order filler using normal public preconditions.
    * Never rely on owner/admin/governance powers, privileged resolver rights, malicious peers/nodes/bridges, leaked keys, or off-chain trust failures.
    * Exclude tests, mocks, scripts, deployments, docs, readmes, toml, generated artifacts, gas-only issues, third-party token quirks, and theory-only claims.
    * Generate 18 to 26 high-signal questions. Avoid generic checklist items and repeated root causes.
    * Every question must name the exact corrupted value at risk and be testable with a unit, integration, property, or fork-style test.

    Each question must include target symbol, attacker-controlled input, required state, call path, invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled ORDER_OR_ESCROW_INPUT under REQUIRED_STATE reach CALL_PATH and violate HASHLOCK_OR_TIMELOCK_OR_ACCOUNTING_INVARIANT, corrupting EXACT_ESCROW_BALANCE_SECRET_INDEX_FEE_AMOUNT_OR_REFUND_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a reproducible test that drives the public path and asserts the invariant should fail closed.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused 1inch Cross-chain Swap exploit-question validation prompt.
    """
    return f"""# 1INCH CROSS-CHAIN SWAP QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only current-bounty 1inch Cross-chain Swap production code in this repository.
- Ignore tests, mocks, scripts, deployments, readmes, toml, generated artifacts, and docs-only issues.
- Do not ask for repo contents or claim files are missing.

## Objective
Decide whether the question leads to a real vulnerability. The attacker must enter through a live order fill, `createDstEscrow`, withdraw/cancel/publicWithdraw/publicCancel/rescueFunds, or the Merkle-validation path available in scoped code.

Reject claims that need owner/admin/governance control, privileged resolver rights, malicious peers/nodes/bridges, leaked keys, off-chain trust failures, or excluded bug classes. Prefer #NoVulnerability unless the path proves direct fund theft, fund freeze, protocol insolvency, fee/refund theft, or current-bounty contract inoperability.

## Required Impacts
{CROSS_CHAIN_ALLOWED_IMPACT_SCOPE}

{CROSS_CHAIN_AUDIT_PIVOTS}

## Method
1. Trace the exact public or unprivileged entrypoint.
2. Map it to the exact scoped files and functions.
3. Follow input -> validation -> state transition -> corrupted value -> impact.
4. Identify the exact escrow balance, safety deposit, fee amount, secret index, clone address, or payout value that becomes wrong.
5. Reject if existing guards preserve the invariant or the impact misses the active bounty thresholds.

## Reject Immediately
- Any assumption requiring owner/admin/governance powers, privileged resolver rights, or malicious nodes/bridges.
- Unsafe setup claims that only exist because a trusted party deliberately chose bad parameters.
- Gas-only issues, style complaints, dependency-only behavior, third-party token quirks, and docs-only claims.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for 1inch Cross-chain Swap issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search this repository for a native analog in escrow, factory, Merkle invalidation, fee-routing, clone-deployment, or zkSync code that matches the same root cause under the unprivileged-user model.

## Required Impacts
{CROSS_CHAIN_ALLOWED_IMPACT_SCOPE}

{CROSS_CHAIN_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, public trigger, broken invariant, exact corrupted value, and matching target scope or allowed impact. Reject privileged operations, malicious node or bridge assumptions, excluded bug classes, tooling-only behavior, and anything outside production scope.

## Work Plan
1. Classify the external bug into one escrow/factory invariant.
2. Map it to exact scoped files and functions.
3. Trace attacker input through production validation and state transitions.
4. Identify the wrong escrow balance, fee amount, refund value, secret index, or deterministic address result.
5. Reject if existing guards preserve the invariant or the loss is not bounty-relevant.

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


def validation_format(report: str) -> str:
    """
    Generate a strict 1inch Cross-chain Swap validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against current-bounty 1inch Cross-chain Swap production code in this repository.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged maker, taker, destination escrow creator, ordinary caller, or order filler.
- Reject owner/admin/governance, privileged resolver, malicious node/bridge, leaked-key, third-party-token, and off-chain-trust assumptions.
- Reject tests, mocks, scripts, docs, readmes, toml, generated artifacts, gas-only issues, style, dependency-only bugs, and theory-only claims.
- The final impact must match one `target_scopes` item or the allowed impacts below, identify the exact corrupted value, and satisfy the active 1inch Smart Contracts bounty rules as of July 28, 2026.

## Required Impacts
{CROSS_CHAIN_ALLOWED_IMPACT_SCOPE}

{CROSS_CHAIN_AUDIT_PIVOTS}

## Required Checks
1. Exact file and function references in scoped code.
2. Clear broken escrow, fee, clone-address, secret-validation, timelock, or settlement invariant tied to allowed impacts.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> wrong value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: escrow balance, maker payout, taker payout, safety deposit, fee amount, secret hash, secret index, clone address, or refund value.
6. Reproducible proof path: unit, integration, property, or fork-style test.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt

import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = "0dotxyz/marginfi-v2"
# todo: the name of the repository
REPO_NAME = "marginfi-v2"

run_number = os.environ.get("GITHUB_RUN_NUMBER", "0")


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
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
    "id-crate/src/lib.rs",
    "type-crate/src/constants.rs",
    "type-crate/src/lib.rs",
    "type-crate/src/macros.rs",
    "type-crate/src/pdas.rs",
    "programs/marginfi/src/allocator.rs",
    "programs/marginfi/src/state/drift.rs",
    "programs/marginfi/src/state/juplend.rs",
    "programs/marginfi/src/state/kamino.rs",
    "programs/marginfi/src/state/solend.rs",
    "programs/marginfi/src/utils/general.rs",
    "programs/marginfi/src/utils/kamino.rs",
    "programs/marginfi/src/utils/mod.rs",
    "programs/marginfi/src/instructions/drift/add_pool.rs",
    "programs/marginfi/src/instructions/drift/claim_bad_debt.rs",
    "programs/marginfi/src/instructions/drift/deposit.rs",
    "programs/marginfi/src/instructions/drift/harvest_reward.rs",
    "programs/marginfi/src/instructions/drift/init_user.rs",
    "programs/marginfi/src/instructions/drift/mod.rs",
    "programs/marginfi/src/instructions/drift/withdraw.rs",
    "programs/marginfi/src/instructions/juplend/add_pool.rs",
    "programs/marginfi/src/instructions/juplend/deposit.rs",
    "programs/marginfi/src/instructions/juplend/init_position.rs",
    "programs/marginfi/src/instructions/juplend/mod.rs",
    "programs/marginfi/src/instructions/juplend/withdraw.rs",
    "programs/marginfi/src/instructions/kamino/add_pool.rs",
    "programs/marginfi/src/instructions/kamino/deposit.rs",
    "programs/marginfi/src/instructions/kamino/harvest_reward.rs",
    "programs/marginfi/src/instructions/kamino/init_obligation.rs",
    "programs/marginfi/src/instructions/kamino/mod.rs",
    "programs/marginfi/src/instructions/kamino/withdraw.rs",
    "programs/marginfi/src/instructions/solend/add_pool.rs",
    "programs/marginfi/src/instructions/solend/deposit.rs",
    "programs/marginfi/src/instructions/solend/init_obligation.rs",
    "programs/marginfi/src/instructions/solend/mod.rs",
    "programs/marginfi/src/instructions/solend/withdraw.rs",
    "programs/marginfi/src/instructions/marginfi_group/add_pool.rs",
    "programs/marginfi/src/instructions/marginfi_group/add_pool_common.rs",
    "programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs",
    "programs/marginfi/src/instructions/marginfi_group/add_pool_with_seed.rs",
    "programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs",
    "programs/marginfi/src/instructions/marginfi_group/backfill_staked_bank_validator_vote_account.rs",
    "programs/marginfi/src/instructions/marginfi_group/clone_bank.rs",
    "programs/marginfi/src/instructions/marginfi_group/close_bank.rs",
    "programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs",
    "programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs",
    "programs/marginfi/src/instructions/marginfi_group/config_group_fee.rs",
    "programs/marginfi/src/instructions/marginfi_group/configure.rs",
    "programs/marginfi/src/instructions/marginfi_group/configure_bank.rs",
    "programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs",
    "programs/marginfi/src/instructions/marginfi_group/configure_rate_limits.rs",
    "programs/marginfi/src/instructions/marginfi_group/configure_withdrawal_limit.rs",
    "programs/marginfi/src/instructions/marginfi_group/copy_fee_state_to_v2.rs",
    "programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs",
    "programs/marginfi/src/instructions/marginfi_group/edit_stake_settings.rs",
    "programs/marginfi/src/instructions/marginfi_group/emode_clone.rs",
    "programs/marginfi/src/instructions/marginfi_group/init_bank_metadata.rs",
    "programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs",
    "programs/marginfi/src/instructions/marginfi_group/init_global_fee_state_v2.rs",
    "programs/marginfi/src/instructions/marginfi_group/init_staked_settings.rs",
    "programs/marginfi/src/instructions/marginfi_group/initialize.rs",
    "programs/marginfi/src/instructions/marginfi_group/mod.rs",
    "programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs",
    "programs/marginfi/src/instructions/marginfi_group/panic_pause.rs",
    "programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs",
    "programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs",
    "programs/marginfi/src/instructions/marginfi_group/propagate_staked_settings.rs",
    "programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs",
    "programs/marginfi/src/instructions/marginfi_group/staked_pool_utils.rs",
    "programs/marginfi/src/instructions/marginfi_group/super_admin_deposit.rs",
    "programs/marginfi/src/instructions/marginfi_group/super_admin_withdraw.rs",
    "programs/marginfi/src/instructions/marginfi_group/update_deleverage_withdrawals.rs",
    "programs/marginfi/src/instructions/marginfi_group/write_bank_metadata.rs",
]


target_scopes = [
    "Critical. An unprivileged caller can bypass admin, delegate, or authority boundaries to change group, bank, oracle, fee, metadata, or pause configuration, or to invoke an integration path reserved for a different role.",
    "Critical. A permissionless maintenance, harvest, init, add-pool, or backfill path can be abused by an unprivileged user to seize value, redirect authority, corrupt bank metadata, or enable later theft.",
    "High. An integration accounting mismatch across Kamino, Juplend, Solend, Drift, staked collateral, or reward flows lets an unprivileged user overstate collateral, understate debt, double count value, or orphan liabilities.",
    "High. An oracle, price-cache, fixed-price, emode, or bank-configuration edge case reachable from an unprivileged path causes exploitable misvaluation, unsafe settlement, or unauthorized state transition.",
    "High. A PDA, seed, account-derivation, CPI account-selection, or optional-account validation bug lets an unprivileged user swap accounts, target the wrong vault or obligation, or write to the wrong state object.",
    "Medium. A release-relevant integration or permissionless admin-adjacent path is exploitable by a normal user for freeze, griefing with durable financial effect, or protocol inconsistency even without immediate theft.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one marginfi target.

    ```
    target_file format:
    "'File Name: programs/marginfi/src/instructions/kamino/deposit.rs -> Scope: Critical. ...'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact marginfi target:

    {target_file}

    Project focus:
    This set covers authority boundaries, permissionless maintenance, PDA derivation, oracle and price wiring, staked collateral logic, and external integrations with Kamino, Juplend, Solend, and Drift/Velocity-related code.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols when possible.
    * Attacker is unprivileged only: a normal user or permissionless caller invoking public instructions with arbitrary account metas, seeds, amounts, and timing.
    * Never assume admin, governance, oracle operator, integration governor, privileged signer, leaked key, malicious validator, malicious peer, or node/config control.
    * Stay on mainnet or release/pre-release relevant behavior. If a path is not active in production, do not frame it as Critical.
    * Do not rely on tests, mocks, generated files, or off-repo assumptions.
    * Out of scope per SECURITY.md: privileged-address assumptions, pure liquidity issues, third-party oracle bad data by itself, Sybil/social engineering, significant-traffic DoS, propagation-only misses, fixed-price values chosen foolishly by admins, T22 listing choices, Solend whitelist omissions, and current non-operational Drift-only issues.
    * Generate 12 to 18 high-signal questions.
    * At least 70% must be multi-step authorization-bypass, PDA/account-selection, CPI-accounting, oracle/price, or permissionless-crank-abuse questions.
    * Every question must be testable by unit test, integration test, invariant test, or fuzz test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Admin-only and delegate-only configuration paths must reject all unprivileged callers.
    * Permissionless helpers and backfills can only touch the intended fields and cannot redirect authority or value.
    * PDA derivation, optional-account handling, and CPI account selection must bind every action to the intended group, bank, vault, obligation, and reward account.
    * Oracle, price, and integration accounting must remain conservative: no phantom collateral, skipped debt, duplicated rewards, or hidden liabilities.
    * Pause, fee, stake-setting, and metadata flows must not be spoofable or weaponizable by an unprivileged user for durable financial impact.

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
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: test/fuzz PARAMETERS and assert AUTHZ_HOLDS, PDA_BINDING, CPI_ACCOUNT_BINDING, PRICE_CONSERVATISM, or NO_VALUE_REDIRECTION.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused marginfi exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: a normal user or permissionless caller using reachable public instructions and arbitrary account metas.
- Reject anything requiring admin/governance/oracle-operator/integration-operator control, leaked keys, malicious validators/peers, direct state mutation, mocks, or best-practice-only cleanup.
- Prefer mainnet or release/pre-release relevant paths. If the claim depends on a non-production feature, do not treat it as Critical.
- Out of scope per SECURITY.md: pure liquidity issues, third-party oracle bad data by itself, Sybil/social engineering, significant-traffic DoS, propagation-only misses, fixed-price values chosen by admins, T22 listing choices, Solend whitelist omissions, and current non-operational Drift-only issues.

## Validate
- Trace the exact reachable Rust path from the public instruction entrypoint into authority checks, PDA derivation, CPI account wiring, oracle/price logic, or integration settlement logic.
- Check whether signer, seed, ownership, bank/group binding, optional-account, and invariant guards already stop it.
- Accept only real authorization bypass, value redirection, exploitable misvaluation, duplicated/phantom value, unauthorized state change, or durable freeze/inconsistency with financial impact.
- Require exact file/function support and a reproducible Rust unit, integration, invariant, or fuzz PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and why it matters under marginfi's bounty rules]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust unit/integration/invariant/fuzz test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for marginfi security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject admin-operator-assumed, oracle-operator-assumed, validator/peer-only, leaked-key, docs/style, mock-only, generated-file, or purely theoretical issues.
- Reject if the exploit needs unrealistic assumptions, user self-harm, direct state mutation, or unsupported protocol behavior.
- Reject if the bug is already acknowledged as out of scope in SECURITY.md.
- A valid report must be triggerable by an unprivileged user unless the claim proves privilege escalation from an unprivileged path.
- The final impact must fit marginfi's on-chain bounty scope: authorization bypass, value redirection, exploitable misvaluation, protocol inconsistency with financial effect, or permanent lock/freeze of funds.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, derivation, binding, or accounting assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: unit PoC, integration test, invariant/fuzz test, or exact manual steps.
7. No obvious rejection reason from SECURITY.md, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal user trigger this without privileged keys?
- Does the code actually bind the right accounts, seeds, authorities, and oracle context?
- Is the impact caused by this code, not by a malicious node, peer, admin, or third-party operator?
- Is the financial effect concrete, not hypothetical?
- Would the marginfi team treat this as in-scope under the current SECURITY.md?
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
[Concrete in-scope impact and severity rationale]

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
    Generate a short cross-project analog scan prompt for marginfi integration and authority paths.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-user analogs in authority checks, PDA derivation, permissionless maintenance, oracle wiring, staked collateral logic, and integration CPI/accounting paths.
- Reject validator, peer, privileged-admin, mocked-only, theoretical-only, or no-impact analogs.
- Reject analogs that only match known out-of-scope issues in SECURITY.md.

## Validate
- Map the bug class to the strongest reachable marginfi path.
- Prove root cause with exact file/function support.
- Accept only concrete authorization bypass, value redirection, exploitable misvaluation, unauthorized state change, or durable freeze/inconsistency with financial effect.

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

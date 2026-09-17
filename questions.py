import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'anza-xyz/agave'
# todo: the name of the repository
REPO_NAME = 'agave'

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
    # Transaction admission: sanitization, signature verification and precompiles
    # =================================================================================
    "runtime-transaction/src/runtime_transaction.rs",
    "runtime-transaction/src/runtime_transaction/sdk_transactions.rs",
    "runtime-transaction/src/runtime_transaction/transaction_view.rs",
    "runtime-transaction/src/sanitize_config.rs",
    "runtime-transaction/src/signature_details.rs",
    "runtime-transaction/src/instruction_data_len.rs",
    "runtime-transaction/src/transaction_meta.rs",
    "perf/src/sigverify.rs",
    "perf/src/packet.rs",
    "precompiles/src/lib.rs",
    "precompiles/src/ed25519.rs",
    "precompiles/src/secp256k1.rs",
    "precompiles/src/secp256r1.rs",

    # =================================================================================
    # Replay protection, nonce, blockhash, fee charging and compute budget parsing
    # =================================================================================
    "runtime/src/bank/check_transactions.rs",
    "accounts-db/src/blockhash_queue.rs",
    "runtime/src/status_cache.rs",
    "svm/src/nonce_info.rs",
    "svm/src/rollback_accounts.rs",
    "fee/src/lib.rs",
    "compute-budget-instruction/src/compute_budget_instruction_details.rs",
    "compute-budget-instruction/src/instructions_processor.rs",
    "compute-budget-instruction/src/builtin_programs_filter.rs",
    "compute-budget-instruction/src/compute_budget_program_id_filter.rs",
    "compute-budget/src/compute_budget_limits.rs",
    "compute-budget/src/compute_budget.rs",
    "runtime/src/bank/fee_distribution.rs",

    # =================================================================================
    # Account loading, lamport/rent conservation, locking and commit
    # =================================================================================
    "svm/src/account_loader.rs",
    "svm/src/rent_calculator.rs",
    "svm/src/transaction_account_state_info.rs",
    "runtime/src/rent_collector.rs",
    "accounts-db/src/account_locks.rs",
    "accounts-db/src/accounts.rs",
    "runtime/src/account_saver.rs",

    # =================================================================================
    # SVM transaction processing, program loading and program cache
    # =================================================================================
    "svm/src/transaction_processor.rs",
    "svm/src/program_loader.rs",
    "program-runtime/src/loaded_programs.rs",
    "program-runtime/src/program_cache_entry.rs",
    "program-runtime/src/loading_task.rs",
    "program-runtime/src/execution_budget.rs",

    # =================================================================================
    # Invoke context, CPI privilege propagation, VM memory and serialization
    # =================================================================================
    "program-runtime/src/invoke_context.rs",
    "program-runtime/src/cpi.rs",
    "program-runtime/src/serialization.rs",
    "program-runtime/src/memory.rs",
    "program-runtime/src/memory_context.rs",
    "program-runtime/src/vm.rs",
    "program-runtime/src/sysvar_cache.rs",
    "program-runtime/src/deploy.rs",
    "transaction-context/src/lib.rs",
    "transaction-context/src/transaction.rs",
    "transaction-context/src/transaction_accounts.rs",
    "transaction-context/src/instruction.rs",
    "transaction-context/src/instruction_accounts.rs",
    "transaction-context/src/vm_slice.rs",
    "transaction-context/src/vm_addresses.rs",
    "syscalls/src/lib.rs",
    "syscalls/src/cpi.rs",
    "syscalls/src/mem_ops.rs",
    "syscalls/src/sysvar.rs",
    "syscalls/src/logging.rs",

    # =================================================================================
    # Native programs any signer can invoke
    # =================================================================================
    "programs/system/src/system_processor.rs",
    "programs/system/src/system_instruction.rs",
    "programs/vote/src/vote_processor.rs",
    "programs/vote/src/vote_state/mod.rs",
    "programs/vote/src/vote_state/handler.rs",
    "programs/bpf_loader/src/lib.rs",
    "programs/compute-budget/src/lib.rs",
    "programs/zk-elgamal-proof/src/lib.rs",
    "builtins/src/core_bpf_migration.rs",
    "builtins/src/lib.rs",
    "runtime/src/bank/builtins/core_bpf_migration/mod.rs",
    "runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs",
    "runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs",

    # =================================================================================
    # Bank state commit, account hashing and cross-validator determinism
    # =================================================================================
    "runtime/src/bank.rs",
    "runtime/src/bank/accounts_lt_hash.rs",
    "runtime/src/bank/sysvar_cache.rs",
    "runtime/src/bank/recent_blockhashes_account.rs",
    "runtime/src/bank/address_lookup_table.rs",
    "lattice-hash/src/lt_hash.rs",
    "accounts-db/src/accounts_db.rs",
    "accounts-db/src/accounts_cache.rs",
    "accounts-db/src/read_only_accounts_cache.rs",

    # =================================================================================
    # Stake weight, epoch stakes and reward distribution driven by on-chain state
    # =================================================================================
    "runtime/src/stakes.rs",
    "runtime/src/stake_account.rs",
    "runtime/src/epoch_stakes.rs",
    "runtime/src/bank/partitioned_epoch_rewards/calculation.rs",
    "runtime/src/bank/partitioned_epoch_rewards/distribution.rs",
    "runtime/src/bank/partitioned_epoch_rewards/sysvar.rs",
    "runtime/src/inflation_rewards/points.rs",

    # =================================================================================
    # Block cost accounting, QoS and leader-side transaction processing
    # =================================================================================
    "cost-model/src/cost_model.rs",
    "cost-model/src/cost_tracker.rs",
    "cost-model/src/block_cost_limits.rs",
    "cost-model/src/transaction_cost.rs",
    "core/src/banking_stage/qos_service.rs",
    "core/src/banking_stage/consumer.rs",
    "core/src/banking_stage/committer.rs",
    "core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs",
    "core/src/banking_stage/transaction_scheduler/transaction_state_container.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler_common.rs",
    "core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs",
    "runtime/src/bank/entry_bytes_budget.rs",
    "runtime/src/prioritization_fee_cache.rs",
]


target_scopes = [
    "Critical. An ordinary fee-paying transaction sender moves lamports or data out of an account they do not sign for, because account privilege derivation - is_signer/is_writable flags, index-to-key resolution, duplicate account dedup, address-lookup-table expansion, or borrow/ref handling in TransactionContext and InstructionContext - lets an unsigned or read-only account be debited or reassigned, giving theft of funds without the owner's signature.",
    "Critical. A crafted instruction or CPI chain escalates privileges the caller never held, because InvokeContext::prepare_next_instruction, the CPI account-info translation in syscalls/src/cpi.rs and program-runtime/src/cpi.rs, or signer-seed/PDA derivation propagates signer or writable status to an account the top-level transaction did not authorize, letting an attacker program drain arbitrary accounts.",
    "Critical. A transaction breaks lamport conservation or rent invariants - creating lamports from nothing, keeping a debited balance after a failed instruction, or evading TransactionAccountStateInfo/rent-exempt checks - through system program handlers, account_loader balance tracking, rollback accounts, or fee collection, resulting in unbacked supply or stolen balances.",
    "Critical. An attacker takes over a vote account or redirects its stake value by exploiting authorized-voter/authorized-withdrawer checks, VoteState deserialization and size handling, or the withdraw/update-commission paths in the vote program, allowing withdrawal of a validator's balance or capture of delegated stake rewards.",
    "Critical. A single submitted transaction makes two honest validators compute different state for the same block - divergent account contents, accounts lt hash, fee or rent result, sysvar snapshot, or capitalization - because execution or hashing depends on ordering, caching, feature-gate evaluation, or uninitialized/nondeterministic data, producing a consensus safety violation and chain fork.",
    "Critical. On-chain state an unprivileged user can write drives stake weight or epoch state incorrectly - stakes cache updates, StakeAccount parsing, epoch_stakes snapshots, delegation activation/deactivation accounting, or partitioned epoch reward calculation and distribution - so leader schedule, vote weight, or reward payout diverges from the true delegated stake.",
    "Critical. A transaction that any user can submit halts or crashes block processing on every validator - a panic, arithmetic overflow, slice/index violation, unwrap on attacker-controlled input, or unrecoverable error surfaced from SVM processing, a builtin program, account loading, or bank commit - producing a cluster-wide liveness failure requiring human intervention.",
    "High. A transaction executes without paying, or is accepted twice, because of flaws in blockhash age validation, the status cache dedup key, durable-nonce advance and rollback handling, fee calculation and refund, or signature counting, letting an attacker obtain free execution, replay a signed transaction, or bypass replay protection.",
    "High. A transaction consumes far more real work than it is charged for, because compute-budget instruction parsing, per-instruction CU defaults, cost-model estimation, cost tracker block limits, entry byte budget, or QoS accounting undercounts it, letting a cheap transaction exhaust the leader's block capacity and starve or stall block production.",
    "High. A user-submitted deploy, upgrade, close, or invocation causes the wrong program bytecode to execute or a stale/poisoned entry to be served, through program cache tombstoning and effective-slot handling, loading-task races, bpf_loader deploy/upgrade authority checks, or core-BPF migration source validation, so a program behaves differently from its on-chain state.",
    "Critical/High blind spot. An unprivileged transaction sender abuses an assumption the protocol never wrote down: a value validated in one stage and trusted as already-validated in a later one, an account or index re-resolved after the check that authorized it, a limit or feature gate enforced only on one execution path (leader vs replay, cached vs freshly loaded, top-level vs CPI), state carried across instruction, transaction, slot or epoch boundaries that was only proven safe within one of them, or an error path that commits partial effects - yielding unsigned fund movement, state divergence between validators, or a cluster-wide stall.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one agave target.

    ```
    target_file format:
    "'File Name: svm/src/account_loader.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact agave target:

    {target_file}

    Project focus:
    agave is the Solana validator. Focus only on what an ordinary user reaches by submitting a signed, fee-paying transaction (including deploying and invoking their own BPF program): sanitization and sigverify, precompiles, blockhash/nonce replay protection, fee and compute-budget accounting, account loading and lamport/rent conservation, SVM execution, CPI privilege propagation, syscalls and VM memory, the system/vote/bpf_loader builtins, bank commit and accounts lt hash determinism, stake and reward accounting, and leader-side cost/QoS limits.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (fn, method, struct, enum, field) when possible.
    * Attacker is unprivileged only: any keypair holder who can pay fees and submit a transaction, deploy and invoke their own BPF program, or create and own stake/vote/token accounts. They sign only for their own keys.
    * Attacker is NOT a validator, leader, node operator, host or DB owner, RPC operator, or upgrade authority of someone else's program. Never assume a malicious peer, malicious leader, malicious node, gossip/turbine/shred/QUIC network attacker, crafted snapshot, Geyser plugin, misconfiguration, or social engineering.
    * Out of scope, never ask about: votor/Alpenglow crates, loader-v4, the VM interpreter, RPC endpoints, snapshots, gossip/turbine/repair, metrics, dependencies.
    * Ignore test files, mocks, fuzz harnesses, benches, docs, generated code, and TOML/config-only findings.
    * Every question must describe a real transaction an attacker actually sends. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" questions without a concrete signed transaction and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target theft or creation of lamports without the owner's signature, CPI/privilege escalation, consensus divergence between validators, stake or reward corruption, replay or free execution, or a transaction-triggered validator panic that halts the cluster.
    * Every question must be testable by a Rust unit test, an SVM/program-test integration test, or a bank-level test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: an account is debited, reassigned, or written only when a required signer signed the transaction, and CPI never grants privileges the caller did not hold.
    * Value is conserved: lamports in equals lamports out plus fees and rent; failed transactions leave only the intended fee and nonce effects.
    * Determinism holds: every validator replaying the same block reaches the same accounts, hash, and capitalization, regardless of order, caching, or timing.
    * Replay protection is sound: a signed transaction executes at most once and only within a valid blockhash or nonce window, and always pays its fee.
    * Accounting is honest: charged compute, cost-model cost, stake weight, and reward payout match the real work and real on-chain state.
    * Execution is total: no attacker-supplied transaction can panic, overflow, or abort block processing.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction: instructions, accounts, signers, data);
    3. preconditions (accounts the attacker owns and funds);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/SVM/bank test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, REPLAY_PROTECTION, HONEST_ACCOUNTING, or TOTAL_EXECUTION.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused agave exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any keypair holder who submits a signed, fee-paying transaction, deploys and invokes their own BPF program, or owns stake/vote/token accounts. No validator, leader, operator, host, RPC, or foreign upgrade-authority access.
- Reject malicious-peer, malicious-leader, malicious-node, gossip/turbine/repair/QUIC network, snapshot, Geyser, operator-only, host-level, and misconfiguration-only paths.
- Reject votor/Alpenglow, loader-v4, VM interpreter, RPC, metrics, dependency-only, and test/mock/bench/docs/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete transaction and no broken invariant.
- Focus on real cluster impact: theft or minting of lamports without the owner's signature, CPI privilege escalation, consensus divergence between validators, stake or reward corruption, replay or free execution, or a transaction that halts block processing.

## Validate
- Trace the exact reachable path from the attacker's transaction (instructions, accounts, signers, data) into the affected function.
- Check whether sanitization, sigverify, feature gates, account privilege checks, balance and rent checks, or existing error handling already stop it.
- Confirm the path is reachable on the default feature set of current mainnet-beta behavior.
- Accept only concrete unsigned fund movement, privilege escalation, state divergence, corrupted stake/reward accounting, replay, or cluster-wide stall.
- Require exact file/function support and a reproducible Rust unit, SVM, program-test, or bank-level PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker transaction inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Solana bounty category: Loss of Funds, Consensus/Safety Violation, Liveness, or DoS via non-RPC protocols]

### Likelihood Explanation
[Preconditions, accounts and funds needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust unit/SVM/bank test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for agave security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-peer, malicious-leader, malicious-node, network-layer, snapshot, Geyser plugin, operator-only, host-level, misconfiguration, dependency-only, docs/style, generated-file, and test/mock/bench/config-only issues.
- Reject votor/Alpenglow crates, loader-v4, VM interpreter, RPC service, metrics, and bootstrap-phase-only issues, per SECURITY.md exclusions.
- Reject if the exploit needs validator, leader, operator, host, or database access, a foreign upgrade authority, victim social engineering, a non-default feature set, or anything outside what an unprivileged keypair holder can put in a submitted transaction.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction sender, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope Solana category: Loss of Funds (theft without the user's signature, including system/stake/vote programs), Consensus/Safety Violation, Liveness/loss of availability requiring human intervention, or remote resource exhaustion via non-RPC protocols.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions (attacker-owned accounts and funds) -> submitted transaction -> trigger -> bad result.
4. Existing sanitization, sigverify, privilege checks, balance/rent checks, feature gates, and error handling reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: Rust unit PoC, SVM/program-test integration test, bank-level test, or exact transaction steps against a local cluster.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary fee-paying user trigger this with a transaction, without validator, operator, or host access?
- Does the code actually behave as claimed under the currently active feature set?
- Is the impact caused by this code, not by a malicious peer, snapshot, plugin, or dependency?
- Is the theft, divergence, replay, or halt concrete rather than hypothetical?
- Would a Solana Foundation triager accept the proof-of-concept?
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
[Concrete in-scope impact, severity rationale, and Solana bounty category]

## Likelihood Explanation
[Attacker capability, accounts and funds required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Rust unit/SVM/bank test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for agave.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction sender can reach: sanitization and sigverify, precompiles, blockhash/nonce replay protection, fee and compute-budget accounting, account loading and lamport/rent conservation, SVM execution and CPI privilege propagation, syscalls and VM memory, system/vote/bpf_loader builtins, bank commit determinism, stake and reward accounting, or leader-side cost limits.
- Reject malicious-peer, malicious-leader, network-layer, snapshot, Geyser, operator-only, votor/Alpenglow, loader-v4, interpreter, RPC, mocked-only paths, dependency-only bugs, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable agave path from a single submitted transaction.
- Prove root cause with exact file/function support.
- Accept only concrete unsigned fund movement or minting, CPI privilege escalation, consensus divergence between validators, stake or reward corruption, replay or free execution, or a transaction-triggered cluster halt.

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

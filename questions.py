import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 22
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
    # SBPF execution and syscalls: guest/host memory confinement, VM escape, metering
    # =================================================================================
    "program-runtime/src/memory.rs",
    "program-runtime/src/memory_context.rs",
    "program-runtime/src/mem_pool.rs",
    "program-runtime/src/serialization.rs",
    "program-runtime/src/vm.rs",
    "program-runtime/src/execution_budget.rs",
    "syscalls/src/lib.rs",
    "syscalls/src/mem_ops.rs",
    "syscalls/src/logging.rs",
    "syscalls/src/sysvar.rs",

    # =================================================================================
    # CPI and privilege propagation: signer seeds, writability, account borrows
    # =================================================================================
    "program-runtime/src/cpi.rs",
    "syscalls/src/cpi.rs",
    "program-runtime/src/invoke_context.rs",
    "transaction-context/src/lib.rs",
    "transaction-context/src/transaction.rs",
    "transaction-context/src/transaction_accounts.rs",
    "transaction-context/src/instruction.rs",
    "transaction-context/src/instruction_accounts.rs",
    "transaction-context/src/vm_addresses.rs",
    "transaction-context/src/vm_slice.rs",

    # =================================================================================
    # Program deployment, loading and caching: attacker-deployed code reaching execution
    # =================================================================================
    "programs/bpf_loader/src/lib.rs",
    "program-runtime/src/deploy.rs",
    "program-runtime/src/program_cache_entry.rs",
    "program-runtime/src/loaded_programs.rs",
    "program-runtime/src/loading_task.rs",
    "svm/src/program_loader.rs",
    "builtins/src/core_bpf_migration.rs",
    "runtime/src/bank/builtins/core_bpf_migration/mod.rs",
    "runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs",
    "runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs",

    # =================================================================================
    # Builtin programs: unauthorized lamport/state movement and signature verification
    # =================================================================================
    "programs/system/src/system_processor.rs",
    "programs/system/src/system_instruction.rs",
    "programs/vote/src/vote_processor.rs",
    "programs/vote/src/vote_state/mod.rs",
    "programs/vote/src/vote_state/handler.rs",
    "programs/compute-budget/src/lib.rs",
    "programs/zk-elgamal-proof/src/lib.rs",
    "precompiles/src/lib.rs",
    "precompiles/src/ed25519.rs",
    "precompiles/src/secp256k1.rs",
    "precompiles/src/secp256r1.rs",
    "reserved-account-keys/src/lib.rs",

    # =================================================================================
    # Transaction sanitization, lookup tables and compute/cost accounting
    # =================================================================================
    "runtime-transaction/src/runtime_transaction.rs",
    "runtime-transaction/src/runtime_transaction/sdk_transactions.rs",
    "runtime-transaction/src/runtime_transaction/transaction_view.rs",
    "runtime-transaction/src/sanitize_config.rs",
    "runtime-transaction/src/instruction_data_len.rs",
    "runtime-transaction/src/signature_details.rs",
    "runtime/src/bank/address_lookup_table.rs",
    "compute-budget-instruction/src/compute_budget_instruction_details.rs",
    "compute-budget-instruction/src/instructions_processor.rs",
    "compute-budget-instruction/src/builtin_programs_filter.rs",
    "compute-budget/src/compute_budget_limits.rs",
    "compute-budget/src/compute_budget.rs",
    "cost-model/src/cost_model.rs",
    "cost-model/src/cost_tracker.rs",
    "cost-model/src/transaction_cost.rs",
    "cost-model/src/block_cost_limits.rs",

    # =================================================================================
    # SVM transaction processing: account loading, fees, rent, nonce, rollback, commit
    # =================================================================================
    "svm/src/transaction_processor.rs",
    "svm/src/account_loader.rs",
    "svm/src/rent_calculator.rs",
    "svm/src/nonce_info.rs",
    "svm/src/rollback_accounts.rs",
    "svm/src/transaction_account_state_info.rs",
    "svm/src/account_overrides.rs",
    "svm-callback/src/lib.rs",
    "fee/src/lib.rs",
    "runtime/src/account_saver.rs",
    "runtime/src/transaction_execution.rs",
    "runtime/src/bank/check_transactions.rs",
    "runtime/src/bank/fee_distribution.rs",
    "runtime/src/rent_collector.rs",

    # =================================================================================
    # Bank state commit and determinism: hashing, caches, stakes, sysvars, replay safety
    # =================================================================================
    "runtime/src/bank.rs",
    "runtime/src/bank/accounts_lt_hash.rs",
    "runtime/src/bank/sysvar_cache.rs",
    "runtime/src/bank/recent_blockhashes_account.rs",
    "runtime/src/status_cache.rs",
    "runtime/src/stakes.rs",
    "runtime/src/stake_account.rs",
    "runtime/src/prioritization_fee_cache.rs",
    "runtime/src/transaction_batch.rs",
    "accounts-db/src/blockhash_queue.rs",

    # =================================================================================
    # Accounts database and index: attacker-created accounts driving corruption or OOM
    # =================================================================================
    "accounts-db/src/accounts.rs",
    "accounts-db/src/accounts_db.rs",
    "accounts-db/src/account_locks.rs",
    "accounts-db/src/append_vec.rs",
    "accounts-db/src/accounts_file.rs",
    "accounts-db/src/accounts_cache.rs",
    "accounts-db/src/read_only_accounts_cache.rs",
    "accounts-db/src/accounts_index.rs",
    "accounts-db/src/accounts_index/in_mem_accounts_index.rs",
    "accounts-db/src/accounts_index/secondary.rs",
    "accounts-db/src/accounts_hash.rs",
    "accounts-db/src/accounts_scan.rs",
    "accounts-db/src/storable_accounts.rs",
    "bucket_map/src/bucket.rs",
    "bucket_map/src/bucket_map.rs",
    "bucket_map/src/bucket_storage.rs",
    "bucket_map/src/index_entry.rs",

    # =================================================================================
    # Transaction ingest and scheduling: remote resource exhaustion on non-RPC paths
    # =================================================================================
    "streamer/src/nonblocking/quic.rs",
    "streamer/src/nonblocking/connection_rate_limiter.rs",
    "streamer/src/nonblocking/stream_throttle.rs",
    "streamer/src/nonblocking/qos.rs",
    "streamer/src/quic.rs",
    "streamer/src/packet.rs",
    "perf/src/sigverify.rs",
    "perf/src/deduper.rs",
    "perf/src/packet.rs",
    "core/src/sigverify_stage.rs",
    "core/src/banking_stage/consumer.rs",
    "core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler_controller.rs",
    "core/src/banking_stage/transaction_scheduler/transaction_state_container.rs",
    "core/src/forwarding_stage/packet_container.rs",

    # =================================================================================
    # RPC request handling: crash or memory exhaustion from a single client request
    # =================================================================================
    "rpc/src/rpc.rs",
    "rpc/src/filter.rs",
    "rpc/src/rpc_service.rs",
    "rpc/src/rpc_pubsub.rs",
    "rpc/src/rpc_subscriptions.rs",
    "rpc/src/rpc_subscription_tracker.rs",
    "rpc/src/parsed_token_accounts.rs",
    "rpc/src/rpc/account_resolver.rs",
]


target_scopes = [
    "Critical. An unprivileged attacker steals or destroys lamports or account data they never signed for, by abusing signer/owner/writability enforcement in `TransactionContext`/`InstructionContext`, `InvokeContext`, CPI signer-seed and PDA derivation in `cpi.rs`, or builtin handlers in `system_processor`/`vote_processor`, so a fee-paying transaction mutates a victim account without the victim's signature.",
    "Critical. An unprivileged attacker escapes SBPF memory confinement with a program they deployed themselves, by defeating address translation in `program-runtime/src/memory.rs`, `MemoryMapping` region permissions, `serialization.rs` input layout, `vm_slice`/`vm_addresses`, or `syscalls/src/mem_ops.rs`, gaining host memory read/write or forging account owner/lamports/data outside its region.",
    "Critical. An unprivileged attacker splits consensus with one transaction, by making execution or state commit nondeterministic across validators - divergent account loading, rent, fee, nonce/rollback, `accounts_lt_hash`, sysvar or status-cache state - so honest validators frozen the same block compute different bank hashes or accept different results.",
    "Critical. An unprivileged attacker breaks compute/cost metering, by crafting compute-budget instructions, CPI depth, or account-data growth that `ComputeBudgetInstructionDetails`, `compute_budget_limits`, `CostModel`, or `CostTracker` account differently from what execution actually consumes, allowing unmetered execution or a block-limit disagreement between leader and replaying validators.",
    "Critical. An unprivileged attacker halts the cluster with one transaction or account state, by reaching a panic, unchecked arithmetic overflow, unbounded allocation, or non-terminating loop on the mandatory replay path in `svm`, `program-runtime`, `transaction-context`, `runtime/src/bank.rs`, or `accounts-db`, so every validator that replays the block dies or stalls and requires human intervention.",
    "Advanced. An unprivileged attacker corrupts or exhausts validator account storage, by using accounts they create and mutate to drive `AccountsDb` store/flush/clean/shrink, `append_vec`/`accounts_file` encoding, `accounts_index`/`in_mem_accounts_index`, or `bucket_map` into inconsistent index state, unbounded memory growth, or an out-of-bounds/panic path.",
    "Advanced. An unprivileged attacker replays or short-changes fees and nonces, by exploiting `check_transactions`, `nonce_info`, `RollbackAccounts`, `status_cache`, `blockhash_queue`, or `fee` so an already-processed transaction executes twice, a failed transaction escapes its fee, or fee/rent distribution in `fee_distribution` credits the wrong account.",
    "Advanced. An unprivileged remote client exhausts a validator's non-RPC ingest path, by opening QUIC connections and streams to the TPU that defeat `connection_rate_limiter`, stream throttling, `ConnectionTable` eviction, packet batching in `streamer`/`perf`, `sigverify_stage` dedup, or the banking-stage container bounds, starving legitimate transactions on default configuration.",
    "Advanced. An unprivileged attacker forges precompile-verified signatures, by crafting instruction data or offsets that `precompiles/src/ed25519.rs`, `secp256k1.rs`, or `secp256r1.rs` accept while the referenced message or public key is not the one actually verified, so on-chain programs trusting the precompile authorize an attacker action.",
    "Intermediate. An unprivileged client crashes or exhausts the RPC process, by sending a single well-formed JSON-RPC or pubsub request within default rate limits that panics or allocates unbounded memory in `rpc.rs`, `filter.rs`, `parsed_token_accounts`, `rpc_service`, or the subscription tracker, killing the node's RPC service.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one agave target.

    ```
    target_file format:
    "'File Name: program-runtime/src/cpi.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact agave target:

    {target_file}

    Project focus:
    Agave is the Solana validator. Anything a fee-paying user can put on chain - a transaction's
    account list, instruction data, compute-budget instructions, address lookup tables, or the SBPF
    bytecode of a program they deploy - reaches transaction sanitization, account loading, the SBPF VM
    and its syscalls, CPI privilege propagation, builtin programs, fee/rent/nonce accounting, accounts-db
    commit, and the TPU/QUIC ingest path. Focus on theft of funds without the victim's signature,
    consensus divergence (bank hash or execution-result mismatch), validator panic or resource
    exhaustion that halts replay, non-RPC remote resource exhaustion, and RPC crashes.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (function, method, struct, enum, trait, const, feature gate) when possible.
    * Attacker is unprivileged only: anyone who can fund a keypair, submit transactions, deploy their own
      SBPF program, create and own accounts, open a QUIC connection to the TPU, or call the public RPC.
    * Attacker is NOT a validator, staked node, leader, gossip/turbine/repair peer, operator, or geyser
      plugin; cannot craft snapshots, shreds, blocks, or votes as a node; and has no keys, admin RPC,
      config, or host access.
    * Out of scope: alpenglow/votor crates, loader-v4, the VM interpreter, geyser and scheduler-bindings,
      malicious snapshots, bootstrap-phase instability, metrics, dependency bugs, test/bench/fixture code.
    * Ignore self-harm (attacker only damaging their own accounts) and pure best-practice critique.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target unauthorized account mutation, VM memory escape, CPI privilege escalation,
      consensus divergence, metering bypass, replay-path panic or exhaustion, or ingest/RPC DoS.
    * Every question must be testable by a Rust unit test, a crafted transaction against `Bank`/SVM, a
      deployed SBPF program, a fuzz or differential test over encoded inputs, or a crafted QUIC/RPC request.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authority is exact: lamports and data change only for accounts marked writable and signed as the
      instruction requires; CPI never grants a signer or writable flag the caller did not itself hold,
      and PDA signing needs the true seeds and owning program.
    * The VM is confined: every guest address resolves inside a mapped region with the right permission
      and length; no syscall exposes host memory, aliases an unwritable account, or writes past a region.
    * Execution is deterministic and metered: identical inputs give identical results, logs, and bank
      hash on every validator, and every byte and cycle is charged against the declared compute and
      block cost limits.
    * The replay path never dies: no attacker-supplied transaction, account, or account layout causes a
      panic, overflow, unbounded allocation, or stall in code every validator must run.
    * Value accounting is exact: fees, rent, nonce advance, rollback, and status-cache dedup are applied
      once, to the right accounts, with no replay of an already-processed transaction.

    Each question must include:
    1. target module/function;
    2. attacker action;
    3. preconditions;
    4. transaction/call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: module::function] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger TRANSACTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Rust test / crafted transaction / deployed SBPF program / QUIC or RPC request INPUTS and assert AUTHORITY_ENFORCEMENT, VM_CONFINEMENT, DETERMINISM, METERING, REPLAY_LIVENESS, or VALUE_ACCOUNTING.",
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
- Attacker is unprivileged only: anyone who can fund a keypair, submit transactions, deploy their own
  SBPF program, own accounts, connect to the TPU over QUIC, or call the public RPC. Not a validator,
  staked node, leader, gossip/turbine/repair peer, operator, or geyser plugin; no keys, admin RPC,
  config, or host access; no malicious block, shred, vote, or snapshot.
- Reject anything requiring a privileged or node-level actor, a stolen key, non-default configuration,
  a bug in a dependency or in the SBPF toolchain rather than in agave, or social engineering.
- Reject alpenglow/votor, loader-v4, the VM interpreter, geyser or scheduler-bindings integrations,
  malicious snapshots, bootstrap-phase instability, metrics, and test/bench/fixture-only issues.
- Reject best-practice cleanup, self-harm on the attacker's own accounts, and RPC issues needing
  repeated calls, multiple clients, or unfiltered getProgramAccounts-style scans.
- Focus on real compromise paths: unauthorized lamport/data mutation, SBPF memory escape, CPI privilege
  escalation, consensus divergence, compute/cost metering bypass, replay-path panic or exhaustion,
  non-RPC remote resource exhaustion, and RPC crashes.

## Validate
- Trace the exact reachable path from attacker input (transaction account list, instruction data,
  compute-budget instruction, lookup table, deployed program bytes, QUIC packet, RPC request) into the
  affected function.
- Check whether existing checks already stop it: transaction sanitization and account-index limits,
  signer/writable checks in `TransactionContext`, `MemoryMapping` region permissions and translation,
  CPI account-privilege checks, feature gates, compute-budget and `CostTracker` limits, and ingest
  rate limits.
- Account for what the attacker actually controls versus what runtime sanitization, the loader, or
  consensus already constrains.
- Accept only concrete impact: funds moved without the required signature, host memory or another
  account's state read/written, divergent bank hash or execution result, unmetered execution, a
  validator panic or stall on the replay path, ingest starvation, or an RPC process crash.
- Require exact file/function support and a reproducible Rust test or transaction-level PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Solana bounty category]

### Likelihood Explanation
[Preconditions, attacker capability, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust test, crafted transaction, SBPF program, or QUIC/RPC request with expected assertions]

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
- Reject node-level or privileged actors (validator, staked node, leader, gossip/turbine/repair peer,
  operator, geyser plugin, admin RPC), leaked-key, MITM, physical, and social-engineering claims.
- Reject alpenglow/votor, loader-v4, VM interpreter, malicious snapshot, bootstrap-phase, metrics,
  dependency-only, docs/style, and test/bench/fixture-only issues.
- Reject self-harm on the attacker's own accounts, missing-hardening claims, scanner output, pure
  volumetric DDoS, and theoretical claims with no demonstrated impact.
- Reject RPC claims needing a call rate above once per `CLUSTER_SLOT_TIME_TARGET / 2`, multiple
  clients, or unfiltered large-scan requests.
- A valid report must be triggerable by an ordinary funded user submitting transactions, deploying
  their own program, or sending TPU/RPC requests to a validator on default configuration.
- The final impact must map to an in-scope class: loss of funds without the victim's signature,
  consensus/safety violation, liveness loss requiring human intervention, remote resource exhaustion
  over a non-RPC protocol, or an RPC crash.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker transaction/program/packet -> trigger -> bad result.
4. Existing sanitization, privilege, translation, metering, and rate-limit checks reviewed and shown
   insufficient.
5. Concrete in-scope impact with realistic likelihood and attacker capability.
6. Reproducible proof path: Rust test, crafted transaction against `Bank`/SVM, deployed SBPF program,
   or crafted QUIC/RPC request.
7. No obvious rejection reason from SECURITY.md, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary funded user trigger this with no validator role, no stake, no keys, and no malicious
  node behaviour?
- Does the code actually behave as claimed on master with default config and current feature gates?
- Is the impact caused by agave's own code, not by the SBPF toolchain, solana-sdk, or a dependency?
- Is the stolen value, escaped memory access, hash divergence, panic, or exhaustion concrete and not
  hypothetical?
- Would an Anza triager accept the proof?
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
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible transaction/program/request sequence or Rust test plan]

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
- Keep only unprivileged analogs in transaction sanitization, account loading, SBPF memory translation
  and syscalls, CPI privilege propagation, builtin programs and precompiles, fee/rent/nonce accounting,
  accounts-db commit and indexing, compute/cost metering, TPU ingest limits, or RPC request handling.
- Reject node-level, privileged, leaked-key, MITM, malicious-snapshot, alpenglow/votor, loader-v4,
  interpreter, dependency-only, test-only, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable agave path from an ordinary user's transaction, deployed
  program, QUIC packet, or RPC call.
- Prove root cause with exact file/module/function support.
- Accept only concrete unauthorized fund or state mutation, VM memory escape, CPI privilege escalation,
  consensus divergence, metering bypass, replay-path panic or exhaustion, ingest starvation, or RPC crash.

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

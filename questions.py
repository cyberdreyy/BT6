import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'near/nearcore'
# todo: the name of the repository
REPO_NAME = 'nearcore'

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
    # Transaction and receipt admission: signatures, nonces, access keys, action limits
    # =================================================================================
    "runtime/runtime/src/verifier.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/access_keys.rs",
    "runtime/runtime/src/config.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/action/delegate.rs",
    "core/primitives/src/signable_message.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/utils.rs",
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/code.rs",
    "core/parameters/src/config.rs",

    # =================================================================================
    # Runtime apply: action execution, balance conservation, refunds, receipt routing
    # =================================================================================
    "runtime/runtime/src/lib.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/universal_account_id.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/pipelining.rs",
    "core/primitives/src/universal_state_init.rs",
    "core/primitives-core/src/universal_state_init.rs",

    # =================================================================================
    # Account lifecycle and per-account trie rows.
    # HIGHEST-YIELD REGION SO FAR: the one ACCEPTED submission to date lives in
    # core/store/src/utils/mod.rs (`remove_account` clears only 5 of ~14 account-scoped
    # TrieKey variants, and NEAR account names are reusable). Confirmed finding F4
    # (`initial_nonce_value` reseed) lives in access_keys.rs. Audit creation and
    # deletion of every account-scoped key SIDE BY SIDE.
    # =================================================================================
    "core/store/src/utils/mod.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives-core/src/trie_key.rs",

    # =================================================================================
    # Cross-shard flow control: congestion info, delayed/buffered queues, bandwidth
    # =================================================================================
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/bandwidth_scheduler/mod.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/bandwidth_scheduler.rs",

    # =================================================================================
    # Epoch rewards, inflation and supply reconciliation.
    # Confirmed finding F5 lives in reward_calculator.rs: the protocol-treasury reward
    # and the per-validator reward are written to ONE HashMap with plain `insert`, so a
    # treasury account that is also a validator loses its share while the returned total
    # still counts it. Audit every place a minted total and a per-account credit are
    # computed separately and must agree.
    # =================================================================================
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/chain/src/runtime/mod.rs",
    "core/primitives/src/chunk_apply_stats.rs",

    # =================================================================================
    # Chunk production, admission and validation: where a produced chunk is accepted
    # or rejected. The one submission currently IN REVIEW lives in
    # chain/client/src/stateless_validation/chunk_endorsement.rs.
    # chain/client/src/pending_transaction_queue.rs is the NEWEST, highest-churn code
    # in the repo (Spice) and carries several unaudited double-spend claims.
    # =================================================================================
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/stateless_validation/chunk_endorsement.rs",
    "core/primitives/src/stateless_validation/chunk_endorsement.rs",
    "chain/chain/src/validate.rs",
    "chain/jsonrpc/src/api/transactions.rs",

    # =================================================================================
    # Resharding: trie split vs in-flight queues and per-account state
    # =================================================================================
    "chain/chain/src/resharding/manager.rs",
    "chain/chain/src/resharding/event_type.rs",

    # =================================================================================
    # VM logic reachable from any attacker-deployed contract: host calls and gas
    # =================================================================================
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/imports.rs",

    # =================================================================================
    # Contract preparation, instrumentation, compilation and caching
    # =================================================================================
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v2.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",

    # =================================================================================
    # Trie state mutated by attacker transactions and the recorded storage proof
    # =================================================================================
    "core/store/src/trie/mod.rs",
    "core/store/src/trie/update.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/outgoing_metadata.rs",

    # =================================================================================
    # REMOVED: runtime/near-wallet-contract/** .
    # A wallet-contract report carrying a WORKING localnet PoC was ruled OUT OF SCOPE by
    # the program (see submitted/out_of_scope_wallet_contract_*.md). It previously
    # absorbed ~50% of all generated reports for zero payable output. Do not re-add.
    # =================================================================================
]


target_scopes = [
    "Critical. An unprivileged signer gets an action executed against an account it does not control, because signature binding, nonce/access-key lookup, FunctionCall access-key method/receiver/allowance restrictions, or DelegateAction relaying let the receiver, predecessor, or signer identity be forged, letting the attacker transfer, deploy, add keys to, or delete the victim's account without the victim's key.",
    "Critical. An unprivileged sender breaks NEAR balance conservation inside one chunk, minting or destroying tokens through gas refund computation, deposit refunds on failed actions, delete-account beneficiary transfer, locked/staked balance accounting, or storage-staking checks, inflating total supply or permanently reducing another account's balance.",
    "Critical. An unprivileged sender gets one transaction, receipt, or DelegateAction applied twice, by defeating access-key nonce monotonicity, transaction-hash uniqueness, delegate nonce/max_block_height checks, or receipt-id and implicit/deterministic account-id derivation, double-spending the attached deposit.",
    "Critical. An unprivileged sender crafts a transaction or contract whose chunk application is nondeterministic across nodes (compiled-contract cache reuse, host-call output depending on node-local state, float/NaN or iteration-order dependence, protocol-version-gated branch, wasm compilation differences), producing divergent state roots or gas burnt and an unintended permanent chain split.",
    "Critical. An unprivileged sender lands a single transaction or receipt that panics, aborts, overflows, or fails to terminate on the chunk apply path, so every node processing that shard crashes or the shard stalls permanently and requires human intervention to recover.",
    "Critical. An unprivileged sender permanently freezes funds: an account's tokens or a cross-shard receipt become unrecoverable because a receipt is stuck forever in the delayed, postponed, yield, or outgoing buffer queue, storage_usage underflow or overflow blocks every future action on the account, or a promise/yield resume path drops the value transfer.",
    "Critical. An attacker-deployed contract escapes its sandbox through near-vm-runner host logic, reading or writing guest memory out of bounds, reusing or forging registers, or building a promise batch that attaches predecessor/signer privileges or gas the caller never held, letting it act as another account and steal from contracts holding user funds.",
    "Critical. ACCOUNT-LIFECYCLE CLEANUP ASYMMETRY. State keyed by an account NAME survives that account's deletion and is inherited by a re-created account of the same name, because the teardown path clears only some of the per-account trie rows that the creation and mutation paths write. Enumerate every account-scoped TrieKey variant and diff the set written against the set cleared by remove_account; a survivor that is later delivered with predecessor_id == receiver_id passes check_actor_permissions and carries owner privilege. This pattern produced the only submission accepted so far.",
    "Critical. SEED-VERSUS-BOUND COLLISION. A value re-initialised from block height, epoch, or an index lands INSIDE the window of values already consumed under the old instance, rather than strictly above it, so a previously used nonce, id, or sequence number becomes valid again. Compare every re-initialisation constant against the admission bound enforced elsewhere and check whether the reseed dominates or merely re-enters the live range. This pattern produced confirmed finding F4.",
    "High. TWO WRITERS, ONE KEY SPACE. Two code paths write per-account amounts into one map, counter, or accumulator with a last-write-wins operation such as HashMap::insert, while a separately computed total counts both contributions. When one account satisfies both roles the map and the total silently disagree, minting or destroying value that no reconciliation pass ever notices. This pattern produced confirmed finding F5.",
    "High. A GUARD WHOSE COMPENSATING BRANCH IS WRONG. One site skips work because a comment or an invariant asserts another site already did it, but that other site does not, cannot, or no longer does. Read the skipped branch and the branch it defers to together and verify the handoff actually happens rather than trusting the comment. This is how F5 escaped detection despite existing tests.",
    "High. An unprivileged sender performs work far exceeding the gas burnt, through host-function metering, wasm instruction instrumentation, contract preparation and compilation charged after the work is done, storage read/write and recorded-proof accounting, or prepaid/attached gas arithmetic, obtaining near-free execution and blowing up block application time.",
    "High. An unprivileged sender makes a produced chunk exceed a validation limit, driving recorded storage-proof size, outgoing receipt size, or per-receipt action limits past what the receiving validators accept, so the chunk cannot be validated and the shard stalls.",
    "High. An unprivileged sender abuses cross-shard flow control, manipulating congestion info, delayed/buffered receipt accounting, or bandwidth-scheduler grant allocation so its own receipts are admitted while a target shard is starved or held congested, denying cross-shard service to other users.",
    "High. An unprivileged sender exploits a protocol blind spot the design never anticipated: an unmodelled interaction between two individually correct mechanisms (global or deterministic contract deployment vs the compiled-contract cache, yield-resume vs the delayed receipt queue, resharding trie split vs buffered receipts and congestion state, universal account init vs implicit account derivation, meta-transaction relaying vs access-key allowance refunds, storage staking vs state resizing mid-receipt) where each side's assumption holds alone but their composition breaks balance conservation, determinism, or authorization.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one nearcore target.

    ```
    target_file format:
    "'File Name: runtime/runtime/src/actions.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact nearcore target:

    {target_file}

    Project focus:
    nearcore is the NEAR Protocol reference client. Focus on what a transaction submitted by any internet client reaches: transaction and receipt validation, access keys and nonces, meta-transactions (DelegateAction), action execution and balance/refund accounting, account creation and DELETION together with every per-account trie row, storage staking, cross-shard receipts with congestion control and the bandwidth scheduler, trie state and recorded storage proofs, near-vm-runner host functions, gas metering, contract preparation and caching, epoch reward and inflation accounting, and chunk admission and validation. The eth-implicit wallet contract is OUT OF SCOPE and must never be targeted.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (fn, method, struct, field, const) when possible.
    * Attacker is unprivileged only: an ordinary client that funds a NEAR account, signs and submits transactions to a public RPC endpoint, deploys its own wasm contract, relays meta-transactions, and fully controls action arguments, deposits, attached gas, contract bytecode, and contract call arguments.
    * Attacker is NOT a validator, block or chunk producer, chunk validator, node or RPC operator, or network peer. Ignore malicious-node, malicious-peer, gossip/network-layer, state-sync, and social-engineering assumptions.
    * Epoch reward, inflation and chunk-validation code IS in scope, but only for defects an unprivileged sender or an ordinary configuration reaches - never for attacks that require the attacker to BE a validator or chunk producer. A supply-accounting error at an epoch boundary counts; forging an endorsement does not.
    * Ignore tests, benches, mocks, fuzz harnesses, docs, generated files, params estimator, sandbox/test-only features, CLI and config, indexer and tooling, and dependency-only issues.
    * Only consider paths reachable under the current mainnet protocol version and default feature set.
    * Every question must be a concrete real-world scenario an unprivileged sender can perform on mainnet. No speculative "unbounded memory/allocation" or resource-hygiene questions unless the scope explicitly targets gas or size accounting.
    * Generate 30 to 40 high-signal questions.
    * At least 70% must target theft or permanent freezing of funds, token minting or destruction, double-spend or replay, authorization escalation across accounts or promises, state-root divergence and chain split, or an apply-path panic that halts a shard.
    * Every question must be testable by a Rust unit test, a runtime/apply or test-loop integration test, or a differential/table test.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO code sites and ask whether they agree: a writer and
      its cleanup, a total and its per-account breakdown, a re-initialisation and the
      bound that admits values, a guard and the branch it defers to. Every finding
      confirmed in this repo so far had that shape, and every refuted cluster came from
      reading one site alone.
    * Prefer a question whose disagreement can be asserted numerically in one test
      (sum of parts equals total, reseeded value exceeds every consumed value, set of
      keys written equals set of keys cleared) over a narrative question.


    Known dead ends - do NOT generate questions about these. Each was audited to a cited
    conclusion and rejected; regenerating them wastes the whole batch:
    * DeleteAccount to a non-existent or self beneficiary "burning" the balance. Intended
      and documented at runtime/runtime/src/actions.rs:895-898; the beneficiary is chosen
      by the account owner and no attacker influences it.
    * action_delete_account burning Account.locked (staked) balance. Unreachable:
      check_actor_permissions rejects DeleteAccount with DeleteAccountStaking whenever
      locked is non-zero.
    * AddressRegistrar::register keeping excess deposit, and any wallet-contract issue.
      Out of scope.
    * DelegateAction lacking chain_id / genesis_hash binding (cross-network replay).
      Already known and already submitted.
    * Duplicate entries in ActionReceipt::input_data_ids desyncing PendingDataCount. The
      desync is real but unreachable: ext.rs mints a fresh data_id per dependency from a
      monotonic counter, so promise_and cannot produce duplicates.
    * Outgoing receipts being forwarded, or validator proposals surviving, after a
      receipt-level failure. ActionResult::set_error clears both and every action routes
      through merge.
    * Unbounded minting via the subsidized_amount skip-deduct path. Capped at 1 yoctoNEAR
      per call and reconciled out of total_balance_burnt in chain/chain/src/runtime/mod.rs.
    * The gas-key nonce prefetch cache going stale within a chunk. It is written back
      immediately after set_gas_key_nonce.
    * Anything whose only "attacker" is the account owner harming their own account, with
      no third party and no protocol invariant broken.

    Core invariants:
    * Authorization exactness: only the transaction signer's own account is acted on, access-key permissions and delegate limits are never widened, and a promise never carries privileges its creator did not hold.
    * Value conservation: total supply changes only by declared fees, gas burnt, refunds, and inflation; no transaction or receipt executes twice.
    * Determinism: the same pre-state and chunk produce identical post state root, gas burnt, and outgoing receipts on every node.
    * Metering totality: every wasm instruction, host call, byte written, and recorded storage-proof byte is charged and bounded before it is consumed.
    * Liveness: no attacker input reaches a panic, overflow, or non-terminating loop on the apply path, and every receipt eventually resolves.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction, action, or contract call);
    3. preconditions (funded account, deployed contract, account state);
    4. transaction/receipt sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger TRANSACTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/integration test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, METERING_TOTALITY, or LIVENESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused nearcore exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an ordinary client that funds a NEAR account, signs and submits transactions to a public RPC endpoint, deploys its own wasm contract, and relays meta-transactions. No validator, block/chunk producer, chunk validator, node or RPC operator, or network peer access; no leaked keys or social engineering.
- Reject malicious-node, malicious-peer, network/gossip-layer, block or chunk production, state-sync, epoch-manager, and misconfiguration-only paths.
- Reject test/mock/bench/fuzz, docs, generated-file, params-estimator, sandbox/test-only feature, CLI/config, indexer/tooling, and dependency-only findings.
- Reject speculative resource-hygiene claims with no reachable mainnet scenario.
- Focus on real impact: theft or permanent freezing of user funds, token inflation or loss, double-spend/replay, authorization escalation across accounts or promises, state-root divergence and chain split, or a shard-halting panic.

## Validate
- Trace the exact reachable path from the attacker's transaction (action list, deposit, attached gas, access key, delegate action, contract bytecode, call arguments) into the affected function.
- Check whether existing signature, nonce, access-key permission, action validation, gas metering, storage-staking, or size-limit checks already stop it.
- Accept only a concrete loss or freezing of funds, consensus divergence, or shard/network halt caused by this code.
- Require exact file/function support and a reproducible Rust unit or runtime/test-loop integration test PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker transaction inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching NEAR bounty category]

### Likelihood Explanation
[Preconditions, cost to the attacker, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Unit/integration test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for nearcore security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-node, malicious-peer, network/gossip-layer, block or chunk production, state-sync, epoch-manager, operator-only, misconfiguration, leaked-key, dependency-only, docs/style, generated-file, and test/mock/bench/fuzz-only issues.
- Reject params-estimator, sandbox and test-only features, CLI/config, indexer and tooling findings.
- Reject if the exploit needs validator, producer, RPC-operator, or peer privileges, victim social engineering, an impossible setup, or anything beyond what an ordinary client can put in a transaction or a deployed contract.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged signer submitting transactions on a default-configured mainnet-like network at the current protocol version.
- The final impact must map to an in-scope NEAR category: direct theft or permanent freezing of funds, unauthorized token minting or supply loss, unintended chain split, or network/shard halt requiring human intervention.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker transaction -> trigger -> bad result.
4. Existing signature, nonce, access-key permission, action validation, gas metering, storage-staking, and size-limit checks reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood and attacker cost.
6. Reproducible proof path: Rust unit PoC, runtime/test-loop integration test, or exact transaction steps against a local network.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary funded account trigger this with a transaction or its own deployed contract, without validator, producer, or operator access?
- Does the code actually behave as claimed at the current mainnet protocol version?
- Is the impact caused by this code, not by a malicious node, peer, or dependency alone?
- Is the theft, freezing, inflation, replay, divergence, or halt concrete rather than hypothetical?
- Would a NEAR triager accept the proof?
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
[Concrete in-scope impact, severity rationale, and NEAR bounty category]

## Likelihood Explanation
[Attacker capability, preconditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or unit/integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for nearcore.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-signer analogs in transaction and receipt validation, access keys and nonces, meta-transactions, action execution and refunds, storage staking, cross-shard receipts and congestion control, trie state and recorded storage proofs, near-vm-runner host functions and gas metering, contract preparation and caching, or the eth-implicit wallet contract.
- Reject malicious-node, malicious-peer, network-layer, block/chunk production, state-sync, epoch-manager, operator-only, mocked-only paths, dependency-only bugs, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable nearcore path from an ordinary client's transaction or deployed contract.
- Prove root cause with exact file/function support.
- Accept only concrete theft or permanent freezing of funds, token inflation or loss, double-spend/replay, authorization escalation across accounts or promises, state-root divergence, or a shard-halting panic.

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

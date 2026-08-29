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
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/pipelining.rs",
    # NIGHTLY-ONLY, kept commented rather than deleted: ProtocolFeature::UniversalAccounts
    # is version 154 and mainnet STABLE_PROTOCOL_VERSION is 87
    # (core/primitives-core/src/version.rs:633, :678), so these are unreachable today.
    # Re-enable them the moment UniversalAccounts stabilises.
    # "runtime/runtime/src/universal_account_id.rs",
    # "core/primitives/src/universal_state_init.rs",
    # "core/primitives-core/src/universal_state_init.rs",

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
    #
    # NOT INCLUDED: chain/client/src/pending_transaction_queue.rs and the rest of the
    # Spice pending-transaction-queue. It is the newest, highest-churn code in the repo
    # and looks attractive, but the whole path is gated on
    # `#[cfg(feature = "protocol_feature_spice")]` (core/chain-configs/src/client_config.rs
    # :880-885), and `protocol_feature_spice = []` is a standalone opt-in that neither
    # `default` nor `nightly` enables. It is unreachable on mainnet, so findings there
    # are not payable. Churn is not reachability - check the feature gate first.
    # =================================================================================
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
    # ---------------------------------------------------------------------------------
    # EVERY scope below maps to a VERBATIM in-scope impact from the program page:
    #   "Stealing or loss of funds" | "Unauthorized transaction" | "Transaction
    #   manipulation" | "Fee payment bypass" | "Balance manipulation" | "Contracts
    #   execution flows" | "Consensus flaws" | "Cryptographic flaws"
    # There is NO liveness, DoS, throughput, starvation or resource-exhaustion category.
    # "Network-level DoS" is explicitly OUT OF SCOPE. A report that cannot name one of
    # the strings above cannot be paid, however real it is.
    #
    # THE BAR, verbatim from the program: "It is not third-party theft from an account
    # that has not authorised the rotation, and that is the bar this program applies."
    # Every scope names a VICTIM WHO SIGNED NOTHING and an ATTACKER WHO GAINS.
    #
    # BANNED, do not generate under any scope: nonce / sequence / replay / re-execution
    # (closed class, "do not resubmit variants"), and anything framed as unbounded growth,
    # starvation, queue delay, throughput denial, or resource exhaustion.
    # ---------------------------------------------------------------------------------

    "Critical. STEALING - CROSS-ACCOUNT ACTION EXECUTION. Maps to \"Unauthorized transaction\". An action executes against an account whose key never authorised it, because the executor derives the acting identity from a field an attacker controls rather than from the verified signer: predecessor_id on a receipt the attacker shaped, a receiver rewritten between validation and execution, or an actor identity inherited from a prior frame. Name the site that sets the acting identity and the site that checks it, and show they can disagree. Victim: the account acted upon.",

    "Critical. STEALING - PROMISE AND RECEIPT PRIVILEGE FORGERY. Maps to \"Contracts execution flows\" and \"Stealing or loss of funds\". An attacker-deployed contract emits a promise or receipt carrying an identity, deposit, or gas allowance its creator never held, so a third-party contract that trusts predecessor_id or signer_id releases funds to the attacker. Target the receipt-construction helpers in runtime/runtime/src/receipt_manager.rs and runtime/runtime/src/ext.rs against the checks applied when that receipt is later applied.",

    "Critical. STEALING - VALUE ROUTED TO AN ATTACKER-CHOSEN RECIPIENT. Maps to \"Stealing or loss of funds\". A refund, beneficiary transfer, gas-key credit, or resumed-promise payout is addressed by a key the attacker can occupy at delivery time rather than by the identity that funded it. Enumerate every construction of a value-bearing receipt and ask what an attacker must control to become its recipient. The victim must be the original payee, not the attacker.",

    "Critical. LOSS OF FUNDS - A THIRD PARTY'S BALANCE MADE UNRECOVERABLE. Maps to \"Stealing or loss of funds\". An attacker's single action leaves another account's tokens permanently unreachable: a storage_usage or locked value that no subsequent action can satisfy, an account state from which every future action aborts, or a value transfer whose destination is provably unreachable. Must be permanent and must survive the attacker ceasing to act - a delay is not a loss and will not be paid.",

    "Critical. BALANCE MANIPULATION - SUPPLY CONSERVATION WITH A THIRD-PARTY PAYEE. Maps to \"Balance manipulation\". Within one chunk, tokens are created or destroyed outside declared fees, gas burnt, refunds and inflation, and the discrepancy accrues to or from an account other than the attacker's. Assert numerically: sum of every account delta plus burnt equals the declared total. Do NOT propose the reward-distribution HashMap collision; that specific pattern is exhausted.",

    "Critical. BALANCE MANIPULATION - A TOTAL AND ITS PARTS COMPUTED FROM DIFFERENT INPUTS. Maps to \"Balance manipulation\". One quantity is aggregated over a set at one site and re-derived over a different set, ordering, or configuration at another, so the two disagree for inputs an unprivileged sender shapes. Name both computations in the question and assert equality in one test. Exclude the epoch reward calculator, which is already mined out.",

    "Critical. UNAUTHORIZED TRANSACTION - SIGNED PAYLOAD VALID IN A CONTEXT IT WAS NOT SIGNED FOR. Maps to \"Unauthorized transaction\" and \"Cryptographic flaws\". Diff the exact byte range covered by the signature against every field the executor subsequently trusts. A discriminator the executor reads but the signature omits - an enum variant tag, a version byte, a receiver, a shard or chain identifier - lets a relayer or observer redirect an authorisation the signer gave for something else. Nonce fields are OUT: that class is closed.",

    "Critical. UNAUTHORIZED TRANSACTION - ACCESS-KEY PERMISSION WIDENING. Maps to \"Unauthorized transaction\". A FunctionCall access key granted to a third party reaches a receiver, method, or deposit outside its declared permission, because the permission is checked against one representation of the action and executed from another, or because a wrapping construct re-enters the executor without re-checking. Victim: the account owner who delegated a deliberately narrow key.",

    "Critical. TRANSACTION MANIPULATION - EXECUTED ACTION DIFFERS FROM THE AUTHORISED ONE. Maps to \"Transaction manipulation\". Between the point where an action is validated or signed and the point where it executes, a field is normalised, re-encoded, defaulted, truncated, or re-parsed, so the executed action is not the authorised one. Target every borsh round-trip, every From/Into between action representations, and every versioned-enum conversion on the path from SignedTransaction to apply_action.",

    "High. FEE PAYMENT BYPASS - WORK PERFORMED BEFORE OR WITHOUT THE CHARGE. Maps to \"Fee payment bypass\". An unprivileged sender obtains deserialization, compilation, linking, instantiation, decompression, hashing, or trie traversal that is charged after it completes, or not charged at all, and aborts before paying. Name the work site and the charge site and show the ordering. Frame the impact as fee bypass, never as resource exhaustion.",

    "High. FEE PAYMENT BYPASS - COST COMPUTED ON THE WRONG QUANTITY. Maps to \"Fee payment bypass\". A fee is derived from a quantity that an attacker can make arbitrarily smaller than what is actually consumed: declared length versus bytes traversed, source size versus expanded size, item count versus per-item cost, a cached measurement versus the live one. Assert the ratio an attacker can achieve, with numbers.",

    "Critical. CONSENSUS FLAW - DIVERGENT STATE ROOTS. Maps to \"Consensus flaws\". An attacker-controlled transaction or contract makes chunk application depend on something absent from the pre-state: compiled-contract cache reuse or eviction, iteration order over a non-deterministic collection, floating point or NaN, a value resolved against the current RuntimeConfig rather than the one in force when it was created, or a branch keyed on node-local state. Two honest nodes reach different roots.",

    "Critical. CONSENSUS FLAW - PRODUCER AND VALIDATOR DISAGREE ON THE SAME CHUNK. Maps to \"Consensus flaws\". An unprivileged sender crafts a receipt for which the chunk producer's recorded storage proof does not determine the post-state a validator re-derives: a read that bypasses the recorder, a size counter that advances without capturing the node, or state a rollback discards that the replay still needs. The attacker need never be a validator; they only supply the transaction.",

    "Critical. CONSENSUS FLAW - APPLY-PATH ABORT FROM UNPRIVILEGED INPUT. Maps to \"Consensus flaws\" and the program's fixed-fee DoS tier. One transaction or receipt reaches a panic, unwrap on None, expect, assert, or checked-arithmetic failure on the chunk-apply path, so every node applying that shard aborts identically. Prioritise arithmetic on attacker-sized quantities, indexing by attacker-supplied indices, and expects justified by a comment rather than a check. Frame as a deterministic abort, not as slowness or exhaustion.",

    "Critical. CRYPTOGRAPHIC FLAW - VERIFICATION SCOPE OR PRIMITIVE MISUSE. Maps to \"Cryptographic flaws\". A signature, hash, or key-derivation primitive is used outside the assumptions that make it sound: a verifier that accepts more than one encoding of the same authorisation, a hash whose preimage an attacker can steer to collide across two type domains, a key recovered rather than checked, or a curve or subgroup assumption enforced in one call path and not its sibling. Verify inside the dependency crate before claiming a check is absent.",

    "High. CONTRACTS EXECUTION FLOWS - SANDBOX AND HOST-FUNCTION BOUNDARY. Maps to \"Contracts execution flows\". An attacker-deployed contract reads or writes outside its guest memory, forges or reuses a register, or observes host state it should not, via near-vm-runner logic. Target the length, offset, and register arguments of every host function against the bounds actually enforced, and name the third-party contract whose funds the escape reaches.",

    "High. CONTRACTS EXECUTION FLOWS - GLOBAL CONTRACT IDENTITY AND CODE SUBSTITUTION. Maps to \"Contracts execution flows\" and \"Stealing or loss of funds\". The global-contract surface is the least-audited value-bearing path in the tree. Hunt whether the code an account resolves through UseGlobalContract can change after opt-in, whether a distribution can bind code to an identity its deployer did not own, and whether the storage fee and the deployment outcome can disagree. Victim: every account that opted into that identifier.",

    "High. CONTRACTS EXECUTION FLOWS - DERIVED ACCOUNT ID COLLISION. Maps to \"Contracts execution flows\" and \"Unauthorized transaction\". Deterministic and implicit account ids are derived from caller-supplied material into a namespace shared with named accounts. Hunt a derivation an attacker can steer onto an id a third party will later derive or already holds, or a path that installs code or keys at a derived id before its rightful deriver arrives. Compare each derive_* helper against the existence and actor checks applied at that id.",

    "High. STEALING - STORAGE STAKING AS A VALUE LEVER. Maps to \"Stealing or loss of funds\" and \"Balance manipulation\". Storage staking converts state size into locked balance, so any path where an attacker changes a THIRD PARTY's storage_usage, or where a size charged differs from the size stored, moves that account's spendable balance without its consent. Compare every storage_usage mutation against the bytes actually written, and every refund of storage against the bytes actually freed.",

    "High. UNMODELLED COMPOSITION OF TWO CORRECT MECHANISMS. Must still name a verbatim impact category. Each side holds alone and the interaction was never specified: global or deterministic deployment against the compiled-contract cache, storage staking against state that resizes mid-receipt, meta-transaction relaying against refund routing, promise data matching against cross-shard ordering, gas keys against fee accounting. State both mechanisms' assumptions explicitly, then name the sequence in which they contradict and the funds that move.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit questions for one nearcore target.

    target_file format:
    "'File Name: runtime/runtime/src/actions.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact nearcore target:

    {target_file}

    nearcore is the NEAR Protocol reference client. Reachable surface: transaction and
    receipt validation, access keys, meta-transactions, action execution and balance/refund
    accounting, account creation and deletion, storage staking, cross-shard receipts,
    congestion and bandwidth, trie state and recorded storage proofs, near-vm-runner host
    functions, gas metering, contract preparation and caching, global and deterministic
    contracts, epoch reward accounting, chunk admission and validation.

    ATTACKER: an ordinary client. Funds an account, signs and submits transactions to a
    public RPC, deploys its own wasm, relays meta-transactions, fully controls action
    arguments, deposits, attached gas, bytecode and call arguments. NOT a validator, chunk
    producer, chunk validator, node/RPC operator or network peer. Ignore malicious-node,
    gossip, network-layer, state-sync and social-engineering premises. Epoch reward and
    chunk-validation code is in scope only for defects an unprivileged sender reaches.

    THREE HARD FILTERS - a question failing any one is discarded regardless of merit.

    1. VICTIM WHO SIGNED NOTHING. The program's stated bar, verbatim: "It is not
       third-party theft from an account that has not authorised the rotation, and that is
       the bar this program applies." Name (a) a victim who signed nothing enabling the
       attack and (b) an attacker who ends with value or authority not already theirs. If
       the only injured party is the account owner acting on their own account, it is a
       footgun - a PoC-perfect report of exactly that shape was closed Informative.

    2. NAME A VERBATIM PAID CATEGORY, one of exactly these: "Stealing or loss of funds",
       "Unauthorized transaction", "Transaction manipulation", "Fee payment bypass",
       "Balance manipulation", "Contracts execution flows", "Consensus flaws",
       "Cryptographic flaws". There is NO liveness or DoS category and "Network-level DoS"
       is out of scope. If none fits, drop the question.

    3. NOT A BANNED CLASS:
       (a) NONCE / SEQUENCE / REPLAY / DOUBLE-EXECUTION in any form - nonce monotonicity or
           reseeding, tx-hash uniqueness, delegate max_block_height, anything executing
           twice. Closed as Informative: "please do not resubmit variants of this class".
       (b) UNBOUNDED / STARVATION / EXHAUSTION framing - unbounded growth or allocation,
           state bloat, queue or phase starvation, throughput denial. If the harm is "it
           gets slower", "it grows without bound" or "the queue never drains", it is
           unpayable. A DETERMINISTIC ABORT halting a shard IS allowed, but name the exact
           panicking expression and frame it as a consensus abort, never as exhaustion.

    QUESTION QUALITY:
    * Treat `File Name:` as the exact file/module and `Scope:` as the ONLY impact to target.
    * Use exact Rust symbols (fn, method, struct, field, const). Assume full repo context.
    * Prefer questions naming TWO code sites and asking whether they agree: a writer and its
      cleanup, a total and its per-account breakdown, a validator and the executor it feeds,
      a guard and the branch it defers to. Every finding ever confirmed here had that shape;
      every refuted cluster came from reading one site alone.
    * Prefer a disagreement assertable numerically in one test (sum of parts equals total,
      keys written equals keys cleared, fee charged equals work consumed) over narrative.
    * Before claiming the runtime mishandles a value, check whether action_validation.rs
      already makes that value unconstructible - validation runs first, and three separate
      report clusters described the execution layer correctly and were still wrong for this.
    * Never assert a limit from parameters.yaml (cumulative overlays); phrase so it resolves
      through RuntimeConfigStore at PROTOCOL_VERSION.
    * A defect that is shallow from one function, in a core file, and years old is probably
      already filed. Prefer compositions of two or three sites, or low-traffic regions.
    * Ignore tests, benches, mocks, fuzz harnesses, docs, generated files, params estimator,
      sandbox/test-only features, CLI/config, indexer, tooling, dependency-only issues.
    * Only paths reachable at mainnet STABLE_PROTOCOL_VERSION = 87 with default features.
    * Every question testable by a Rust unit test, a runtime/apply or test-loop integration
      test, or a differential/table test. Avoid generic checklists and repeated root causes.
    * Generate 40 to 80 questions. At least 80% must target theft of a third party's funds,
      permanent irrecoverable loss, token minting or destruction, authorization escalation
      across accounts or promises, fee payment bypass, state-root divergence or
      producer/validator disagreement, or a deterministic apply-path abort.

    DEAD ENDS - each already audited to a cited conclusion across 1328 adjudicated reports.
    Regenerating any of these wastes the batch.

    Exhausted seams (produced every finding this audit confirmed; all now closed):
    * Account-name reuse after DeleteAccount - any state keyed by account name surviving
      remove_account and inherited by a re-created account. Filed and CLOSED.
    * Nonce reseeding into an already-consumed window. CLOSED AS INFORMATIVE.
    * Two writers into one key space with a separately computed total (reward
      distribution HashMap::insert). Needs treasury == staked validator; impossible on
      mainnet.

    Known in-tree - an issue number or named ProtocolFeature already covers it:
    * Receipts exceeding max_receipt_size via output_data_receivers appended after
      validation, and all try_forward / bandwidth-request consequences. Issue 12606; the
      size clamp is the deliberate mitigation.
    * Contract-loading fee charged after the work / computed on source bytes.
      FixContractLoadingCost. * ML-DSA compute charged to the wrong shard.
      FixMlDsaCostCharging. * WithdrawFromGasKey nested in a DelegateAction.
      RejectWithdrawFromGasKeyInDelegate (live at 87). * 2000-byte removal charge not
      refunded on rollback. Issue 10890, deliberate safe-direction upper bound.
    * Resharding x congestion-control integration. dynamic_resharding.md TODOs 11-12.
    * CongestionInfo underflow -> panic. TODO(#2152), deliberate fail-fast.
    * DelegateAction lacking chain_id / genesis_hash binding. Already submitted.

    Prevented by a guard one frame away - the state is unconstructible:
    * Gas key with a FunctionCallPermission allowance (action_validation.rs:349 rejects it);
      attacker-supplied GasKeyInfo.balance (:364); gas key on the account-funded path
      (verifier.rs:284-290); refund reaching panic!("must be implicit") (lib.rs:551);
      alt_bn128 G2 subgroup check (inside zeropool-bn: AffineG::new runs subgroup_check and
      G2Params::check_order() is true); action_create_account overwriting an account or
      CreateAccount hijacking an implicit id (check_account_existence); third party
      overwriting a GlobalContractIdentifier (actions.rs:759-772); duplicate input_data_ids
      (ext.rs mints fresh ids); multiple Delegates inflating fees
      (DelegateActionMustBeOnlyOne, action_validation.rs:98-111); total_send_fees using the
      outer sender_is_receiver (config.rs:335 uses the inner); unmetered O(num_nonces)
      writes in add_gas_key (fee multiplies by num_nonces, capped 1024).

    Receipt unwind is atomic - stop proposing double refunds, phantom accounts or surviving
    side effects after a failed action:
    * set_error (lib.rs:488-493) clears new_receipts, validator_proposals, tokens_burnt,
      subsidized_amount. * A failed receipt calls state_update.rollback() (lib.rs:1045) and
      TrieUpdate::rollback (update.rs:225-228) clears the ENTIRE prospective write set,
      including direct writes like set_access_key. * lib.rs:1262 makes the inline and
      receipt-level deposit refunds mutually exclusive.

    Unreachable configuration - resolve via RuntimeConfigStore, never parameters.yaml:
    * prepare_v2 anything (at 87 vm_kind == Wasmtime, so prepare_v3 always runs).
    * prepare_v3 duplicate Import/Export sections (gated on !discard_custom_sections, which
      is true). * Any feature above 87: FixContractLoadingCost 129, ShuffleShardAssignments
      143, EarlyKickout 152, FixMlDsaCostCharging 153, UniversalAccounts 154 (so all `0u` /
      UniversalStateInit paths), Spice 180. DelegateV2 landed at 85 but RejectDelegateV2
      disables it at 87. * Any defect whose only window is a version BELOW 87.
    * The Spice pending-transaction-queue (non-default protocol_feature_spice cfg).

    Not consensus - a bypass only means a tx reaches a chunk then fails, burning the
    sender's own fee:
    * chain/client/src/pending_transaction_queue.rs entirely and every PendingConstraints
      rule. verifier.rs:307-309 and :486-489 both state pending constraints are zero on the
      consensus path and the RPC path "does not affect consensus".
    * rpc_handler.rs admission accounting. * Node-local I/O failures (contract cache
      sync_data): not attacker-controlled, degrades one operator.

    Fails the bar - real behaviour, no non-consenting victim:
    * Anything whose only injured party is the account owner on their own account (allowance
      refund landing on a re-added key, gas-key refund lost on re-add, deposit burned by an
      owner-chosen beneficiary). AddKey/DeleteKey are owner-only.
    * DeleteAccount to a self/nonexistent beneficiary (intended, actions.rs:895-898).
    * Burning locked stake on delete (check_actor_permissions rejects it).
    * The 1-yoctoNEAR subsidised skip-deduct path (intended, capped, tracked, and loses ~19
      orders of magnitude in gas). * Any wallet-contract issue - out of scope by ruling.

    Invariants to assert against: authorization exactness (only the signer's account is
    acted on; permissions never widen; a promise never carries privileges its creator
    lacked); value conservation (supply changes only by declared fees, gas burnt, refunds,
    inflation); determinism (same pre-state and chunk produce identical post-root, gas burnt
    and outgoing receipts everywhere); metering totality (every instruction, host call, byte
    written and recorded-proof byte charged and bounded before consumption).

    Each question must include: target fn/method; attacker action; preconditions;
    transaction/receipt sequence; invariant tested; scoped impact; proof idea.

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

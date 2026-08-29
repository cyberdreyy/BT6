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
    # EXPANDED SCOPE — broad sweep of all in-scope protocol/consensus/runtime/storage/
    # crypto source. EXCLUDED: tests/mocks/benches/fuzz, generated files, build.rs,
    # params-estimator, chain/network (peer/gossip layer — out of attacker model),
    # jsonrpc/rosetta/indexer/tooling, neard/nearcore binaries, benchmarks, genesis
    # tooling, DB backends/migrations/cold+split+cloud storage (node-local, not
    # consensus), and runtime/near-wallet-contract/** (ruled OUT OF SCOPE by the program,
    # see submitted/out_of_scope_wallet_contract_*.md).
    #
    # AUDIT KNOWLEDGE carried from prior passes (do not lose):
    #  - core/store/src/utils/mod.rs: `remove_account` clears only 5 of ~14 account-scoped
    #    TrieKey variants and NEAR names are reusable (one ACCEPTED submission). Audit
    #    creation and deletion of every account-scoped key SIDE BY SIDE.
    #  - runtime/runtime/src/access_keys.rs: confirmed finding F4 (`initial_nonce_value`
    #    reseed).
    #  - chain/epoch-manager/src/reward_calculator.rs: confirmed finding F5 (treasury +
    #    per-validator reward written to ONE HashMap with plain `insert`).
    #  - NIGHTLY-ONLY / above STABLE_PROTOCOL_VERSION=87 and therefore unreachable today:
    #    universal_account_id / universal_state_init (UniversalAccounts v154). Not listed.
    #  - Spice pending-transaction-queue (chain/client/src/pending_transaction_queue.rs and
    #    the protocol_feature_spice cfg) is gated off on mainnet — findings there are not
    #    payable. Not listed.
    # =================================================================================
    # core/crypto
    # =================================================================================
    "core/crypto/src/errors.rs",
    "core/crypto/src/hash_domain.rs",
    "core/crypto/src/hash.rs",
    "core/crypto/src/key_conversion.rs",
    "core/crypto/src/key_file.rs",
    "core/crypto/src/lib.rs",
    "core/crypto/src/signature.rs",
    "core/crypto/src/signer.rs",
    "core/crypto/src/traits.rs",
    "core/crypto/src/util.rs",
    "core/crypto/src/vrf.rs",

    # =================================================================================
    # core/primitives-core
    # =================================================================================
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/apply.rs",
    "core/primitives-core/src/chains.rs",
    "core/primitives-core/src/code.rs",
    "core/primitives-core/src/config.rs",
    "core/primitives-core/src/deterministic_account_id.rs",
    "core/primitives-core/src/errors.rs",
    "core/primitives-core/src/gas.rs",
    "core/primitives-core/src/global_contract.rs",
    "core/primitives-core/src/hash.rs",
    "core/primitives-core/src/lib.rs",
    "core/primitives-core/src/serialize.rs",
    "core/primitives-core/src/trie_key.rs",
    "core/primitives-core/src/types.rs",
    "core/primitives-core/src/universal_account_id.rs",
    "core/primitives-core/src/universal_state_init.rs",
    "core/primitives-core/src/version.rs",

    # =================================================================================
    # core/primitives
    # =================================================================================
    "core/primitives/src/action/delegate.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/bandwidth_scheduler.rs",
    "core/primitives/src/block_body.rs",
    "core/primitives/src/block_header.rs",
    "core/primitives/src/block.rs",
    "core/primitives/src/challenge.rs",
    "core/primitives/src/chunk_apply_stats.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/epoch_block_info.rs",
    "core/primitives/src/epoch_info.rs",
    "core/primitives/src/epoch_manager.rs",
    "core/primitives/src/epoch_sync.rs",
    "core/primitives/src/errors.rs",
    "core/primitives/src/genesis/block.rs",
    "core/primitives/src/genesis/chunk.rs",
    "core/primitives/src/genesis/mod.rs",
    "core/primitives/src/lib.rs",
    "core/primitives/src/merkle.rs",
    "core/primitives/src/network.rs",
    "core/primitives/src/optimistic_block.rs",
    "core/primitives/src/profile_data_v2.rs",
    "core/primitives/src/profile_data_v3.rs",
    "core/primitives/src/rand.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/reed_solomon.rs",
    "core/primitives/src/sandbox.rs",
    "core/primitives/src/shard_layout/mod.rs",
    "core/primitives/src/shard_layout/utils.rs",
    "core/primitives/src/shard_layout/v0.rs",
    "core/primitives/src/shard_layout/v1.rs",
    "core/primitives/src/shard_layout/v2.rs",
    "core/primitives/src/shard_layout/v3.rs",
    "core/primitives/src/sharding.rs",
    "core/primitives/src/sharding/shard_chunk_header_inner.rs",
    "core/primitives/src/signable_message.rs",
    "core/primitives/src/spice/chunk_endorsement.rs",
    "core/primitives/src/spice/mod.rs",
    "core/primitives/src/spice/partial_data.rs",
    "core/primitives/src/spice/state_witness.rs",
    "core/primitives/src/state_part.rs",
    "core/primitives/src/state_record.rs",
    "core/primitives/src/state_sync.rs",
    "core/primitives/src/state.rs",
    "core/primitives/src/stateless_validation/chunk_endorsement.rs",
    "core/primitives/src/stateless_validation/chunk_endorsements_bitmap.rs",
    "core/primitives/src/stateless_validation/contract_distribution.rs",
    "core/primitives/src/stateless_validation/mod.rs",
    "core/primitives/src/stateless_validation/partial_witness.rs",
    "core/primitives/src/stateless_validation/state_witness.rs",
    "core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs",
    "core/primitives/src/stateless_validation/validator_assignment.rs",
    "core/primitives/src/telemetry.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives/src/trie_split.rs",
    "core/primitives/src/types.rs",
    "core/primitives/src/types/chunk_validator_stats.rs",
    "core/primitives/src/universal_state_init.rs",
    "core/primitives/src/upgrade_schedule.rs",
    "core/primitives/src/utils.rs",
    "core/primitives/src/utils/compression.rs",
    "core/primitives/src/utils/io.rs",
    "core/primitives/src/utils/min_heap.rs",
    "core/primitives/src/validator_mandates/compute_price.rs",
    "core/primitives/src/validator_mandates/mod.rs",
    "core/primitives/src/validator_signer.rs",
    "core/primitives/src/version.rs",
    "core/primitives/src/views.rs",

    # =================================================================================
    # core/parameters
    # =================================================================================
    "core/parameters/src/config_store.rs",
    "core/parameters/src/config.rs",
    "core/parameters/src/cost.rs",
    "core/parameters/src/lib.rs",
    "core/parameters/src/parameter_table.rs",
    "core/parameters/src/parameter.rs",
    "core/parameters/src/view.rs",
    "core/parameters/src/vm.rs",

    # =================================================================================
    # core/chain-configs
    # =================================================================================
    "core/chain-configs/src/client_config.rs",
    "core/chain-configs/src/genesis_config.rs",
    "core/chain-configs/src/genesis_validate.rs",
    "core/chain-configs/src/lib.rs",
    "core/chain-configs/src/updatable_config.rs",

    # =================================================================================
    # core/store
    # =================================================================================
    "core/store/src/adapter/chain_store.rs",
    "core/store/src/adapter/chunk_store.rs",
    "core/store/src/adapter/epoch_store.rs",
    "core/store/src/adapter/flat_store.rs",
    "core/store/src/adapter/mod.rs",
    "core/store/src/adapter/trie_store.rs",
    "core/store/src/columns.rs",
    "core/store/src/config.rs",
    "core/store/src/contract.rs",
    "core/store/src/db/cold_column_checked.rs",
    "core/store/src/db/colddb.rs",
    "core/store/src/db/metadata.rs",
    "core/store/src/db/mixeddb.rs",
    "core/store/src/db/mod.rs",
    "core/store/src/db/recoverydb.rs",
    "core/store/src/db/refcount.rs",
    "core/store/src/db/slice.rs",
    "core/store/src/db/splitdb.rs",
    "core/store/src/deserialized_column.rs",
    "core/store/src/flat/chunk_view.rs",
    "core/store/src/flat/delta.rs",
    "core/store/src/flat/manager.rs",
    "core/store/src/flat/mod.rs",
    "core/store/src/flat/storage.rs",
    "core/store/src/flat/types.rs",
    "core/store/src/genesis/initialization.rs",
    "core/store/src/genesis/mod.rs",
    "core/store/src/genesis/state_applier.rs",
    "core/store/src/lib.rs",
    "core/store/src/merkle_proof.rs",
    "core/store/src/metrics/mod.rs",
    "core/store/src/node_storage/mod.rs",
    "core/store/src/spice_proof_verifier.rs",
    "core/store/src/store.rs",
    "core/store/src/trie/config.rs",
    "core/store/src/trie/from_flat.rs",
    "core/store/src/trie/iterator.rs",
    "core/store/src/trie/mem/arena/alloc.rs",
    "core/store/src/trie/mem/arena/concurrent.rs",
    "core/store/src/trie/mem/arena/frozen.rs",
    "core/store/src/trie/mem/arena/hybrid.rs",
    "core/store/src/trie/mem/arena/mod.rs",
    "core/store/src/trie/mem/arena/single_thread.rs",
    "core/store/src/trie/mem/construction.rs",
    "core/store/src/trie/mem/flexible_data/children.rs",
    "core/store/src/trie/mem/flexible_data/encoding.rs",
    "core/store/src/trie/mem/flexible_data/extension.rs",
    "core/store/src/trie/mem/flexible_data/mod.rs",
    "core/store/src/trie/mem/flexible_data/value.rs",
    "core/store/src/trie/mem/freelist.rs",
    "core/store/src/trie/mem/iter.rs",
    "core/store/src/trie/mem/loading.rs",
    "core/store/src/trie/mem/lookup.rs",
    "core/store/src/trie/mem/memtrie_update.rs",
    "core/store/src/trie/mem/memtries.rs",
    "core/store/src/trie/mem/mod.rs",
    "core/store/src/trie/mem/nibbles_utils.rs",
    "core/store/src/trie/mem/node/encoding.rs",
    "core/store/src/trie/mem/node/mod.rs",
    "core/store/src/trie/mem/node/view.rs",
    "core/store/src/trie/mem/parallel_loader.rs",
    "core/store/src/trie/mod.rs",
    "core/store/src/trie/nibble_slice.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/interface.rs",
    "core/store/src/trie/ops/iter.rs",
    "core/store/src/trie/ops/mod.rs",
    "core/store/src/trie/ops/resharding.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/outgoing_metadata.rs",
    "core/store/src/trie/prefetching_trie_storage.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/shard_tries.rs",
    "core/store/src/trie/split.rs",
    "core/store/src/trie/state_parts.rs",
    "core/store/src/trie/state_snapshot.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/trie_storage.rs",
    "core/store/src/trie/update.rs",
    "core/store/src/trie/update/iterator.rs",
    "core/store/src/utils/mod.rs",
    "core/store/src/utils/sync_utils.rs",

    # =================================================================================
    # runtime/runtime
    # =================================================================================
    "runtime/runtime/src/access_keys.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "runtime/runtime/src/bandwidth_scheduler/mod.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/bandwidth_scheduler/simulator.rs",
    "runtime/runtime/src/cache_warming.rs",
    "runtime/runtime/src/config.rs",
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/contract_code.rs",
    "runtime/runtime/src/conversions.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/lib.rs",
    "runtime/runtime/src/pipelining.rs",
    "runtime/runtime/src/prefetch.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/state_viewer/errors.rs",
    "runtime/runtime/src/state_viewer/mod.rs",
    "runtime/runtime/src/types.rs",
    "runtime/runtime/src/universal_account_id.rs",
    "runtime/runtime/src/verifier.rs",

    # =================================================================================
    # runtime/near-vm-runner
    # =================================================================================
    "runtime/near-vm-runner/benchmarks/compile_contracts.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/errors.rs",
    "runtime/near-vm-runner/src/features.rs",
    "runtime/near-vm-runner/src/imports.rs",
    "runtime/near-vm-runner/src/lib.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/dependencies.rs",
    "runtime/near-vm-runner/src/logic/errors.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/mocks/mod.rs",
    "runtime/near-vm-runner/src/logic/mod.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/types.rs",
    "runtime/near-vm-runner/src/logic/utils.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v2.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/profile.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/utils.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/mod.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/trap_classification.rs",

    # =================================================================================
    # chain/epoch-manager
    # =================================================================================
    "chain/epoch-manager/src/adapter.rs",
    "chain/epoch-manager/src/epoch_info_aggregator.rs",
    "chain/epoch-manager/src/epoch_sync.rs",
    "chain/epoch-manager/src/genesis.rs",
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/shard_assignment/mod.rs",
    "chain/epoch-manager/src/shard_assignment/sticky_resharding.rs",
    "chain/epoch-manager/src/shard_tracker.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/epoch-manager/src/validator_stats.rs",

    # =================================================================================
    # chain/chain
    # =================================================================================
    "chain/chain/src/approval_verification.rs",
    "chain/chain/src/backfill_receipt_to_tx.rs",
    "chain/chain/src/block_processing_utils.rs",
    "chain/chain/src/blocks_delay_tracker.rs",
    "chain/chain/src/chain_update.rs",
    "chain/chain/src/chain.rs",
    "chain/chain/src/crypto_hash_timer.rs",
    "chain/chain/src/doomslug.rs",
    "chain/chain/src/flat_storage_init.rs",
    "chain/chain/src/garbage_collection.rs",
    "chain/chain/src/genesis.rs",
    "chain/chain/src/lib.rs",
    "chain/chain/src/lightclient.rs",
    "chain/chain/src/missing_chunks.rs",
    "chain/chain/src/orphan.rs",
    "chain/chain/src/pending_shard_jobs.rs",
    "chain/chain/src/pending.rs",
    "chain/chain/src/receipt_to_tx.rs",
    "chain/chain/src/resharding/event_type.rs",
    "chain/chain/src/resharding/flat_storage_resharder.rs",
    "chain/chain/src/resharding/manager.rs",
    "chain/chain/src/resharding/mod.rs",
    "chain/chain/src/resharding/resharding_actor.rs",
    "chain/chain/src/resharding/trie_state_resharder.rs",
    "chain/chain/src/resharding/types.rs",
    "chain/chain/src/runtime/errors.rs",
    "chain/chain/src/runtime/mod.rs",
    "chain/chain/src/runtime/signer_overlay.rs",
    "chain/chain/src/runtime/trie_update_wrapper.rs",
    "chain/chain/src/sharding.rs",
    "chain/chain/src/signature_verification.rs",
    "chain/chain/src/spice/activation.rs",
    "chain/chain/src/spice/all_stake_fallback.rs",
    "chain/chain/src/spice/ancestry_endorsements.rs",
    "chain/chain/src/spice/block_application.rs",
    "chain/chain/src/spice/chain.rs",
    "chain/chain/src/spice/chunk_application.rs",
    "chain/chain/src/spice/chunk_validation.rs",
    "chain/chain/src/spice/core_writer_actor.rs",
    "chain/chain/src/spice/core.rs",
    "chain/chain/src/spice/mod.rs",
    "chain/chain/src/state_snapshot_actor.rs",
    "chain/chain/src/state_sync/adapter.rs",
    "chain/chain/src/state_sync/mod.rs",
    "chain/chain/src/state_sync/state_request_tracker.rs",
    "chain/chain/src/state_sync/utils.rs",
    "chain/chain/src/stateless_validation/chunk_endorsement.rs",
    "chain/chain/src/stateless_validation/chunk_validation.rs",
    "chain/chain/src/stateless_validation/mod.rs",
    "chain/chain/src/stateless_validation/processing_tracker.rs",
    "chain/chain/src/stateless_validation/state_witness.rs",
    "chain/chain/src/store_validator.rs",
    "chain/chain/src/store_validator/validate.rs",
    "chain/chain/src/store/mod.rs",
    "chain/chain/src/store/utils.rs",
    "chain/chain/src/types.rs",
    "chain/chain/src/update_shard.rs",
    "chain/chain/src/validate.rs",

    # =================================================================================
    # chain/chunks
    # =================================================================================
    "chain/chunks/src/adapter.rs",
    "chain/chunks/src/chunk_cache.rs",
    "chain/chunks/src/client.rs",
    "chain/chunks/src/lib.rs",
    "chain/chunks/src/logic.rs",
    "chain/chunks/src/shards_manager_actor.rs",

    # =================================================================================
    # chain/chunks-primitives
    # =================================================================================
    "chain/chunks-primitives/src/error.rs",
    "chain/chunks-primitives/src/lib.rs",

    # =================================================================================
    # chain/chain-primitives
    # =================================================================================
    "chain/chain-primitives/src/error.rs",
    "chain/chain-primitives/src/lib.rs",

    # =================================================================================
    # chain/client
    # =================================================================================
    "chain/client/src/adapter.rs",
    "chain/client/src/adversarial.rs",
    "chain/client/src/chunk_distribution_network.rs",
    "chain/client/src/chunk_endorsement_handler.rs",
    "chain/client/src/chunk_inclusion_tracker.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/client/src/client_actor.rs",
    "chain/client/src/client.rs",
    "chain/client/src/config_updater.rs",
    "chain/client/src/debug.rs",
    "chain/client/src/gc_actor.rs",
    "chain/client/src/info.rs",
    "chain/client/src/lib.rs",
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/prepare_transactions.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/spice/chunk_executor_actor/coordinator.rs",
    "chain/client/src/spice/chunk_executor_actor/mod.rs",
    "chain/client/src/spice/chunk_executor_actor/per_shard.rs",
    "chain/client/src/spice/chunk_executor_actor/receipt_tracker.rs",
    "chain/client/src/spice/chunk_executor_actor/storage.rs",
    "chain/client/src/spice/chunk_validator_actor.rs",
    "chain/client/src/spice/data_distributor_actor.rs",
    "chain/client/src/spice/data_manager/item.rs",
    "chain/client/src/spice/data_manager/mod.rs",
    "chain/client/src/spice/mod.rs",
    "chain/client/src/spice/timer.rs",
    "chain/client/src/state_request_actor.rs",
    "chain/client/src/stateless_validation/chunk_endorsement.rs",
    "chain/client/src/stateless_validation/chunk_validation_actor.rs",
    "chain/client/src/stateless_validation/chunk_validator/mod.rs",
    "chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs",
    "chain/client/src/stateless_validation/mod.rs",
    "chain/client/src/stateless_validation/partial_witness/encoding.rs",
    "chain/client/src/stateless_validation/partial_witness/mod.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_deploys_tracker.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs",
    "chain/client/src/stateless_validation/shadow_validate.rs",
    "chain/client/src/stateless_validation/state_witness_producer.rs",
    "chain/client/src/stateless_validation/state_witness_tracker.rs",
    "chain/client/src/stateless_validation/validate.rs",
    "chain/client/src/sync_jobs_actor.rs",
    "chain/client/src/sync/block.rs",
    "chain/client/src/sync/epoch.rs",
    "chain/client/src/sync/external.rs",
    "chain/client/src/sync/handler.rs",
    "chain/client/src/sync/header.rs",
    "chain/client/src/sync/mod.rs",
    "chain/client/src/sync/state/chain_requests.rs",
    "chain/client/src/sync/state/downloader.rs",
    "chain/client/src/sync/state/mod.rs",
    "chain/client/src/sync/state/network.rs",
    "chain/client/src/sync/state/shard.rs",
    "chain/client/src/sync/state/task_tracker.rs",
    "chain/client/src/sync/state/util.rs",
    "chain/client/src/verified_peer_heights.rs",
    "chain/client/src/view_client_actor.rs",

    # =================================================================================
    # chain/client-primitives
    # =================================================================================
    "chain/client-primitives/src/debug.rs",
    "chain/client-primitives/src/lib.rs",
    "chain/client-primitives/src/types.rs",
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

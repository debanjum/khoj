import hashlib
import logging
import uuid
from abc import ABC, abstractmethod
from itertools import repeat
from typing import Any, Callable, List, Set, Tuple

from tqdm import tqdm

from khoj.database.adapters import EntryAdapters, get_user_search_model_or_default
from khoj.database.models import Entry as DbEntry
from khoj.database.models import EntryDates, KhojUser
from khoj.search_filter.date_filter import DateFilter
from khoj.utils.rawconfig import Entry

logger = logging.getLogger(__name__)


class TextToEntries(ABC):
    def __init__(self, config: Any = None):
        self.embeddings_model = state.embeddings_model
        self.config = config
        self.date_filter = DateFilter()

    @abstractmethod
    def process(
        self, files: dict[str, str] = None, full_corpus: bool = True, user: KhojUser = None, regenerate: bool = False
    ) -> Tuple[int, int]:
        ...

    @staticmethod
    def hash_func(key: str) -> Callable:
        return lambda entry: hashlib.md5(bytes(getattr(entry, key), encoding="utf-8")).hexdigest()

    @staticmethod
    def split_entries_by_max_tokens(
        entries: List[Entry], max_tokens: int = 256, max_word_length: int = 500
    ) -> List[Entry]:
        "Split entries if compiled entry length exceeds the max tokens supported by the ML model."
        chunked_entries: List[Entry] = []
        for entry in entries:
            # Split entry into words
            compiled_entry_words = [word for word in entry.compiled.split(" ") if word != ""]

            # Drop long words instead of having entry truncated to maintain quality of entry processed by models
            compiled_entry_words = [word for word in compiled_entry_words if len(word) <= max_word_length]
            corpus_id = uuid.uuid4()

            # Split entry into chunks of max tokens
            for chunk_index in range(0, len(compiled_entry_words), max_tokens):
                compiled_entry_words_chunk = compiled_entry_words[chunk_index : chunk_index + max_tokens]
                compiled_entry_chunk = " ".join(compiled_entry_words_chunk)

                # Prepend heading to all other chunks, the first chunk already has heading from original entry
                if chunk_index > 0:
                    # Snip heading to avoid crossing max_tokens limit
                    # Keep last 100 characters of heading as entry heading more important than filename
                    snipped_heading = entry.heading[-100:]
                    compiled_entry_chunk = f"{snipped_heading}.\n{compiled_entry_chunk}"

                chunked_entries.append(
                    Entry(
                        compiled=compiled_entry_chunk,
                        raw=entry.raw,
                        heading=entry.heading,
                        file=entry.file,
                        corpus_id=corpus_id,
                    )
                )

        return chunked_entries

    def update_embeddings(
        self,
        current_entries: List[Entry],
        file_type: str,
        file_source: str,
        key="compiled",
        logger: logging.Logger = None,
        deletion_filenames: Set[str] = None,
        user: KhojUser = None,
        regenerate: bool = False,
    ):
        with timer("Constructed current entry hashes in", logger):
            hashes_by_file = dict[str, set[str]]()
            current_entry_hashes = list(map(TextToEntries.hash_func(key), current_entries))
            hash_to_current_entries = dict(zip(current_entry_hashes, current_entries))
            for entry in tqdm(current_entries, desc="Hashing Entries"):
                hashes_by_file.setdefault(entry.file, set()).add(TextToEntries.hash_func(key)(entry))

        num_deleted_entries = 0
        if regenerate:
            with timer("Cleared existing dataset for regeneration in", logger):
                logger.debug(f"Deleting all entries for file type {file_type}")
                num_deleted_entries = EntryAdapters.delete_all_entries_by_type(user, file_type)

        hashes_to_process = set()
        with timer("Identified entries to add to database in", logger):
            for file in tqdm(hashes_by_file, desc="Identify new entries"):
                hashes_for_file = hashes_by_file[file]
                existing_entries = DbEntry.objects.filter(
                    user=user, hashed_value__in=hashes_for_file, file_type=file_type
                )
                existing_entry_hashes = set([entry.hashed_value for entry in existing_entries])
                hashes_to_process |= hashes_for_file - existing_entry_hashes

        embeddings = []
        with timer("Generated embeddings for entries to add to database in", logger):
            entries_to_process = [hash_to_current_entries[hashed_val] for hashed_val in hashes_to_process]
            data_to_embed = [getattr(entry, key) for entry in entries_to_process]
            model = get_user_search_model_or_default(user)
            embeddings += self.embeddings_model[model.name].embed_documents(data_to_embed)

        added_entries: list[DbEntry] = []
        with timer("Added entries to database in", logger):
            num_items = len(hashes_to_process)
            assert num_items == len(embeddings)
            batch_size = min(200, num_items)
            entry_batches = zip(hashes_to_process, embeddings)

            for entry_batch in tqdm(batcher(entry_batches, batch_size), desc="Add entries to database"):
                batch_embeddings_to_create = []
                for entry_hash, new_entry in entry_batch:
                    entry = hash_to_current_entries[entry_hash]
                    batch_embeddings_to_create.append(
                        DbEntry(
                            user=user,
                            embeddings=new_entry,
                            raw=entry.raw,
                            compiled=entry.compiled,
                            heading=entry.heading[:1000],  # Truncate to max chars of field allowed
                            file_path=entry.file,
                            file_source=file_source,
                            file_type=file_type,
                            hashed_value=entry_hash,
                            corpus_id=entry.corpus_id,
                        )
                    )
                added_entries += DbEntry.objects.bulk_create(batch_embeddings_to_create)
            logger.debug(f"Added {len(added_entries)} {file_type} entries to database")

        new_dates = []
        with timer("Indexed dates from added entries in", logger):
            for added_entry in added_entries:
                dates_in_entries = zip(self.date_filter.extract_dates(added_entry.raw), repeat(added_entry))
                dates_to_create = [
                    EntryDates(date=date, entry=added_entry)
                    for date, added_entry in dates_in_entries
                    if not is_none_or_empty(date)
                ]
                new_dates += EntryDates.objects.bulk_create(dates_to_create)
            logger.debug(f"Indexed {len(new_dates)} dates from added {file_type} entries")

        with timer("Deleted entries identified by server from database in", logger):
            for file in hashes_by_file:
                existing_entry_hashes = EntryAdapters.get_existing_entry_hashes_by_file(user, file)
                to_delete_entry_hashes = set(existing_entry_hashes) - hashes_by_file[file]
                num_deleted_entries += len(to_delete_entry_hashes)
                EntryAdapters.delete_entry_by_hash(user, hashed_values=list(to_delete_entry_hashes))

        with timer("Deleted entries requested by clients from database in", logger):
            if deletion_filenames is not None:
                for file_path in deletion_filenames:
                    deleted_count = EntryAdapters.delete_entry_by_file(user, file_path)
                    num_deleted_entries += deleted_count

        return len(added_entries), num_deleted_entries

    @staticmethod
    def mark_entries_for_update(
        current_entries: List[Entry],
        previous_entries: List[Entry],
        key="compiled",
        logger: logging.Logger = None,
        deletion_filenames: Set[str] = None,
    ):
        # Hash all current and previous entries to identify new entries
        with timer("Hash previous, current entries", logger):
            current_entry_hashes = list(map(TextToEntries.hash_func(key), current_entries))
            previous_entry_hashes = list(map(TextToEntries.hash_func(key), previous_entries))
            if deletion_filenames is not None:
                deletion_entries = [entry for entry in previous_entries if entry.file in deletion_filenames]
                deletion_entry_hashes = list(map(TextToEntries.hash_func(key), deletion_entries))
            else:
                deletion_entry_hashes = []

        with timer("Identify, Mark, Combine new, existing entries", logger):
            hash_to_current_entries = dict(zip(current_entry_hashes, current_entries))
            hash_to_previous_entries = dict(zip(previous_entry_hashes, previous_entries))

            # All entries that did not exist in the previous set are to be added
            new_entry_hashes = set(current_entry_hashes) - set(previous_entry_hashes)
            # All entries that exist in both current and previous sets are kept
            existing_entry_hashes = set(current_entry_hashes) & set(previous_entry_hashes)
            # All entries that exist in the previous set but not in the current set should be preserved
            remaining_entry_hashes = set(previous_entry_hashes) - set(current_entry_hashes)
            # All entries that exist in the previous set and also in the deletions set should be removed
            to_delete_entry_hashes = set(previous_entry_hashes) & set(deletion_entry_hashes)

            preserving_entry_hashes = existing_entry_hashes

            if deletion_filenames is not None:
                preserving_entry_hashes = (
                    (existing_entry_hashes | remaining_entry_hashes)
                    if len(deletion_entry_hashes) == 0
                    else (set(previous_entry_hashes) - to_delete_entry_hashes)
                )

            # load new entries in the order in which they are processed for a stable sort
            new_entries = [
                (current_entry_hashes.index(entry_hash), hash_to_current_entries[entry_hash])
                for entry_hash in new_entry_hashes
            ]
            new_entries_sorted = sorted(new_entries, key=lambda e: e[0])
            # Mark new entries with -1 id to flag for later embeddings generation
            new_entries_sorted = [(-1, entry[1]) for entry in new_entries_sorted]

            # Set id of existing entries to their previous ids to reuse their existing encoded embeddings
            existing_entries = [
                (previous_entry_hashes.index(entry_hash), hash_to_previous_entries[entry_hash])
                for entry_hash in preserving_entry_hashes
            ]
            existing_entries_sorted = sorted(existing_entries, key=lambda e: e[0])

            entries_with_ids = existing_entries_sorted + new_entries_sorted

        return entries_with_ids

    @staticmethod
    def convert_text_maps_to_jsonl(entries: List[Entry]) -> str:
        # Convert each entry to JSON and write to JSONL file
        return "".join([f"{entry.to_json()}\n" for entry in entries])

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelRequestContext:
    save_folder: str
    download_mode: str
    hide_below_enabled: bool
    hide_below_minutes: int
    hide_above_enabled: bool
    hide_above_minutes: int


@dataclass(frozen=True)
class FetchRequestToken:
    generation: int
    request_id: int
    channel_input: str
    context: ChannelRequestContext


@dataclass(frozen=True)
class LoadMoreRequestToken:
    generation: int
    request_id: int
    channel_id: str
    uploads_playlist_id: str
    page_token: str
    start_order: int
    context: ChannelRequestContext


@dataclass
class ChannelRequestState:
    generation: int = 0
    request_sequence: int = 0
    active_fetch: FetchRequestToken | None = None
    active_load_more: LoadMoreRequestToken | None = None
    pending_manual_key: str = ""
    pending_manual_key_request_id: int | None = None
    loaded_generation: int | None = None
    loaded_context: ChannelRequestContext | None = None

    def next_request_id(self) -> int:
        self.request_sequence += 1
        return self.request_sequence

    def begin_fetch(
        self,
        channel_input: str,
        context: ChannelRequestContext,
        manual_key: str,
    ) -> FetchRequestToken:
        self.generation += 1
        token = FetchRequestToken(
            generation=self.generation,
            request_id=self.next_request_id(),
            channel_input=channel_input,
            context=context,
        )
        self.active_fetch = token
        self.pending_manual_key = manual_key
        self.pending_manual_key_request_id = token.request_id
        return token

    def begin_load_more(
        self,
        *,
        channel_id: str,
        uploads_playlist_id: str,
        page_token: str,
        start_order: int,
        context: ChannelRequestContext,
    ) -> LoadMoreRequestToken:
        if self.loaded_generation is None:
            raise ValueError("loaded channel generation is required")
        token = LoadMoreRequestToken(
            generation=self.loaded_generation,
            request_id=self.next_request_id(),
            channel_id=channel_id,
            uploads_playlist_id=uploads_playlist_id,
            page_token=page_token,
            start_order=start_order,
            context=context,
        )
        self.active_load_more = token
        return token

    def restore_loaded_generation(self) -> None:
        if self.loaded_generation is not None:
            self.generation = self.loaded_generation

    def clear_pending_manual_key(self, token: FetchRequestToken | None = None) -> None:
        if token is not None and self.pending_manual_key_request_id != token.request_id:
            return
        self.pending_manual_key = ""
        self.pending_manual_key_request_id = None

    def take_accepted_manual_key(self, token: FetchRequestToken) -> str | None:
        if self.pending_manual_key_request_id != token.request_id:
            return None
        manual_key = self.pending_manual_key.strip()
        self.clear_pending_manual_key(token)
        return manual_key

    def accept_fetch(self, token: FetchRequestToken) -> None:
        self.loaded_generation = token.generation
        self.loaded_context = token.context
        self.active_fetch = None

    def fail_fetch(self, token: FetchRequestToken) -> None:
        if self.active_fetch == token:
            self.active_fetch = None
        self.restore_loaded_generation()
        self.clear_pending_manual_key(token)

    def complete_load_more(self, token: LoadMoreRequestToken) -> None:
        if self.active_load_more == token:
            self.active_load_more = None

    def is_current_request_log(self, token) -> bool:
        if isinstance(token, FetchRequestToken):
            return self.is_current_fetch(token)
        if isinstance(token, LoadMoreRequestToken):
            return self.is_current_load_more_token(token)
        return False

    def is_current_fetch(self, token: FetchRequestToken) -> bool:
        active = self.active_fetch
        return bool(
            isinstance(token, FetchRequestToken)
            and isinstance(active, FetchRequestToken)
            and token.generation == self.generation
            and token.request_id == active.request_id
            and token.generation == active.generation
        )

    def is_current_load_more_token(self, token: LoadMoreRequestToken) -> bool:
        active = self.active_load_more
        return bool(
            isinstance(token, LoadMoreRequestToken)
            and isinstance(active, LoadMoreRequestToken)
            and token.generation == self.generation
            and token.generation == self.loaded_generation
            and token.request_id == active.request_id
            and token.generation == active.generation
        )

    def is_current_load_more(
        self,
        token: LoadMoreRequestToken,
        *,
        channel_id: str,
        uploads_playlist_id: str,
        page_token: str,
        start_order: int,
    ) -> bool:
        return bool(
            self.is_current_load_more_token(token)
            and channel_id == token.channel_id
            and uploads_playlist_id == token.uploads_playlist_id
            and page_token == token.page_token
            and start_order == token.start_order
        )


@dataclass
class DownloadSessionState:
    run_sequence: int = 0
    active_run_id: int | None = None
    run_start_number: int | None = None
    selected_ids: set[str] = field(default_factory=set)
    initial_complete_ids: set[str] = field(default_factory=set)
    completed_ids: set[str] = field(default_factory=set)
    terminal_received: bool = False
    terminal_outcome: str = ""
    terminal_message: str = ""

    def begin_run(
        self,
        run_start_number: int,
        selected_video_ids,
        initial_complete_ids,
    ) -> int:
        self.run_sequence += 1
        self.active_run_id = self.run_sequence
        self.run_start_number = run_start_number
        self.selected_ids = self._normalized_ids(selected_video_ids)
        self.initial_complete_ids = self._normalized_ids(initial_complete_ids).intersection(
            self.selected_ids
        )
        self.completed_ids = set()
        self.reset_terminal()
        return self.active_run_id

    def record_completion(self, run_id: int, video_id) -> int | None:
        try:
            normalized_id = str(video_id or "").strip()
        except Exception:
            return None
        if run_id != self.active_run_id or not normalized_id:
            return None
        if normalized_id not in self.selected_ids:
            return None
        if normalized_id in self.initial_complete_ids or normalized_id in self.completed_ids:
            return None
        if not isinstance(self.run_start_number, int):
            return None
        self.completed_ids.add(normalized_id)
        return self.run_start_number + len(self.completed_ids)

    def record_terminal(self, outcome: str, message: str = "") -> bool:
        if self.terminal_received:
            return False
        self.terminal_received = True
        self.terminal_outcome = outcome or "completed"
        self.terminal_message = message or ""
        return True

    def reset_terminal(self) -> None:
        self.terminal_received = False
        self.terminal_outcome = ""
        self.terminal_message = ""

    def finish_run(self) -> None:
        self.reset_terminal()
        self.active_run_id = None

    @staticmethod
    def _normalized_ids(values) -> set[str]:
        normalized: set[str] = set()
        for value in values:
            try:
                item = str(value or "").strip()
            except Exception:
                continue
            if item:
                normalized.add(item)
        return normalized

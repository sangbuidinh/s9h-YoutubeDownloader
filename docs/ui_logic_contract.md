# YouTube Downloaderbs UI Logic Contract

This report maps the real production UI contract from the current codebase. The standalone prototype is not a source of truth and must not be ported into production.

Sources inspected:

- `ui/main_window.py`
- `core/downloader.py`, limited to `DownloadOptions`, `DownloadController`, cookie-source constants, `validate_download_environment()`, `effective_cookies_path()`, and where `--cookies` is added to yt-dlp commands
- `core/app_settings.py`, limited to protected API-key persistence, cookie source, manual cookie path, and bridge cookie path settings
- `core/download_modes.py`

## Startup Window Sizing

- The root window remains hidden while the production widgets are built and measured, then is shown with the fitted geometry to avoid a startup-size flash.
- Startup width prefers `1440` pixels but is capped to the current Tk screen/maximum width.
- Startup height is the greater of `700` pixels and the real requested widget height plus 8 pixels, capped to the current Tk screen/maximum height.
- When the display can fit the requested content, the minimum window height is raised to that requested height so the download action row cannot be resized below the visible client area.

## A. State Variables

### Tkinter Variables

| Variable | Type / initial value | Production role |
| --- | --- | --- |
| `api_key_var` | `tk.StringVar(value=api_key_state.api_key)` from a single startup `load_api_key_persistence_state()` call | API key input. A non-empty key is automatically protected with Windows DPAPI after an accepted successful Fetch. There is no remember checkbox or opt-out state. |
| `channel_var` | `tk.StringVar()` | Channel URL / channel ID / handle input. Required by `start_fetch()`. |
| `save_folder_var` | `tk.StringVar()` | Selected output folder. Used by local status reconciliation and `DownloadOptions.base_folder`. |
| `cookies_enabled_var` | `tk.BooleanVar(value=False)` | Main "Sử dụng Cookies" toggle. Not persisted. Controls cookie UI state and `DownloadOptions.cookies_enabled`. |
| `cookies_path_var` | `tk.StringVar(value=load_cookies_path())` | Manual `cookies*.txt` path for the File cookies source. Persisted as a path string only; missing stored files remain visible as missing and are not automatically cleared. |
| `cookie_source_var` | `tk.StringVar(value=COOKIE_SOURCE_LABELS[load_cookie_source()])` | UI label for current cookie source. Values are `File cookies.txt` and `Local Cookie Bridge`; mapped back to downloader values by `_current_cookie_source()`. |
| `bridge_cookie_path_var` | `tk.StringVar(value=load_bridge_cookie_path())` | Local Cookie Bridge `youtube_cookies.txt` path. Fresh default is empty; the old development `D:\...` path is compatibility-only and used only when `bridge_cookie_path` is absent and the real legacy file exists. |
| `cookie_status_var` | `tk.StringVar()` | Inline status for the active cookie source/path: `Missing` or `Found | N bytes | modified YYYY-MM-DD HH:MM:SS`. |
| `speed_limit_var` | `tk.StringVar()` | Download limit input. Validated by `validate_speed_limit()`; empty or zero means no limit. |
| `file_start_number_var` | `tk.StringVar(value="")` | Required session-only start number for output filenames. Blank by default on each launch, positive integer only, not persisted. Downloads are blocked until a valid value is entered. |
| `download_mode_var` | `tk.StringVar(value=MODE_VIDEO_THUMB)` | Current download mode. Values come from `DOWNLOAD_MODES`: `Video + Thumb`, `Audio MP3 + Thumb`, `Video + Audio MP3 + Thumb`. |
| `download_engine_var` | `tk.StringVar(value="Stable - yt-dlp internal")` | Session-only download engine selector. Values map to downloader engines `stable` and `aria2_fast`; unknown labels fall back to stable. The selected value is captured for the batch and is locked while downloads run. |
| `hide_below_enabled_var` | `tk.BooleanVar(value=True)` | Session-only lower duration filter checkbox. Defaults to enabled on every startup. |
| `hide_below_minutes_var` | `tk.StringVar(value="3")` | Session-only lower duration threshold entry. Zero through four ASCII digits only; empty while editing resets to `3` on focus loss or Fetch/Load More validation. |
| `hide_above_enabled_var` | `tk.BooleanVar(value=True)` | Session-only upper duration filter checkbox. Defaults to enabled on every startup. |
| `hide_above_minutes_var` | `tk.StringVar(value="60")` | Session-only upper duration threshold entry. Zero through four ASCII digits only; empty while editing resets to `60` on focus loss or Fetch/Load More validation. |
| `filter_var` | `tk.StringVar(value=FILTER_ALL)` | Filter combobox. Values are `Hiển thị tất cả` and `Chỉ hiển thị video chưa tải`. |
| `search_var` | `tk.StringVar()` | Search text. Traced to `_on_search_text_changed()`; search navigates visible title matches, it does not filter rows. |
| `search_status_var` | `tk.StringVar()` | Search status text: blank, `Không tìm thấy`, `0/N`, or current match index `N/M`. |
| `progress_current_var` | `tk.StringVar(value="Đang tải: Sẵn sàng")` | First progress line. Updated from `progress_queue` via `format_progress_event_lines()` and localized for the UI. |
| `progress_detail_var` | `tk.StringVar(value="Đang xử lý: -")` | Second progress line. Updated from `progress_queue` via `format_progress_event_lines()` and localized for the UI. |

### Runtime State And Flags

| State | Initial value | Production role |
| --- | --- | --- |
| `events` | `queue.Queue()` | Cross-thread UI event queue for logs, fetch results, load-more results, status updates, and download completion/errors. Polled every 100 ms. |
| `progress_queue` | `queue.Queue(maxsize=1)` | Latest-progress queue. Polled every 300 ms; stale progress is collapsed to the latest event. |
| `channel_info` | `None` | Current channel metadata after fetch. Required for load more, status reconciliation, and downloads. |
| `videos` | `[]` | Loaded video objects. Rows are keyed by `video.display_order`. |
| `selected_orders` | `set[int]()` | Selected video display orders. Selection persists across filtering but is pruned to loaded videos. |
| `visible_orders` | `list[int]()` | Display orders currently visible after short-video and downloaded-state filters. |
| `next_page_token` | `""` | YouTube API page token. Controls whether `Xem thêm video` can run. |
| `fetching` | `False` | True while the initial fetch worker is running. Disables fetch and load-more, but does not lock all controls. |
| `loading_more` | `False` | True while the load-more worker is running. Disables load-more. |
| `downloading` | `False` | True while the download worker is running. Locks most controls and blocks table selection/status edits. |
| `download_controller` | `None` | Set to `DownloadController()` for active downloads. It owns cancellation and current process termination. |
| `download_stop_requested` | `False` | True after stop/close cancellation is requested. Disables the stop button while the worker winds down. |
| `exit_after_download_stop` | `False` | True when close flow should exit after a stop request. |
| `close_requested` | `False` | Guards repeated close handling. |
| `cancel_download` | `False` | UI-side cancellation flag set during stop/close flows. Current downloader cancellation is driven by `download_controller.request_cancel()`. |
| `status_editor` | `None` | Inline combobox editor for the Status column. Destroyed before table refreshes and while changing selection/filter. |
| `search_match_orders` | `[]` | Visible display orders whose title matches `search_var`. |
| `current_search_match_index` | `-1` | Current search result position before/after Enter navigation. |
| `tree_column_drag`, `tree_column_ratios`, `tree_column_fit_after_id`, `tree_column_fit_in_progress` | Resize bookkeeping | Preserve user-driven/responsive Treeview column sizing. |

## B. Existing Button And Widget Handlers

| Visible control | Text / values | Exact handler or binding |
| --- | --- | --- |
| Fetch button | `Lấy danh sách Video` | `command=self.start_fetch` |
| Filter combobox | `Hiển thị tất cả`, `Chỉ hiển thị video chưa tải` | `<<ComboboxSelected>> -> self.apply_filter()` |
| Search entry | `Tìm kiếm` field | `<Return> -> self._find_next_match()`, `<Shift-Return> -> self._find_previous_match()`, `search_var.trace_add("write", self._on_search_text_changed)` |
| Lower duration filter | `Ẩn video dưới:` checkbox, numeric entry, `phút` label | checkbox `command=self._on_duration_filter_changed`; entry `validatecommand=self._validate_duration_filter_text`, `StringVar` write trace for immediate local filtering, `<FocusOut> -> self._restore_empty_duration_filter_entry("below")` |
| Upper duration filter | `Ẩn video trên:` checkbox, numeric entry, `phút` label | checkbox `command=self._on_duration_filter_changed`; entry `validatecommand=self._validate_duration_filter_text`, `StringVar` write trace for immediate local filtering, `<FocusOut> -> self._restore_empty_duration_filter_entry("above")` |
| Treeview checkbox column | `[ ]` heading and row cells | `<Button-1> -> self._on_tree_click`; header toggles visible rows, row cell toggles one row |
| Treeview title double-click | `Video title` column | `<Double-1> -> self._on_tree_double_click`; column `#2` opens `_show_title_copy_popup()` |
| Treeview status double-click | `Status` column | `<Double-1> -> self._on_tree_double_click`; column `#5` opens `_open_status_editor()` |
| Treeview right-click | Row context menu | `<Button-3> -> self._on_tree_right_click` |
| Treeview space key | Focused row selection | `<space> -> self._on_tree_space` |
| Load more | `Xem thêm video` | `command=self.start_load_more` |
| Select by date | `Chọn video theo ngày` | `command=self.open_select_by_date_dialog` |
| Save folder | `Chọn thư mục` | `command=self.choose_save_folder` |
| Cookies checkbox | `Sử dụng Cookies` | `command=self._update_cookies_state` |
| Manual cookie browse | `Chọn cookies*.txt` | `command=self.choose_cookies_file` |
| Cookie source combobox | `File cookies.txt`, `Local Cookie Bridge` | `<<ComboboxSelected>> -> self._on_cookie_source_changed()` |
| Bridge check button | `Check` | `command=self.check_bridge_cookie_file` |
| Bridge cookie browse | `Chọn youtube_cookies.txt` | `command=self.choose_bridge_cookie_file` |
| Download mode combobox | `Video + Thumb`, `Audio MP3 + Thumb`, `Video + Audio MP3 + Thumb` | `<<ComboboxSelected>> -> self._on_download_mode_changed()` |
| Download selected | Dynamic `Tải (N) video đã chọn` | `command=self.start_download`; text set by `_update_download_button_text()` |
| Stop | `Dừng tải` | `command=self.stop_download` |

### Download Engine Selector

- The download frame includes a session-only `Download engine` readonly combobox with `Stable - yt-dlp internal` as the default and `Fast - aria2c experimental` as the optional fast mode.
- `Stable - yt-dlp internal` maps to `DownloadOptions.download_engine == "stable"` and preserves the existing yt-dlp internal media downloader behavior.
- `Fast - aria2c experimental` maps to `DownloadOptions.download_engine == "aria2_fast"` and resolves the optional runtime through `data/bin/aria2c.exe` via `runtime_file("aria2c.exe")`.
- The selected engine is fixed for the entire batch. Both engines use the same sequential per-video loop and the same numbered output rules.
- Missing, invalid, or unstartable aria2 prevents a selected Fast batch from starting. Stable mode, application startup, and package preflight do not require aria2.
- Fast failures do not automatically change the selector. The user can manually choose Stable for a later batch and retry.
- Fast engine parity contract: Stable and Fast use the same Premiere-safe format selector, codec requirements, maximum resolution, yt-dlp extraction behavior, isolated cookie-copy mechanism, HTTP 403 fallback, authenticated info-json fallback, retry handling, one-video lookahead, merge/remux, Premiere-safe validation, atomic promotion, SQLite state rules, and sequential item order.
- The only intended engine difference is media transfer: Stable uses yt-dlp's internal media downloader, while Fast supplies aria2c through yt-dlp `--downloader` and `--downloader-args` media-transfer options.
- Video commands for both engines use `PREMIERE_SAFE_VIDEO_FORMAT`, `-N 1`, `--merge-output-format mp4`, and the same no-info/description/thumbnail write controls. Fast adds only aria2c `-x 16 -s 16 -j 16 -k 1M`.
- Fast metadata extraction, thumbnails, lookahead metadata, API calls, Cookie Bridge, SQLite, probing, validation, and MP3 extraction do not inherit aria2 media downloader options.
- Cookies are inserted only through the isolated per-attempt `cookies.txt` copy prepared by `_prepared_cookie_attempt(...)`. Fast does not pass the selected canonical cookie file directly to yt-dlp.
- Saved-media transfer from authenticated info-json retains Fast aria2 media-transfer options while removing cookies and the YouTube watch URL.
- Fast does not perform a full video transcode. If no MP4 H.264/AAC format at 1080p or below exists, both engines fail strictly instead of downloading VP9/AV1 or transcoding unrestricted streams.

### aria2 HTTP-response exit handling

Fast media transfer may surface `ERROR: aria2c exited with code 22`. aria2 code 22 means the HTTP response header was bad or unexpected; it does not prove an HTTP 403 status.

For a Fast video media command, this failure is routed into the existing HTTP media-access recovery workflow:

1. The initial media attempt uses an isolated cookie copy.
2. Code 22 triggers authenticated info-json extraction.
3. Metadata extraction does not use aria2.
4. Saved media URLs are downloaded cookieless through aria2.
5. Repeated code 22 during saved-media transfer follows the existing metadata-age retry targets and one-video lookahead.
6. Media transfer never automatically switches to Stable.

The internal `HTTP_403` failure kind is reused as a compatibility class for retry routing, while technical logs retain the distinct `aria2_http_response_exit_22` detail.

### File Start Number

- The download frame includes a required `File start number` entry. It is blank by default, accepts only a positive integer at batch validation, and is locked while a batch is running.
- The value is session-only and is not written to app settings, SQLite, channel records, the registry, or environment variables.
- Output stems use minimum three-digit formatting: `001 Title`, `009 Title`, `051 Title`, `999 Title`, and `1000 Title`. Video, audio, and thumbnail outputs for the same item share the same numbered stem.
- Numbered output stems are rebuilt from the video's canonical title for every batch. A numbered stem from a previous run must never be used as the next batch's source title, so prefixes cannot accumulate.
- Number assignment is fixed by original selected-list order. Skipped, failed, unavailable, cancelled, and partially complete items still consume their assigned number; later items are not renumbered based on success count.

Manual status context menu commands:

- `Đánh dấu là Đã tải` -> `_apply_manual_status_to_selected("Đã tải")`
- `Đánh dấu là Chưa tải` -> `_apply_manual_status_to_selected("Chưa tải")`
- `Đánh dấu thiếu thumbnail` -> `_apply_manual_status_to_selected("Thiếu thumbnail")`
- `Đánh dấu thiếu video` -> `_apply_manual_status_to_selected("Thiếu video")`
- `Đánh dấu thiếu audio` -> `_apply_manual_status_to_selected(STATUS_MISSING_AUDIO)`
- `Đánh dấu thiếu video/audio` -> `_apply_manual_status_to_selected(STATUS_MISSING_VIDEO_AUDIO)`
- `Đánh dấu thiếu video/thumbnail` -> `_apply_manual_status_to_selected(STATUS_MISSING_VIDEO_THUMB)`
- `Đánh dấu thiếu audio/thumbnail` -> `_apply_manual_status_to_selected(STATUS_MISSING_AUDIO_THUMB)`
- `Xoá trạng thái thủ công` -> `_clear_manual_status_for_selected`

## C. Enable / Disable Rules

### Idle

Idle is the default state when `fetching == False`, `loading_more == False`, and `downloading == False`.

- `fetch_button` is normally enabled.
- `more_button` is enabled only when `channel_info` exists, `next_page_token` is non-empty, and no fetch/load/download is active.
- `stop_button` is disabled because `_update_stop_button_state()` requires `downloading and not download_stop_requested`.
- `download_button` is enabled even with zero selected rows; `start_download()` performs the real validation and logs an error when no selected videos exist.
- `mode_box` and `filter_box` are `readonly`; duration threshold entries are editable only while their checkbox is enabled.
- Cookie controls are governed by `_update_cookies_state()`.

### Fetching

`start_fetch()` exits early if `fetching` or `downloading` is true. When it starts:

- `fetching = True`
- `loading_more = False`
- `next_page_token = ""`
- `fetch_button` is disabled through `_refresh_interaction_control_states()`
- `more_button` is disabled through `_update_more_button_state()`
- `selected_orders` is cleared
- `videos` is cleared and `apply_filter()` refreshes the table
- Other semantic controls are locked through `_refresh_interaction_control_states()`.

On `fetch_done` or `fetch_error`, `_handle_event()` sets `fetching = False`, re-enables `fetch_button`, refreshes filter/table state, and updates `more_button`.

### Loading More

`start_load_more()` exits early if `loading_more`, `fetching`, or `downloading` is true. It also exits if there is no `channel_info` or `next_page_token`.

When it starts:

- `loading_more = True`
- `_update_more_button_state()` disables `more_button`
- Other semantic controls are locked through `_refresh_interaction_control_states()`.

On `load_more_done` or `load_more_error`, `_handle_event()` sets `loading_more = False` and updates `more_button`.

### Downloading

`start_download()` exits early if `downloading` is true. After validating selected videos, speed limit, `DownloadOptions`, and environment, it sets:

- `downloading = True`
- `download_stop_requested = False`
- `exit_after_download_stop = False`
- `close_requested = False`
- `cancel_download = False`
- `download_controller = DownloadController()`

Then `_set_download_controls_locked(True)` applies:

- `api_key_entry`, `channel_entry`, `fetch_button`, `select_by_date_button`, `choose_folder_button`, `cookies_check`, `speed_limit_entry`, and `download_button` disabled
- `mode_box` and `filter_box` disabled
- `hide_below_check`, `hide_below_entry`, `hide_above_check`, and `hide_above_entry` disabled
- Cookie path/source controls disabled through `_update_cookies_state()`
- `more_button` disabled through `_update_more_button_state()`
- `stop_button` enabled while `downloading` is true and `download_stop_requested` is false
- Table selection, right-click status menu, space selection, and inline status editing are blocked by their handlers while `downloading` is true

On `download_done` or `download_error`, `_finish_download_ui()` clears active download state and calls `_set_download_controls_locked(False)` unless the close-after-stop flow is exiting the app.

### Stop / Cancel Requested

`stop_download()` calls `_request_download_stop(exit_after=False, log_message=...)`.

`_request_download_stop()`:

- sets `cancel_download = True`
- sets `download_stop_requested = True`
- optionally sets `exit_after_download_stop` and `close_requested`
- logs the stop request once
- enqueues `ProgressEvent(kind="stop_requested")`
- disables `stop_button` through `_update_stop_button_state()`
- calls `download_controller.request_cancel()` when a controller exists

`DownloadController.request_cancel()` sets a threading event and terminates the current subprocess if one is registered.

### Cookies Disabled

When `cookies_enabled_var` is false, `_update_cookies_state()` sets:

- `cookie_source_box`: `disabled`
- `cookies_entry`: `disabled`
- `cookies_button`: `disabled`
- `bridge_cookie_entry`: `disabled`
- `bridge_cookie_button`: `disabled`
- `bridge_check_button`: `disabled`

### Cookies Enabled + File cookies.txt

When cookies are enabled and `_current_cookie_source()` is `COOKIE_SOURCE_FILE`:

- `cookie_source_box`: `readonly`
- `cookies_entry`: `readonly`
- `cookies_button`: `normal`
- `bridge_cookie_entry`: `disabled`
- `bridge_cookie_button`: `disabled`
- `bridge_check_button`: `disabled`

### Cookies Enabled + Local Cookie Bridge

When cookies are enabled and `_current_cookie_source()` is `COOKIE_SOURCE_BRIDGE`:

- `cookie_source_box`: `readonly`
- `cookies_entry`: `disabled`
- `cookies_button`: `disabled`
- `bridge_cookie_entry`: `readonly`
- `bridge_cookie_button`: `normal`
- `bridge_check_button`: `normal`

Downloading overrides all cookie-source states and disables every cookie source/path/status action.

## D. Cookie Workflow

### File cookies.txt

UI path:

1. User enables `Sử dụng Cookies`.
2. User selects cookie source `File cookies.txt`.
3. `_update_cookies_state()` enables `cookies_entry` as readonly and `Chọn cookies*.txt`.
4. `choose_cookies_file()` opens `askopenfilename()` with title `Choose cookies*.txt`.
5. If a path is chosen, `cookies_path_var` is set and `save_cookies_path(path)` is called immediately. If saving fails, the chosen path remains active for the current session and a safe warning is logged without the full path.

Download path:

- `start_download()` builds `DownloadOptions(cookies_enabled=..., cookies_path=self.cookies_path_var.get().strip(), cookie_source=self._current_cookie_source(), bridge_cookie_path=...)`.
- It calls `save_cookie_preferences(options.cookie_source, options.cookies_path, options.bridge_cookie_path)` once, then calls `validate_download_environment(options)`. Preference-save failure logs a safe warning and does not block the current download.
- `validate_download_environment()` calls `effective_cookies_path(options)`.
- `effective_cookies_path()` returns `""` if cookies are disabled.
- If cookies are enabled and source is File cookies, it validates `options.cookies_path`; missing/unreadable paths raise `DownloadError("Cookies file missing")`.
- If valid, an isolated temporary copy is added to yt-dlp as `--cookies <temp path>` for each attempt; the canonical path is not passed in the base yt-dlp command.

### Local Cookie Bridge

UI path:

1. Startup loads the bridge path through `load_bridge_cookie_path()`. Fresh installs default to an empty Bridge path. The old `D:\s9h-youtube-cookie-bridge\data\runtime\youtube_cookies.txt` location is compatibility-only and is returned only when `bridge_cookie_path` is absent from settings and that real legacy file currently exists.
2. `bridge_cookie_path_var` has a write trace that calls `_update_bridge_cookie_status()`.
3. User enables `Sử dụng Cookies`.
4. User selects `Local Cookie Bridge`.
5. `_on_cookie_source_changed()` persists the selected source, updates cookie UI state, and refreshes bridge status.
6. `choose_bridge_cookie_file()` opens `askopenfilename()` with title `Choose youtube_cookies.txt`.
7. If a path is chosen, it sets `bridge_cookie_path_var`, immediately calls `save_bridge_cookie_path(path)`, and refreshes bridge status. If saving fails, the chosen path remains active for the current session and a safe warning is logged without the full path.
8. `Check` only calls `_update_bridge_cookie_status()`.

Bridge status:

- Empty path: unselected.
- Missing/unreadable/not-file/malformed path: missing.
- Valid readable file: `Found | {stat.st_size} bytes | modified {YYYY-MM-DD HH:MM:SS}`

Download path:

- `start_download()` passes `bridge_cookie_path_var.get().strip()` into `DownloadOptions.bridge_cookie_path`.
- `effective_cookies_path()` chooses `options.bridge_cookie_path` when source is `COOKIE_SOURCE_BRIDGE`.
- Missing/unreadable bridge file raises the bridge-specific message: `Local Cookie Bridge cookie file not found. Open the bridge extension and click Export YouTube Cookies, then try again.`
- If valid, the same isolated temporary-copy behavior is used for yt-dlp attempts.

### Persisted Versus Not Persisted

Persisted in `core/app_settings.py`:

- `last_api_key_protected`, via `save_last_api_key()` after an accepted successful Fetch with a non-empty manual API key
- `cookie_source`, via `save_cookie_source()`
- `cookies_path`, via `save_cookies_path()` after manual File cookies browse and via `save_cookie_preferences()` before download
- `bridge_cookie_path`, via `save_bridge_cookie_path()` after Bridge browse and via `save_cookie_preferences()` before download

Cookie path settings store path strings only. They never store cookie contents, Netscape cookie lines, browser profile data, session tokens, cookie hashes, copied cookie files, or file snapshots. Path normalization strips only outer whitespace, rejects empty, oversized, non-string, and NUL-containing values, and does not expand environment variables, resolve symlinks, convert relative paths to absolute paths, or require the file to exist. UNC paths, Unicode paths, Windows extended paths, spaces inside paths, and relative paths are preserved.

Manual File cookies path and Bridge path are retained even while the other cookie source is selected. Empty manual File cookies saves remove the optional `cookies_path` field because no legacy fallback applies to it. Empty Bridge saves store `bridge_cookie_path` as the literal empty string, which is the tombstone for an explicitly cleared Bridge path. Missing stored paths remain stored and are shown as missing; they are not automatically cleared because a drive may be temporarily unavailable. Malformed paths fail safely in settings loading, inline UI status, and downloader validation.

Bridge compatibility depends on field presence, not only the normalized value. If `bridge_cookie_path` is absent, the settings may predate Phase 3E, so `load_bridge_cookie_path()` may return the old legacy path only when that path normalizes to a non-empty value and exists as a regular file. Loading never writes settings and never auto-persists the legacy fallback. If `bridge_cookie_path` is present, it represents explicit post-Phase-3E state: a valid non-empty path is returned without checking whether the file currently exists, and empty, invalid, non-string, NUL-containing, or oversized values return empty without inspecting the legacy path. Users can manually select the old legacy file again if they want it.

`last_api_key_protected` is a Windows current-user DPAPI payload with provider `windows_dpapi_current_user`, version `1`, and base64 ciphertext. The UI has no remember checkbox. After an accepted successful Fetch, a non-empty API key captured from `api_key_var` is protected and saved automatically on the main thread. Successful persistence is silent; only actionable storage, decryption, payload, or settings-write failures produce warnings. A blank manual key does not clear an existing protected key. Workers and request tokens never persist settings and never contain the API key.

Plaintext `last_api_key` is legacy-only and is removed by every successful app-settings write. The obsolete `remember_api_key` field is ignored regardless of whether it contains true, false, null, or another value, and is removed on the next successful settings cleanup/write. It never blocks restoration of a valid protected payload. Unrelated settings writes preserve `last_api_key_protected` while removing legacy plaintext and the obsolete preference field.

Valid legacy plaintext is migrated to a protected payload whenever Windows secure storage is available, even when an old installation contains `remember_api_key=false`. If both legacy plaintext and a protected payload are present, the protected payload is authoritative and plaintext is removed. Invalid or oversized legacy plaintext is removed; if a protected payload also exists, it is loaded in the same startup. Cleanup-write failure is fail-closed for plaintext cleanup.

Protected payload absence reports `not_remembered` without creating settings. A present but invalid payload reports `unsupported_payload`; a decryption failure reports `decrypt_failed`; unavailable Windows protection reports `secure_storage_unavailable`. Ciphertext is preserved for recovery. Normal states, successful saves, migration, and cleanup do not add informational process logs. Raw API keys, plaintext legacy values, ciphertext, settings JSON, and raw exception text must never be logged.

`data/api key.txt` is separate from UI-key persistence. It remains an explicit user-managed plaintext fallback read by `core.youtube_api.read_api_keys(...)`; the settings persistence code does not create, modify, delete, or copy UI keys into that file.

Not persisted by current production code:

- `cookies_enabled_var`
- `save_folder_var`
- `channel_var`
- `speed_limit_var`
- `download_mode_var`
- `hide_below_enabled_var`
- `hide_below_minutes_var`
- `hide_above_enabled_var`
- `hide_above_minutes_var`
- `filter_var`
- `search_var`
- table selection

## E. Table Behavior

### Columns

Treeview columns are:

- `selected`, heading `[ ]`
- `title`, heading `Video title`
- `duration`, heading `Duration`
- `published`, heading `Upload date`
- `status`, heading `Status`

Rows use `iid=str(video.display_order)` and values:

`([x]/[ ], video.title, video.duration, video.published_at, video.status)`

### Selection And Checkbox Column

- `selected_orders` stores selected `display_order` integers.
- `visible_orders` stores display orders currently visible after filtering.
- Clicking the checkbox column heading toggles all visible rows.
- Clicking a row checkbox toggles that row.
- Pressing space toggles the focused row.
- Selection is blocked while downloading.
- `_prune_selected_orders()` removes selections for videos no longer loaded.
- `_update_header_checkbox()` sets heading to:
  - `[ ]` for no visible rows or no selected visible rows
  - `[x]` when all visible rows are selected
  - `[-]` when some visible rows are selected
- `_update_download_button_text()` counts only `selected_orders` intersecting `visible_orders`.
- Downloads use `_selected_visible_videos()`, not every selected loaded video hidden by current filters.

### Right-Click Manual Status Menu

- Right-click is blocked while downloading.
- If the clicked row is not already selected, right-click clears current selection and selects only that row.
- The status menu acts on `_selected_visible_videos()`.
- Manual status changes call `update_manual_status(...)`, update the in-memory `video.status`, log a message, then `apply_filter()`.
- Clearing manual status calls `clear_manual_status(...)`, then `apply_statuses(...)`, then `apply_filter()`.

### Double Click Behavior

- Double-click title column `#2`: opens `_show_title_copy_popup()` using `show_copy_text_dialog(...)`.
- Double-click status column `#5`: opens an inline readonly `ttk.Combobox` over the Status cell using `SUPPORTED_STATUS_VALUES`.
- Committing the inline status editor calls `_save_manual_status(video, status)`.
- Inline status editing is blocked while downloading.

### Search Behavior

- Search does not filter rows out of the table.
- `_on_search_text_changed()` refreshes matches when `search_var` is non-empty and clears match state when blank.
- `_refresh_search_matches()` searches only `video.title.casefold()` among `visible_orders`.
- If no matches: `search_status_var = "Không tìm thấy"`.
- Before navigation: `search_status_var = "0/N"`.
- Enter moves to next match; Shift+Enter moves to previous match.
- `_focus_search_order()` focuses, selects, and scrolls the matched row into view.

### Filter Behavior

- `FILTER_ALL = "Hiển thị tất cả"`.
- `FILTER_NOT_DOWNLOADED = "Chỉ hiển thị video chưa tải"`.
- `apply_filter()` always applies duration visibility first.
- The not-downloaded filter uses `should_show_not_downloaded(video)`, not a simple string compare against one status.

### Duration Filter Behavior

- The old `Hiển thị video ngắn` checkbox and fixed threshold dropdown are replaced by two independent session-only filters: `Ẩn video dưới:` and `Ẩn video trên:`.
- Every startup defaults to both filters enabled, lower `3` minutes, and upper `60` minutes. These values are not written to `app_settings.json`, SQLite, the registry, environment variables, or any other config.
- Each threshold uses a compact `ttk.Entry`, not a Combobox. Validation accepts only zero through four ASCII digits `0` through `9`; invalid mixed paste and five-or-more-digit paste are rejected as a whole. Temporary empty text is allowed for editing.
- Empty lower text resets to `3`; empty upper text resets to `60` on focus loss and before Fetch or Load More validation.
- Fetch repeats strict raw-text validation on the main thread before token creation: non-empty text must be one through four ASCII decimal characters before `int()` is called. Non-empty invalid text never silently falls back to `3` or `60`.
- Active thresholds must be integers from `1` through `9999`. Active `0`, values above `9999`, malformed text, non-ASCII digits, and oversized raw strings block Fetch or Load More with a safe validation dialog and do not create a request token or worker.
- When both filters are enabled, the upper threshold must be greater than the lower threshold. Disabled thresholds are preserved in memory but are not validated or applied.
- Unchecking a filter disables only its matching entry and preserves the current session value; rechecking restores the entry to editable state with that value.
- Known durations are hidden only with strict comparisons: `duration_seconds < lower_minutes * 60` and `duration_seconds > upper_minutes * 60`. Exact lower and upper boundary values remain visible.
- Unknown duration, missing duration metadata, malformed ISO-8601 duration, `PT0S`, live streams, upcoming streams, and IDs omitted from `videos.list` duration metadata remain visible and are not treated as zero.
- UI filtering, hidden-count logging, and the YouTube API bounded scanner share `is_video_visible_by_duration(...)` semantics.
- Fetch captures `hide_below_enabled`, `hide_below_minutes`, `hide_above_enabled`, and `hide_above_minutes` as primitive immutable request-context fields. Workers never read Tk variables or widgets.
- An accepted Fetch installs one immutable request context. While the app is idle, each valid checkbox or numeric-entry change immediately updates only the duration fields of the active loaded context and reapplies the current table without starting Fetch or consuming API quota.
- The current table, local search, date selection, not-downloaded filter, and hidden-duration calculations use the latest valid values shown in the duration controls. Existing loaded videos are re-filtered immediately; videos not yet loaded remain available through Load More.
- A temporarily empty, invalid, oversized, or reversed range does not replace the last valid active context. A newly accepted Fetch installs its captured context; worker-start failure, request failure, stale terminal events, and duplicate terminal events do not corrupt the active loaded list or context.
- Load More validates the current controls and snapshots the latest valid active duration context when clicked. The controls are locked during Load More, so its worker remains immutable while subsequent pages use the same filter currently shown in the table.
- The scanner remains bounded by the visible target, checked-ID limit, and playlist end. Hidden-below and hidden-above items do not count toward the visible target; unknown/live/upcoming items do count as visible.

### Load More Behavior

- `start_load_more()` requires `channel_info`, `next_page_token`, and no active fetch/load/download.
- It calls `fetch_more_videos(...)` with uploads playlist ID, current page token, next display order start, manual API key, progress logger, and the current valid duration-filter snapshot captured when Load More starts.
- On success, `_handle_event("load_more_done")` extends `videos`, updates `next_page_token`, reapplies local file statuses when `channel_info` exists, reapplies filters, and updates `more_button`.
- If there is no next token, the UI logs `[INFO] No more videos.` and disables `more_button`.

## F. Progress And Log Behavior

### Progress Labels

- `progress_current_var` starts as `Đang tải: Sẵn sàng`.
- `progress_detail_var` starts as `Đang xử lý: -`.
- `start_download()` clears stale progress, resets sticky progress display state, and restores both initial labels before starting the worker.
- The download worker passes `progress_callback=self._enqueue_progress_event` into `download_items(...)`.
- `_enqueue_progress_event()` writes to `progress_queue` with `put_latest_progress_event(...)`.
- `_poll_progress_queue()` runs every 300 ms, drains the queue to the latest event, merges sticky percent/speed/fragment display fields, formats lines with `format_progress_event_lines(...)`, and writes both Tkinter variables.
- Generic FFmpeg progress events use `ProgressEvent(kind="ffmpeg_progress", phase="FFmpeg")`; their detail line uses the `speed` label instead of the yt-dlp speed label. The production Fast video path does not use full-transcode progress.
- Sticky progress state resets for `batch_complete`, `stop_requested`, and `error` events.

### Log Text

- `log_text` is a disabled `tk.Text` widget with vertical scrollbar.
- `_append_log()` sanitizes text with `sanitize_log_text()`, picks a tag from `_log_tag_for()`, temporarily enables the widget, inserts one line, scrolls to end, then disables it again.
- Log tag colors:
  - `success`: messages starting `[SUCCESS]`, foreground `green`
  - `error`: messages starting `[ERROR]`, foreground `red`
  - `skip`: messages starting `[SKIP]`, foreground `#b58900`
  - `warning`: messages starting `[WARNING]`, foreground `#b58900`

### Event Queue

Worker threads never directly update Tk widgets. They enqueue events:

- `_thread_log(message)` -> `("log", sanitize_log_text(message))`
- Fetch worker -> `("fetch_done", request_token, channel, videos, next_page_token)` or `("fetch_error", request_token, message)`
- Load-more worker -> `("load_more_done", request_token, videos, next_page_token)` or `("load_more_error", request_token, message)`
- Download worker status callback -> `("status_update", video.display_order, video.status)`
- Download worker completion -> `("download_done",)` or `("download_error", message)`

`_process_events()` runs every 100 ms and dispatches through `_handle_event()`.

Fetch and Load More request tokens carry immutable non-secret request context. The Fetch worker receives the manual key only as a direct captured worker argument; the key is not placed in the token or event payload. Workers do not persist settings. Only the accepted current `fetch_done` terminal event may save the pending non-empty manual key, so stale and duplicate terminal events cannot persist it.

## G. Safe UI Refactor Plan

The safe path is to reorganize the existing widgets into grouped `ttk.LabelFrame` sections while preserving the production object names, Tkinter variables, handlers, bindings, and state-update methods.

Recommended groups:

1. `Source`
   - Move existing `api_key_entry`, `channel_entry`, and `fetch_button`.
   - Reuse `api_key_var`, `channel_var`, and `start_fetch` exactly.

2. `Filters`
   - Move existing `filter_box`, search entry/status label, `hide_below_check`, `hide_below_entry`, `hide_above_check`, and `hide_above_entry`.
   - Reuse `filter_var`, `search_var`, `search_status_var`, `hide_below_enabled_var`, `hide_below_minutes_var`, `hide_above_enabled_var`, and `hide_above_minutes_var`.
   - Preserve `apply_filter`, `_on_search_text_changed`, `_find_next_match`, `_find_previous_match`, and `_on_duration_filter_changed`.

3. `Video list`
   - Move the existing `tree`, scrollbar, `more_button`, and `select_by_date_button`.
   - Preserve `TREE_COLUMN_IDS`, `TREE_COLUMN_DEFAULTS`, row `iid=str(display_order)`, all tree bindings, column-fit behavior, context menu, and inline status editor.
   - Reuse `start_load_more` and `open_select_by_date_dialog` exactly.

4. `Output & Cookies`
   - Move existing save-folder row and all cookie controls.
   - Reuse `save_folder_var`, `cookies_enabled_var`, `cookies_path_var`, `cookie_source_var`, `bridge_cookie_path_var`, and `cookie_status_var`.
   - Reuse `choose_save_folder`, `_update_cookies_state`, `choose_cookies_file`, `_on_cookie_source_changed`, `choose_bridge_cookie_file`, `check_bridge_cookie_file`, and `_update_bridge_cookie_status`.
   - If the visual design hides inactive cookie rows, it must still call `_update_cookies_state()` and must not change `DownloadOptions`, persistence, or effective cookie-path behavior.

5. `Download`
   - Move `mode_box`, `speed_limit_entry`, `download_button`, and `stop_button`.
   - Reuse `download_mode_var`, `speed_limit_var`, `_on_download_mode_changed`, `start_download`, `stop_download`, `_update_download_button_text`, `_set_download_controls_locked`, and `_update_stop_button_state`.

6. `Progress / Logs`
   - Move existing progress labels and `log_text`.
   - Reuse `progress_current_var`, `progress_detail_var`, `_poll_progress_queue`, `_append_log`, `_log_tag_for`, `_process_events`, and `_handle_event`.

Non-negotiable reuse list for any production UI refactor:

- Variables: `api_key_var`, `channel_var`, `save_folder_var`, `cookies_enabled_var`, `cookies_path_var`, `cookie_source_var`, `bridge_cookie_path_var`, `cookie_status_var`, `speed_limit_var`, `download_mode_var`, `download_engine_var`, `hide_below_enabled_var`, `hide_below_minutes_var`, `hide_above_enabled_var`, `hide_above_minutes_var`, `filter_var`, `search_var`, `search_status_var`, `progress_current_var`, `progress_detail_var`
- Runtime collections/flags: `videos`, `channel_info`, `selected_orders`, `visible_orders`, `next_page_token`, `fetching`, `loading_more`, `downloading`, `download_controller`, `download_stop_requested`, `exit_after_download_stop`, `close_requested`, `cancel_download`
- Handlers: `start_fetch`, `start_load_more`, `open_select_by_date_dialog`, `choose_save_folder`, `_update_cookies_state`, `choose_cookies_file`, `_on_cookie_source_changed`, `choose_bridge_cookie_file`, `check_bridge_cookie_file`, `_on_download_mode_changed`, `start_download`, `stop_download`
- Table handlers: `_on_tree_click`, `_on_tree_double_click`, `_on_tree_right_click`, `_on_tree_space`, `_open_status_editor`, `_save_manual_status`, `_apply_manual_status_to_selected`, `_clear_manual_status_for_selected`
- State-update helpers: `apply_filter`, `_update_more_button_state`, `_update_stop_button_state`, `_set_download_controls_locked`, `_finish_download_ui`, `_update_download_button_text`, `_update_bridge_cookie_status`
- Download contract: `DownloadOptions` fields and values must remain unchanged; `effective_cookies_path()` selection between `cookies_path` and `bridge_cookie_path` must remain unchanged; downloader core behavior must remain unchanged.

Implementation recommendation:

- First refactor only `_build_ui()` into smaller private layout-building methods that create the same widgets with the same `self.*` names.
- Do not rename handlers.
- Do not change `DownloadOptions`.
- Do not change downloader, SQLite/state, app settings, cookie bridge, or yt-dlp command behavior.
- Do not change cookie path persistence, atomic preference saving, malformed-path safety, or isolated-cookie attempt behavior as part of a visual regrouping.
- Validate by exercising fetch, load more, filter/search, both duration-filter toggles and entries, date selection, manual status edits, both cookie sources, download validation, stop/cancel, and close-while-downloading flows.

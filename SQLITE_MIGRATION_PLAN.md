# Kế hoạch migration `download_state.json` -> SQLite (không phá UI hiện tại)

## 1) Phân tích cấu trúc source hiện tại

- `app.py`: entrypoint, chỉ gọi UI chính.
- `ui/`: toàn bộ giao diện Tkinter và tương tác người dùng.
  - `ui/main_window.py`: orchestration chính (fetch video, filter, start download, update status UI, manual override status).
  - `ui/dialogs.py`: helper dialog.
- `core/`: nghiệp vụ và persistence.
  - `core/youtube_api.py`: gọi YouTube Data API, parse dữ liệu video/channel.
  - `core/downloader.py`: workflow tải bằng `yt-dlp`/`ffmpeg`, cập nhật trạng thái từng part.
  - `core/file_status.py`: áp trạng thái vào danh sách video cho UI.
  - `core/state_store.py`: nguồn logic persistence trạng thái tải (đọc/ghi `download_state.json`).
  - `core/runtime_paths.py`: xác định `app_root`, `data_dir`, runtime files.
  - `core/app_settings.py`: đọc/ghi `app_settings.json` (lưu API key cuối cùng).
  - các file core còn lại: constants, enum-like mode, sanitize, messages.
- `data/`: dữ liệu runtime (ví dụ `app_settings.json`, `download_state.json`) theo `runtime_paths.py`.

## 2) Nơi đang đọc/ghi `download_state.json`

### Điểm xác định đường dẫn file state
- `core/runtime_paths.py`
  - `state_file()` trả về `data_dir()/"download_state.json"`.

### Điểm đọc state
- `core/state_store.py`
  - `load_state()` mở JSON từ `state_file()`.
  - `get_channel_video_entries()`, `get_video_entry()` đọc gián tiếp qua `load_state()`.

### Điểm ghi state
- `core/state_store.py`
  - `_save_state(state)` ghi atomic qua file temp + `replace`.
  - Các API ghi dữ liệu gọi `_save_state()`:
    - `update_manual_status(...)`
    - `clear_manual_status(...)`
    - `update_video_part_state(...)`
    - `reconcile_downloaded_item_state(...)`
    - `update_video_state(...)`

### Call-site chính sử dụng state
- `core/downloader.py`
  - đọc: `get_video_entry`, `is_mode_complete`, `missing_parts_for_mode`.
  - ghi: `update_video_part_state`, `reconcile_downloaded_item_state`.
- `core/file_status.py`
  - đọc để map status vào object video (qua `get_video_entry`/`get_effective_status`).
- `ui/main_window.py`
  - ghi manual override: `update_manual_status`, `clear_manual_status`.
  - đọc status: `get_video_entry` (khi clear override và refresh state).

## 3) Kế hoạch chuyển sang SQLite (giữ nguyên UI contract)

## Nguyên tắc
- Không đổi contract UI/Downloader hiện tại: giữ nguyên function signatures trong `core/state_store.py` mà UI đang gọi.
- Không hardcode API key/cookies/path máy cá nhân/binary runtime.
- Dữ liệu path runtime vẫn đi qua `core/runtime_paths.py`.
- Migration theo từng bước có fallback an toàn, có thể rollback.

## Giai đoạn A — Chuẩn bị abstraction persistence
1. Tạo lớp repository backend (ví dụ `StateRepository`) với các hành vi đang dùng:
   - `load_video_entry(channel_id, video_id)`
   - `upsert_manual_status(...)`
   - `clear_manual_status(...)`
   - `upsert_part_status(...)`
   - `reconcile_from_files(...)`
   - `list_channel_videos(channel_id)` (nếu cần cho mở rộng)
2. `core/state_store.py` trở thành façade giữ nguyên API public cũ; bên trong gọi repository.
3. Mặc định chọn backend SQLite; tạm thời có thể giữ JSON backend để migration an toàn.

## Giai đoạn B — Thiết kế schema SQLite (tương thích dữ liệu hiện tại)
1. Tạo DB file tại `data/download_state.db` (đường dẫn qua `runtime_paths`, KHÔNG hardcode).
2. Schema đề xuất:
   - `channels`:
     - `channel_id` (PK)
     - `channel_name`
     - `save_base_folder`
     - `updated_at`
   - `videos`:
     - `channel_id` + `video_id` (composite PK)
     - `original_title`, `sanitized_filename_base`
     - `display_order_at_download`
     - `status`, `manual_status`, `manual_override`
     - `video_status`, `thumb_status`, `audio_status`
     - `video_filename`, `thumb_filename`, `audio_filename`
     - `video_path`, `thumb_path`, `audio_path`
     - `downloaded_at`, `updated_at`
3. Index:
   - `(channel_id, updated_at)` để load nhanh theo kênh.
   - `(channel_id, manual_override)` nếu lọc override sau này.
4. Dùng `sqlite3` chuẩn Python + transaction ngắn; bật WAL để tránh lock UI khi ghi nhiều.

## Giai đoạn C — One-time migration JSON -> SQLite
1. Khi app khởi động hoặc khi first access state:
   - Nếu có `download_state.db`: dùng DB.
   - Nếu chưa có DB nhưng có `download_state.json`: import một lần.
2. Import idempotent:
   - upsert theo `(channel_id, video_id)`.
   - validate kiểu dữ liệu tương tự logic `load_state()` hiện tại.
3. Sau import thành công:
   - backup JSON sang `download_state.json.bak` (không xóa ngay).
   - ghi cờ `state_backend = sqlite` trong metadata table hoặc file settings.

## Giai đoạn D — Giữ nguyên hành vi status/UI
1. Reuse 100% hàm suy luận status hiện có (`get_effective_status`, `part_status_from_entry`, `missing_parts_for_mode`) để tránh thay đổi business rules.
2. Repository trả về `dict` shape tương đương entry cũ để `ui/main_window.py`, `core/file_status.py`, `core/downloader.py` không cần thay đổi lớn.
3. Manual override precedence giữ nguyên (`manual_override == True` ưu tiên).

## Giai đoạn E — Quan sát & dọn dẹp
1. Thêm log kỹ thuật mức INFO cho migration + backend đang dùng (không lộ secrets).
2. Sau 1-2 bản stable: cân nhắc bỏ JSON backend write-path, chỉ giữ import legacy.

## 4) Danh sách file cần sửa (chưa sửa code)

### Bắt buộc
- `core/runtime_paths.py`
  - thêm helper đường dẫn DB (ví dụ `state_db_file()`).
- `core/state_store.py`
  - refactor sang façade + gọi SQLite repository; giữ API public hiện tại.
- `core/file_status.py`
  - chỉ chỉnh nếu cần tối ưu call pattern (không đổi behavior).
- `core/downloader.py`
  - chỉ chỉnh nếu cần batching/transaction boundary (không đổi luồng UI).

### Nên thêm mới
- `core/state_repository.py` (hoặc `core/state_store_sqlite.py`)
  - chứa CRUD/transaction SQLite.
- `core/state_migration.py`
  - import JSON -> SQLite, idempotent.

### Tài liệu / mẫu dữ liệu
- `README.md`
  - cập nhật phần storage backend từ JSON sang SQLite + đường dẫn DB runtime.
- `data/`:
  - có thể thêm ví dụ schema hoặc ghi chú migration (không commit dữ liệu thật).

## 5) Ràng buộc bảo mật & cấu hình
- Không hardcode API key/cookies: tiếp tục lấy từ input/UI và file settings hiện hữu.
- Không hardcode đường dẫn máy cá nhân (`D:\...`) cho logic mới; dùng `runtime_paths`.
- Không hardcode binary runtime (`yt-dlp.exe`, `ffmpeg.exe`, `deno.exe`) trong storage layer mới.
- Không log plaintext secrets; nếu cần log lỗi, tiếp tục sanitize như hiện tại.

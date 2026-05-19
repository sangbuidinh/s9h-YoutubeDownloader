# Mã Nguồn YouTube Downloader

Đây là phiên bản mã nguồn của ứng dụng Windows desktop.

## Tải xuống

Bạn có thể tải phiên bản mới nhất [tại đây](https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest).

## Cách chạy

```powershell
cd "D:\Youtube Downloader Source"
python app.py
```

## Cấu trúc file chạy yêu cầu (Required runtime files)

Dựa theo bản package, ứng dụng yêu cầu các công cụ và thư mục bên dưới nằm cạnh file thực thi chính:

```text
Youtube Downloader/
|-- Youtube Downloaderbs.exe
|-- yt-dlp.exe
|-- ffmpeg.exe
|-- deno.exe
|-- api key.txt
\-- data/
    |-- app_settings.json
    |-- download_state.json
    \-- download_state.sqlite3
```

- `api key.txt`: File chỉ đọc (read-only). Ứng dụng không bao giờ sửa đổi file này.
- `deno.exe`: Có thể dùng để yt-dlp giải quyết các thử thách JavaScript của YouTube. Các công cụ này được để bên ngoài file .exe nhằm mục đích có thể cập nhật độc lập mà không cần build lại app.

Khi chạy trực tiếp từ mã nguồn, dữ liệu cấu hình ứng dụng được lưu trữ ở `D:\Youtube Downloader Source\data`. Các công cụ chạy ứng dụng (như yt-dlp, ffmpeg) sẽ được ưu tiên đọc từ thư mục mã nguồn nếu có, hoặc đọc fallback từ `D:\Youtube Downloader` trong quá trình phát triển (development).

## Các chế độ tải (Download modes)

Dropdown `Kiểu tải` điều khiển các file nào sẽ được tải xuống cho các video được chọn:

1. `Video + Thumb`
2. `Audio MP3 + Thumb`
3. `Video + Audio MP3 + Thumb`

Chế độ mặc định là `Video + Thumb`. Việc trích xuất MP3 yêu cầu phải có `ffmpeg.exe`.

## Cấu trúc thư mục đầu ra

Với một thư mục lưu file đã chọn, các file tải về sẽ được phân bổ theo cấu trúc sau:

```text
<Thư mục lưu>
\-- <Tên Kênh>
    |-- video/
    |   |-- Example Title.mp4
    |   |-- Example Title (2).mp4
    |   \-- Another Video Title.mp4
    |-- thumb/
    |   |-- Example Title.jpg
    |   |-- Example Title (2).jpg
    |   \-- Another Video Title.jpg
    \-- audio/
        |-- Example Title.mp3
        |-- Example Title (2).mp3
        \-- Another Video Title.mp3
```

Thư mục `audio/` chỉ được tạo ra khi bạn dùng các chế độ tải MP3. Thư mục kênh đầu ra chỉ nên chứa các thư mục con do ứng dụng này tạo ra (`video/`, `thumb/`, và `audio/`). Các file tạm (temp) sẽ được tạo ra bên ngoài thư mục đầu ra này và sẽ tự động dọn dẹp khi có thể.

Trạng thái ứng dụng được lưu bên ngoài thư mục kênh tải về. Backend runtime mặc định hiện là SQLite tại `data\download_state.sqlite3`. File `data\download_state.json` vẫn được giữ làm dữ liệu rollback/fallback và không bị xóa tự động, nhưng có thể cũ hơn SQLite nếu không export snapshot mới.

`download_state.sqlite3` là nguồn dữ liệu runtime mặc định cho trạng thái của các video. Các giá trị đường dẫn `video_path`, `thumb_path`, và `audio_path` được lưu lại chỉ mang tính chất tham khảo cho UI, vì người dùng có thể đổi tên file gốc sau khi tải xuống. Runtime SQLite files (`data/*.sqlite3` và `data/*.sqlite3-*`) được ignore bởi git.

Có thể ép backend bằng biến môi trường `YTDL_STATE_BACKEND`:

```powershell
$env:YTDL_STATE_BACKEND='json'    # rollback về JSON
$env:YTDL_STATE_BACKEND='sqlite'  # ép dùng SQLite
Remove-Item Env:YTDL_STATE_BACKEND
```

Các lệnh thủ công cho migration và kiểm tra dữ liệu:

```powershell
python scripts/migrate_download_state_to_sqlite.py
python scripts/validate_download_state_migration.py
```

Các lệnh bảo trì SQLite runtime:

```powershell
python scripts/sqlite_state_health_check.py
python scripts/backup_sqlite_state.py
python scripts/export_sqlite_state_to_json.py
```

API key cuối cùng được nhập bằng tay sẽ được lưu ở `data\app_settings.json`. Ứng dụng sẽ tự động bỏ qua nếu file cài đặt bị thiếu hoặc bị lỗi (corrupted) và sẽ khởi động với ô nhập API key trống.

Khi người dùng tự cập nhật trạng thái tải, chúng sẽ được lưu ngay lập tức vào backend trạng thái đang được chọn theo khóa định danh video, không dùng tên file hoặc đường dẫn làm khóa chính. Trạng thái sửa đổi thủ công này sẽ được ưu tiên hiển thị ở các lần mở app sau này, cho đến khi người dùng xóa trạng thái thủ công đó hoặc tiến hành tải lại thành công.

Các file tải về dùng đúng tên gốc của video trên YouTube, chỉ chuẩn hóa các ký tự không hợp lệ cho tương thích với quy tắc đặt tên file của Windows. Các file Video, hình thu nhỏ (thumbnail), và nhạc (audio) của cùng một video sẽ luôn dùng chung một tên gốc (base filename).

Quá trình tải video mặc định dùng định dạng tốt nhất từ yt-dlp. File xuất ra cuối cùng sẽ được tự động nối (merge) thành định dạng chuẩn `.mp4` khi có thể.

## Giới hạn tốc độ tải (Download limit)

Trường giới hạn tốc độ thiết kế riêng cho đơn vị MB/s:

- Bỏ trống hoặc nhập `0` có nghĩa là không giới hạn.
- Nhập `5` sẽ truyền cho yt-dlp tham số `--limit-rate 5M`.
- Nhập `1.5` sẽ truyền cho yt-dlp tham số `--limit-rate 1.5M`.
- Chữ viết, số âm, hay các lệnh kiểu command như `--anything` đều sẽ bị tự động từ chối.

## Tải thêm video

Các video ngắn (shorts) sẽ được ẩn đi theo mặc định. Ứng dụng sử dụng API trả về `videos.list(contentDetails.duration)` và ngưỡng UI ở ô `Ẩn video dưới: [3] phút` để quyết định ẩn/hiện. Sử dụng checkbox `Hiển thị video ngắn` để xem lại toàn bộ các video đã lấy về mà không cần gọi API lại.

Lần gọi API đầu tiên sẽ quét qua danh sách tải lên (uploads playlist) cho đến khi lấy được đủ 100 video thỏa mãn điều kiện hiển thị (sau khi lọc thời lượng), hoặc khi không còn video nào nữa trên kênh, hoặc khi đạt ngưỡng giới hạn an toàn là quét qua 500 video. Dùng nút `Xem thêm video` để tiếp tục tải các video cũ hơn từ kênh đó.

## Giới hạn thiết kế (Current limitations)

- SQLite là backend trạng thái mặc định; JSON vẫn được giữ để rollback/fallback.
- Không xuất dữ liệu metadata, sidecar, JSON, TXT, hay CSV.
- Không chia cấu trúc một-thư-mục-cho-mỗi-video.
- Không có hệ thống đồng bộ đám mây (cloud sync) hay đăng nhập.

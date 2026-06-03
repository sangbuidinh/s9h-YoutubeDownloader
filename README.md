# YouTube Downloaderbs

Ứng dụng desktop portable cho Windows giúp lấy danh sách video từ kênh YouTube bằng YouTube Data API và tải video, thumbnail hoặc MP3 thông qua yt-dlp và ffmpeg.

<p align="center">
  <a href="https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest">
    <img alt="Latest Release" src="https://img.shields.io/github/v/release/sangbuidinh/s9h-YoutubeDownloader?label=latest%20release">
  </a>
  <a href="https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases">
    <img alt="Total Downloads" src="https://img.shields.io/github/downloads/sangbuidinh/s9h-YoutubeDownloader/total?label=downloads">
  </a>
  <a href="https://github.com/yt-dlp/yt-dlp">
    <img alt="yt-dlp" src="https://img.shields.io/badge/runtime-yt--dlp-lightgrey">
  </a>
  <a href="https://ffmpeg.org/">
    <img alt="ffmpeg" src="https://img.shields.io/badge/media-ffmpeg-green">
  </a>
  <a href="https://www.sqlite.org/index.html">
    <img alt="SQLite" src="https://img.shields.io/badge/state-SQLite-informational">
  </a>
  <a href="https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest">
    <img alt="Windows Portable" src="https://img.shields.io/badge/Windows-portable-blue">
  </a>
</p>

> [!IMPORTANT]
> Ứng dụng cần YouTube Data API key để lấy danh sách video từ kênh. Một số video có thể cần cookies hợp lệ nếu YouTube yêu cầu đăng nhập hoặc xác minh bot.

## ✨ Tính năng chính

- Lấy danh sách video từ kênh YouTube bằng YouTube Data API.
- Chọn từng video cần tải thay vì tải toàn bộ kênh.
- Tải video MP4 thân thiện với Premiere: H.264/AAC, tối đa 1080p.
- Tải thumbnail JPG.
- Tải hoặc trích xuất audio MP3.
- Lưu lịch sử tải và trạng thái thủ công bằng SQLite.
- Hỗ trợ `cookies*.txt` cho trường hợp YouTube yêu cầu đăng nhập hoặc xác minh bot.
- Hiển thị tiến trình tải nhẹ, 2 dòng, không làm rối log.
- Dạng portable: runtime tools nằm trong `data/bin`.

## ⬇️ Tải bản mới nhất

⬇️ Tải bản đóng gói mới nhất [tại đây](https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest).

> [!NOTE]
> Giải nén toàn bộ file zip trước khi chạy. Không chỉ kéo riêng file `.exe` ra ngoài vì app cần thư mục `data/bin`.

## 🚀 Hướng dẫn nhanh

1. Tải bản release mới nhất.
2. Giải nén toàn bộ thư mục.
3. Chạy `Youtube Downloaderbs.exe`.
4. Nhập YouTube Data API key.
5. Dán Channel URL / Channel ID / Handle.
6. Bấm `Lấy danh sách Video`.
7. Chọn video cần tải.
8. Chọn thư mục lưu và kiểu tải.
9. Bấm tải.

## 📁 Cấu trúc portable

```text
Youtube Downloader/
|-- Youtube Downloaderbs.exe
`-- data/
    |-- api key.txt.example
    |-- cookies.txt.example
    |-- app_settings.example.json
    `-- bin/
        |-- yt-dlp.exe
        |-- ffmpeg.exe
        `-- deno.exe
```

Runtime tools được đặt ngoài file `.exe` để có thể cập nhật riêng. Hãy giữ nguyên cấu trúc thư mục khi sử dụng bản portable.

## ✅ Yêu cầu

| Thành phần | Bắt buộc | Ghi chú |
|---|---:|---|
| Windows | Có | Ứng dụng desktop cho Windows |
| YouTube Data API key | Có | Dùng để lấy danh sách video từ kênh |
| `yt-dlp.exe` | Có | Đặt trong `data/bin` |
| `ffmpeg.exe` | Có | Đặt trong `data/bin`, dùng để merge và trích xuất MP3 |
| `deno.exe` | Không | Hỗ trợ một số YouTube JavaScript challenge |
| `cookies*.txt` | Không | Dùng khi YouTube yêu cầu đăng nhập hoặc xác minh bot |

## 🔑 YouTube Data API key

YouTube Data API key là bắt buộc để lấy danh sách video từ kênh. Bạn có thể nhập API key trực tiếp trong app trước khi tải danh sách video.

API key nhập gần nhất được lưu cục bộ trong:

```text
data/app_settings.json
```

App cũng có thể đọc thêm API key từ `data/api key.txt`, mỗi dòng một key. File `data/api key.txt.example` trong bản đóng gói chỉ là mẫu.

> [!WARNING]
> Không commit, upload hoặc chia sẻ API key thật.

## 🍪 Cookies

Cookies là tùy chọn. Hãy dùng cookies khi YouTube yêu cầu đăng nhập, xác minh bot, truy cập video giới hạn tuổi, video riêng tư hoặc nội dung phụ thuộc phiên đăng nhập.

App hỗ trợ chọn file `cookies*.txt` hoặc `cookies.txt`. File cookies nên được xuất theo Netscape cookies format.

> [!WARNING]
> Không upload cookies thật lên GitHub và không đưa cookies thật vào release package.

## 🎞️ Kiểu tải

| Kiểu tải | Kết quả |
|---|---|
| Video + Thumb | `.mp4` + `.jpg` |
| Audio MP3 + Thumb | `.mp3` + `.jpg` |
| Video + Audio MP3 + Thumb | `.mp4` + `.mp3` + `.jpg` |

## 📦 Cấu trúc thư mục đầu ra

Khi chọn thư mục lưu, app tạo cấu trúc theo tên kênh và loại file:

```text
<Save folder>/
`-- <Channel name>/
    |-- video/
    |   `-- Example Title.mp4
    |-- thumb/
    |   `-- Example Title.jpg
    `-- audio/
        `-- Example Title.mp3
```

Thư mục `audio` chỉ được dùng khi chọn kiểu tải có MP3.

## 🗃️ Lịch sử tải / SQLite state

Trạng thái tải và trạng thái chỉnh thủ công được lưu trong SQLite:

```text
data/download_state.sqlite3
```

SQLite là nguồn dữ liệu chính cho trạng thái trong app. Trạng thái tải được lưu theo định danh kênh/video trong SQLite và không chỉ dựa vào việc quét thư mục đầu ra. Cách này giúp trạng thái ổn định hơn khi người dùng đổi tên hoặc di chuyển file đã tải.

Các file sidecar của SQLite có thể xuất hiện bên cạnh database:

```text
data/download_state.sqlite3-wal
data/download_state.sqlite3-shm
```

> [!IMPORTANT]
> Không xóa các file `.sqlite3`, `.wal` hoặc `.shm` nếu bạn muốn giữ lịch sử tải và trạng thái thủ công.

## 🧰 Khắc phục sự cố

| Vấn đề | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| API key không hợp lệ | Key sai, bị tắt hoặc chưa bật YouTube Data API | Tạo hoặc nhập YouTube Data API key hợp lệ |
| Hết quota API | Quota hằng ngày đã dùng hết | Chờ quota reset hoặc dùng API key hợp lệ khác |
| Thiếu `yt-dlp.exe` | Runtime file không nằm trong `data/bin` | Giữ `yt-dlp.exe` trong `data/bin` |
| Thiếu `ffmpeg.exe` | Runtime file không nằm trong `data/bin` | Giữ `ffmpeg.exe` trong `data/bin` |
| YouTube yêu cầu đăng nhập / xác minh bot | YouTube yêu cầu phiên đăng nhập hoặc chặn bot | Bật cookies và chọn file `cookies*.txt` hợp lệ |
| Tải chậm hoặc bị ngắt | Mạng, CDN hoặc YouTube throttling | Thử lại sau, cập nhật yt-dlp hoặc dùng cookies hợp lệ |
| Lỗi trích xuất MP3 | Thiếu ffmpeg hoặc file MP4 nguồn không hợp lệ | Kiểm tra `ffmpeg.exe` và thử tải lại |

<details>
<summary><strong>▶️ Chạy từ source</strong></summary>

```powershell
python app.py
```

Khi chạy từ source, dữ liệu app nằm trong thư mục `data` của repository. Runtime tools được đọc từ `data/bin`.

</details>

<details>
<summary><strong>🛠️ Đóng gói bằng PyInstaller</strong></summary>

Chạy từ thư mục gốc của repository:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Youtube Downloaderbs" app.py
```

Output dự kiến:

```text
dist/Youtube Downloaderbs.exe
```

Release package phải giữ user data và runtime tools ở ngoài file `.exe`.

</details>

## 🔒 Ghi chú bảo mật

Không commit hoặc upload:

- `data/download_state.sqlite3`
- `data/download_state.sqlite3-wal`
- `data/download_state.sqlite3-shm`
- `data/app_settings.json`
- cookies files
- API key files
- generated `.exe`
- release archives

Chỉ các file mẫu như `data/app_settings.example.json`, `data/api key.txt.example` và `data/cookies.txt.example` là phù hợp để đưa vào release package.

## ⚠️ Giới hạn hiện tại

- Cần YouTube Data API key để lấy danh sách video từ kênh.
- Một số lượt tải có thể cần cookies hợp lệ.
- Runtime tools cần được cập nhật thủ công trong `data/bin`.
- Hành vi của YouTube có thể thay đổi và có thể cần cập nhật yt-dlp.
- Trạng thái tải được duy trì trong SQLite, không dựa vào việc quét toàn bộ filesystem.

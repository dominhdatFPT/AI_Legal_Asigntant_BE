# Document Task 6 — worker và processing/extraction

Trạng thái hiện tại sau T7/T8: API/worker/antivirus thật đã tắt theo user. Outbox005 đã apply; dispatcher mặc định tắt vì receiver chưa implement. Docker mới rootless/read-only; xem [DOCUMENT_RELEASE.md](DOCUMENT_RELEASE.md). Các ghi nhận bật runtime phía dưới là lịch sử lần chạy với queue rỗng.

Backend triển khai ngày09/10/2026. Internal `/api/v1/documents`, FE qua Gateway `/api/documents`; JWT/owner/envelope/no-store theo nhóm. API không chạy OCR trong request, không đọc DB User/Contract và chưa phát sự kiện Notification (T7).

## Luồng file và màn hình FE

1. POST `/api/documents/` trả202, file pending_check/job queued.
2. Poll GET `/{document_id}/processing` mỗi2–3s. Dừng khi completed/failed, rời trang hoặc lỗi auth; tạm dừng tab ẩn. Không tự gán available từ upload202.
3. Worker đọc đúng private object, kiểm tra size/hash → ClamAV → kiểm tra cấu trúc trong tiến trình riêng → scan bản chuẩn hóa → lưu safe object immutable → file available.
4. Worker trích chữ/layout trong tiến trình riêng; result + job completed commit cùng transaction. GET `/{document_id}/extraction` chỉ trả kết quả attempt mới nhất completed/hash đúng nguồn.
5. FE hiển thị text/pages/blocks/warnings cho user review. Contract tiếp tục tự parse cấu trúc DOCX từ content; chưa tạo nháp/handoff trong T6.

Hai trạng thái độc lập: available + failed nghĩa file đã qua kiểm tra nhưng OCR/extraction lỗi; vẫn tải được. rejected + failed không được tải/xem extraction. Scan không sẵn sàng không làm file available hoặc coi file là malware.

Text OCR là dữ liệu không tin cậy: FE render escaped text/textContent, không innerHTML hoặc tự thực thi/linkify nội dung tài liệu. Không dùng confidence để chứng thực danh tính.

GET processing trả document_id, job_id, attempt, status, file_status, error_code và timestamps. GET extraction trả schema_version1.0, source_sha256, source_kind, text, pages, extracted_fields, warnings, engine_version, created_at. Chỉ owner; admin không bypass. Queued/processing409 EXTRACTION_NOT_READY; failed409 EXTRACTION_FAILED; rejected409 FILE_REJECTED; deleted/khác chủ404.

## Engine và giới hạn

- DOCX: XML an toàn, thứ tự paragraph/table cell/tab/break trong body; pages=[] và DOCX_TEXT_REFERENCE_ONLY. Không dựng layout trang, không OCR ảnh nhúng; có ảnh nhúng sẽ warning. Không lấy header/footer như nội dung body. Contract phải đọc DOCX thật, không tái dựng từ text.
- PDF: PDFium trích chữ có sẵn và bbox thật, confidence=null. Trang không có chữ được render có giới hạn rồi OCR; pdf_text/pdf_scan/pdf_mixed theo tổ hợp trang. Crop/rotation được áp dụng vào bbox chuẩn hóa0..1 (gốc trên trái), page1-based; FE không xoay thêm. Trang có chữ cùng raster giữ text và warning PDF_IMAGE_REGION_NOT_OCR_ON_TEXT_PAGE, chưa OCR vùng raster trên cùng trang.
- Ảnh/scan: PaddleOCR3.7.0 + PaddlePaddle3.3.1 CPU, detector PP-OCRv5_mobile_det, recognizer latin_PP-OCRv5_mobile_rec, pretrained không train. Model tải chính thức khi build, runtime dùng local directories, không gửi file tới provider. Đây là engine T6; T4 hai ảnh CCCD vẫn Tesseract5 vie+eng.
- Ảnh được sửa EXIF orientation và tạo lại từ pixel mới, bỏ EXIF/GPS/trailing bytes. Layout ảnh coi1 pixel là1 point ở72dpi; bbox là normalized, không lấy DPI metadata làm dữ liệu đáng tin.
- Không tự suy luận thông tin người dùng/giá tiền/phone/email: T6 extracted_fields=[]; user review thủ công. Không tự lưu profile, đổi KYC hoặc cập nhật nháp cũ.
- File9MiB, PDF100trang, ảnh25triệu pixel/1frame; giới hạn render cạnh2400, text1triệu ký tự, blocktext10k,5000block/trang, JSON16MiB (có headroom serialization). Vượt giới hạn →job failed, không cắt chữ âm thầm. Trang trắng hợp lệ với blocks=[].
- Child mặc định timeout240s (config10..600), kill/reap khi timeout, POSIX CPU240s/address-space8GiB; worker container cap4GiB/2CPU, filesystem read-only/tmpfs128MiB, no-new-privileges/cap_drop. Child không nhận DB/Storage/JWT secrets; stderr/library output bị bỏ, không ghi ảnh/nội dung ra temp. Python network calls bị chặn, nhưng đây không phải sandbox OS chống mọi lỗ hổng native; hardening bổ sung thuộc T8.

## Kiểm tra an toàn, retry và recovery

ClamAV INSTREAM có giới hạn request/reply, timeout; clean mới được publish. Không có chế độ bỏ scan. DOCX từ chối macro, ActiveX, embedded object, field code/altChunk, external relationship (kể cả hyperlink external). PDF từ chối JavaScript/Launch/OpenAction/AA, file nhúng/XFA và action nguy hiểm; admission cũng chặn encrypted/signed PDF. Mức kiểm tra này không phải chứng nhận file hoàn toàn vô hại hoặc xác minh CCCD.

Worker chờ healthcheck ClamAV chính thức trước startup để không tiêu hết retry khi đang tải signature. Image antivirus pin digest; signature volume cập nhật qua FreshClam. Trước production cần vận hành cập nhật signature và nâng phiên bản/digest, không coi model/antivirus bất biến mãi mãi.

Worker khóa nguồn file trước job. Session advisory lock giữ qua I/O nhưng không giữ SQL transaction lâu; started_at là fencing token, worker cũ không commit vào job đã được nhận lại. Vì dùng session lock, kết nối worker phải Direct hoặc **Session pooler**, không Transaction pooler. Một worker xử lý một job mỗi vòng; có thể chạy nhiều instance, không có API retry thủ công.

Storage/scan transient lỗi retry tối đa3, backoff10/20/40s (SQL dùng updated_at/retry_count). Queued retry hết ngân sách →failed nhưng file pending_check không tự available/rejected. Crash processing được nhận lại cùng job, tăng retry_count; hết ngân sách →WORKER_RECOVERY_EXHAUSTED. Terminal result/job bất biến; không fallback result attempt cũ. Stale worker bị fence.

Safe path gắn owner/document/job/content hash, upsert=false; retry object đã tồn tại chỉ chấp nhận khi đọc lại đủsize/hash/bytes. Không xóa object nếu commit không rõ kết quả. Quarantine/orphan và soft-deleted file còn giữ private; retention/purge/maintenance chưa triển khai.

## Chạy trong nhóm (opt-in)

Ngày 09/10/2026 đã build/recreate riêng Document API và bật document-worker + document-antivirus bằng overlay, kết nối project Supabase Document thật. API health qua Gateway trả200, request không JWT trả401; antivirus healthy, worker running không restart, kiểm tra DB/private Storage/ClamAV và model local đạt. Queue và file đang rỗng lúc bật nên chưa nghiệm thu một file thật xuyên suốt Storage cloud. Không restart service khác. Đường dẫn JWT_KEYS_DIR trong .env local được sửa về ./.keys/keys để dùng đúng bộ key hiện có, không tạo key mới. Không cần migration mới cho T6; dùng schema001, và003/004 nếu dùng T4/T5. Giữ secrets trong backend/.env, không push GitLab/đưa FE.

Từ backend, khi nhóm muốn cho worker xử lý queue thật:

```powershell
# Build code API mới, chỉ recreate Document (không restart các service khác).
docker compose build document-service
docker compose up -d --no-deps document-service
# Overlay riêng: không thay service list/health dependencies của Compose nền.
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml build document-worker
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml up -d document-antivirus document-worker
```

Worker không cần JWT private/public key. DOCUMENT_DATABASE_URL/POOLER_HOST và Document Storage config như T1. Optional DOCUMENT_PROCESSING_TIMEOUT_SECONDS=240. Docker model dir cố định /opt/document-ocr; local cần cài requirements-worker và chuẩn bị đúng model directories. Không tự download model trong request/job.

## Kiểm thử

Nghiệm thu cuối ngày09/10/2026: **379 test liên quan pass, không skip** trên DB rỗng (36 test T6). Hai image API/worker build pass; full OpenAPI validator/Compose overlay/cú pháp pass. Model thật chạy offline cho DOCX/PDF text/scan/mixed/ảnh. Smoke full JWT/HTTP → ClamAV thật → parser/PaddleOCR thật → PostgreSQL tạm → owner-only content/extraction/hash và ZIP/DOCX chứa EICAR bị rejected đã pass. Image worker cũng chạy được read-only/tmpfs/cap-drop; engine CCCD T4 Tesseract sáu trường ảnh tổng hợp vẫn pass.

Test vòng đầu phát hiện handle PDFium không hỗ trợ with trực tiếp, dependency child test và JWT fixture; đã sửa và test lại. Bài smoke được giới hạn vào đúng document_id tổng hợp để không tiêu queue từ test khác. EICAR phải là nội dung test chuẩn trong ZIP/DOCX, không dùng chuỗi gắn cuối PNG để suy ra malware detector hỏng. Không có ảnh CCCD/tài liệu thực tế hoặc Storage cloud trong kết quả nghiệm thu này.

Test SQL chỉ chấp nhận DOCUMENT_TEST_DATABASE_URL localhost, DB document_schema_test; không đặt DSN Supabase vào biến test. Test unit dùng dữ liệu tổng hợp, scanner/Storage giả lập; script trong worker image kiểm thử model thật không mạng. Opt-in --run-local-test dùng model + ClamAV thật + JWT/HTTP/SQL tạm, **Storage vẫn in-memory**, không phải full cloud e2e. Rows tổng hợp giữ ở DB disposable rồi dọn cùng container.

```powershell
python -m pytest tests/test_document_worker.py -q
docker run --rm --network none codex-document-task6-worker python scripts/check_document_worker.py
# Chỉ khi đã chuẩn bị DB tạm/schema và ClamAV test riêng:
# docker run --rm -e DOCUMENT_TEST_DATABASE_URL=<DSN localhost guarded> codex-document-task6-worker python scripts/check_document_worker.py --run-local-test --scanner-port <test-port>
```

Đối chiếu nguồn engine: [PaddleOCR pipeline/model chính thức](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html), [PDFium Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html). Không khẳng định độ chính xác CCCD/tài liệu thực tế từ test tổng hợp.

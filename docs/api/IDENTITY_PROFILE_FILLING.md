# Luồng chốt: CCCD lưu một lần, chỉ gợi ý thông tin CCCD

**Backend profile T5 đã triển khai ngày 09/10/2026; FE chưa được nối trong task này.** GET/PUT/DELETE owner-only, sáu field user review, revision CAS; xem [Document database](../../services/document-service/database/README.md). Worker tổng quát T6 và handoff Contract T7 chưa triển khai.

## 1. Trang detail user

1. POST `/api/documents/identity-ocr` hai ảnh front/back, Idempotency-Key UUID (T4); lấy session_id và fields của session completed. Không tự tạo profile.
2. Hiển thị sáu field cho user sửa/xác nhận; thiếu hoặc không chắc để null.
3. `PUT /api/documents/identity-profile/me` với source_session_id, expected_revision và đủ sáu fields đã review.
4. Document lưu một hồ sơ CCCD current/user trong `user_identity_profiles`, không sao chép sang DB User.

FE đọc `GET /api/documents/identity-profile/me`. Không upload CCCD lại mỗi hợp đồng. “Một lần” nghĩa tái sử dụng; vẫn cho thay khi cần bằng session mới. Hai ảnh T4 là một nguồn profile. Luồng source_document_id cũ hỗ trợ available + latest completed; T6 đã có worker opt-in, file mới vẫn queued nếu nhóm chưa bật worker. FE CCCD hai mặt ưu tiên session T4. Internal URL `/api/v1/documents`, Gateway `/api/documents`.

## 2. Chỉ được gợi ý 6 thông tin CCCD v1

| key | Kiểu |
|---|---|
| full_name | text |
| date_of_birth | date YYYY-MM-DD |
| identity_number | chuỗi 12 chữ số, giữ số 0 đầu |
| issue_date | date YYYY-MM-DD |
| issue_place | text |
| permanent_address | text |

Thiếu/không đọc chắc ->null, user tự kiểm tra. Không tự điền phone/email từ User, giá tiền, thời hạn, diện tích, mục đích, điều khoản hoặc thông tin người khác. Những mục này chỉ có hướng dẫn, không có giá trị AI tự sinh.

User vẫn được tự nhập/sửa mọi field trong nháp. Giới hạn trên là giới hạn gợi ý tự động, không phải cấm nhập tay.

## 3. Khi làm hợp đồng

```text
Mẫu chung hoặc mẫu riêng → xác nhận ô điền → tạo nháp
                        ↓
Chọn bên của mình và ô nào lấy từ CCCD đã lưu
                        ↓
POST /api/contracts/drafts/{draft_id}/identity-suggestions
                        ↓
Contract GET Document identity-profile/me (không raw OCR)
                        ↓
Gợi ý CCCD + hướng dẫn các mục còn lại tự nhập
                        ↓
User chấp nhận/sửa → PATCH field_values → preview → xuất
```

Request suggestions nhận bindings, ví dụ party_a_name -> full_name. Template field phải có identity_key đã xác nhận và binding khớp key; null/thiếu là manual-only. Không nhận document_id CCCD/owner mới. User xác nhận mình là bên nào; không tự đoán từ nhãn hoặc áp CCCD của mình cho bên còn lại. Key ngoài whitelist, field không tồn tại, sai type hoặc duplicate field ->422.

Response: suggestions (có value, is_review_required=true), manual_fields (chỉ instruction, không value), identity_profile_revision và draft_updated_at. Field đã có giá trị không gợi ý ghi đè. Profile/field thiếu -> hướng dẫn nhập tay.

API suggestions phía Contract là thiết kế T7 chưa triển khai, không sửa trong T5. Thiết kế không sửa nháp; user chấp nhận thì FE PATCH, nếu nháp thay đổi khi đang chờ bỏ suggestions cũ. Không tự điền lúc generate. Required chưa điền vẫn chặn final.

Chưa có profile/CCCD chưa confirm: Document404 với code IDENTITY_PROFILE_NOT_FOUND đúng schema, Contract200 suggestions=[] + hướng dẫn nhập tay; không ép upload mới được tạo hợp đồng. 404 route chưa có không phải thiếu profile (502 BAD_RESPONSE). Document lỗi mạng/5xx phải báo502/503/504, không giả làm thiếu CCCD.

## 4. Sửa/thay/xóa và quyền truy cập

- PUT application/json <=16KiB, đủ 6 key, thiếu null, ít nhất một field có giá trị. Đúng một nguồn không-null: source_session_id completed cùng owner hoặc legacy source_document_id identity_card JPEG/PNG/PDF available + latest completed; server chọn result/hash. Không nhận owner/result ID.
- expected_revision=0 khi chưa từng có row; sửa/thay phải khớp revision, lệch409. Đổi profile không thay nháp cũ.
- DELETE profile với expected_revision soft-delete trả204 **không body** theo API_GUIDELINES nhóm. GET404 kèm error.current_revision mới; không reset revision. Lặp unlink cuối nhận revision deleted hiện tại hoặc trước lần unlink cuối, trả204 không tăng nữa. Stale DELETE không xóa profile đã recreate active.
- File nguồn profile active không xóa được (409 PROFILE_SOURCE_IN_USE); unlink/thay trước, rồi xóa file riêng nếu muốn.
- Xóa/thay CCCD không xóa dữ liệu user đã chấp nhận trong nháp/hợp đồng. Xóa bản sao cần API Contract và retention riêng.
- Profile là dữ liệu user confirm, không phải KYC; không dùng cấp quyền admin hoặc chứng thực người ký. Không log PII, không gửi CCCD qua Notification.
- Mọi API profile lấy owner từ JWT, admin không vượt quyền. Cookie mutation cần Origin/CSRF check; response no-store.

## 5. Thiết kế đã đổi

Document có GET/PUT/DELETE identity-profile/me implemented; migration 004 nối session T4, giữ legacy profile. Contract POST identity-suggestions vẫn planned; T5 không sửa Contract/User/Notification.

## 6. Validation, retention và kiểm thử T5

Chuỗi NFC/trim, không rỗng/control/format characters; full_name <=200, issue_place <=500, permanent_address <=2000. identity_number chuỗi đúng12 chữ số; ngày thật YYYY-MM-DD không tương lai, issue_date không trước date_of_birth. JSON trùng key/malformed →400; sai schema →422 không echo input. Revision integer0..2147483647, không bool/float/string. PUT thay toàn bộ sáu field, không merge âm thầm.

Owner advisory lock → nguồn cũ/mới có thứ tự → profile; recheck CAS trong transaction. Nếu mất response/timeout, GET trước khi retry PUT; không tự tăng expected_revision để ghi đè. Nguồn completed/failed và provenance session bất biến ở SQL.

Unlink chỉ ngừng dùng, **không purge** ảnh/JSONB/sáu field hoặc bản sao Contract. FK giữ session kể cả profile deleted; retention/purge chưa có. Không coi profile là KYC, không đổi role User.

Lượt cuối DB rỗng: **343 test liên quan pass, không skip**, gồm bảo toàn profile legacy khi nâng cấp, Swagger PUT và setup verifier. Image riêng codex-document-task5-test đã smoke actual OCR → JWT/HTTP/SQL tạm → user sửa/profile/CAS/owner/unlink/recreate. Ảnh tổng hợp/Storage stub, chưa CCCD thật/full cloud e2e, không restart container nhóm.

Migration additive 004 sau001/003; script mặc định chỉ đọc, thêm --apply để nâng cấp schema thiếu, không rerun nền hoặc cắt dữ liệu cũ:

```powershell
python services/document-service/scripts/setup_identity_profile.py --project-ref srrxrnohunfyyolbfsew
python services/document-service/scripts/setup_identity_profile.py --project-ref srrxrnohunfyyolbfsew --apply
```

Nhóm build/recreate riêng Document sau khi migration verify pass; health không thay kiểm tra DB/Storage.

Migration004 đã apply đúng project Supabase Document srrxrnohunfyyolbfsew ngày09/10/2026; verify chỉ đọc lại constraints/FK/triggers/RLS/browser revoke/backend privileges pass, giữ profile cũ/bucket.

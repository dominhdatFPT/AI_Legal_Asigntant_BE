# C6 — gợi ý CCCD để người dùng xem và chấp nhận

Đã triển khai `POST /api/v1/contracts/drafts/{draft_id}/identity-suggestions`. Chỉ đọc nháp của người gọi và profile đã xác nhận ở Document. Không tự sửa nháp, sinh file, gọi OCR/VNPT/LLM hoặc ghi database User/Document. Không cần migration mới.

## Nguồn theo OpenAPI nhóm

Contract gọi `GET {DOCUMENT_SERVICE_URL}/api/v1/documents/identity-profile/me` bằng Bearer user. Chỉ dùng sáu key: `full_name`, `date_of_birth`, `identity_number`, `issue_date`, `issue_place`, `permanent_address`. Kiểm tra owner đúng JWT, revision, thời điểm xác nhận và đúng một nguồn session/document. Không trả cả profile/source IDs cho FE.

Không tự trộn citizen-profile User: tên trường/địa chỉ khác và không có revision tương đương. `INTERNAL_TOKEN` dùng cho guard tạo nháp C5, không gửi Document ở C6. Profile là thông tin user xác nhận, **không phải chứng thực người ký/KYC**.

Máy chạy cần `CONTRACT_DATABASE_URL` và `DOCUMENT_SERVICE_URL`; Docker dùng hostname service. Không cần key Supabase Document trong Contract. Không đưa key/token vào Git hay Postman export.

## Postman

`base_url=http://localhost:8080/api/contracts` qua Gateway; uvicorn Contract cổng8000: `http://localhost:8000/api/v1/contracts`. Header `Authorization: Bearer {{user_access_token}}`, token lấy qua đăng nhập User. Có thể thêm `X-Request-ID: c6-postman`.

1. Chuẩn bị nháp theo [C5](README_DRAFTS.md). User `/status` phải hoạt động để tạo nháp; C6 đọc nháp đã có không gọi `/status`.
2. Người dùng xem kết quả CCCD và xác nhận profile ở Document. OCR completed một mình chưa đủ; không lấy OCR thô.
3. Version mẫu phải khai báo `identity_key` trước publish: `party_a_name` có `type=text, identity_key=full_name`; ngày sinh có `type=date, identity_key=date_of_birth`. Published bất biến: đổi mapping cần version và nháp mới. Key null/thiếu là tự nhập.
4. User chọn đúng bên của mình, xem mapping, gọi `POST {{base_url}}/drafts/{{draft_id}}/identity-suggestions`, Content-Type application/json:

```json
{"bindings":[{"field_name":"party_a_name","identity_key":"full_name"}]}
```

Không gửi owner/user_id/document_id/source. Không tự đoán bên A/B từ nhãn. `{"bindings":[]}` chỉ nhận hướng dẫn nhập tay, không gọi Document.

5. Response `{data,error,request_id}`, Cache-Control no-store. `data.suggestions` có field_name/identity_key/value/is_review_required=true; `manual_fields` chỉ field_name/reason/instruction, không value. Có identity_profile_revision (null nếu không đọc/chưa có profile), draft_updated_at và warnings.
6. User xem/sửa/bỏ từng gợi ý. Chưa chấp nhận thì không PATCH. GET lại nháp; updated_at khác mốc gợi ý thì bỏ gợi ý cũ và lấy lại. Merge **chỉ gợi ý được chấp nhận vào ô vẫn trống**, giữ mọi giá trị cũ rồi PATCH field_values qua C5.

PATCH C5 thay toàn bộ map, không merge: đừng gửi mỗi vài key làm mất dữ liệu khác. PATCH chưa có CAS/If-Match; GET lại giảm xung đột nhưng không bảo đảm chống sửa đồng thời giữa GET và PATCH. FE tránh hai thao tác lưu song song; đây không phải autosave an toàn đồng thời.

## Các trường hợp cần thử

| Trường hợp | Kết quả |
|---|---|
| Binding đúng, ô trống, profile confirmed | 200, gợi ý yêu cầu review; nháp giữ nguyên |
| Ô có giá trị, kể cả 0/false/khoảng trắng | Không ghi đè; already_filled |
| Không chọn binding | manual_only; không dùng CCCD cho bên còn lại |
| Field profile null | identity_missing, nhập tay |
| Document404 chuẩn IDENTITY_PROFILE_NOT_FOUND | 200, profile_missing + warning |
| Sai key/type, field không có, trùng field_name | 422 trước gọi Document |
| Nháp người khác, kể cả admin | 404, không đọc profile |
| Nháp đổi trong lúc lấy profile | 409 DRAFT_CHANGED; tải lại |
| Route404 chung/response sai/owner lệch | 502 DOCUMENT_BAD_RESPONSE |
| Document401/mạng hoặc5xx/timeout | 401/503/504, không fallback User/OCR |
| Cookie POST thiếu/sai Origin | 403; Bearer hợp lệ được ưu tiên |

Ngày sinh/ngày cấp chỉ map date; bốn key khác map text. Giá tiền, thời hạn, phone/email và thỏa thuận phải nhập tay. Đổi/xóa profile không tự thay dữ liệu đã chấp nhận ở nháp cũ.

## Test và giới hạn

`tests/test_contract_identity_suggestions.py` dùng JWT/profile giả, HTTP mock và SQL tạm. SQL opt-in: CONTRACT_SUGGESTION_TEST_CONTAINER=codex-contract-c6-pg, label codex.c6.disposable=true, bind localhost, database contract_suggestion_test. Không dùng URI cloud cho test. Có kiểm tra API theo OpenAPI, quyền owner, lỗi upstream, không overwrite, catalog500 field và thay đổi nháp trong lúc gọi Document.

Chưa e2e với tài khoản/CCCD thật hay deploy API nhóm. Preview/PDF C7 đã triển khai cho mẫu văn bản C4, xem [phạm vi renderer](README_GENERATIONS.md); file gốc/import C8 và Notification C9 chưa triển khai.

Kết quả10/10/2026: 69 test C6 pass (66 offline/API +3 SQL tạm); hồi quy C1–C6 429 pass không skip. Hai OpenAPI/Compose validate, Docker build và smoke non-root/read-only/network-none pass. Một warning TestClient deprecation, không test fail. Chưa commit/push.

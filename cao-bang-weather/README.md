# Cao Bằng Weather

Mini web/PWA + Android APK để so sánh dự báo thời tiết tại Cao Bằng từ 3 mô hình toàn cầu:

- ECMWF IFS HRES (Open-Meteo ECMWF API)
- NOAA GFS (Open-Meteo GFS API)
- CMA GRAPES Global (Open-Meteo CMA API)

## Tính năng

- Mặc định Thành phố Cao Bằng, có tìm địa điểm và GPS.
- So sánh nhiệt độ, mưa, gió, độ ẩm theo từng nguồn.
- Bảng 24 giờ, lấy mẫu mỗi 2 giờ.
- Xu hướng 10 ngày.
- Tính mức đồng thuận 3 nguồn về tín hiệu mưa, không đánh đồng với xác suất mưa.
- Nút mở dự báo chính thức của Trung tâm Dự báo KTTV Quốc gia.
- PWA: có thể Add to Home Screen trên iPhone/iPad.
- Android: WebView wrapper nhẹ, không cần API key.

## Chạy web

Phục vụ thư mục `web/` bằng HTTP server tĩnh:

```bash
python3 -m http.server 8080 -d web
```

## Build Android

Yêu cầu JDK 17, Gradle 8.9 và Android SDK 35.

```bash
gradle :app:assembleDebug
```

APK nằm tại `app/build/outputs/apk/debug/app-debug.apk`.

GitHub Actions trong `.github/workflows/build-apk.yml` tự build APK và lưu artifact.

## iOS

Mở bản web bằng Safari > Share > Add to Home Screen. PWA chạy toàn màn hình và dùng cùng mã nguồn web.

## Nguồn dữ liệu

Dữ liệu mô hình được truy cập qua Open-Meteo. Dự báo chính thức Việt Nam được mở trực tiếp từ NCHMF để người dùng đối chiếu; ứng dụng không giả lập hay gắn nhãn dữ liệu mô hình là dữ liệu KTTV Việt Nam.
